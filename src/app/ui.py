"""Interfaz gráfica interactiva local de Hermetic-ID.

Diseño:
- Arquitectura 100% Local / On-Premise (Privacy-by-Design, sin telemetría ni cookies).
- Inspección multicara simultánea (Anverso y Reverso con cruce determinista).
- Mapas térmicos forenses ELA visibles (Colormap Inferno de alta resolución).
- Panel de Control KYC directivo para gestores con vigencia y caducidad automática.
- Toolbar de acciones rápidas y reseteo de expedientes.
"""

import atexit
import html
import io
import json
import os
import re
import shutil
import tempfile
from datetime import date
from typing import List, Optional, Tuple
import cv2
import numpy as np
from PIL import Image

# Configuración estricta de privacidad ANTES de importar Gradio:
# - Sin telemetría.
# - Las subidas de Gradio se escriben en disco (no hay alternativa en memoria): se confinan a una
#   carpeta dedicada que se purga periódicamente, al arrancar y al cerrar (ver purge_upload_cache).
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ.setdefault("GRADIO_TEMP_DIR", os.path.join(tempfile.gettempdir(), "hermetic_id_uploads"))
UPLOAD_CACHE_DIR = os.environ["GRADIO_TEMP_DIR"]
# Cada UPLOAD_PURGE_EVERY_S segundos se borran las subidas con más de UPLOAD_MAX_AGE_S segundos
UPLOAD_PURGE_EVERY_S = 60
UPLOAD_MAX_AGE_S = 300

Image.MAX_IMAGE_PIXELS = 25_000_000

# Valor del desplegable que deja que el tipo de documento se deduzca de la MRZ o del número
DOC_TYPE_AUTO = "Auto (detectar)"

import gradio as gr

from src.core.algorithms import mrz_check_passed, validate_dni, validate_nie, validate_mrz_td1
from src.core.forensics import compute_ela, inspect_image_exif, analyze_photo_boundary_sharpness
from src.core.preprocessor import assess_image_quality
from src.detectors.ocr_engine import (
    DocumentFrontData,
    DocumentAutoDetector,
    MRZTextCleaner,
    cross_verify_front_with_mrz,
    create_manager_summary,
)
from src.reporting.report_generator import build_audit_report, RiskLevel, ManagerSummary
from tests.fixtures.synthetic_generator import (
    create_specimen_front_image,
    create_specimen_back_image,
    generate_synthetic_mrz_td1,
)

CORPORATE_CSS = """
/* Reset y paleta corporativa */
:root {
  --corporate-primary: #1e40af;
  --corporate-primary-hover: #1d4ed8;
  --corporate-navy: #0f172a;
  --corporate-slate: #1e293b;
  --corporate-bg: #f8fafc;
  --corporate-card-bg: #ffffff;
  --corporate-border: #e2e8f0;
}

body, .gradio-container {
  background-color: var(--corporate-bg) !important;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
  color: #1e293b !important;
}

/* Header Corporativo */
.corp-header {
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  color: #ffffff;
  padding: 20px 24px;
  border-radius: 12px;
  margin-bottom: 16px;
  box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.corp-header .title-group h1 {
  font-size: 22px;
  font-weight: 700;
  margin: 0;
  color: #ffffff;
  display: flex;
  align-items: center;
  gap: 8px;
}

.corp-header .title-group p {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: #94a3b8;
}

.corp-header .badge-privacy {
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #34d399;
  padding: 6px 14px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 6px;
}

/* Botones y Toolbar */
button.primary-analyze-btn {
  background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
  color: #ffffff !important;
  font-weight: 700 !important;
  font-size: 15px !important;
  padding: 12px 24px !important;
  border-radius: 8px !important;
  border: none !important;
  box-shadow: 0 4px 6px -1px rgba(37, 99, 235, 0.25) !important;
  transition: all 0.2s ease !important;
}

button.primary-analyze-btn:hover {
  background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%) !important;
  transform: translateY(-1px);
  box-shadow: 0 6px 10px -1px rgba(37, 99, 235, 0.35) !important;
}

/* Botones de Barra de Acciones */
.toolbar-btn {
  background: #ffffff !important;
  border: 1px solid #cbd5e1 !important;
  color: #0f172a !important;
  font-weight: 600 !important;
  font-size: 13px !important;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05) !important;
  border-radius: 6px !important;
}

.toolbar-btn:hover {
  background: #f1f5f9 !important;
  border-color: #94a3b8 !important;
}

/* Pestañas de Gradio de Alto Contraste */
button.tab-nav, .tab-nav button {
  font-size: 14px !important;
  font-weight: 600 !important;
  color: #475569 !important;
  padding: 10px 18px !important;
  border-bottom: 2px solid transparent !important;
}

button.tab-nav.selected, .tab-nav button.selected {
  color: #1e40af !important;
  border-bottom: 2px solid #2563eb !important;
  font-weight: 700 !important;
}
"""


def purge_upload_cache() -> None:
    """Elimina todas las subidas que Gradio haya dejado en la carpeta temporal dedicada."""
    shutil.rmtree(UPLOAD_CACHE_DIR, ignore_errors=True)


def _load_image_to_memory(img_input) -> Tuple[Optional[Image.Image], Optional[bytes]]:
    """Carga la imagen en memoria conservando los BYTES ORIGINALES del archivo.

    Los bytes originales son imprescindibles para el análisis forense: re-codificar la imagen
    elimina los metadatos EXIF y sustituye la historia de compresión JPEG del archivo.
    Solo las entradas que ya llegan decodificadas (array/PIL) se codifican a PNG sin pérdida.
    """
    if img_input is None:
        return None, None
    try:
        path = img_input.get("path") if isinstance(img_input, dict) else img_input if isinstance(img_input, str) else None
        if path:
            with open(path, "rb") as fh:
                raw_bytes = fh.read()
            pil_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
            return pil_img, raw_bytes

        if isinstance(img_input, np.ndarray):
            pil_img = Image.fromarray(img_input).convert("RGB")
        elif isinstance(img_input, Image.Image):
            pil_img = img_input.convert("RGB")
        else:
            return None, None

        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return pil_img, buf.getvalue()
    except Exception:
        return None, None


def render_corporate_kyc_dashboard(
    report,
    summary: Optional[ManagerSummary]
) -> str:
    """Genera un cuadro de mando ejecutivo unificado estilo FinTech / Corporate KYC.

    Todo dato procedente del OCR se escapa: el texto de una imagen manipulada no debe poder inyectar HTML.
    """
    esc = lambda value: html.escape(str(value)) if value is not None else ""
    # 1. Definición del estado de seguridad y riesgo
    if report.risk_level == RiskLevel.CLEAR:
        verdict_bg = "#ecfdf5"
        verdict_border = "#10b981"
        verdict_color = "#047857"
        verdict_icon = "🛡️"
        verdict_title = "EXPEDIENTE SIN INCONSISTENCIAS"
        risk_pill = f"<span style='background: #059669; color: white; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 12px;'>RIESGO: {report.risk_score}/100 · CONTROLES OK: {report.controls_passed}</span>"
    elif report.risk_level == RiskLevel.WARNING:
        verdict_bg = "#fffbeb"
        verdict_border = "#f59e0b"
        verdict_color = "#b45309"
        verdict_icon = "⚠️"
        verdict_title = "ADVERTENCIA: REVISIÓN MANUAL REQUERIDA"
        risk_pill = f"<span style='background: #fef3c7; color: #92400e; border: 1px solid #f59e0b; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 12px;'>RIESGO: {report.risk_score}/100 · AVISOS: {report.controls_warning}</span>"
    else:
        verdict_bg = "#fef2f2"
        verdict_border = "#ef4444"
        verdict_color = "#b91c1c"
        verdict_icon = "🚨"
        verdict_title = "INCONSISTENCIAS DETECTADAS / NO VÁLIDO"
        risk_pill = f"<span style='background: #dc2626; color: white; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 12px;'>RIESGO: {report.risk_score}/100 · FALLOS: {report.controls_failed}</span>"

    # 2. Datos de filiación del gestor
    if summary:
        if summary.is_expired:
            validity_badge = f"<span style='background: #dc2626; color: white; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 12px;'>🚨 CADUCADO ({abs(summary.days_until_expiry or 0)} días)</span>"
            validity_color = "#dc2626"
        elif summary.days_until_expiry is not None and summary.days_until_expiry <= 30:
            validity_badge = f"<span style='background: #fef3c7; color: #92400e; border: 1px solid #f59e0b; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 12px;'>⚠️ VENCE EN {summary.days_until_expiry} DÍAS</span>"
            validity_color = "#b45309"
        else:
            validity_badge = f"<span style='background: #059669; color: white; padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 12px;'>✅ EN VIGOR ({summary.days_until_expiry} días)</span>"
            validity_color = "#059669"

        age_text = f" ({summary.age_years} años)" if summary.age_years is not None else ""
        support_display = f" · Soporte: <code>{esc(summary.support_number)}</code>" if summary.support_number else ""

        kyc_grid = f"""
        <div style='display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-top: 14px;'>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Titular del Documento</div>
            <div style='font-size: 15px; font-weight: 700; color: #0f172a; margin-top: 4px;'>{esc(summary.full_name)}</div>
          </div>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Nº Identificador Oficial</div>
            <div style='font-size: 15px; font-weight: 700; color: #1e40af; font-family: monospace; margin-top: 4px;'>{esc(summary.doc_number)}{support_display}</div>
          </div>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Tipo y Nacionalidad</div>
            <div style='font-size: 14px; font-weight: 600; color: #334155; margin-top: 4px;'>{esc(summary.doc_type)} <span style='background: #e2e8f0; padding: 2px 6px; border-radius: 4px; font-size: 12px;'>{esc(summary.nationality)}</span></div>
          </div>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Fecha de Nacimiento</div>
            <div style='font-size: 14px; font-weight: 600; color: #334155; margin-top: 4px;'>{esc(summary.birth_date)}{age_text}</div>
          </div>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Fecha de Caducidad</div>
            <div style='font-size: 14px; font-weight: 700; color: {validity_color}; margin-top: 4px;'>{esc(summary.expiry_date)}</div>
          </div>
          <div style='background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px;'>
            <div style='color: #64748b; font-size: 11px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px;'>Vigencia Administrativa</div>
            <div style='margin-top: 4px;'>{validity_badge}</div>
          </div>
        </div>
        """
    else:
        kyc_grid = "<div style='color: #64748b; font-size: 13px; margin-top: 10px; font-style: italic;'>Cargue el documento para extraer la filiación KYC de forma desatendida.</div>"

    dashboard_html = f"""
    <div style='background: #ffffff; border: 1px solid {verdict_border}; border-top: 4px solid {verdict_border}; border-radius: 10px; padding: 18px; box-shadow: 0 2px 5px rgba(0,0,0,0.04); margin-bottom: 16px;'>
      <div style='display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; flex-wrap: wrap; gap: 8px;'>
        <div>
          <span style='font-size: 16px; font-weight: 800; color: {verdict_color}; letter-spacing: -0.3px;'>
            {verdict_icon} {verdict_title}
          </span>
          <div style='font-size: 13px; color: #64748b; margin-top: 2px;'>{esc(report.summary_message)}</div>
        </div>
        <div>
          {risk_pill}
        </div>
      </div>
      
      {kyc_grid}
    </div>
    """
    return dashboard_html


def run_document_audit(
    front_image_input,
    back_image_input,
    doc_type: str,
    front_doc_number: str,
    front_expiry_date: str,
    front_birth_date: str,
    front_surname: str,
    mrz_text: str,
    progress=gr.Progress(track_tqdm=True),
) -> Tuple[str, str, Optional[Image.Image], Optional[Image.Image], str, str]:
    """Ejecuta el pipeline completo de auditoría forense con soporte multicara y renderizado corporate."""
    try:
        progress(0.1, desc="📥 Cargando y normalizando imágenes en memoria volátil...")
        front_pil, front_bytes = _load_image_to_memory(front_image_input)
        back_pil, back_bytes = _load_image_to_memory(back_image_input)

        # Si no se pasó ninguna imagen, cargar anverso y reverso sintéticos para demo
        if front_pil is None and back_pil is None:
            front_pil = create_specimen_front_image()
            back_pil = create_specimen_back_image()
            front_buf = io.BytesIO()
            front_pil.save(front_buf, format="JPEG", quality=95)
            front_bytes = front_buf.getvalue()
            back_buf = io.BytesIO()
            back_pil.save(back_buf, format="JPEG", quality=95)
            back_bytes = back_buf.getvalue()

        # Si solo se subió una cara en el slot de anverso pero contiene MRZ, clasificarla como reverso.
        # El resultado del OCR se reutiliza después para no ejecutar EasyOCR dos veces sobre la misma imagen.
        cached_scan_front = None
        cached_scan_back = None
        if front_pil is not None and back_pil is None:
            probe_scan = DocumentAutoDetector.analyze_image_auto(front_pil)
            if probe_scan.is_mrz_detected:
                back_pil, back_bytes = front_pil, front_bytes
                front_pil, front_bytes = None, None
                cached_scan_back = probe_scan
            else:
                cached_scan_front = probe_scan

        # 1. Controles de Calidad y Forense ELA Térmico
        progress(0.25, desc="🔬 Ejecutando análisis térmico ELA, metadatos EXIF y contorno...")
        primary_quality = None
        front_ela_img = None
        back_ela_img = None
        primary_ela = None
        primary_exif = None
        primary_edge = None

        if front_pil is not None and front_bytes is not None:
            q_front = assess_image_quality(front_pil)
            ela_front = compute_ela(front_pil, quality=90, scale=15)
            exif_front = inspect_image_exif(front_bytes)
            primary_quality = q_front
            primary_ela = ela_front
            primary_exif = exif_front
            if ela_front.ela_image_bytes:
                front_ela_img = Image.open(io.BytesIO(ela_front.ela_image_bytes))

            # Detección del contorno de fotografía facial en anverso (estándar ID-1 ~25% ancho, ~55% alto en cuadrante izquierdo)
            w_f, h_f = front_pil.size
            photo_box_est = (int(w_f * 0.05), int(h_f * 0.12), int(w_f * 0.28), int(h_f * 0.62))
            try:
                front_np = cv2.cvtColor(np.array(front_pil), cv2.COLOR_RGB2BGR)
                primary_edge = analyze_photo_boundary_sharpness(front_np, photo_box_est)
            except Exception:
                primary_edge = None

        if back_pil is not None and back_bytes is not None:
            q_back = assess_image_quality(back_pil)
            ela_back = compute_ela(back_pil, quality=90, scale=15)
            exif_back = inspect_image_exif(back_bytes)
            if primary_quality is None or not q_back.is_acceptable:
                primary_quality = q_back
            if primary_ela is None or ela_back.anomaly_detected:
                primary_ela = ela_back
            if primary_exif is None or exif_back.is_suspicious_software:
                primary_exif = exif_back
            if ela_back.ela_image_bytes:
                back_ela_img = Image.open(io.BytesIO(ela_back.ela_image_bytes))

        # 2. Extracción OCR Automática
        progress(0.60, desc="👁️ Procesando OCR local y decodificación MRZ ICAO TD1...")
        clean_doc_number = (front_doc_number or "").strip().upper()
        clean_expiry = (front_expiry_date or "").strip()
        clean_birth = (front_birth_date or "").strip()
        clean_surname = (front_surname or "").strip()
        clean_mrz = (mrz_text or "").strip()

        auto_ocr_info = []
        detected_front_surname = None
        detected_front_names = None
        detected_front_doc = None
        detected_front_expiry = None
        detected_front_birth = None
        detected_front_support = None

        ocr_corrections: List[str] = []

        # Escanear Anverso si está presente
        if front_pil is not None:
            scan_front = cached_scan_front or DocumentAutoDetector.analyze_image_auto(front_pil)
            if scan_front.detected_doc_number:
                detected_front_doc = scan_front.detected_doc_number
                auto_ocr_info.append(f"**Anverso**: Identificador `{detected_front_doc}` ({scan_front.detected_doc_type or 'Detectado'}).")
            if scan_front.detected_surname:
                detected_front_surname = scan_front.detected_surname
                auto_ocr_info.append(f"**Anverso**: Titular `{detected_front_surname}`.")
            if scan_front.detected_given_names:
                detected_front_names = scan_front.detected_given_names
            if scan_front.detected_expiry_date:
                detected_front_expiry = scan_front.detected_expiry_date
            if scan_front.detected_birth_date:
                detected_front_birth = scan_front.detected_birth_date
            if scan_front.detected_support_number:
                detected_front_support = scan_front.detected_support_number

        # Escanear Reverso si está presente
        mrz_res = None
        detected_back_doc = None
        detected_back_expiry = None
        detected_back_birth = None
        detected_back_surname = None
        detected_back_names = None
        detected_nationality = "ESP"

        if back_pil is not None and not clean_mrz:
            scan_back = cached_scan_back or DocumentAutoDetector.analyze_image_auto(back_pil)
            if scan_back.is_mrz_detected and scan_back.mrz_result:
                mrz_res = scan_back.mrz_result
                ocr_corrections.extend(scan_back.ocr_corrections)
                auto_ocr_info.append(
                    f"**Reverso**: Banda MRZ decodificada (`{html.escape(scan_back.detected_doc_number or '-')}`, "
                    f"{'válida' if mrz_res.is_valid else 'con fallos de control'})."
                )

        # MRZ manual de override
        if clean_mrz:
            raw_mrz_lines = [l.strip() for l in clean_mrz.split("\n") if l.strip()]
            if len(raw_mrz_lines) == 3:
                sanitized_lines = MRZTextCleaner.sanitize_mrz_lines(raw_mrz_lines)
                mrz_res = validate_mrz_td1(sanitized_lines)

        if mrz_res:
            # En DNI/TIE el número del titular es el DNI/NIE de los datos opcionales, no el soporte
            detected_back_doc = mrz_res.personal_number or mrz_res.document_number
            detected_back_expiry = mrz_res.expiry_date
            detected_back_birth = mrz_res.birth_date
            detected_back_surname = mrz_res.surname
            detected_back_names = mrz_res.given_names
            detected_nationality = mrz_res.nationality

        # Consolidar datos de anverso (priorizando manual > OCR anverso > OCR reverso)
        effective_doc_number = clean_doc_number or detected_front_doc or detected_back_doc or ""
        effective_expiry = clean_expiry or detected_front_expiry or detected_back_expiry or ""
        effective_birth = clean_birth or detected_front_birth or detected_back_birth or ""
        effective_surname = clean_surname or detected_front_surname or detected_back_surname or ""
        effective_names = detected_front_names or detected_back_names or ""
        effective_support = detected_front_support or (mrz_res.support_number if mrz_res else None) or ""

        # 3. Comprobaciones Algorítmicas Directas (Módulo 23): solo si el identificador tiene forma de DNI/NIE
        direct_checks = []
        normalized_doc = re.sub(r"[\s\-]", "", effective_doc_number.upper())
        if normalized_doc[:1] in ("X", "Y", "Z"):
            direct_checks.append(validate_nie(normalized_doc))
        elif normalized_doc[:8].isdigit():
            direct_checks.append(validate_dni(normalized_doc))

        # 4. Cruce de Coherencia Automático Anverso vs Reverso
        progress(0.85, desc="⚖️ Verificando cruce de coherencia anverso-reverso y Módulo 23...")
        # Solo se cruzan datos que proceden del anverso (manuales u OCR del anverso). Los "effective_*"
        # pueden venir de la propia MRZ y cruzarlos consigo misma daría coincidencias vacías.
        cross_res = None
        front_data = DocumentFrontData(
            document_number=clean_doc_number or detected_front_doc or None,
            support_number=detected_front_support or None,
            expiry_date=clean_expiry or detected_front_expiry or None,
            birth_date=clean_birth or detected_front_birth or None,
            surname=clean_surname or detected_front_surname or None,
        )
        if mrz_res and any((front_data.document_number, front_data.support_number, front_data.expiry_date,
                            front_data.birth_date, front_data.surname)):
            cross_res = cross_verify_front_with_mrz(front_data, mrz_res)

        # 5. Resumen del Gestor y Vigencia Temporal
        full_name_cand = f"{effective_surname} {effective_names}".strip() if (effective_surname or effective_names) else None
        # Vigencia y edad: manda la fecha de la MRZ si su dígito de control es correcto. La del anverso
        # puede ser justo la manipulada (p. ej. caducidad 2035 pintada sobre un documento de 2030).
        summary_expiry = mrz_res.expiry_date if mrz_check_passed(mrz_res, "MRZ_EXPIRY_CHECKSUM") else effective_expiry
        summary_birth = mrz_res.birth_date if mrz_check_passed(mrz_res, "MRZ_BIRTH_CHECKSUM") else effective_birth
        manager_summary = create_manager_summary(
            doc_number=effective_doc_number,
            # El desplegable solo manda si el operador elige un tipo; si no, se usa el detectado
            doc_type=None if (not doc_type or doc_type == DOC_TYPE_AUTO) else doc_type,
            full_name=full_name_cand,
            surname=effective_surname,
            given_names=effective_names,
            birth_date_str=summary_birth,
            expiry_date_str=summary_expiry,
            nationality=detected_nationality,
            support_number=effective_support,
        )

        # 6. Informe Consolidado de Riesgo
        report = build_audit_report(
            quality=primary_quality,
            ela=primary_ela,
            exif=primary_exif,
            edge=primary_edge,
            mrz=mrz_res,
            crosscheck=cross_res,
            direct_checks=direct_checks,
            manager_summary=manager_summary,
            ocr_corrections=ocr_corrections,
        )

        # 7. Cuadro de Mando Ejecutivo Corporate
        kyc_dashboard_html = render_corporate_kyc_dashboard(report, manager_summary)

        # Checklist Formateado
        checklist_md = "| Control Técnico de Seguridad | Estado | Diagnóstico Forense |\n| :--- | :---: | :--- |\n"
        for item in report.checklist:
            if item["estado"] == "OK":
                icon = "<span style='color: #059669; font-weight: 700;'>● OK</span>"
            elif item["estado"] == "ADVERTENCIA":
                icon = "<span style='color: #d97706; font-weight: 700;'>▲ AVISO</span>"
            else:
                icon = "<span style='color: #dc2626; font-weight: 700;'>✖ FALLO</span>"
            detalle = html.escape(str(item["detalle"])).replace("|", "\\|")
            checklist_md += f"| {html.escape(item['control'])} | {icon} | {detalle} |\n"

        if ocr_corrections:
            auto_ocr_info.append(
                "**Correcciones heurísticas aplicadas al OCR** (revisar manualmente):\n"
                + "\n".join(f"  - {html.escape(c)}" for c in ocr_corrections)
            )

        if auto_ocr_info:
            ocr_trace_md = "### 👁️ Trazabilidad OCR Local (Extracción Desatendida)\n" + "\n".join(f"- {info}" for info in auto_ocr_info)
        else:
            ocr_trace_md = "No se requirieron lecturas automáticas o los datos fueron introducidos manualmente."

        json_report = report.to_json(indent=2)

        return (
            kyc_dashboard_html,
            ocr_trace_md,
            front_ela_img,
            back_ela_img,
            checklist_md,
            json_report,
        )

    except Exception as err:
        err_html = f"""
        <div style='background: #fef2f2; border: 1px solid #ef4444; border-radius: 8px; padding: 14px; color: #b91c1c;'>
          <strong>❌ Error interno en auditoría:</strong> {html.escape(str(err))}
        </div>
        """
        return err_html, f"Error: {html.escape(str(err))}", None, None, "Error al generar controles.", "{}"


def load_preset_specimen_valid():
    """Carga muestra sintética válida en ambas caras."""
    front_img = create_specimen_front_image(
        doc_number="12345678Z",
        name="CARMEN GARCIA LOPEZ",
        expiry_date="01/01/2030",
        birth_date="01/01/1985",
        support_number="BAA000001",
        tampered=False,
    )
    back_img = create_specimen_back_image(
        doc_number="12345678Z",
        birth_date="850101",
        expiry_date="300101",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
        nationality="ESP",
    )
    mrz_lines = "\n".join(generate_synthetic_mrz_td1(
        personal_number="12345678Z",
        birth_date="850101",
        expiry_date="300101",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
    ))
    return front_img, back_img, "DNI 4.0 / 3.0", "12345678Z", "01/01/2030", "01/01/1985", "GARCIA LOPEZ", mrz_lines


def load_preset_specimen_tampered():
    """Carga muestra sintética manipulada (discrepancia anverso 2035 vs reverso 2030)."""
    front_img = create_specimen_front_image(
        doc_number="12345678Z",
        name="CARMEN GARCIA LOPEZ",
        expiry_date="01/01/2035",
        birth_date="01/01/1985",
        support_number="BAA000001",
        tampered=True,
    )
    back_img = create_specimen_back_image(
        doc_number="12345678Z",
        birth_date="850101",
        expiry_date="300101",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
        nationality="ESP",
    )
    mrz_lines = "\n".join(generate_synthetic_mrz_td1(
        personal_number="12345678Z",
        birth_date="850101",
        expiry_date="300101",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
    ))
    return front_img, back_img, "DNI 4.0 / 3.0", "12345678Z", "01/01/2035", "01/01/1985", "GARCIA LOPEZ", mrz_lines


def load_preset_specimen_expired():
    """Carga muestra sintética de documento caducado."""
    front_img = create_specimen_front_image(
        doc_number="12345678Z",
        name="CARMEN GARCIA LOPEZ",
        expiry_date="15/03/2024",
        birth_date="01/01/1985",
        support_number="BAA000001",
        tampered=False,
    )
    back_img = create_specimen_back_image(
        doc_number="12345678Z",
        birth_date="850101",
        expiry_date="240315",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
        nationality="ESP",
    )
    mrz_lines = "\n".join(generate_synthetic_mrz_td1(
        personal_number="12345678Z",
        birth_date="850101",
        expiry_date="240315",
        surname="GARCIA LOPEZ",
        given_names="CARMEN",
    ))
    return front_img, back_img, "DNI 4.0 / 3.0", "12345678Z", "15/03/2024", "01/01/1985", "GARCIA LOPEZ", mrz_lines


def reset_audit_case():
    """Limpia todos los campos para iniciar un nuevo expediente sin residuos (incluidas las subidas en disco)."""
    purge_upload_cache()
    return (
        None,             # front
        None,             # back
        DOC_TYPE_AUTO,    # doc_type
        "",               # doc_num
        "",               # expiry
        "",               # birth
        "",               # surname
        "",               # mrz
        "",               # dashboard html
        "",               # ocr trace
        None,             # ela front
        None,             # ela back
        "",               # checklist
        "{}",             # json
    )


def build_app() -> gr.Blocks:
    """Construye la interfaz corporativa moderna."""
    with gr.Blocks(
        title="Hermetic-ID | Inspector local de documentos de identidad",
        delete_cache=(UPLOAD_PURGE_EVERY_S, UPLOAD_MAX_AGE_S),
    ) as demo:
        # Header Corporativo
        gr.HTML(
            """
            <div class="corp-header">
              <div class="title-group">
                <h1>🛡️ Hermetic-ID <span style="font-weight: 700; color: #38bdf8; font-size: 15px; margin-left: 8px; padding: 2px 10px; background: rgba(56, 189, 248, 0.2); border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 6px;">Beta</span></h1>
                <p style="color: #cbd5e1; margin-top: 4px;">Auditoría de Integridad Documental, OCR-B y Coherencia Multicara (DNI · NIE · TIE)</p>
              </div>
              <div class="badge-privacy">
                <span>🔒</span> 100% On-Premise · Sin Telemetría · Subidas Purgadas en 5 min
              </div>
            </div>
            """
        )

        with gr.Row():
            # COLUMNA IZQUIERDA: CAPTURA DE DOCUMENTOS
            with gr.Column(scale=5):
                gr.Markdown("#### 📥 Captura Documental (Doble Cara)")
                with gr.Row():
                    input_front = gr.Image(
                        type="filepath",
                        # Forense: image_mode=None entrega SIEMPRE el archivo original (con "RGB", Gradio
                        # re-codifica las imágenes en gris o RGBA y se pierden EXIF y la compresión original).
                        # format="png": las muestras, el portapapeles y la webcam se guardan sin pérdida (el
                        # "webp" por defecto borra los artefactos JPEG que analiza el ELA).
                        image_mode=None,
                        format="png",
                        sources=["upload", "clipboard", "webcam"],
                        label="📸 Anverso (Foto y Filiación) — Arrastre, Pegue [Ctrl+V] o Cámara",
                    )
                    input_back = gr.Image(
                        type="filepath",
                        image_mode=None,
                        format="png",
                        sources=["upload", "clipboard", "webcam"],
                        label="📸 Reverso (Zona MRZ / Chip) — Arrastre, Pegue [Ctrl+V] o Cámara",
                    )

                with gr.Row():
                    btn_analyze = gr.Button(
                        "🔍 Ejecutar Auditoría Forense y Cruce Multicara",
                        variant="primary",
                        size="lg",
                        scale=4,
                        elem_classes=["primary-analyze-btn"]
                    )
                    # Siempre visible: borra también las copias temporales de las subidas
                    btn_reset = gr.Button("🧹 Limpiar", variant="stop", size="lg", scale=1, elem_classes=["toolbar-btn"])

                with gr.Accordion("⚙️ Parámetros Manuales y Override (Opcional)", open=False):
                    doc_type = gr.Dropdown(
                        choices=[DOC_TYPE_AUTO, "DNI 4.0 / 3.0", "NIE / TIE (Extranjeros)", "Tarjeta Roja (Asilo)"],
                        value=DOC_TYPE_AUTO,
                        label="Tipo de Soporte",
                    )
                    front_doc_number = gr.Textbox(label="Nº Documento manual", placeholder="Ej: 12345678Z o Y1234567X")
                    with gr.Row():
                        front_expiry_date = gr.Textbox(label="Caducidad manual", placeholder="DD/MM/AAAA")
                        front_birth_date = gr.Textbox(label="Nacimiento manual", placeholder="DD/MM/AAAA")
                    front_surname = gr.Textbox(label="Primer Apellido manual", placeholder="Ej: GARCIA")
                    mrz_text = gr.Textbox(
                        label="Líneas MRZ manuales (3 líneas ICAO TD1)",
                        placeholder="Si está vacío, el OCR local extrae la MRZ automáticamente",
                        lines=3,
                    )

                # Muestras sintéticas SPECIMEN: útiles para demos, plegadas para no quitar espacio a la herramienta
                with gr.Accordion("🧪 Muestras de demostración (SPECIMEN)", open=False):
                    with gr.Row():
                        btn_load_valid = gr.Button("📋 Válida", variant="secondary", size="sm", elem_classes=["toolbar-btn"])
                        btn_load_tampered = gr.Button("⚠️ Manipulada", variant="secondary", size="sm", elem_classes=["toolbar-btn"])
                        btn_load_expired = gr.Button("⌛ Caducada", variant="secondary", size="sm", elem_classes=["toolbar-btn"])

            # COLUMNA DERECHA: RESULTADOS Y DICTAMEN KYC
            with gr.Column(scale=6):
                gr.Markdown("#### 📊 Dictamen y Cuadro de Mando KYC")
                output_dashboard = gr.HTML(label="Cuadro de Mando KYC")

                with gr.Tabs():
                    with gr.Tab("🌡️ Inspección Forense ELA"):
                        gr.Markdown(
                            """
                            **Mapa Térmico de Nivel de Error (ELA / Error Level Analysis):**
                            *Las zonas homogéneas sin manipulación presentan un tono azul uniforme. Parches, textos o números recompilados digitalmente destacan en tonalidades amarillas o rojas intensas.*
                            """
                        )
                        with gr.Row():
                            output_ela_front = gr.Image(label="ELA Anverso")
                            output_ela_back = gr.Image(label="ELA Reverso")
                        gr.HTML(
                            """
                            <div style="background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; padding: 14px 18px; margin-top: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.06);">
                              <div style="font-size: 13px; font-weight: 700; color: #0f172a; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 16px;">🔍</span> Referencias del Mapa Forense ELA (Overlay de Micro-Compresión):
                              </div>
                              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; font-size: 13px;">
                                <div style="display: flex; align-items: center; gap: 10px; background: #eff6ff; border: 1px solid #bfdbfe; padding: 8px 12px; border-radius: 6px;">
                                  <span style="display: inline-block; width: 16px; height: 16px; background: #0000a0; border-radius: 4px; flex-shrink: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.2);"></span>
                                  <div>
                                    <strong style="color: #1e40af; display: block; font-size: 12px;">Azul Cobalto / Cian</strong>
                                    <span style="color: #334155; font-size: 11px;">Fondo homogéneo sin alteración</span>
                                  </div>
                                </div>
                                <div style="display: flex; align-items: center; gap: 10px; background: #f0fdf4; border: 1px solid #bbf7d0; padding: 8px 12px; border-radius: 6px;">
                                  <span style="display: inline-block; width: 16px; height: 16px; background: #00c800; border-radius: 4px; flex-shrink: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.2);"></span>
                                  <div>
                                    <strong style="color: #166534; display: block; font-size: 12px;">Verde / Amarillo</strong>
                                    <span style="color: #334155; font-size: 11px;">Bordes y tipografía legítima</span>
                                  </div>
                                </div>
                                <div style="display: flex; align-items: center; gap: 10px; background: #fef2f2; border: 1px solid #fecaca; padding: 8px 12px; border-radius: 6px;">
                                  <span style="display: inline-block; width: 16px; height: 16px; background: #e60000; border-radius: 4px; flex-shrink: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.2);"></span>
                                  <div>
                                    <strong style="color: #991b1b; display: block; font-size: 12px;">Rojo</strong>
                                    <span style="color: #7f1d1d; font-size: 11px;">Pico de error de compresión: revisar manualmente</span>
                                  </div>
                                </div>
                                <div style="display: flex; align-items: center; gap: 10px; background: #fff7ed; border: 1px solid #fed7aa; padding: 8px 12px; border-radius: 6px;">
                                  <span style="display: inline-block; width: 16px; height: 16px; background: #ff4500; border-radius: 4px; flex-shrink: 0; box-shadow: 0 1px 2px rgba(0,0,0,0.2);"></span>
                                  <div>
                                    <strong style="color: #9a3412; display: block; font-size: 12px;">Halo Naranja</strong>
                                    <span style="color: #7c2d12; font-size: 11px;">Zona oscura sin ruido de sensor: texto sólido impreso o trazo digital</span>
                                  </div>
                                </div>
                              </div>
                              <div style="font-size: 11px; color: #475569; margin-top: 10px;">
                                El ELA es un indicio heurístico: en fotos de móvil o capturas de pantalla produce falsos positivos. Nunca marca un documento como inconsistente por sí solo.
                              </div>
                            </div>
                            """
                        )

                    with gr.Tab("📋 Matriz de Controles Técnicos"):
                        output_checklist = gr.Markdown()

                    with gr.Tab("👁️ Trazabilidad OCR Local"):
                        output_ocr_trace = gr.Markdown()

                    with gr.Tab("📑 Informe JSON Estructurado"):
                        output_json = gr.Code(language="json", label="Salida API para Auditoría")

        # Footer Legal
        gr.Markdown(
            """
            ---
            <div style="font-size: 11px; color: #475569; text-align: center; font-weight: 500;">
              Hermetic-ID · Código Abierto para Auditoría de Ciberseguridad e Integridad Documental ·
              Ejecución local en <code>127.0.0.1</code> · Las subidas se guardan temporalmente en una carpeta dedicada y se purgan a los 5 minutos y al cerrar · No constituye certificación de autenticidad física de soporte oficial.
            </div>
            """
        )

        # Conexión de eventos
        btn_load_valid.click(
            fn=load_preset_specimen_valid,
            inputs=[],
            outputs=[input_front, input_back, doc_type, front_doc_number, front_expiry_date, front_birth_date, front_surname, mrz_text],
        )

        btn_load_tampered.click(
            fn=load_preset_specimen_tampered,
            inputs=[],
            outputs=[input_front, input_back, doc_type, front_doc_number, front_expiry_date, front_birth_date, front_surname, mrz_text],
        )

        btn_load_expired.click(
            fn=load_preset_specimen_expired,
            inputs=[],
            outputs=[input_front, input_back, doc_type, front_doc_number, front_expiry_date, front_birth_date, front_surname, mrz_text],
        )

        btn_reset.click(
            fn=reset_audit_case,
            inputs=[],
            outputs=[
                input_front,
                input_back,
                doc_type,
                front_doc_number,
                front_expiry_date,
                front_birth_date,
                front_surname,
                mrz_text,
                output_dashboard,
                output_ocr_trace,
                output_ela_front,
                output_ela_back,
                output_checklist,
                output_json,
            ],
        )

        btn_analyze.click(
            fn=run_document_audit,
            inputs=[input_front, input_back, doc_type, front_doc_number, front_expiry_date, front_birth_date, front_surname, mrz_text],
            outputs=[output_dashboard, output_ocr_trace, output_ela_front, output_ela_back, output_checklist, output_json],
        )

    return demo


def main():
    """Punto de entrada para arranque seguro en localhost.

    HERMETIC_HOST solo debe cambiarse dentro de un contenedor (0.0.0.0), donde la exposición
    real la limita el mapeo de puertos del host (127.0.0.1:7860:7860 en docker-compose).
    """
    purge_upload_cache()
    atexit.register(purge_upload_cache)

    demo = build_app()
    launch_kwargs = {
        "server_name": os.getenv("HERMETIC_HOST", "127.0.0.1"),
        "server_port": int(os.getenv("HERMETIC_PORT", "7860")),
        "share": False,
    }
    try:
        demo.launch(**launch_kwargs, theme=gr.themes.Soft(), css=CORPORATE_CSS)
    except TypeError:
        demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()
