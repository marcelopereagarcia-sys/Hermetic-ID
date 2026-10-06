"""API REST Headless de Auditoría y Verificación Documental Hermetic-ID.

Proporciona integración server-to-server (B2B / FinTech / KYC) sin interfaz gráfica.
Garantías y configuración:
- Las subidas se mantienen en RAM: el umbral de volcado a disco de Starlette se eleva por encima
  del tamaño máximo de petición, y las peticiones mayores se rechazan antes de leer el cuerpo.
- Sin telemetría. El OCR (EasyOCR) solo descarga modelos si HERMETIC_OFFLINE_MODE no es "true".
- HERMETIC_API_KEY: si se define, los endpoints /api/v1/audit/* exigen la cabecera X-API-Key.
- HERMETIC_CORS_ORIGINS: orígenes permitidos separados por comas (por defecto ninguno).
"""

from datetime import datetime, timezone
import io
import os
import re
import secrets
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from PIL import Image
from starlette.formparsers import MultiPartParser

from src import __version__
from src.core.algorithms import (
    mrz_check_passed,
    validate_mrz_td1,
    validate_mrz_td3,
    validate_dni,
    validate_nie,
    MRZTD1Result,
    MRZTD3Result,
    ValidationResult,
)
from src.core.forensics import (
    compute_ela,
    inspect_image_exif,
    analyze_photo_boundary_sharpness,
)
from src.core.preprocessor import assess_image_quality
from src.detectors.ocr_engine import (
    DocumentAutoDetector,
    DocumentFrontData,
    cross_verify_front_with_mrz,
    create_manager_summary,
)
from src.reporting.report_generator import build_audit_report

app = FastAPI(
    title="Hermetic-ID API",
    version=__version__,
    description="Motor forense y de verificación de documentos de identidad españoles (DNI 3.0/4.0, NIE, TIE, Pasaportes ICAO 9303)",
)

MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB max por imagen (Anti-DoS)
# Anverso + reverso + campos de formulario
MAX_REQUEST_SIZE_BYTES = 2 * MAX_UPLOAD_SIZE_BYTES + 1024 * 1024

# Starlette vuelca a un archivo temporal en disco toda subida > 1 MB. Elevar el umbral por encima
# del máximo admitido mantiene los documentos de identidad exclusivamente en memoria.
MultiPartParser.spool_max_size = MAX_REQUEST_SIZE_BYTES

# CORS cerrado por defecto: una API sin sesión no debe ser legible desde cualquier web que visite el operador
_cors_origins = [o.strip() for o in os.getenv("HERMETIC_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )


@app.middleware("http")
async def reject_oversized_requests(request: Request, call_next):
    """Rechaza peticiones demasiado grandes antes de leer el cuerpo (Content-Length declarado)."""
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_SIZE_BYTES:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"detail": f"La petición excede el máximo de {MAX_REQUEST_SIZE_BYTES // (1024 * 1024)} MB."},
        )
    return await call_next(request)


def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    """Exige X-API-Key si HERMETIC_API_KEY está definida (comparación en tiempo constante)."""
    expected = os.getenv("HERMETIC_API_KEY")
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key ausente o inválida.")


class HealthResponse(BaseModel):
    status: str
    version: str
    offline_mode: bool
    timestamp: str


class MRZAuditRequest(BaseModel):
    lines: List[str] = Field(..., description="Líneas MRZ (3 líneas para TD1, 2 líneas para TD3/Pasaporte)")


class DocumentNumberRequest(BaseModel):
    document_number: str = Field(..., description="Número de DNI o NIE español (ej. '12345678Z' o 'Y1234567L')")


def _read_image_bytes(upload: UploadFile) -> Tuple[Image.Image, bytes]:
    """Carga y valida un archivo de imagen en memoria volátil sin persistencia en disco."""
    # Leer como máximo un byte más del límite: suficiente para detectar el exceso sin cargarlo entero
    content = upload.file.read(MAX_UPLOAD_SIZE_BYTES + 1)
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede el tamaño máximo permitido de {MAX_UPLOAD_SIZE_BYTES // (1024*1024)} MB.",
        )
    try:
        Image.MAX_IMAGE_PIXELS = 25_000_000
        img = Image.open(io.BytesIO(content))
        img.verify()
        # Reabrir tras verify() porque verify invalida el puntero
        img = Image.open(io.BytesIO(content)).convert("RGB")
        return img, content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato de imagen inválido o corrupto: {str(e)}",
        )


@app.get("/health", response_model=HealthResponse)
@app.get("/api/v1/health", response_model=HealthResponse)
def health_check():
    """Estado de salud del microservicio."""
    offline = os.getenv("HERMETIC_OFFLINE_MODE", "false").lower() == "true"
    return HealthResponse(
        status="ok",
        version=__version__,
        offline_mode=offline,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.post("/api/v1/audit/document-number", dependencies=[Depends(require_api_key)])
def audit_document_number(payload: DocumentNumberRequest) -> Dict[str, Any]:
    """Validación algorítmica instantánea de DNI o NIE español mediante Módulo 23."""
    doc_num = payload.document_number.strip().upper()
    if re.match(r"^[XYZ]\d{7}[A-Z]$", doc_num):
        res = validate_nie(doc_num)
        doc_type = "NIE"
    elif re.match(r"^\d{8}[A-Z]$", doc_num):
        res = validate_dni(doc_num)
        doc_type = "DNI"
    else:
        return {
            "is_valid": False,
            "document_type": "UNKNOWN",
            "document_number": doc_num,
            "details": "Formato no reconocido como DNI (8 dígitos + letra) ni NIE (X/Y/Z + 7 dígitos + letra).",
        }

    return {
        "is_valid": res.passed,
        "document_type": doc_type,
        "document_number": doc_num,
        "expected_letter": res.expected,
        "actual_letter": res.actual,
        "details": res.details,
    }


@app.post("/api/v1/audit/mrz", dependencies=[Depends(require_api_key)])
def audit_mrz_block(payload: MRZAuditRequest) -> Dict[str, Any]:
    """Valida y decodifica un bloque MRZ (TD1 DNI/TIE de 3 líneas o TD3 Pasaporte de 2 líneas)."""
    clean_lines = [l.strip().upper() for l in payload.lines if l.strip()]

    if len(clean_lines) == 3:
        res = validate_mrz_td1(clean_lines)
        return {
            "format": "TD1",
            "is_valid": res.is_valid,
            "document_type": res.document_type,
            "issuing_country": res.issuing_country,
            "document_number": res.document_number,
            "personal_number": res.personal_number,
            "support_number": res.support_number,
            "birth_date": res.birth_date,
            "sex": res.sex,
            "expiry_date": res.expiry_date,
            "nationality": res.nationality,
            "surname": res.surname,
            "given_names": res.given_names,
            "composite_check_passed": res.composite_check_passed,
            "checks": [
                {
                    "check_name": c.check_name,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                    "details": c.details,
                }
                for c in res.checks
            ],
        }
    elif len(clean_lines) == 2:
        res = validate_mrz_td3(clean_lines)
        return {
            "format": "TD3",
            "is_valid": res.is_valid,
            "document_type": res.document_type,
            "issuing_country": res.issuing_country,
            "document_number": res.document_number,
            "personal_number": res.personal_number,
            "support_number": res.support_number,
            "birth_date": res.birth_date,
            "sex": res.sex,
            "expiry_date": res.expiry_date,
            "nationality": res.nationality,
            "surname": res.surname,
            "given_names": res.given_names,
            "optional_data": res.optional_data,
            "composite_check_passed": res.composite_check_passed,
            "checks": [
                {
                    "check_name": c.check_name,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                    "details": c.details,
                }
                for c in res.checks
            ],
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cantidad de líneas no válida ({len(clean_lines)}). Un bloque MRZ debe constar de 3 líneas (TD1) o 2 líneas (TD3).",
        )


@app.post("/api/v1/audit/document", dependencies=[Depends(require_api_key)])
def audit_document(
    front_image: Optional[UploadFile] = File(None),
    back_image: Optional[UploadFile] = File(None),
    doc_type: Optional[str] = Form(None),
    front_doc_number: Optional[str] = Form(None),
    front_expiry_date: Optional[str] = Form(None),
    front_birth_date: Optional[str] = Form(None),
    front_surname: Optional[str] = Form(None),
    mrz_text: Optional[str] = Form(None),
) -> Dict[str, Any]:
    """Auditoría forense completa e integrada de documentos de identidad.
    
    Acepta imágenes de anverso y/o reverso (multipart/form-data) y campos opcionales.
    Ejecuta en memoria:
    - Control de calidad fotográfica (blur, reflejos, monocromía).
    - Análisis forense de imagen (ELA por bloques Z-score, EXIF sanitizado, nitidez de borde).
    - Detección OCR e inferencia de tipos (DNI 3.0/4.0, NIE/TIE, Pasaporte).
    - Verificación algorítmica ICAO 9303 / Módulo 23.
    - Cruce de coherencia anverso vs reverso.
    """
    if front_image is None and back_image is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar al menos una imagen (front_image o back_image).",
        )

    front_pil, front_bytes = _read_image_bytes(front_image) if front_image else (None, None)
    back_pil, back_bytes = _read_image_bytes(back_image) if back_image else (None, None)

    # Si solo se subió front_image pero contiene MRZ, clasificarla como reverso.
    # El resultado se reutiliza para no ejecutar el OCR dos veces sobre la misma imagen.
    cached_scan_front = None
    cached_scan_back = None
    if front_pil is not None and back_pil is None:
        probe = DocumentAutoDetector.analyze_image_auto(front_pil)
        if probe.is_mrz_detected:
            back_pil, back_bytes = front_pil, front_bytes
            front_pil, front_bytes = None, None
            cached_scan_back = probe
        else:
            cached_scan_front = probe

    # 1. Controles de Calidad y Forense ELA
    primary_quality = None
    primary_ela = None
    primary_exif = None
    primary_edge = None

    if front_pil is not None and front_bytes is not None:
        primary_quality = assess_image_quality(front_pil)
        primary_ela = compute_ela(front_pil, quality=90, scale=15)
        primary_exif = inspect_image_exif(front_bytes)

        w_f, h_f = front_pil.size
        photo_box_est = (int(w_f * 0.05), int(h_f * 0.12), int(w_f * 0.28), int(h_f * 0.62))
        try:
            import cv2
            import numpy as np
            front_np = cv2.cvtColor(np.array(front_pil), cv2.COLOR_RGB2BGR)
            primary_edge = analyze_photo_boundary_sharpness(front_np, photo_box_est)
        except Exception:
            primary_edge = None

    if back_pil is not None and back_bytes is not None:
        q_back = assess_image_quality(back_pil)
        ela_back = compute_ela(back_pil, quality=90, scale=15)
        exif_back = inspect_image_exif(back_bytes)
        if primary_quality is None or (not q_back.is_acceptable):
            primary_quality = q_back
        if primary_ela is None or (ela_back.anomaly_detected and not (primary_ela and primary_ela.anomaly_detected)):
            primary_ela = ela_back
        if primary_exif is None or (exif_back.is_suspicious_software and not (primary_exif and primary_exif.is_suspicious_software)):
            primary_exif = exif_back

    # 2. Extracción OCR e Inferencia
    scan_front = cached_scan_front or (DocumentAutoDetector.analyze_image_auto(front_pil) if front_pil is not None else None)
    scan_back = cached_scan_back or (DocumentAutoDetector.analyze_image_auto(back_pil) if back_pil is not None else None)
    ocr_corrections: List[str] = []

    # Consolidar datos del anverso
    effective_doc_num = front_doc_number or (scan_front.detected_doc_number if scan_front else None)
    effective_expiry = front_expiry_date or (scan_front.detected_expiry_date if scan_front else None)
    effective_birth = front_birth_date or (scan_front.detected_birth_date if scan_front else None)
    effective_surname = front_surname or (scan_front.detected_surname if scan_front else None)
    effective_given_names = scan_front.detected_given_names if scan_front else None

    # Consolidar MRZ del reverso
    mrz_res = None
    if scan_back and scan_back.is_mrz_detected and scan_back.mrz_result:
        mrz_res = scan_back.mrz_result
        ocr_corrections.extend(scan_back.ocr_corrections)
    elif mrz_text and mrz_text.strip():
        lines = [l.strip() for l in mrz_text.strip().split("\n") if l.strip()]
        if len(lines) == 3:
            mrz_res = validate_mrz_td1(lines)
        elif len(lines) == 2:
            mrz_res = validate_mrz_td3(lines)

    # 3. Cruce de Coherencia Anverso vs Reverso
    cross_result = None
    if mrz_res:
        front_data = DocumentFrontData(
            document_number=effective_doc_num,
            support_number=scan_front.detected_support_number if scan_front else None,
            surname=effective_surname,
            expiry_date=effective_expiry,
            birth_date=effective_birth,
            given_names=effective_given_names,
        )
        cross_result = cross_verify_front_with_mrz(front_data, mrz_res)

    # 4. Resumen Ejecutivo
    summary = None
    if mrz_res:
        summary = create_manager_summary(
            doc_number=mrz_res.personal_number or mrz_res.document_number,
            surname=mrz_res.surname,
            given_names=mrz_res.given_names,
            # Las fechas de la MRZ solo mandan si su dígito de control es correcto
            birth_date_str=mrz_res.birth_date if mrz_check_passed(mrz_res, "MRZ_BIRTH_CHECKSUM") else (effective_birth or mrz_res.birth_date),
            expiry_date_str=mrz_res.expiry_date if mrz_check_passed(mrz_res, "MRZ_EXPIRY_CHECKSUM") else (effective_expiry or mrz_res.expiry_date),
            nationality=mrz_res.nationality,
            support_number=mrz_res.support_number,
        )
    elif effective_doc_num or effective_surname:
        summary = create_manager_summary(
            doc_number=effective_doc_num,
            surname=effective_surname,
            given_names=effective_given_names,
            birth_date_str=effective_birth,
            expiry_date_str=effective_expiry,
        )

    # 5. Construcción del Informe Consolidado
    report = build_audit_report(
        quality=primary_quality,
        ela=primary_ela,
        exif=primary_exif,
        edge=primary_edge,
        mrz=mrz_res,
        crosscheck=cross_result,
        manager_summary=summary,
        ocr_corrections=ocr_corrections,
    )

    return report.to_dict()
