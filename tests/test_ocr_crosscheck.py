"""Pruebas unitarias para normalización de caracteres OCR y verificación cruzada anverso/reverso."""

import pytest
from src.core.algorithms import validate_mrz_td1
from src.detectors.ocr_engine import (
    DocumentFrontData,
    MRZTextCleaner,
    cross_verify_front_with_mrz,
    parse_date_to_yymmdd,
)
from tests.fixtures.synthetic_generator import generate_synthetic_mrz_td1


class TestMRZTextCleaner:
    """Verificación de la limpieza y corrección de artefactos en tipografía OCR-B."""

    def test_clean_mrz_line_replaces_guillemets_and_spaces(self):
        dirty = "IDESP«123456784  AAA000000"
        cleaned = MRZTextCleaner.clean_mrz_line(dirty)
        assert "«" not in cleaned
        assert " " not in cleaned
        assert cleaned.startswith("IDESP<123456784<<AAA000000")

    def test_sanitize_mrz_lines_enforces_30_chars(self):
        raw_lines = [
            "IDESP123456784AAA000000",  # Corta
            "8501014F3001018ESP<<<<<<<<<<<2EXTRACHARS",  # Larga
            "PRUEBA<<ESPECIMEN<<<<<<<<<<<<<",
        ]
        sanitized = MRZTextCleaner.sanitize_mrz_lines(raw_lines)
        assert len(sanitized) == 3
        for line in sanitized:
            assert len(line) == 30


class TestDateParsing:
    """Conversión de fechas comunes a formato YYMMDD."""

    def test_parse_valid_dates(self):
        assert parse_date_to_yymmdd("01/01/2030") == "300101"
        assert parse_date_to_yymmdd("15-08-1985") == "850815"
        assert parse_date_to_yymmdd("31.12.2028") == "281231"


class TestCrossVerification:
    """Auditoría cruzada determinista entre anverso y reverso."""

    @pytest.fixture
    def valid_mrz_result(self):
        lines = generate_synthetic_mrz_td1(
            personal_number="12345678Z",
            birth_date="850101",
            expiry_date="300101",
            surname="GARCIA",
            given_names="CARMEN",
        )
        return validate_mrz_td1(lines)

    def test_matching_front_and_back_has_no_discrepancies(self, valid_mrz_result):
        front = DocumentFrontData(
            document_number="12345678Z",
            birth_date="01/01/1985",
            expiry_date="01/01/2030",
            surname="GARCIA",
        )
        report = cross_verify_front_with_mrz(front, valid_mrz_result)
        assert report.has_discrepancies is False
        assert report.failed_checks_count == 0
        assert report.passed_checks_count == 4

    def test_mismatch_expiry_date_is_flagged(self, valid_mrz_result):
        # Frontal declara 2035 pero MRZ tiene 2030 (300101)
        front = DocumentFrontData(
            document_number="12345678Z",
            expiry_date="01/01/2035",
        )
        report = cross_verify_front_with_mrz(front, valid_mrz_result)
        assert report.has_discrepancies is True
        assert report.failed_checks_count >= 1
        expiry_check = next(f for f in report.findings if f.check_name == "CROSS_CHECK_EXPIRY_DATE")
        assert expiry_check.passed is False

    def test_mismatch_document_number_is_flagged(self, valid_mrz_result):
        # Frontal declara otro número
        front = DocumentFrontData(
            document_number="87654321A",
        )
        report = cross_verify_front_with_mrz(front, valid_mrz_result)
        assert report.has_discrepancies is True
        doc_check = next(f for f in report.findings if f.check_name == "CROSS_CHECK_DOC_NUMBER")
        assert doc_check.passed is False

    def test_mismatch_surname_is_flagged(self, valid_mrz_result):
        front = DocumentFrontData(
            surname="MARTINEZ",  # En MRZ es GARCIA
        )
        report = cross_verify_front_with_mrz(front, valid_mrz_result)
        assert report.has_discrepancies is True
        surname_check = next(f for f in report.findings if f.check_name == "CROSS_CHECK_SURNAME")
        assert surname_check.passed is False
