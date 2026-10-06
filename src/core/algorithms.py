"""Algoritmos oficiales y estándares para validación documental en España.

Incluye:
- Algoritmo de control del Documento Nacional de Identidad (DNI) según Real Decreto 1553/2005.
- Algoritmo para Número de Identidad de Extranjero (NIE).
- Estándar internacional ICAO Doc 9303 (Parte 5: TD1 - Formato tarjeta de 3 líneas x 30 caracteres).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple
import re

# Tabla oficial del algoritmo módulo 23 para DNI y NIE (BOE)
DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"

# Pesos cíclicos del algoritmo de dígito de control según estándar ICAO Doc 9303
ICAO_WEIGHTS = [7, 3, 1]

# Patrones de identificadores personales españoles
DNI_PATTERN = re.compile(r"\d{8}[A-Z]")
NIE_PATTERN = re.compile(r"[XYZ]\d{7}[A-Z]")


def extract_spanish_id_number(text: Optional[str]) -> Optional[str]:
    """Extrae un DNI o NIE completo de un campo MRZ (ignorando el relleno '<').

    En el DNI 3.0/4.0 y la TIE, el número personal (DNI/NIE) viaja en los datos
    opcionales de la línea 1, mientras que el campo "número de documento" contiene
    el número de soporte (IDESP).
    """
    if not text:
        return None
    cleaned = text.replace("<", "").strip().upper()
    if DNI_PATTERN.fullmatch(cleaned) or NIE_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


@dataclass
class ValidationResult:
    """Resultado estructurado de una comprobación individual."""
    check_name: str
    passed: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    details: str = ""


@dataclass
class MRZTD1Result:
    """Resultado de la decodificación y validación de una zona MRZ TD1 (3 líneas)."""
    is_valid: bool
    document_type: str
    issuing_country: str
    document_number: str
    birth_date: str  # YYMMDD
    sex: str
    expiry_date: str  # YYMMDD
    nationality: str
    surname: str
    given_names: str
    optional_data_1: str
    optional_data_2: str
    composite_check_passed: bool
    checks: List[ValidationResult] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)

    @property
    def personal_number(self) -> Optional[str]:
        """DNI o NIE codificado en los datos opcionales (DNI 3.0/4.0 y TIE españoles)."""
        return extract_spanish_id_number(self.optional_data_1)

    @property
    def support_number(self) -> Optional[str]:
        """Número de soporte (IDESP): en documentos españoles es el campo 'número de documento'."""
        if self.issuing_country == "ESP" and self.personal_number:
            return self.document_number or None
        return None


@dataclass
class MRZTD3Result:
    """Resultado de la decodificación y validación de una zona MRZ TD3 (2 líneas x 44 caracteres - Pasaportes)."""
    is_valid: bool
    document_type: str
    issuing_country: str
    surname: str
    given_names: str
    document_number: str
    nationality: str
    birth_date: str  # YYMMDD
    sex: str
    expiry_date: str  # YYMMDD
    optional_data: str
    composite_check_passed: bool
    checks: List[ValidationResult] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)

    @property
    def optional_data_1(self) -> str:
        """Alias para interoperabilidad con validadores TD1."""
        return self.optional_data

    @property
    def personal_number(self) -> Optional[str]:
        """DNI/NIE del titular en el campo de número personal (pasaportes españoles)."""
        return extract_spanish_id_number(self.optional_data)

    @property
    def support_number(self) -> Optional[str]:
        """Los pasaportes no tienen número de soporte IDESP."""
        return None


def calculate_dni_letter(number: int) -> str:
    """Calcula la letra correspondiente a un número de DNI usando módulo 23."""
    if not (0 <= number <= 99999999):
        raise ValueError(f"Número de DNI fuera de rango: {number}")
    return DNI_LETTERS[number % 23]


def validate_dni(dni_str: str) -> ValidationResult:
    """Valida si un DNI español tiene el formato y la letra de control correcta.
    
    Acepta formatos: '12345678Z', '12345678-Z', '12345678 Z'.
    """
    cleaned = re.sub(r"[\s\-]", "", dni_str.strip().upper())
    
    if len(cleaned) != 9:
        return ValidationResult(
            check_name="DNI_FORMAT",
            passed=False,
            expected="8 dígitos seguidos de 1 letra",
            actual=dni_str,
            details=f"Longitud incorrecta ({len(cleaned)} caracteres en lugar de 9)."
        )
    
    num_part = cleaned[:8]
    letter_part = cleaned[8]
    
    if not num_part.isdigit():
        return ValidationResult(
            check_name="DNI_FORMAT",
            passed=False,
            expected="8 dígitos numéricos",
            actual=num_part,
            details="La parte numérica contiene caracteres no dígitos."
        )
    
    expected_letter = calculate_dni_letter(int(num_part))
    passed = (letter_part == expected_letter)
    
    return ValidationResult(
        check_name="DNI_CHECKSUM",
        passed=passed,
        expected=expected_letter,
        actual=letter_part,
        details="Letra de control verificada con éxito." if passed else f"Letra errónea: calculada {expected_letter}, recibida {letter_part}."
    )


def validate_nie(nie_str: str) -> ValidationResult:
    """Valida si un NIE español tiene el formato y la letra de control correcta.
    
    Los NIEs comienzan por X, Y o Z, que equivalen a 0, 1 o 2 respectivamente,
    seguidos de 7 dígitos y una letra de control final calculada con módulo 23.
    """
    cleaned = re.sub(r"[\s\-]", "", nie_str.strip().upper())
    
    if len(cleaned) != 9:
        return ValidationResult(
            check_name="NIE_FORMAT",
            passed=False,
            expected="X/Y/Z seguido de 7 dígitos y 1 letra",
            actual=nie_str,
            details=f"Longitud incorrecta ({len(cleaned)} caracteres en lugar de 9)."
        )
    
    first_char = cleaned[0]
    middle_digits = cleaned[1:8]
    last_char = cleaned[8]
    
    prefix_map = {"X": "0", "Y": "1", "Z": "2"}
    if first_char not in prefix_map:
        return ValidationResult(
            check_name="NIE_PREFIX",
            passed=False,
            expected="X, Y o Z",
            actual=first_char,
            details=f"El prefijo inicial '{first_char}' no es un identificador NIE válido."
        )
        
    if not middle_digits.isdigit():
        return ValidationResult(
            check_name="NIE_FORMAT",
            passed=False,
            expected="7 dígitos numéricos centrales",
            actual=middle_digits,
            details="La sección central contiene caracteres no numéricos."
        )
        
    full_numeric = prefix_map[first_char] + middle_digits
    expected_letter = calculate_dni_letter(int(full_numeric))
    passed = (last_char == expected_letter)
    
    return ValidationResult(
        check_name="NIE_CHECKSUM",
        passed=passed,
        expected=expected_letter,
        actual=last_char,
        details="Letra NIE verificada con éxito." if passed else f"Letra errónea: calculada {expected_letter}, recibida {last_char}."
    )


def icao_char_value(c: str) -> int:
    """Convierte un carácter alfanumérico a su valor numérico según ICAO Doc 9303.
    
    '<' = 0
    '0'-'9' = 0-9
    'A'-'Z' = 10-35
    """
    if c == "<":
        return 0
    if c.isdigit():
        return int(c)
    if "A" <= c <= "Z":
        return ord(c) - ord("A") + 10
    raise ValueError(f"Carácter no válido en MRZ ICAO: {c!r}")


def calculate_icao_check_digit(data_str: str) -> str:
    """Calcula el dígito de control según algoritmo ponderado ICAO 7-3-1 (módulo 10)."""
    total = 0
    for i, char in enumerate(data_str):
        weight = ICAO_WEIGHTS[i % 3]
        val = icao_char_value(char)
        total += val * weight
    return str(total % 10)


def sanitize_ocr_b_numeric(text: str) -> str:
    """Sanea confusiones habituales de tipografía OCR-B en campos estrictamente numéricos.
    
    Mapeos seguros:
    - 'O', 'o', 'Q', 'q', 'D' -> '0'
    - 'I', 'i', 'l', 'L'      -> '1'
    - 'Z', 'z'                -> '2'
    - 'S', 's'                -> '5'
    - 'B', 'b'                -> '8'
    """
    subs = {
        "O": "0", "o": "0",
        "Q": "0", "q": "0",
        "D": "0",
        "I": "1", "i": "1",
        "L": "1", "l": "1",
        "Z": "2", "z": "2",
        "S": "5", "s": "5",
        "B": "8", "b": "8",
    }
    return "".join(subs.get(c, c) for c in text)


MRZ_CHARSET = re.compile(r"[A-Z0-9<]+")


def _charset_failure(line_index: int, line: str) -> ValidationResult:
    """Resultado de fallo para una línea MRZ con caracteres fuera del juego ICAO (A-Z, 0-9, '<')."""
    invalid = sorted({c for c in line if not MRZ_CHARSET.fullmatch(c)})
    return ValidationResult(
        check_name=f"MRZ_LINE_{line_index}_CHARSET",
        passed=False,
        expected="Solo A-Z, 0-9 y '<'",
        actual="".join(invalid),
        details=f"Línea {line_index} contiene caracteres no permitidos por ICAO 9303: {invalid!r}.",
    )


def validate_mrz_td1(lines: List[str]) -> MRZTD1Result:
    """Analiza y valida un bloque MRZ de formato TD1 (3 líneas x 30 caracteres).
    
    Estándar oficial utilizado en el reverso de DNI 3.0 / DNI 4.0 y tarjetas TIE.
    """
    clean_lines = [l.strip().upper() for l in lines if l.strip()]
    checks: List[ValidationResult] = []
    
    if len(clean_lines) != 3:
        return MRZTD1Result(
            is_valid=False,
            document_type="",
            issuing_country="",
            document_number="",
            birth_date="",
            sex="",
            expiry_date="",
            nationality="",
            surname="",
            given_names="",
            optional_data_1="",
            optional_data_2="",
            composite_check_passed=False,
            checks=[
                ValidationResult(
                    check_name="MRZ_LINE_COUNT",
                    passed=False,
                    expected="3 líneas",
                    actual=f"{len(clean_lines)} líneas",
                    details="Un bloque MRZ TD1 debe constar exactamente de 3 líneas."
                )
            ],
            raw_lines=clean_lines,
        )
        
    for i, line in enumerate(clean_lines, 1):
        if len(line) != 30:
            checks.append(ValidationResult(
                check_name=f"MRZ_LINE_{i}_LENGTH",
                passed=False,
                expected="30 caracteres",
                actual=f"{len(line)} caracteres",
                details=f"Línea {i} tiene longitud anómala."
            ))
        elif not MRZ_CHARSET.fullmatch(line):
            checks.append(_charset_failure(i, line))

    if any(not c.passed for c in checks):
        return MRZTD1Result(
            is_valid=False,
            document_type="",
            issuing_country="",
            document_number="",
            birth_date="",
            sex="",
            expiry_date="",
            nationality="",
            surname="",
            given_names="",
            optional_data_1="",
            optional_data_2="",
            composite_check_passed=False,
            checks=checks,
            raw_lines=clean_lines,
        )

    l1, l2, l3 = clean_lines[0], clean_lines[1], clean_lines[2]

    # --- Línea 1:
    # Pos 0-1: Tipo documento (ej. 'ID', 'IR')
    # Pos 2-4: País emisor (ej. 'ESP')
    # Pos 5-13: Número de documento (9 chars)
    # Pos 14: Dígito de control del número de documento
    # Pos 15-29: Datos opcionales 1 (15 chars)
    doc_type = l1[0:2].replace("<", "")
    issuing_country = l1[2:5]
    doc_num_field = l1[5:14]
    doc_num_check = sanitize_ocr_b_numeric(l1[14])
    opt_1 = l1[15:30]

    calc_doc_check = calculate_icao_check_digit(doc_num_field)
    passed_doc = (doc_num_check == calc_doc_check)
    checks.append(ValidationResult(
        check_name="MRZ_DOC_NUM_CHECKSUM",
        passed=passed_doc,
        expected=calc_doc_check,
        actual=doc_num_check,
        details="Dígito de control del número de documento correcto." if passed_doc else "Inconsistencia en dígito del número de documento."
    ))

    # --- Línea 2:
    # Pos 0-5: Fecha de nacimiento YYMMDD
    # Pos 6: Dígito de control de fecha de nacimiento
    # Pos 7: Sexo (M, F, <)
    # Pos 8-13: Fecha de caducidad YYMMDD
    # Pos 14: Dígito de control de fecha de caducidad
    # Pos 15-17: Nacionalidad
    # Pos 18-28: Datos opcionales 2 (11 chars)
    # Pos 29: Dígito de control compuesto general
    birth_field = sanitize_ocr_b_numeric(l2[0:6])
    birth_check = sanitize_ocr_b_numeric(l2[6])
    sex = l2[7]
    expiry_field = sanitize_ocr_b_numeric(l2[8:14])
    expiry_check = sanitize_ocr_b_numeric(l2[14])
    nationality = l2[15:18]
    opt_2 = l2[18:29]
    composite_check = sanitize_ocr_b_numeric(l2[29])

    # Validar fecha nacimiento
    calc_birth_check = calculate_icao_check_digit(birth_field)
    passed_birth = (birth_check == calc_birth_check)
    checks.append(ValidationResult(
        check_name="MRZ_BIRTH_CHECKSUM",
        passed=passed_birth,
        expected=calc_birth_check,
        actual=birth_check,
        details="Dígito de control de fecha de nacimiento correcto." if passed_birth else "Inconsistencia en fecha de nacimiento."
    ))

    # Validar fecha caducidad
    calc_expiry_check = calculate_icao_check_digit(expiry_field)
    passed_expiry = (expiry_check == calc_expiry_check)
    checks.append(ValidationResult(
        check_name="MRZ_EXPIRY_CHECKSUM",
        passed=passed_expiry,
        expected=calc_expiry_check,
        actual=expiry_check,
        details="Dígito de control de fecha de caducidad correcto." if passed_expiry else "Inconsistencia en fecha de caducidad."
    ))

    # Dígito de control compuesto (Composite Check Digit)
    # En ICAO TD1, se calcula sobre:
    # Línea 1: pos 5-29 (doc_num_field + doc_num_check + opt_1) = 25 chars
    # Línea 2: pos 0-6 (birth_field + birth_check) + pos 8-14 (expiry_field + expiry_check) + pos 18-28 (opt_2) = 7 + 7 + 11 = 25 chars
    composite_data = doc_num_field + doc_num_check + opt_1 + birth_field + birth_check + expiry_field + expiry_check + opt_2
    calc_composite_check = calculate_icao_check_digit(composite_data)
    passed_composite = (composite_check == calc_composite_check)
    checks.append(ValidationResult(
        check_name="MRZ_COMPOSITE_CHECKSUM",
        passed=passed_composite,
        expected=calc_composite_check,
        actual=composite_check,
        details="Dígito de control compuesto general correcto." if passed_composite else "Inconsistencia en dígito compuesto general."
    ))

    # Documentos españoles: el DNI/NIE de los datos opcionales tiene su propia letra de control (módulo 23)
    personal_number = extract_spanish_id_number(opt_1)
    if issuing_country == "ESP" and personal_number:
        personal_check = validate_nie(personal_number) if personal_number[0] in "XYZ" else validate_dni(personal_number)
        checks.append(ValidationResult(
            check_name="MRZ_PERSONAL_NUMBER_CHECKSUM",
            passed=personal_check.passed,
            expected=personal_check.expected,
            actual=personal_check.actual,
            details=f"DNI/NIE codificado en la MRZ ({personal_number}): {personal_check.details}",
        ))

    # --- Línea 3:
    # Nombres y apellidos separados por '<<'
    # Formato: APELLIDO1<APELLIDO2<<NOMBRE1<NOMBRE2
    name_parts = l3.split("<<", 1)
    surname = name_parts[0].replace("<", " ").strip()
    given_names = name_parts[1].replace("<", " ").strip() if len(name_parts) > 1 else ""

    clean_doc_number = doc_num_field.replace("<", "")
    all_passed = all(c.passed for c in checks)

    return MRZTD1Result(
        is_valid=all_passed,
        document_type=doc_type,
        issuing_country=issuing_country,
        document_number=clean_doc_number,
        birth_date=birth_field,
        sex=sex,
        expiry_date=expiry_field,
        nationality=nationality,
        surname=surname,
        given_names=given_names,
        optional_data_1=opt_1,
        optional_data_2=opt_2,
        composite_check_passed=passed_composite,
        checks=checks,
        raw_lines=clean_lines,
    )


def validate_mrz_td3(lines: List[str]) -> MRZTD3Result:
    """Analiza y valida un bloque MRZ de formato TD3 (2 líneas x 44 caracteres - Pasaportes).
    
    Estándar oficial ICAO Doc 9303 Parte 4.
    """
    clean_lines = [l.strip().upper() for l in lines if l.strip()]
    checks: List[ValidationResult] = []

    if len(clean_lines) != 2:
        return MRZTD3Result(
            is_valid=False,
            document_type="",
            issuing_country="",
            surname="",
            given_names="",
            document_number="",
            nationality="",
            birth_date="",
            sex="",
            expiry_date="",
            optional_data="",
            composite_check_passed=False,
            checks=[
                ValidationResult(
                    check_name="MRZ_LINE_COUNT",
                    passed=False,
                    expected="2 líneas",
                    actual=f"{len(clean_lines)} líneas",
                    details="Un bloque MRZ TD3 (Pasaporte) debe constar exactamente de 2 líneas."
                )
            ],
            raw_lines=clean_lines,
        )

    for i, line in enumerate(clean_lines, 1):
        if len(line) != 44:
            checks.append(ValidationResult(
                check_name=f"MRZ_LINE_{i}_LENGTH",
                passed=False,
                expected="44 caracteres",
                actual=f"{len(line)} caracteres",
                details=f"Línea {i} tiene longitud anómala para formato TD3."
            ))
        elif not MRZ_CHARSET.fullmatch(line):
            checks.append(_charset_failure(i, line))

    if any(not c.passed for c in checks):
        return MRZTD3Result(
            is_valid=False,
            document_type="",
            issuing_country="",
            surname="",
            given_names="",
            document_number="",
            nationality="",
            birth_date="",
            sex="",
            expiry_date="",
            optional_data="",
            composite_check_passed=False,
            checks=checks,
            raw_lines=clean_lines,
        )

    l1, l2 = clean_lines[0], clean_lines[1]

    # --- Línea 1 (44 caracteres):
    # Pos 0-1: Tipo documento (ej. 'P<', 'PA')
    # Pos 2-4: País emisor (3 chars, ej. 'ESP')
    # Pos 5-43: Apellidos << Nombres
    doc_type = l1[0:2].replace("<", "")
    issuing_country = l1[2:5]
    names_block = l1[5:44]
    name_parts = names_block.split("<<", 1)
    surname = name_parts[0].replace("<", " ").strip()
    given_names = name_parts[1].replace("<", " ").strip() if len(name_parts) > 1 else ""

    # --- Línea 2 (44 caracteres):
    # Pos 0-8: Número de pasaporte (9 chars)
    # Pos 9: Dígito de control del número de pasaporte
    # Pos 10-12: Nacionalidad (3 chars)
    # Pos 13-18: Fecha de nacimiento YYMMDD
    # Pos 19: Dígito de control fecha de nacimiento
    # Pos 20: Sexo (M, F, <)
    # Pos 21-26: Fecha de caducidad YYMMDD
    # Pos 27: Dígito de control fecha de caducidad
    # Pos 28-41: Número personal / datos opcionales (14 chars)
    # Pos 42: Dígito de control de datos opcionales
    # Pos 43: Dígito de control compuesto general
    doc_num_field = l2[0:9]
    doc_num_check = sanitize_ocr_b_numeric(l2[9])
    nationality = l2[10:13]
    birth_field = sanitize_ocr_b_numeric(l2[13:19])
    birth_check = sanitize_ocr_b_numeric(l2[19])
    sex = l2[20]
    expiry_field = sanitize_ocr_b_numeric(l2[21:27])
    expiry_check = sanitize_ocr_b_numeric(l2[27])
    opt_field = l2[28:42]
    opt_check = l2[42]
    composite_check = sanitize_ocr_b_numeric(l2[43])

    # 1. Validar dígito número de documento
    calc_doc_check = calculate_icao_check_digit(doc_num_field)
    passed_doc = (doc_num_check == calc_doc_check)
    checks.append(ValidationResult(
        check_name="MRZ_DOC_NUM_CHECKSUM",
        passed=passed_doc,
        expected=calc_doc_check,
        actual=doc_num_check,
        details="Dígito de control del número de pasaporte correcto." if passed_doc else "Inconsistencia en número de pasaporte."
    ))

    # 2. Validar fecha nacimiento
    calc_birth_check = calculate_icao_check_digit(birth_field)
    passed_birth = (birth_check == calc_birth_check)
    checks.append(ValidationResult(
        check_name="MRZ_BIRTH_CHECKSUM",
        passed=passed_birth,
        expected=calc_birth_check,
        actual=birth_check,
        details="Dígito de control de fecha de nacimiento correcto." if passed_birth else "Inconsistencia en fecha de nacimiento."
    ))

    # 3. Validar fecha caducidad
    calc_expiry_check = calculate_icao_check_digit(expiry_field)
    passed_expiry = (expiry_check == calc_expiry_check)
    checks.append(ValidationResult(
        check_name="MRZ_EXPIRY_CHECKSUM",
        passed=passed_expiry,
        expected=calc_expiry_check,
        actual=expiry_check,
        details="Dígito de control de fecha de caducidad correcto." if passed_expiry else "Inconsistencia en fecha de caducidad."
    ))

    # 4. Validar dígito de control opcional (si aplica)
    if opt_check != "<":
        calc_opt_check = calculate_icao_check_digit(opt_field)
        passed_opt = (sanitize_ocr_b_numeric(opt_check) == calc_opt_check)
        checks.append(ValidationResult(
            check_name="MRZ_OPTIONAL_DATA_CHECKSUM",
            passed=passed_opt,
            expected=calc_opt_check,
            actual=opt_check,
            details="Dígito de control de datos opcionales correcto." if passed_opt else "Inconsistencia en datos opcionales."
        ))

    # 5. Dígito de control compuesto (Composite Check Digit)
    # ICAO Doc 9303 Part 4: pos 0-9 (10) + pos 13-19 (7) + pos 21-42 (22) = 39 caracteres
    composite_data = doc_num_field + doc_num_check + birth_field + birth_check + expiry_field + expiry_check + opt_field + opt_check
    calc_composite_check = calculate_icao_check_digit(composite_data)
    passed_composite = (composite_check == calc_composite_check)
    checks.append(ValidationResult(
        check_name="MRZ_COMPOSITE_CHECKSUM",
        passed=passed_composite,
        expected=calc_composite_check,
        actual=composite_check,
        details="Dígito de control compuesto general correcto." if passed_composite else "Inconsistencia en dígito compuesto general."
    ))

    clean_doc_number = doc_num_field.replace("<", "")
    all_passed = all(c.passed for c in checks)

    return MRZTD3Result(
        is_valid=all_passed,
        document_type=doc_type,
        issuing_country=issuing_country,
        surname=surname,
        given_names=given_names,
        document_number=clean_doc_number,
        nationality=nationality,
        birth_date=birth_field,
        sex=sex,
        expiry_date=expiry_field,
        optional_data=opt_field.replace("<", " ").strip(),
        composite_check_passed=passed_composite,
        checks=checks,
        raw_lines=clean_lines,
    )


def mrz_check_passed(mrz_result, check_name: str) -> bool:
    """Indica si un control concreto de la MRZ se ejecutó y fue superado.

    Un campo de la MRZ con su dígito de control correcto es la fuente más fiable del documento:
    el anverso puede estar manipulado sin que nada lo delate, la MRZ no sin romper un dígito.
    """
    if mrz_result is None:
        return False
    return any(c.check_name == check_name and c.passed for c in mrz_result.checks)


def parse_icao_date_to_date(
    yymmdd: str,
    is_expiry: bool = False,
    reference_date: Optional[date] = None,
) -> Optional[date]:
    """Convierte una fecha YYMMDD de formato ICAO Doc 9303 a objeto date.
    
    Para caducidad:
      Asume siglo XXI (2000+YY) para documentos contemporáneos.
    Para nacimiento:
      Si YY > (año_actual % 100), asume siglo XX (1900+YY).
      Si YY <= (año_actual % 100), asume siglo XXI (2000+YY).
    """
    if not yymmdd or len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    
    if reference_date is None:
        reference_date = date.today()
        
    yy = int(yymmdd[0:2])
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    
    if mm < 1 or mm > 12 or dd < 1 or dd > 31:
        return None
        
    if is_expiry:
        year = 2000 + yy
    else:
        current_yy = reference_date.year % 100
        year = 1900 + yy if yy > current_yy else 2000 + yy
        
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def parse_human_date_to_date(date_str: str) -> Optional[date]:
    """Convierte fechas en formatos legibles (DD/MM/AAAA, DD-MM-AAAA, AAAA-MM-DD)."""
    if not date_str:
        return None
    cleaned = re.sub(r"[\s\.\-]+", "/", date_str.strip())
    parts = cleaned.split("/")
    if len(parts) == 3:
        try:
            if len(parts[0]) == 4:
                return date(int(parts[0]), int(parts[1]), int(parts[2]))
            elif len(parts[2]) == 4:
                return date(int(parts[2]), int(parts[1]), int(parts[0]))
            elif len(parts[2]) == 2:
                return date(2000 + int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            return None
    return None


def evaluate_document_validity(
    expiry: date,
    reference_date: Optional[date] = None,
) -> Tuple[int, bool, str]:
    """Evalúa la vigencia temporal del documento.
    
    Devuelve:
    - days_remaining: int (negativo si caducó, positivo si en vigor)
    - is_expired: bool (True si days_remaining <= 0)
    - validity_label: str descriptivo
    """
    if reference_date is None:
        reference_date = date.today()
        
    days_remaining = (expiry - reference_date).days
    is_expired = days_remaining < 0
    
    if days_remaining < 0:
        validity_label = f"CADUCADO (vencido hace {abs(days_remaining)} días)"
    elif days_remaining == 0:
        is_expired = True
        validity_label = "CADUCA HOY (NO VÁLIDO)"
    elif days_remaining <= 30:
        validity_label = f"EN VIGOR - PRÓXIMO A VENCER ({days_remaining} días restantes)"
    else:
        validity_label = f"EN VIGOR ({days_remaining} días restantes)"
        
    return days_remaining, is_expired, validity_label


def calculate_age(birth: date, reference_date: Optional[date] = None) -> int:
    """Calcula la edad en años a partir de la fecha de nacimiento."""
    if reference_date is None:
        reference_date = date.today()
    return reference_date.year - birth.year - ((reference_date.month, reference_date.day) < (birth.month, birth.day))

