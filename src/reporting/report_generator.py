"""Generador de informes forenses y motor de evaluación de riesgo unificado.

Consolida los hallazgos de:
- Control de calidad de imagen (desenfoque, reflejos, resolución).
- Análisis forense de imagen (ELA, bordes de inserción, metadatos EXIF).
- Algoritmos oficiales y verificación matemática (DNI/NIE, ICAO 9303 MRZ).
- Cruce de coherencia anverso vs reverso.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union
import json

from src.core.algorithms import MRZTD1Result, MRZTD3Result, ValidationResult
from src.core.forensics import ELAResult, ExifForensicResult, EdgeAnomalyResult
from src.core.preprocessor import QualityAssessmentResult
from src.detectors.ocr_engine import CrossVerificationResult


class RiskLevel(str, Enum):
    CLEAR = "SIN_INCONSISTENCIAS"       # Verde: Todos los controles superados
    WARNING = "ADVERTENCIA"            # Amarillo: Calidad insuficiente o indicios forenses heurísticos (ELA, EXIF, bordes)
    CRITICAL = "INCONSISTENCIAS_CRITICAS" # Rojo: Fallo determinista (checksum, cruce anverso/MRZ) o documento caducado

# Política de severidad:
# - Controles DETERMINISTAS (dígitos de control, módulo 23, cruce anverso/MRZ, caducidad) → FALLO (rojo).
#   Son demostrables: el mismo dato da siempre el mismo resultado.
# - Controles HEURÍSTICOS (ELA, EXIF, borde de foto) → ADVERTENCIA (amarillo). Sus umbrales no están
#   calibrados contra un conjunto de imágenes reales y producen falsos positivos en fotos de móvil;
#   por sí solos nunca deben marcar un documento como inconsistente.


@dataclass
class ManagerSummary:
    """Resumen estructurado de datos de filiación y vigencia para el gestor / analista."""
    full_name: str
    doc_number: str
    doc_type: str
    nationality: str
    birth_date: str
    age_years: Optional[int]
    expiry_date: str
    days_until_expiry: Optional[int]
    is_expired: bool
    validity_status_label: str
    support_number: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "full_name": self.full_name,
            "doc_number": self.doc_number,
            "doc_type": self.doc_type,
            "nationality": self.nationality,
            "birth_date": self.birth_date,
            "age_years": self.age_years,
            "expiry_date": self.expiry_date,
            "days_until_expiry": self.days_until_expiry,
            "is_expired": self.is_expired,
            "validity_status_label": self.validity_status_label,
            "support_number": self.support_number,
        }


@dataclass
class ForensicAuditReport:
    """Informe consolidado final de auditoría documental."""
    risk_level: RiskLevel
    status_label: str
    risk_score: int  # 0 (óptimo) a 100 (máximo riesgo)
    controls_passed: int
    controls_failed: int
    controls_warning: int
    summary_message: str
    checklist: List[Dict[str, Any]] = field(default_factory=list)
    quality_details: Optional[Dict[str, Any]] = None
    forensic_details: Optional[Dict[str, Any]] = None
    mrz_details: Optional[Dict[str, Any]] = None
    crosscheck_details: Optional[Dict[str, Any]] = None
    manager_summary: Optional[Dict[str, Any]] = None
    ocr_corrections: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_level": self.risk_level.value,
            "status_label": self.status_label,
            "risk_score": self.risk_score,
            "controls_passed": self.controls_passed,
            "controls_failed": self.controls_failed,
            "controls_warning": self.controls_warning,
            "summary_message": self.summary_message,
            "checklist": self.checklist,
            "manager_summary": self.manager_summary,
            "quality_details": self.quality_details,
            "forensic_details": self.forensic_details,
            "mrz_details": self.mrz_details,
            "crosscheck_details": self.crosscheck_details,
            "ocr_corrections": self.ocr_corrections,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def build_audit_report(
    quality: Optional[QualityAssessmentResult] = None,
    ela: Optional[ELAResult] = None,
    exif: Optional[ExifForensicResult] = None,
    edge: Optional[EdgeAnomalyResult] = None,
    mrz: Optional[Union[MRZTD1Result, MRZTD3Result]] = None,
    crosscheck: Optional[CrossVerificationResult] = None,
    direct_checks: Optional[List[ValidationResult]] = None,
    manager_summary: Optional[ManagerSummary] = None,
    ocr_corrections: Optional[List[str]] = None,
) -> ForensicAuditReport:
    """Compila y pondera todos los controles ejecutados sobre el documento."""
    checklist: List[Dict[str, Any]] = []
    passed = 0
    failed = 0
    warnings = 0
    risk_points = 0

    # 1. Control de Calidad
    if quality:
        if quality.is_acceptable:
            passed += 1
            checklist.append({"control": "Calidad y Enfoque de Imagen", "estado": "OK", "detalle": "Imagen nítida con iluminación homogénea."})
        else:
            warnings += 1
            risk_points += 20
            checklist.append({"control": "Calidad y Enfoque de Imagen", "estado": "ADVERTENCIA", "detalle": quality.summary})

    # 2. Análisis Forense ELA
    if ela:
        if not ela.anomaly_detected:
            passed += 1
            checklist.append({"control": "Nivel de Error JPEG (ELA)", "estado": "OK", "detalle": f"Compresión uniforme (Desviación: {ela.std_error:.1f})."})
        else:
            warnings += 1
            risk_points += 35
            checklist.append({"control": "Nivel de Error JPEG (ELA)", "estado": "ADVERTENCIA", "detalle": f"{ela.details} Indicio heurístico: requiere revisión manual."})

    # 3. Metadatos EXIF
    if exif:
        if exif.is_suspicious_software:
            warnings += 1
            risk_points += 40
            checklist.append({"control": "Metadatos de Edición Digital", "estado": "ADVERTENCIA", "detalle": f"{exif.details} Pasar por un editor no implica alteración (p. ej. un recorte)."})
        elif exif.has_exif:
            passed += 1
            checklist.append({"control": "Metadatos de Edición Digital", "estado": "OK", "detalle": "Sin firmas de herramientas de diseño/retoque."})

    # 4. Bordes de Fotografía
    if edge:
        if edge.is_suspicious_edge:
            warnings += 1
            risk_points += 30
            checklist.append({"control": "Transición de Borde de Fotografía", "estado": "ADVERTENCIA", "detalle": edge.details})
        else:
            passed += 1
            checklist.append({"control": "Transición de Borde de Fotografía", "estado": "OK", "detalle": "Gradiente perimetral natural sin discontinuidades."})

    # 5. Algoritmos MRZ ICAO 9303
    if mrz:
        for c in mrz.checks:
            if c.passed:
                passed += 1
                checklist.append({"control": f"Algoritmo MRZ: {c.check_name}", "estado": "OK", "detalle": c.details})
            else:
                failed += 1
                risk_points += 40
                checklist.append({"control": f"Algoritmo MRZ: {c.check_name}", "estado": "FALLO", "detalle": c.details})

    # 6. Comprobaciones Directas (DNI/NIE)
    if direct_checks:
        for c in direct_checks:
            if c.passed:
                passed += 1
                checklist.append({"control": f"Validación: {c.check_name}", "estado": "OK", "detalle": c.details})
            else:
                failed += 1
                risk_points += 40
                checklist.append({"control": f"Validación: {c.check_name}", "estado": "FALLO", "detalle": c.details})

    # 7. Cruce Anverso vs Reverso
    if crosscheck:
        for f in crosscheck.findings:
            if f.passed:
                passed += 1
                checklist.append({"control": f"Cruce Coherencia: {f.check_name}", "estado": "OK", "detalle": f.details})
            else:
                failed += 1
                risk_points += 45
                checklist.append({"control": f"Cruce Coherencia: {f.check_name}", "estado": "FALLO", "detalle": f.details})

    # 8. Vigencia Temporal para el Gestor
    if manager_summary:
        if manager_summary.is_expired:
            failed += 1
            risk_points += 50
            days_ago = abs(manager_summary.days_until_expiry or 0)
            checklist.append({
                "control": "Vigencia Temporal del Documento",
                "estado": "FALLO",
                "detalle": f"DOCUMENTO CADUCADO ({manager_summary.expiry_date}). Vencido hace {days_ago} días. NO VÁLIDO para trámites."
            })
        elif manager_summary.days_until_expiry is not None and 0 <= manager_summary.days_until_expiry <= 30:
            warnings += 1
            risk_points += 10
            checklist.append({
                "control": "Vigencia Temporal del Documento",
                "estado": "ADVERTENCIA",
                "detalle": f"Documento próximo a caducar ({manager_summary.expiry_date}, restan {manager_summary.days_until_expiry} días)."
            })
        else:
            passed += 1
            remaining = f"restan {manager_summary.days_until_expiry} días" if manager_summary.days_until_expiry is not None else "vigente"
            checklist.append({
                "control": "Vigencia Temporal del Documento",
                "estado": "OK",
                "detalle": f"Documento en vigor ({manager_summary.expiry_date}, {remaining})."
            })

    # Decisión de Estado de Riesgo (Estricta honestidad técnica)
    risk_score = min(100, risk_points)

    if manager_summary and manager_summary.is_expired:
        level = RiskLevel.CRITICAL
        label = "DOCUMENTO CADUCADO / NO VÁLIDO"
        summary = (
            f"El documento está CADUCADO (fecha de vencimiento: {manager_summary.expiry_date}). "
            "No es apto para trámites legales ni contratación. "
            f"Se detectaron además {max(0, failed - 1)} inconsistencia(s) en otros controles técnicos."
        )
    elif failed > 0:
        level = RiskLevel.CRITICAL
        label = "INCONSISTENCIAS DETECTADAS"
        summary = f"Se han detectado {failed} inconsistencia(s) en controles deterministas (dígitos de control o cruce anverso/MRZ). Los datos del documento no son coherentes entre sí."
    elif warnings > 0 or risk_score > 0:
        level = RiskLevel.WARNING
        label = "INDICIOS CON REVISIÓN MANUAL PENDIENTE"
        summary = "No hay fallos deterministas, pero hay advertencias de calidad o indicios forenses heurísticos que requieren revisión manual."
    else:
        level = RiskLevel.CLEAR
        label = "SIN INCONSISTENCIAS DETECTADAS EN CONTROLES EJECUTADOS"
        summary = (
            f"Superados los {passed} controles técnicos aplicados. "
            "Aviso: Este resultado no constituye certificación de autenticidad física del soporte ni sustituye inspección oficial."
        )

    ela_dict = None
    if ela:
        ela_dict = {
            "mean_error": ela.mean_error,
            "max_error": ela.max_error,
            "std_error": ela.std_error,
            "anomaly_detected": ela.anomaly_detected,
            "details": ela.details,
        }

    mrz_dict = None
    if mrz:
        mrz_dict = {
            "is_valid": mrz.is_valid,
            "document_type": mrz.document_type,
            "issuing_country": mrz.issuing_country,
            "document_number": mrz.document_number,
            "birth_date": mrz.birth_date,
            "sex": mrz.sex,
            "expiry_date": mrz.expiry_date,
            "nationality": mrz.nationality,
            "surname": mrz.surname,
            "given_names": mrz.given_names,
            "composite_check_passed": mrz.composite_check_passed,
        }

    crosscheck_dict = None
    if crosscheck:
        crosscheck_dict = {
            "has_discrepancies": crosscheck.has_discrepancies,
            "passed_checks_count": crosscheck.passed_checks_count,
            "failed_checks_count": crosscheck.failed_checks_count,
            "summary": crosscheck.summary,
        }

    manager_dict = manager_summary.to_dict() if manager_summary else None

    return ForensicAuditReport(
        risk_level=level,
        status_label=label,
        risk_score=risk_score,
        controls_passed=passed,
        controls_failed=failed,
        controls_warning=warnings,
        summary_message=summary,
        checklist=checklist,
        quality_details=quality.__dict__ if quality else None,
        forensic_details=ela_dict,
        mrz_details=mrz_dict,
        crosscheck_details=crosscheck_dict,
        manager_summary=manager_dict,
        ocr_corrections=list(ocr_corrections or []),
    )
