"""Pruebas unitarias para los algoritmos matemáticos y estándares ICAO."""

import pytest
from src.core.algorithms import (
    calculate_dni_letter,
    validate_dni,
    validate_nie,
    calculate_icao_check_digit,
    validate_mrz_td1,
    validate_mrz_td3,
    sanitize_ocr_b_numeric,
    icao_char_value,
)
from tests.fixtures.synthetic_generator import (
    generate_synthetic_dni,
    generate_synthetic_nie,
    generate_synthetic_mrz_td1,
)


class TestDNIAlgorithms:
    """Verificación del algoritmo Módulo 23 para DNI español."""

    def test_calculate_dni_letter_official_cases(self):
        # 0 % 23 = 0 -> 'T'
        assert calculate_dni_letter(0) == "T"
        # 12345678 % 23 = 14 -> 'Z'
        assert calculate_dni_letter(12345678) == "Z"
        # 99999999 % 23 = 14 -> 'R' (99999999 = 4347826 * 23 + 1 -> 'R')
        assert calculate_dni_letter(99999999) == "R"

    def test_validate_dni_valid_cases(self):
        res = validate_dni("12345678Z")
        assert res.passed is True
        assert res.expected == "Z"
        assert res.actual == "Z"

        # Con guión y espacios
        res_spaced = validate_dni(" 12345678-Z ")
        assert res_spaced.passed is True

    def test_validate_dni_invalid_letter(self):
        res = validate_dni("12345678A")  # Debería ser Z
        assert res.passed is False
        assert res.check_name == "DNI_CHECKSUM"
        assert res.expected == "Z"
        assert res.actual == "A"

    def test_validate_dni_invalid_formats(self):
        res_short = validate_dni("1234Z")
        assert res_short.passed is False
        assert res_short.check_name == "DNI_FORMAT"

        res_letters = validate_dni("ABCDEFGHZ")
        assert res_letters.passed is False


class TestNIEAlgorithms:
    """Verificación del algoritmo Módulo 23 para NIE (extranjeros)."""

    def test_validate_nie_valid_prefixes(self):
        # X1234567 -> 01234567 % 23 = 19 -> 'L'
        res_x = validate_nie("X1234567L")
        assert res_x.passed is True

        # Y1234567 -> 11234567 % 23 = 6 -> 'Y'
        # 11234567 = 488459 * 23 + 10 -> 'X'
        expected_y_letter = calculate_dni_letter(11234567)
        res_y = validate_nie(f"Y1234567{expected_y_letter}")
        assert res_y.passed is True

        # Z1234567
        expected_z_letter = calculate_dni_letter(21234567)
        res_z = validate_nie(f"Z1234567{expected_z_letter}")
        assert res_z.passed is True

    def test_validate_nie_invalid_prefix(self):
        res = validate_nie("A1234567L")
        assert res.passed is False
        assert res.check_name == "NIE_PREFIX"

    def test_validate_nie_wrong_letter(self):
        res = validate_nie("X1234567A")
        assert res.passed is False
        assert res.check_name == "NIE_CHECKSUM"


class TestICAOAlgorithms:
    """Verificación del estándar internacional ICAO Doc 9303 TD1."""

    def test_icao_char_values(self):
        assert icao_char_value("<") == 0
        assert icao_char_value("0") == 0
        assert icao_char_value("9") == 9
        assert icao_char_value("A") == 10
        assert icao_char_value("Z") == 35

    def test_icao_check_digit_calculation(self):
        # Caso de prueba: 'HA599999' -> cálculo con pesos 7, 3, 1
        # H=17*7=119, A=10*3=30, 5=5*1=5, 9=9*7=63, 9=9*3=27, 9=9*1=9, 9=9*7=63, 9=9*3=27
        # Total = 119 + 30 + 5 + 63 + 27 + 9 + 63 + 27 = 343 -> 343 % 10 = 3
        assert calculate_icao_check_digit("HA599999") == "3"

    def test_validate_mrz_td1_valid(self):
        lines = generate_synthetic_mrz_td1(
            support_number="BAA000001",
            birth_date="850101",
            expiry_date="300101",
            surname="GARCIA",
            given_names="CARMEN",
        )
        res = validate_mrz_td1(lines)
        assert res.is_valid is True
        assert res.document_number == "BAA000001"
        assert res.surname == "GARCIA"
        assert res.given_names == "CARMEN"
        assert res.composite_check_passed is True

    def test_validate_mrz_td1_tampered_document_number(self):
        lines = generate_synthetic_mrz_td1(support_number="BAA000001")
        # Alterar un dígito del número de documento en la línea 1 sin actualizar el dígito de control
        corrupted_l1 = lines[0][:5] + "BAA000009" + lines[0][14:]
        corrupted_lines = [corrupted_l1, lines[1], lines[2]]
        
        res = validate_mrz_td1(corrupted_lines)
        assert res.is_valid is False
        doc_check = next(c for c in res.checks if c.check_name == "MRZ_DOC_NUM_CHECKSUM")
        assert doc_check.passed is False

    def test_validate_mrz_td1_tampered_expiry_date(self):
        lines = generate_synthetic_mrz_td1(expiry_date="300101")
        # Alterar el año de caducidad de 30 a 35 en línea 2 (posiciones 8-14)
        # Línea 2: birth(6) + check(1) + sex(1) + expiry(6)...
        corrupted_l2 = lines[1][:8] + "350101" + lines[1][14:]
        corrupted_lines = [lines[0], corrupted_l2, lines[2]]
        
        res = validate_mrz_td1(corrupted_lines)
        assert res.is_valid is False
        expiry_check = next(c for c in res.checks if c.check_name == "MRZ_EXPIRY_CHECKSUM")
        assert expiry_check.passed is False

    def test_cross_validation_with_reference_mrz_library(self):
        """Validación cruzada de nuestros algoritmos contra la librería oficial mrz."""
        from mrz.checker.td1 import TD1CodeChecker

        lines = generate_synthetic_mrz_td1(
            support_number="BAA000001",
            birth_date="850101",
            expiry_date="300101",
            surname="GARCIA",
            given_names="CARMEN",
        )
        # Validar con nuestro motor
        our_result = validate_mrz_td1(lines)
        assert our_result.is_valid is True

        # Validar con la librería de referencia estándar mrz
        mrz_str = "\n".join(lines)
        checker = TD1CodeChecker(mrz_str)
        assert bool(checker) is True  # Ambos coinciden en que es 100% válido


class TestMRZTD3Algorithms:
    """Verificación de algoritmos ICAO Doc 9303 Parte 4 (Pasaportes TD3 - 2 líneas x 44 caracteres)."""

    def test_validate_mrz_td3_valid(self):
        # Muestra oficial estándar ICAO Doc 9303
        lines = [
            "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
            "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
        ]
        res = validate_mrz_td3(lines)
        assert res.is_valid is True
        assert res.document_type == "P"
        assert res.issuing_country == "UTO"
        assert res.surname == "ERIKSSON"
        assert res.given_names == "ANNA MARIA"
        assert res.document_number == "L898902C3"
        assert res.birth_date == "740812"
        assert res.expiry_date == "120415"
        assert res.sex == "F"
        assert res.composite_check_passed is True

    def test_validate_mrz_td3_tampered_document_number(self):
        lines = [
            "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
            "L898902C46UTO7408122F1204159ZE184226B<<<<<10",  # Alterado C3 -> C4
        ]
        res = validate_mrz_td3(lines)
        assert res.is_valid is False
        doc_check = next(c for c in res.checks if c.check_name == "MRZ_DOC_NUM_CHECKSUM")
        assert doc_check.passed is False

    def test_validate_mrz_td3_invalid_line_count_or_length(self):
        # 1 sola línea
        res_short = validate_mrz_td3(["P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"])
        assert res_short.is_valid is False
        assert any(c.check_name == "MRZ_LINE_COUNT" for c in res_short.checks)

        # Longitud incorrecta
        res_bad_len = validate_mrz_td3(["P<SHORT", "L898902C36UTO7408122F1204159ZE184226B<<<<<10"])
        assert res_bad_len.is_valid is False
        assert any("LENGTH" in c.check_name for c in res_bad_len.checks)


class TestOCRSanitizer:
    """Verificación del saneamiento contextual OCR-B para ranuras numéricas."""

    def test_sanitize_ocr_b_numeric_replaces_glyphs(self):
        assert sanitize_ocr_b_numeric("O85O1O1") == "0850101"
        assert sanitize_ocr_b_numeric("I2ZSB") == "12258"
        assert sanitize_ocr_b_numeric("QDls") == "0015"

    def test_validate_mrz_td1_with_ocr_b_glyph_noise_passes(self):
        # Línea 2 con 'O' en fecha de nacimiento y 'I' en lugar de '1'
        # Fecha original: 850101 -> con OCR ruidoso: 85O1O1
        lines = generate_synthetic_mrz_td1(
            support_number="BAA000001",
            birth_date="850101",
            expiry_date="300101",
        )
        # Inyectar 'O' en ranura numérica de fecha de nacimiento
        noisy_l2 = "85O1O1" + lines[1][6:]
        noisy_lines = [lines[0], noisy_l2, lines[2]]

        res = validate_mrz_td1(noisy_lines)
        assert res.is_valid is True
        assert res.birth_date == "850101"


