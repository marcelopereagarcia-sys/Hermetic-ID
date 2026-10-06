"""Pruebas unitarias para el motor de reportes, panel KYC del gestor y pipeline de la UI."""

from datetime import date
import pytest
from src.core.algorithms import (
    ValidationResult,
    parse_icao_date_to_date,
    evaluate_document_validity,
    calculate_age,
)
from src.core.forensics import ELAResult
from src.core.preprocessor import QualityAssessmentResult
from src.reporting.report_generator import build_audit_report, RiskLevel, ManagerSummary
from src.detectors.ocr_engine import create_manager_summary
from src.app.ui import (
    run_document_audit,
    load_preset_specimen_valid,
    load_preset_specimen_tampered,
    load_preset_specimen_expired,
    reset_audit_case,
)
from tests.fixtures.synthetic_generator import (
    create_specimen_front_image,
    create_specimen_back_image,
    generate_synthetic_mrz_td1,
)


class TestReportingEngine:
    """Verificación de la lógica de scoring de riesgo y vigencia del informe unificado."""

    def test_report_clear_when_all_pass(self):
        quality = QualityAssessmentResult(
            is_acceptable=True,
            blur_score=150.0,
            is_blurred=False,
            glare_percentage=1.0,
            has_excessive_glare=False,
            mean_brightness=120.0,
            is_underexposed=False,
            resolution=(1000, 600),
            has_sufficient_resolution=True,
            summary="OK",
        )
        ela = ELAResult(
            mean_error=2.5,
            max_error=15.0,
            std_error=8.0,
            anomaly_detected=False,
            details="Homogéneo",
        )
        checks = [
            ValidationResult(check_name="DNI_CHECKSUM", passed=True, details="OK")
        ]
        mgr = ManagerSummary(
            full_name="CARMEN GARCIA LOPEZ",
            doc_number="12345678Z",
            doc_type="DNI 4.0 / 3.0",
            nationality="ESP",
            birth_date="01/01/1985",
            age_years=41,
            expiry_date="01/01/2030",
            days_until_expiry=1182,
            is_expired=False,
            validity_status_label="EN VIGOR",
        )

        report = build_audit_report(quality=quality, ela=ela, direct_checks=checks, manager_summary=mgr)
        assert report.risk_level == RiskLevel.CLEAR
        assert report.risk_score == 0
        assert report.controls_failed == 0
        assert report.controls_passed >= 3
        assert report.manager_summary is not None
        assert report.manager_summary["doc_number"] == "12345678Z"

    def test_report_critical_when_expired(self):
        mgr_expired = ManagerSummary(
            full_name="CARMEN GARCIA LOPEZ",
            doc_number="12345678Z",
            doc_type="DNI 4.0 / 3.0",
            nationality="ESP",
            birth_date="01/01/1985",
            age_years=41,
            expiry_date="15/03/2024",
            days_until_expiry=-935,
            is_expired=True,
            validity_status_label="CADUCADO (vencido hace 935 días)",
        )
        report = build_audit_report(manager_summary=mgr_expired)
        assert report.risk_level == RiskLevel.CRITICAL
        assert report.controls_failed >= 1
        assert "CADUCADO" in report.status_label
        check_expired = next(c for c in report.checklist if c["control"] == "Vigencia Temporal del Documento")
        assert check_expired["estado"] == "FALLO"
        assert "NO VÁLIDO" in check_expired["detalle"]

    def test_calculate_expiry_and_age(self):
        ref = date(2026, 10, 6)
        # Fecha en vigor
        exp_date = date(2028, 3, 8)
        days_rem, is_exp, label = evaluate_document_validity(exp_date, reference_date=ref)
        assert days_rem == 519
        assert is_exp is False
        assert "EN VIGOR" in label

        # Fecha caducada
        exp_old = date(2024, 3, 8)
        days_old, is_exp_old, label_old = evaluate_document_validity(exp_old, reference_date=ref)
        assert days_old == -942
        assert is_exp_old is True
        assert "CADUCADO" in label_old

        # Edad
        birth = date(1985, 1, 1)
        age = calculate_age(birth, reference_date=ref)
        assert age == 41

    def test_create_manager_summary_from_data(self):
        ref = date(2026, 10, 6)
        summary = create_manager_summary(
            doc_number="12345678Z",
            surname="GARCIA",
            given_names="CARMEN",
            birth_date_str="850101",
            expiry_date_str="300101",
            nationality="ESP",
            reference_date=ref,
        )
        assert summary is not None
        assert summary.full_name == "GARCIA CARMEN"
        assert summary.expiry_date == "01/01/2030"
        assert summary.is_expired is False
        assert summary.days_until_expiry > 0


class TestUIPipeline:
    """Verificación de la carga de muestras, botón de reinicio y ejecución dual en la UI."""

    def test_load_presets(self):
        f_v, b_v, dtype_v, doc_v, exp_v, birth_v, sur_v, mrz_v = load_preset_specimen_valid()
        assert f_v is not None
        assert b_v is not None
        assert doc_v == "12345678Z"
        assert len(mrz_v.split("\n")) == 3

        f_t, b_t, dtype_t, doc_t, exp_t, birth_t, sur_t, mrz_t = load_preset_specimen_tampered()
        assert f_t is not None
        assert exp_t == "01/01/2035"

        f_e, b_e, dtype_e, doc_e, exp_e, birth_e, sur_e, mrz_e = load_preset_specimen_expired()
        assert f_e is not None
        assert "2024" in exp_e

    def test_reset_audit_case(self):
        res = reset_audit_case()
        assert len(res) == 14
        assert res[0] is None  # front image
        assert res[1] is None  # back image
        assert res[3] == ""    # doc number
        assert res[8] == ""    # dashboard html
        assert res[9] == ""    # ocr trace
        assert res[13] == "{}" # json

    def test_run_document_audit_dual_face_valid(self):
        f_img = create_specimen_front_image(doc_number="12345678Z", name="CARMEN GARCIA LOPEZ", expiry_date="01/01/2030")
        b_img = create_specimen_back_image(doc_number="12345678Z", expiry_date="300101", surname="GARCIA LOPEZ", given_names="CARMEN")
        mrz_sample = "\n".join(generate_synthetic_mrz_td1(personal_number="12345678Z", expiry_date="300101", surname="GARCIA LOPEZ", given_names="CARMEN"))

        dashboard_html, ocr_trace, ela_front, ela_back, checklist, json_rep = run_document_audit(
            front_image_input=f_img,
            back_image_input=b_img,
            doc_type="DNI 4.0 / 3.0",
            front_doc_number="12345678Z",
            front_expiry_date="01/01/2030",
            front_birth_date="01/01/1985",
            front_surname="GARCIA LOPEZ",
            mrz_text=mrz_sample,
        )

        assert "SIN INCONSISTENCIAS" in dashboard_html or "EXPEDIENTE SIN INCONSISTENCIAS" in dashboard_html
        assert "EN VIGOR" in dashboard_html
        assert "12345678Z" in dashboard_html
        assert "GARCIA LOPEZ" in dashboard_html
        assert ela_front is not None
        assert ela_back is not None
        assert "Vigencia Temporal" in checklist
        assert "risk_score" in json_rep

    def test_run_document_audit_expired_flags_critical(self):
        f_img = create_specimen_front_image(doc_number="12345678Z", expiry_date="15/03/2024")
        b_img = create_specimen_back_image(doc_number="12345678Z", expiry_date="240315")
        mrz_sample = "\n".join(generate_synthetic_mrz_td1(personal_number="12345678Z", expiry_date="240315"))

        dashboard_html, ocr_trace, ela_front, ela_back, checklist, json_rep = run_document_audit(
            front_image_input=f_img,
            back_image_input=b_img,
            doc_type="DNI 4.0 / 3.0",
            front_doc_number="12345678Z",
            front_expiry_date="15/03/2024",
            front_birth_date="01/01/1985",
            front_surname="GARCIA LOPEZ",
            mrz_text=mrz_sample,
        )

        assert "CADUCADO" in dashboard_html
        assert "NO VÁLIDO" in dashboard_html
        assert "FALLO" in checklist
