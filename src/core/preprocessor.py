"""Módulo de preprocesamiento y control de calidad de imágenes documentales.

Funciones:
- Evaluación de desenfoque (Blur detection) mediante varianza del operador Laplaciano.
- Detección de reflejos especulares (glare/overexposure) y sombras severas.
- Verificación de resolución mínima aceptable para lectura óptica fiable.
- Binarización adaptativa optimizada para bandas de caracteres OCR-B (MRZ).
"""

from dataclasses import dataclass
from typing import Optional, Tuple, Union
import numpy as np
import cv2
from PIL import Image


@dataclass
class QualityAssessmentResult:
    """Evaluación técnica de calidad de la imagen para determinar viabilidad de análisis."""
    is_acceptable: bool
    blur_score: float  # Varianza del Laplaciano (mayor = más nítida)
    is_blurred: bool
    glare_percentage: float  # % de píxeles sobreexpuestos
    has_excessive_glare: bool
    mean_brightness: float  # 0 a 255
    is_underexposed: bool
    resolution: Tuple[int, int]  # (ancho, alto)
    has_sufficient_resolution: bool
    saturation_mean: float = 0.0
    is_monochrome: bool = False
    summary: str = ""


def assess_image_quality(
    image: Union[np.ndarray, Image.Image],
    blur_threshold: float = 80.0,
    glare_max_percent: float = 12.0,
    min_resolution: Tuple[int, int] = (600, 380),
    max_resolution: Tuple[int, int] = (6000, 6000),
    monochrome_threshold: float = 5.0,
    allow_monochrome: bool = True,
) -> QualityAssessmentResult:
    """Evalúa objetivamente si una imagen reúne las condiciones mínimas para un análisis fiable.
    
    Evita falsos positivos y falsos negativos derivados de fotos movidas o con destellos de flash.
    """
    Image.MAX_IMAGE_PIXELS = 25_000_000

    # 1. Normalizar a NumPy array BGR
    if isinstance(image, Image.Image):
        rgb_arr = np.array(image.convert("RGB"))
        img_bgr = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2BGR)
    elif isinstance(image, np.ndarray):
        img_bgr = image
    else:
        raise TypeError(f"Formato no compatible: {type(image)}")

    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if len(img_bgr.shape) == 3 else img_bgr

    # 2. Resolución mínima y máxima de seguridad
    min_w, min_h = min_resolution
    max_w, max_h = max_resolution
    has_suff_res = (w >= min_w and h >= min_h and w <= max_w and h <= max_h)

    # 3. Detección de desenfoque (Varianza del Laplaciano)
    laplacian_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    is_blurred = (laplacian_var < blur_threshold)

    # 4. Detección de deslumbramiento / reflejos especulares (>250 en escala de grises)
    total_pixels = float(h * w)
    glare_pixels = float(np.sum(gray >= 250))
    glare_pct = (glare_pixels / total_pixels) * 100.0
    has_glare = (glare_pct > glare_max_percent)

    # 5. Detección de subexposición
    mean_brightness = float(np.mean(gray))
    is_underexposed = (mean_brightness < 45.0)

    # 6. Detección de fotocopia o imagen monocromática (en espacio HSV)
    if len(img_bgr.shape) == 3 and img_bgr.shape[2] == 3:
        hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        sat_mean = float(np.mean(hsv[:, :, 1]))
    else:
        sat_mean = 0.0
    is_monochrome = (sat_mean < monochrome_threshold)

    # Diagnóstico global
    issues = []
    if not has_suff_res:
        if w < min_w or h < min_h:
            issues.append(f"Resolución baja ({w}x{h} px, mínimo recomendado: {min_w}x{min_h})")
        else:
            issues.append(f"Resolución excesiva ({w}x{h} px, máximo permitido: {max_w}x{max_h})")
    if is_blurred:
        issues.append(f"Imagen desenfocada o movida (Score: {laplacian_var:.1f} < {blur_threshold})")
    if has_glare:
        issues.append(f"Reflejos o destellos excesivos ({glare_pct:.1f}% de sobreexposición)")
    if is_underexposed:
        issues.append(f"Imagen demasiado oscura (Brillo medio: {mean_brightness:.1f})")
    if is_monochrome and not allow_monochrome:
        issues.append(f"Documento monocromático o fotocopia en blanco y negro (Saturación: {sat_mean:.1f} < {monochrome_threshold})")

    is_acceptable = len(issues) == 0
    summary = "Calidad de imagen adecuada para auditoría." if is_acceptable else "; ".join(issues)

    return QualityAssessmentResult(
        is_acceptable=is_acceptable,
        blur_score=round(laplacian_var, 2),
        is_blurred=is_blurred,
        glare_percentage=round(glare_pct, 2),
        has_excessive_glare=has_glare,
        mean_brightness=round(mean_brightness, 2),
        is_underexposed=is_underexposed,
        resolution=(w, h),
        has_sufficient_resolution=has_suff_res,
        saturation_mean=round(sat_mean, 2),
        is_monochrome=is_monochrome,
        summary=summary,
    )


def preprocess_mrz_region(mrz_crop: np.ndarray) -> np.ndarray:
    """Aplica binarización y filtrado morfológico para maximizar la legibilidad de la banda OCR-B.
    
    Acentúa el contraste entre los caracteres de la MRZ y el fondo guilloche de seguridad.
    """
    gray = cv2.cvtColor(mrz_crop, cv2.COLOR_BGR2GRAY) if len(mrz_crop.shape) == 3 else mrz_crop.copy()
    
    # Reducir ruido de fondo conservando contornos
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)
    
    # Binarización adaptativa con método de Otsu
    _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    
    # Invertir si el fondo quedó negro y el texto blanco (queremos texto oscuro sobre fondo blanco)
    if np.mean(thresh) < 127:
        thresh = cv2.bitwise_not(thresh)
        
    return thresh
