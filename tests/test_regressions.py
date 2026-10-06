"""Pruebas de regresión de la revisión del 2026-10-06 (ver CHANGELOG.md).

Cada prueba reproduce un fallo detectado con la estructura REAL de la MRZ del DNI español:
línea 1 = 'ID' + 'ESP' + número de SOPORTE + dígito + DNI/NIE en datos opcionales.
El OCR se sustituye por un lector falso para que las pruebas sean deterministas.
"""

import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from src.api.main import app
from src.app.ui import _load_image_to_memory, run_document_audit
from src.core.algorithms import calculate_dni_letter, calculate_icao_check_digit, validate_mrz_td1
from src.core.forensics import ELAResult, MAX_ELA_DISPLAY_DIM, compute_ela, inspect_image_exif
from src.detectors.ocr_engine import DocumentAutoDetector, DocumentFrontData, cross_verify_front_with_mrz
from src.reporting.report_generator import RiskLevel, build_audit_report
from tests.fixtures.synthetic_generator import (
    create_specimen_back_image,
    generate_synthetic_mrz_td1,
    generate_synthetic_nie,
)


class FakeReader:
    """Sustituto de EasyOCR que devuelve líneas fijas."""

    def __init__(self, lines):
        self.lines = lines

    def readtext(self, img, detail=0):
        return list(self.lines)


@pytest.fixture
def fake_ocr(monkeypatch):
    def _install(lines):
        monkeypatch.setattr(DocumentAutoDetector, "_reader", FakeReader(lines))
    return _install


def _dni(number: int) -> str:
    return f"{number:08d}{calculate_dni_letter(number)}"


BLANK = np.zeros((10, 10, 3), dtype=np.uint8)


class TestRealSpanishMRZLayout:
    """Fallo 1 y 2: la 'corrección' NIE alteraba los DNI que empiezan por 0, 1 o 2."""

    @pytest.mark.parametrize("number", [1234567, 12345678, 23456789, 87654321])
    def test_dni_mrz_survives_ocr_pipeline_unchanged(self, fake_ocr, number):
        lines = generate_synthetic_mrz_td1(personal_number=_dni(number), surname="GARCIA LOPEZ", given_names="CARMEN")
        fake_ocr(lines)

        result = DocumentAutoDetector.analyze_image_auto(BLANK)

        assert result.mrz_lines == lines
        assert result.mrz_result.is_valid is True
        assert result.detected_doc_number == _dni(number)
        assert result.detected_support_number == "BAA000001"
        assert result.detected_doc_type == "DNI 4.0 / 3.0"
        assert result.ocr_corrections == []

    def test_tie_nie_prefix_misread_is_corrected_and_recorded(self, fake_ocr):
        nie_body, nie_letter = generate_synthetic_nie(prefix="Z", number=1234567)
        lines = generate_synthetic_mrz_td1(personal_number=nie_body + nie_letter, support_number="E00000001")
        misread = [lines[0][:15] + "2" + lines[0][16:], lines[1], lines[2]]
        fake_ocr(misread)

        result = DocumentAutoDetector.analyze_image_auto(BLANK)

        assert result.mrz_result.is_valid is True
        assert result.detected_doc_number == nie_body + nie_letter
        assert result.detected_doc_type == "NIE / TIE (Extranjeros)"
        assert any("prefijo NIE" in c for c in result.ocr_corrections)

    def test_unreadable_sex_is_not_guessed(self, fake_ocr):
        lines = generate_synthetic_mrz_td1()
        fake_ocr([lines[0], lines[1][:7] + "7" + lines[1][8:], lines[2]])

        result = DocumentAutoDetector.analyze_image_auto(BLANK)

        assert result.mrz_result.sex == "<"
        assert any("sexo ilegible" in c for c in result.ocr_corrections)

    def test_personal_and_support_number_properties(self):
        res = validate_mrz_td1(generate_synthetic_mrz_td1(personal_number="12345678Z", support_number="BAA000001"))
        assert res.document_number == "BAA000001"
        assert res.personal_number == "12345678Z"
        assert res.support_number == "BAA000001"

    def test_wrong_dni_letter_inside_mrz_is_flagged(self):
        # Se recalculan los dígitos de la MRZ para que SOLO falle la letra módulo 23 del DNI
        lines = generate_synthetic_mrz_td1(personal_number="12345678A")
        res = validate_mrz_td1(lines)
        personal_check = next(c for c in res.checks if c.check_name == "MRZ_PERSONAL_NUMBER_CHECKSUM")
        assert personal_check.passed is False
        assert res.composite_check_passed is True
        assert res.is_valid is False


class TestMRZCharset:
    """Fallo 16: un carácter fuera del juego ICAO provocaba ValueError (500 en la API)."""

    def test_invalid_character_returns_invalid_result(self):
        lines = generate_synthetic_mrz_td1()
        broken = [lines[0][:10] + " " + lines[0][11:], lines[1], lines[2]]
        res = validate_mrz_td1(broken)
        assert res.is_valid is False
        assert any(c.check_name == "MRZ_LINE_1_CHARSET" for c in res.checks)

    def test_api_mrz_with_invalid_character_is_not_500(self):
        lines = generate_synthetic_mrz_td1()
        broken = [lines[0][:10] + "é" + lines[0][11:], lines[1], lines[2]]
        resp = TestClient(app).post("/api/v1/audit/mrz", json={"lines": broken})
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is False


class TestCrossCheckNormalization:
    """Fallos 4 y 15: tildes/Ñ daban discrepancias falsas y las subcadenas eran demasiado permisivas."""

    @pytest.fixture
    def mrz(self):
        return validate_mrz_td1(generate_synthetic_mrz_td1(surname="GARCIA MUNOZ", given_names="CARMEN"))

    def _surname_check(self, mrz, surname):
        report = cross_verify_front_with_mrz(DocumentFrontData(surname=surname), mrz)
        return next(f for f in report.findings if f.check_name == "CROSS_CHECK_SURNAME")

    def test_accents_and_enye_match_icao_transliteration(self, mrz):
        assert self._surname_check(mrz, "GARCÍA MUÑOZ").passed is True

    def test_first_surname_only_is_reported_as_partial(self, mrz):
        check = self._surname_check(mrz, "García")
        assert check.passed is True
        assert "parcial" in check.details.lower()

    def test_second_surname_alone_or_other_surname_fails(self, mrz):
        assert self._surname_check(mrz, "MUÑOZ").passed is False
        assert self._surname_check(mrz, "GARCIA LOPEZ").passed is False

    def test_document_number_requires_exact_match(self, mrz):
        report = cross_verify_front_with_mrz(DocumentFrontData(document_number="1234567"), mrz)
        assert report.findings[0].passed is False

    def test_support_number_is_cross_checked(self, mrz):
        report = cross_verify_front_with_mrz(DocumentFrontData(support_number="BAA000009"), mrz)
        support = next(f for f in report.findings if f.check_name == "CROSS_CHECK_SUPPORT_NUMBER")
        assert support.passed is False


class TestFrontDateAssignment:
    """Las fechas del anverso se asignan por valor, no por el orden de lectura del OCR."""

    def test_dates_sorted_by_value(self, fake_ocr):
        fake_ocr(["VALIDEZ 01 01 2031", "EMISION 01 01 2021", "NACIMIENTO 15 08 1985", "12345678Z"])
        result = DocumentAutoDetector.analyze_image_auto(BLANK)
        assert result.detected_birth_date == "15 08 1985"
        assert result.detected_expiry_date == "01 01 2031"


class TestUIPipelineRegressions:
    """Fallos 3, 10 y 11 en la interfaz."""

    def test_back_only_upload_uses_dni_not_support_number(self, fake_ocr):
        lines = generate_synthetic_mrz_td1(personal_number="12345678Z", expiry_date="300101")
        fake_ocr(lines)
        back_img = create_specimen_back_image(doc_number="12345678Z", expiry_date="300101")

        dashboard, _, _, _, checklist, json_rep = run_document_audit(
            front_image_input=back_img, back_image_input=None, doc_type="DNI 4.0 / 3.0",
            front_doc_number="", front_expiry_date="", front_birth_date="", front_surname="", mrz_text="",
        )
        report = json.loads(json_rep)

        assert report["manager_summary"]["doc_number"] == "12345678Z"
        assert report["manager_summary"]["support_number"] == "BAA000001"
        assert not any(c["control"].endswith("DNI_FORMAT") for c in report["checklist"])
        # Sin anverso no hay cruce: no debe aparecer como superado comparando la MRZ consigo misma
        assert not any("Cruce Coherencia" in c["control"] for c in report["checklist"])
        assert report["risk_level"] != RiskLevel.CRITICAL.value

    def test_photo_edge_check_runs_in_ui(self, fake_ocr):
        fake_ocr([])
        front = Image.new("RGB", (800, 500), color=(230, 230, 230))
        _, _, _, _, checklist, _ = run_document_audit(
            front_image_input=front, back_image_input=None, doc_type="DNI 4.0 / 3.0",
            front_doc_number="", front_expiry_date="", front_birth_date="", front_surname="", mrz_text="",
        )
        assert "Transición de Borde de Fotografía" in checklist

    def test_validity_uses_checksum_verified_mrz_date_not_tampered_front(self, fake_ocr):
        # Anverso manipulado: caducidad 2035 sobre un documento cuya MRZ (válida) dice 2030
        lines = generate_synthetic_mrz_td1(personal_number="12345678Z", expiry_date="300101", surname="GARCIA LOPEZ")
        fake_ocr([])
        _, _, _, _, _, json_rep = run_document_audit(
            front_image_input=None, back_image_input=create_specimen_back_image(), doc_type="DNI 4.0 / 3.0",
            front_doc_number="12345678Z", front_expiry_date="01/01/2035", front_birth_date="01/01/1985",
            front_surname="GARCIA LOPEZ", mrz_text="\n".join(lines),
        )
        report = json.loads(json_rep)
        assert report["manager_summary"]["expiry_date"] == "01/01/2030"
        assert report["risk_level"] == RiskLevel.CRITICAL.value

    def test_document_type_is_detected_unless_operator_overrides(self, fake_ocr):
        from src.app.ui import DOC_TYPE_AUTO

        nie_body, nie_letter = generate_synthetic_nie(prefix="Z", number=1234567)
        lines = generate_synthetic_mrz_td1(personal_number=nie_body + nie_letter, support_number="E00000001", nationality="ARG")
        fake_ocr(lines)

        def audited_type(selected):
            _, _, _, _, _, json_rep = run_document_audit(
                front_image_input=create_specimen_back_image(), back_image_input=None, doc_type=selected,
                front_doc_number="", front_expiry_date="", front_birth_date="", front_surname="", mrz_text="",
            )
            return json.loads(json_rep)["manager_summary"]["doc_type"]

        # Antes el valor por defecto del desplegable ("DNI 4.0 / 3.0") pisaba la detección en una TIE
        assert audited_type(DOC_TYPE_AUTO) == "NIE / TIE (Extranjeros)"
        assert audited_type("Tarjeta Roja (Asilo)") == "Tarjeta Roja (Asilo)"

    def test_image_inputs_preserve_original_file(self):
        import gradio as gr
        from src.app.ui import build_app

        demo = build_app()
        inputs = [b for b in demo.blocks.values() if isinstance(b, gr.Image) and b.type == "filepath"]
        assert len(inputs) == 2
        for component in inputs:
            assert component.image_mode is None  # sin re-codificación de JPEG en gris o PNG RGBA
            assert component.format == "png"  # muestras y capturas sin pérdida (no webp)

    def test_uploaded_file_keeps_original_exif(self, tmp_path):
        img = Image.new("RGB", (64, 64), color=(200, 200, 200))
        exif = Image.Exif()
        exif[305] = "Adobe Photoshop 25.0"
        path = tmp_path / "upload.jpg"
        img.save(path, format="JPEG", exif=exif)

        _, raw_bytes = _load_image_to_memory(str(path))

        assert raw_bytes == path.read_bytes()
        assert inspect_image_exif(raw_bytes).is_suspicious_software is True


class TestForensicSeverity:
    """Fallo 12: el ELA trabaja a resolución original y sus indicios no marcan rojo por sí solos."""

    def test_ela_display_is_downscaled_but_analysis_is_not(self):
        big = Image.new("RGB", (3000, 2000), color=(220, 225, 230))
        res = compute_ela(big)
        shown = Image.open(io.BytesIO(res.ela_image_bytes))
        assert max(shown.size) == MAX_ELA_DISPLAY_DIM
        assert "fiabilidad reducida" not in res.details

    def test_ela_anomaly_alone_is_warning_not_critical(self):
        ela = ELAResult(mean_error=3.0, max_error=40.0, std_error=9.0, anomaly_detected=True, details="Pico")
        report = build_audit_report(ela=ela)
        assert report.risk_level == RiskLevel.WARNING
        assert report.controls_failed == 0


class TestAPIHardening:
    """Fallos 6 y 8: CORS abierto, sin autenticación y volcado de subidas a disco."""

    def test_no_cors_headers_by_default(self):
        resp = TestClient(app).get("/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in resp.headers

    def test_api_key_enforced_when_configured(self, monkeypatch):
        monkeypatch.setenv("HERMETIC_API_KEY", "clave-de-prueba")
        client = TestClient(app)
        payload = {"document_number": "12345678Z"}
        assert client.post("/api/v1/audit/document-number", json=payload).status_code == 401
        ok = client.post("/api/v1/audit/document-number", json=payload, headers={"X-API-Key": "clave-de-prueba"})
        assert ok.status_code == 200

    def test_oversized_request_rejected_before_parsing(self):
        resp = TestClient(app).post(
            "/api/v1/audit/document",
            content=b"x",
            headers={"Content-Length": str(200 * 1024 * 1024), "Content-Type": "multipart/form-data; boundary=x"},
        )
        assert resp.status_code == 413

    def test_uploads_are_kept_in_memory(self):
        from starlette.formparsers import MultiPartParser
        from src.api.main import MAX_REQUEST_SIZE_BYTES
        assert MultiPartParser.spool_max_size >= MAX_REQUEST_SIZE_BYTES
