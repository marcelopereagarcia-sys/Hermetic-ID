"""Módulo de análisis forense digital de imágenes documentales.

Técnicas implementadas:
- Error Level Analysis (ELA) ejecutado 100% en memoria volátil (RAM).
- Detección de anomalías de compresión JPEG y cálculo de mapas de calor.
- Detección de discontinuidades de gradiente en bordes de inserción/recorte.
- Inspección de metadatos EXIF en busca de firmas de software de edición.
"""

from dataclasses import dataclass, field
import io
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
from PIL import Image, ImageChops, ImageEnhance
import cv2


@dataclass
class ELAResult:
    """Resultado del análisis de nivel de error (ELA)."""
    mean_error: float
    max_error: float
    std_error: float
    anomaly_detected: bool
    details: str
    ela_image_bytes: Optional[bytes] = None  # Imagen PNG en memoria para la UI


@dataclass
class ExifForensicResult:
    """Resultado de la inspección de metadatos EXIF."""
    has_exif: bool
    software_tag: Optional[str] = None
    is_suspicious_software: bool = False
    metadata_entries: Dict[str, str] = field(default_factory=dict)
    details: str = ""


@dataclass
class EdgeAnomalyResult:
    """Resultado de detección de discontinuidades de recorte/bordes."""
    boundary_contrast_score: float
    is_suspicious_edge: bool
    details: str


MAX_ELA_WORK_PIXELS = 25_000_000  # Tope anti-OOM (coincide con Image.MAX_IMAGE_PIXELS)
MAX_ELA_DISPLAY_DIM = 1920  # Lado máximo del mapa térmico devuelto para la interfaz

KNOWN_EDITING_SOFTWARE = [
    "photoshop", "gimp", "canva", "lightroom", "pixelmator",
    "snapseed", "paint.net", "corel", "affinity", "picsart"
]


def compute_ela(
    image_input: Union[Image.Image, bytes, np.ndarray],
    quality: int = 90,
    scale: int = 15,
    anomaly_std_threshold: float = 35.0,
) -> ELAResult:
    """Calcula el Error Level Analysis (ELA) de una imagen completamente en memoria.
    
    El ELA compara la imagen original con una versión recomprimida a calidad conocida.
    En imágenes no modificadas, el nivel de error es homogéneo.
    En imágenes donde se ha insertado un elemento digital (foto, texto o firma)
    con diferente ciclo de compresión, dicha zona muestra discrepancias estadísticas.
    
    Args:
        image_input: Imagen PIL, bytes en memoria o array NumPy (BGR o RGB).
        quality: Calidad JPEG de recompresión de referencia (default: 90).
        scale: Factor de amplificación visual de la diferencia.
        anomaly_std_threshold: Umbral de desviación estándar para alertar dispersión anómala.
        
    Returns:
        ELAResult con métricas cuantitativas y la imagen de calor en bytes.
    """
    # Proteger contra Decompression Bombs en Pillow (máx 25 Megapíxeles)
    Image.MAX_IMAGE_PIXELS = 25_000_000

    # 1. Normalizar entrada a objeto PIL RGB
    if isinstance(image_input, bytes):
        pil_orig = Image.open(io.BytesIO(image_input)).convert("RGB")
    elif isinstance(image_input, np.ndarray):
        # Asume formato OpenCV BGR -> convertir a RGB
        if len(image_input.shape) == 3 and image_input.shape[2] == 3:
            rgb_arr = cv2.cvtColor(image_input, cv2.COLOR_BGR2RGB)
        else:
            rgb_arr = image_input
        pil_orig = Image.fromarray(rgb_arr).convert("RGB")
    elif isinstance(image_input, Image.Image):
        pil_orig = image_input.convert("RGB")
    else:
        raise TypeError(f"Tipo de entrada no soportado: {type(image_input)}")

    # El ELA se calcula a resolución ORIGINAL: redimensionar antes destruye la rejilla 8x8 de
    # compresión JPEG que el análisis necesita (una foto de móvil siempre supera 1920 px).
    # Solo por encima del tope anti-OOM se reduce, y el resultado lo indica.
    w_orig, h_orig = pil_orig.size
    downscaled_for_memory = False
    if w_orig * h_orig > MAX_ELA_WORK_PIXELS:
        scale_ratio = (MAX_ELA_WORK_PIXELS / (w_orig * h_orig)) ** 0.5
        new_w = max(1, int(w_orig * scale_ratio))
        new_h = max(1, int(h_orig * scale_ratio))
        pil_orig = pil_orig.resize((new_w, new_h), Image.Resampling.LANCZOS)
        downscaled_for_memory = True

    # 2. Recomprimir temporalmente en un buffer de memoria BytesIO (memoria cero en disco)
    compressed_buffer = io.BytesIO()
    try:
        pil_orig.save(compressed_buffer, format="JPEG", quality=quality)
        compressed_buffer.seek(0)
        pil_recompressed = Image.open(compressed_buffer)

        # 3. Calcular la diferencia absoluta de píxeles
        diff = ImageChops.difference(pil_orig, pil_recompressed)
        
        # 4. Convertir a array NumPy para cálculo estadístico
        diff_arr = np.asarray(diff, dtype=np.float32)
        mean_err = float(np.mean(diff_arr))
        max_err = float(np.max(diff_arr))
        std_err = float(np.std(diff_arr))

        # 5. Generar mapa térmico forense adaptativo para visualización
        diff_gray = cv2.cvtColor(np.asarray(diff), cv2.COLOR_RGB2GRAY).astype(np.float32)

        # Mapeo térmico no lineal para resaltar picos anómalos sobre fondo homogéneo
        p90 = float(np.percentile(diff_gray, 90))
        max_e = float(np.max(diff_gray))

        ela_scaled = np.zeros_like(diff_gray, dtype=np.uint8)
        mask_low = diff_gray <= p90
        ela_scaled[mask_low] = np.clip(diff_gray[mask_low] / (p90 + 1e-5) * 55, 15, 60).astype(np.uint8)

        mask_high = diff_gray > p90
        if max_e > p90:
            ela_scaled[mask_high] = np.clip(70 + ((diff_gray[mask_high] - p90) / (max_e - p90 + 1e-5)) * 185, 80, 255).astype(np.uint8)

        # Detección forense de sobreescritura digital / retoque por pincel plano (Inpainting / Flat Brush Stroke)
        # Los trazos manuales con herramientas de dibujo móvil/PC tienen varianza local nula (sin ruido óptico de sensor)
        orig_np = np.asarray(pil_orig)
        orig_gray = cv2.cvtColor(orig_np, cv2.COLOR_RGB2GRAY)
        mean_local = cv2.blur(orig_gray.astype(np.float32), (5, 5))
        sq_local = cv2.blur((orig_gray.astype(np.float32)) ** 2, (5, 5))
        std_local = np.sqrt(np.maximum(0, sq_local - mean_local ** 2))

        # Píxeles con nivel de negro o saturación anormal y dispersión nula
        paint_mask = (orig_gray < 18) & (std_local < 2.5)
        num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(paint_mask.astype(np.uint8))
        flat_anomaly_mask = np.zeros_like(orig_gray, dtype=np.uint8)
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            if 35 <= area <= 6000:
                flat_anomaly_mask[labels == i] = 255

        # Mapa térmico JET: Azul cobalto para fondo, Amarillo/Rojo vivo para picos de compresión
        heatmap_bgr = cv2.applyColorMap(ela_scaled, cv2.COLORMAP_JET)

        # Superponer silueta estructural del documento original (25% estructura + 75% mapa térmico)
        orig_bgr = cv2.cvtColor(orig_np, cv2.COLOR_RGB2BGR)
        orig_gray_3ch = cv2.cvtColor(orig_gray, cv2.COLOR_GRAY2BGR)

        blend_bgr = cv2.addWeighted(orig_gray_3ch, 0.25, heatmap_bgr, 0.75, 0)

        # Si se detectaron trazos manuales planos de edición digital, destacarlos en rojo/naranja fosforito (#FF4500)
        if np.any(flat_anomaly_mask):
            dilated_halo = cv2.dilate(flat_anomaly_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
            blend_bgr[dilated_halo > 0] = [0, 69, 255]

        blend_rgb = cv2.cvtColor(blend_bgr, cv2.COLOR_BGR2RGB)

        ela_pil = Image.fromarray(blend_rgb)
        # Solo la imagen de visualización se reduce; las métricas ya se calcularon a resolución completa
        if max(ela_pil.size) > MAX_ELA_DISPLAY_DIM:
            ela_pil.thumbnail((MAX_ELA_DISPLAY_DIM, MAX_ELA_DISPLAY_DIM), Image.Resampling.LANCZOS)
        with io.BytesIO() as ela_out_buffer:
            ela_pil.save(ela_out_buffer, format="PNG")
            ela_bytes = ela_out_buffer.getvalue()

    finally:
        compressed_buffer.close()

    # 6. Evaluación de anomalía por bloques locales vectorizada (Patch Z-Score vía boxFilter)
    h, w = diff_gray.shape
    bs = 32
    if h >= bs and w >= bs:
        box_filtered = cv2.boxFilter(diff_gray, -1, (bs, bs), normalize=True)
        bm_grid = box_filtered[bs // 2 :: bs, bs // 2 :: bs]
        median_b = float(np.median(bm_grid))
        std_b = float(np.std(bm_grid))
        max_b = float(np.max(bm_grid))
        z_score = float((max_b - median_b) / (std_b + 1e-5))
    else:
        median_b, std_b, max_b, z_score = 0.0, 0.0, 0.0, 0.0

    # Detección de anomalías:
    # 1. Dispersión global extrema en la imagen completa (std_err >= 6.0)
    # 2. O pico local significativo (Z-score >= 5.0) respaldado por dispersión absoluta medible
    #    ((max_b - median_b) >= 0.8 y max_err >= 8.0) para descartar ruido cuántico en imágenes limpias.
    is_anomaly = bool(
        std_err >= 6.0
        or (z_score >= 5.0 and (max_b - median_b) >= 0.8 and max_err >= 8.0)
    )
    details = (
        f"Compresión uniforme en toda la superficie (Desv: {std_err:.1f}, Z-score local: {z_score:.1f})."
        if not is_anomaly
        else f"Dispersión anómala detectada en compresión local (Z-score: {z_score:.1f}, Pico: {max_err:.1f}). Indicio de alteración digital o edición por capas."
    )
    if downscaled_for_memory:
        details += " Aviso: la imagen superaba el tope de memoria y se redujo antes del ELA; fiabilidad reducida."

    return ELAResult(
        mean_error=round(mean_err, 2),
        max_error=round(max_err, 2),
        std_error=round(std_err, 2),
        anomaly_detected=is_anomaly,
        details=details,
        ela_image_bytes=ela_bytes,
    )


def inspect_image_exif(image_bytes: bytes) -> ExifForensicResult:
    """Inspecciona metadatos EXIF en busca de firmas de software de retoque fotográfico."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif_data = img.getexif()
        
        if not exif_data:
            return ExifForensicResult(
                has_exif=False,
                details="Sin metadatos EXIF incrustados (habitual en imágenes procesadas para web o exportadas limpias)."
            )

        metadata_dict: Dict[str, str] = {}
        software_detected: Optional[str] = None
        is_suspicious = False

        import html

        # Tag 305 en estándar EXIF corresponde a 'Software'
        # Tag 271 'Make', Tag 272 'Model', Tag 306 'DateTime'
        for tag_id, value in exif_data.items():
            val_clean = html.escape(str(value).strip())
            key_clean = html.escape(str(tag_id))
            metadata_dict[key_clean] = val_clean
            if tag_id == 305:  # Software tag
                software_detected = val_clean
                lower_soft = val_clean.lower()
                if any(tool in lower_soft for tool in KNOWN_EDITING_SOFTWARE):
                    is_suspicious = True

        details = (
            f"Software de edición detectado en metadatos: '{software_detected}'."
            if is_suspicious
            else ("Metadatos presentes sin firmas de edición reconocidas." if software_detected else "Metadatos estándar presentes.")
        )

        return ExifForensicResult(
            has_exif=True,
            software_tag=software_detected,
            is_suspicious_software=is_suspicious,
            metadata_entries=metadata_dict,
            details=details,
        )
    except Exception as e:
        return ExifForensicResult(
            has_exif=False,
            details=f"No se pudieron leer metadatos EXIF: {str(e)}"
        )


def analyze_photo_boundary_sharpness(
    document_image: np.ndarray,
    photo_bbox: Tuple[int, int, int, int]
) -> EdgeAnomalyResult:
    """Analiza la transición de gradiente en el borde del recuadro de la fotografía.
    
    Cuando una foto se pega digitalmente sobre un DNI escaneado sin difuminar (antialiasing)
    o con un recorte tosco, el gradiente en el contorno del recuadro muestra
    una discontinuidad matemática desproporcionada respecto a la textura del policarbonato.
    
    Args:
        document_image: Imagen completa en escala de grises o BGR (NumPy array).
        photo_bbox: Coordenadas (x, y, w, h) de la foto facial.
        
    Returns:
        EdgeAnomalyResult con puntuación y advertencia.
    """
    x, y, w, h = photo_bbox
    h_img, w_img = document_image.shape[:2]
    
    # Validar límites
    if x < 2 or y < 2 or (x + w + 2) > w_img or (y + h + 2) > h_img:
        return EdgeAnomalyResult(
            boundary_contrast_score=0.0,
            is_suspicious_edge=False,
            details="Región de fotografía demasiado próxima al borde del documento para evaluar contorno."
        )

    gray = cv2.cvtColor(document_image, cv2.COLOR_BGR2GRAY) if len(document_image.shape) == 3 else document_image

    # Extraer banda exterior e interior del contorno (grosor de 2 píxeles)
    # Comparar el gradiente Sobel a lo largo del perímetro
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    gradient_mag = np.sqrt(sobelx**2 + sobely**2)

    # Medir intensidad media de gradiente en el perímetro del rectángulo
    perimeter_pixels = []
    # Bordes horizontales (superior e inferior)
    perimeter_pixels.extend(gradient_mag[y, x:x+w].tolist())
    perimeter_pixels.extend(gradient_mag[y+h, x:x+w].tolist())
    # Bordes verticales (izquierdo y derecho)
    perimeter_pixels.extend(gradient_mag[y:y+h, x].tolist())
    perimeter_pixels.extend(gradient_mag[y:y+h, x+w].tolist())

    mean_perimeter_grad = float(np.mean(perimeter_pixels))
    
    # Gradiente medio en el resto del documento para normalizar
    global_mean_grad = float(np.mean(gradient_mag)) + 1e-5
    relative_edge_ratio = mean_perimeter_grad / global_mean_grad

    # Un ratio excesivamente alto (> 4.5) con gradiente perimetral absoluto fuerte indica un contorno artificial de recorte
    is_suspicious = bool(relative_edge_ratio > 4.5 and mean_perimeter_grad > 60.0)
    
    return EdgeAnomalyResult(
        boundary_contrast_score=round(relative_edge_ratio, 2),
        is_suspicious_edge=is_suspicious,
        details=(
            f"Transición de borde natural (Ratio: {relative_edge_ratio:.2f})."
            if not is_suspicious
            else f"Borde perimetral artificialmente abrupto (Ratio: {relative_edge_ratio:.2f}x la media del fondo). Indicio de recorte superpuesto."
        )
    )
