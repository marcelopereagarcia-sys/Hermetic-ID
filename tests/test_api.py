"""Pruebas funcionales e integración para la API REST Headless de Hermetic-ID."""

import io
from fastapi.testclient import TestClient
import pytest
from PIL import Image

from src import __version__
from src.api.main import app
from tests.fixtures.synthetic_generator import (
    create_specimen_front_image,
    create_specimen_back_image,
    generate_synthetic_mrz_td1,
)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


class TestHealthEndpoints:
    """Verificación de endpoints de salud y metadatos."""

    def test_health_check_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == __version__
        assert "timestamp" in data

    def test_v1_health_check_alias(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestDocumentNumberEndpoint:
    """Verificación de comprobación instantánea de DNI / NIE."""

    def test_valid_dni_passes(self, client):
        resp = client.post(
            "/api/v1/audit/document-number",
            json={"document_number": "12345678Z"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["document_type"] == "DNI"
        assert data["expected_letter"] == "Z"

    def test_invalid_dni_checksum_fails(self, client):
        resp = client.post(
            "/api/v1/audit/document-number",
            json={"document_number": "12345678A"},  # Debería ser Z
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is False
        assert data["expected_letter"] == "Z"
        assert data["actual_letter"] == "A"

    def test_valid_nie_passes(self, client):
        resp = client.post(
            "/api/v1/audit/document-number",
            json={"document_number": "Y1234567X"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_valid"] is True
        assert data["document_type"] == "NIE"

    def test_unrecognized_format_returns_invalid(self, client):
        resp = client.post(
            "/api/v1/audit/document-number",
            json={"document_number": "INVALID999"},
        )
        assert resp.status_code == 200
        assert resp.json()["is_valid"] is False


class TestMRZBlockEndpoint:
    """Verificación del validador criptográfico/algorítmico de MRZ."""

    def test_valid_td1_block(self, client):
        lines = generate_synthetic_mrz_td1(
            support_number="BAA000001",
            birth_date="850101",
            expiry_date="300101",
            surname="MUESTRA",
            given_names="ESPECIMEN",
        )
        resp = client.post("/api/v1/audit/mrz", json={"lines": lines})
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "TD1"
        assert data["is_valid"] is True
        assert data["document_number"] == "BAA000001"
        assert data["composite_check_passed"] is True

    def test_valid_td3_passport_block(self, client):
        lines = [
            "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
            "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
        ]
        resp = client.post("/api/v1/audit/mrz", json={"lines": lines})
        assert resp.status_code == 200
        data = resp.json()
        assert data["format"] == "TD3"
        assert data["is_valid"] is True
        assert data["surname"] == "ERIKSSON"
        assert data["composite_check_passed"] is True

    def test_invalid_mrz_line_count_raises_400(self, client):
        resp = client.post("/api/v1/audit/mrz", json={"lines": ["ONLY_ONE_LINE"]})
        assert resp.status_code == 400


class TestDocumentAuditEndpoint:
    """Verificación del endpoint principal de auditoría forense multipart."""

    def test_audit_without_images_raises_400(self, client):
        resp = client.post("/api/v1/audit/document")
        assert resp.status_code == 400

    def test_audit_with_specimen_images_returns_report(self, client):
        front_img = create_specimen_front_image()
        back_img = create_specimen_back_image()

        f_buf = io.BytesIO()
        front_img.save(f_buf, format="JPEG")
        f_buf.seek(0)

        b_buf = io.BytesIO()
        back_img.save(b_buf, format="JPEG")
        b_buf.seek(0)

        files = {
            "front_image": ("front.jpg", f_buf, "image/jpeg"),
            "back_image": ("back.jpg", b_buf, "image/jpeg"),
        }
        data = {
            "front_doc_number": "12345678Z",
            "front_surname": "GARCIA LOPEZ",
            "front_expiry_date": "01/01/2030",
            "front_birth_date": "01/01/1985",
        }

        resp = client.post("/api/v1/audit/document", files=files, data=data)
        assert resp.status_code == 200
        report = resp.json()

        assert "risk_level" in report
        assert "risk_score" in report
        assert "checklist" in report
        assert isinstance(report["checklist"], list)
        assert len(report["checklist"]) > 0
        assert report["quality_details"] is not None
        assert report["forensic_details"] is not None
