"""Motor de procesamiento de texto OCR, limpieza de caracteres OCR-B y verificación cruzada anverso/reverso.

Diseñado para auditar discrepancias entre los campos visibles del anverso
y la información codificada en la zona de lectura mecánica (MRZ) del reverso.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
import re
import unicodedata
from typing import Dict, List, Optional, Tuple, Union

from src.core.algorithms import (
    MRZTD1Result,
    MRZTD3Result,
    NIE_PATTERN,
    ValidationResult,
    validate_mrz_td1,
    validate_mrz_td3,
    sanitize_ocr_b_numeric,
    parse_icao_date_to_date,
    parse_human_date_to_date,
    evaluate_document_validity,
    calculate_age,
)


def normalize_name_tokens(text: Optional[str]) -> List[str]:
    """Normaliza un nombre a tokens A-Z como en la MRZ (transliteración ICAO: Á→A, Ñ→N, Ç→C).

    Descompone en NFKD y elimina los diacríticos antes de filtrar, para que
    'GARCÍA MUÑOZ' del anverso equivalga a 'GARCIA<MUNOZ' de la MRZ.
    """
    if not text:
        return []
    decomposed = unicodedata.normalize("NFKD", text.upper())
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^A-Z]+", " ", ascii_only).split()


@dataclass
class DocumentFrontData:
    """Datos declarados o leídos del anverso del documento."""
    document_number: Optional[str] = None  # ej: "12345678Z" o "X1234567L"
    support_number: Optional[str] = None   # ej: "AAA000000"
    surname: Optional[str] = None          # ej: "GARCIA LOPEZ"
    given_names: Optional[str] = None      # ej: "CARMEN"
    birth_date: Optional[str] = None       # Formato DD/MM/AAAA o DD-MM-AAAA
    expiry_date: Optional[str] = None      # Formato DD/MM/AAAA o DD-MM-AAAA


@dataclass
class CrossVerificationResult:
    """Informe de inconsistencias detectadas al cruzar anverso con reverso."""
    has_discrepancies: bool
    passed_checks_count: int
    failed_checks_count: int
    findings: List[ValidationResult] = field(default_factory=list)
    summary: str = ""


# Palabras que forman las etiquetas impresas del anverso (DNI 3.0/4.0 y TIE, bilingües ES/EN).
# Una línea compuesta solo por estas palabras es una etiqueta, nunca un valor.
FRONT_LABEL_WORDS = {
    "PRIMER", "SEGUNDO", "APELLIDO", "APELLIDOS", "SURNAME", "SURNAMES", "NOMBRE", "NOMBRES", "NAME", "NAMES",
    "GIVEN", "SEXO", "SEX", "NACIONALIDAD", "NATIONALITY", "FECHA", "DE", "DEL", "NACIMIENTO", "DATE", "OF",
    "BIRTH", "VALIDO", "VALIDEZ", "HASTA", "VALID", "UNTIL", "EXPIRY", "EMISION", "EXPEDICION", "ISSUE", "NUM",
    "SOPORT", "SOPORTE", "IDESP", "DNI", "NIE", "CAN", "DOCUMENTO", "NACIONAL", "IDENTIDAD", "REINO", "ESPANA",
    "PERMISO", "RESIDENCIA", "TIPO", "TYPE", "TARJETA", "DOMICILIO", "LUGAR", "OBSERVACIONES", "ESP",
}
# Etiquetas que introducen apellidos o nombre: lo que sigue a la etiqueta en la misma línea, o las
# líneas siguientes hasta la próxima etiqueta, son el valor
SURNAME_LABELS = ("APELLIDO", "APELLIDOS", "SURNAME", "SURNAMES")
GIVEN_NAME_LABELS = ("NOMBRE", "NOMBRES", "NAME", "NAMES")


def _is_label_line(tokens: List[str]) -> bool:
    return bool(tokens) and all(t in FRONT_LABEL_WORDS for t in tokens)


def _is_name_value(tokens: List[str]) -> bool:
    """Valor plausible de nombre: solo letras, al menos una palabra de 2+ letras y ninguna etiqueta."""
    return bool(tokens) and any(len(t) >= 2 for t in tokens) and not any(t in FRONT_LABEL_WORDS for t in tokens)


def extract_labeled_names(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extrae apellidos y nombre del anverso a partir de sus etiquetas impresas.

    Soporta 'PRIMER APELLIDO' + 'SEGUNDO APELLIDO' (DNI 3.0) y 'APELLIDOS / SURNAMES' (DNI 4.0, TIE),
    con el valor en la misma línea que la etiqueta o en las líneas siguientes. Cualquier línea con
    dígitos o con palabras de etiqueta corta el valor, para no arrastrar fechas ni otros campos.
    """
    surnames: List[str] = []
    given: List[str] = []
    target: Optional[List[str]] = None
    budget = 0  # Nº máximo de líneas de valor tras una etiqueta

    for raw in lines:
        if any(c.isdigit() for c in raw):
            target = None
            continue
        tokens = normalize_name_tokens(raw)
        if not tokens:
            continue

        if any(t in SURNAME_LABELS for t in tokens):
            target, budget = surnames, 2
        elif any(t in GIVEN_NAME_LABELS for t in tokens) and "APELLIDO" not in tokens:
            target, budget = given, 1
        elif not _is_label_line(tokens) and target is not None and budget > 0 and _is_name_value(tokens):
            target.append(" ".join(tokens))
            budget -= 1
            continue
        else:
            # Etiqueta de otro campo (sexo, nacionalidad...) o valor ajeno: deja de recoger
            if _is_label_line(tokens):
                target = None
            continue

        # La línea es una etiqueta de nombre: el valor puede venir pegado a la etiqueta
        inline = [t for t in tokens if t not in FRONT_LABEL_WORDS]
        if inline and _is_name_value(inline):
            target.append(" ".join(inline))
            budget -= 1

    return (" ".join(surnames) or None), (" ".join(given) or None)


class MRZTextCleaner:
    """Corrector heurístico de errores comunes de OCR sobre tipografía OCR-B."""

    @staticmethod
    def clean_mrz_line(raw_line: str) -> str:
        """Limpia caracteres de ruido en una línea MRZ."""
        line = raw_line.strip().upper()
        # Normalizar comillas angulares o artefactos a '<'
        line = line.replace("«", "<").replace("»", "<").replace(" ", "<")
        # Eliminar cualquier caracter ajeno a A-Z, 0-9 y <
        line = re.sub(r"[^A-Z0-9<]", "", line)
        return line

    @staticmethod
    def sanitize_mrz_lines(raw_lines: List[str]) -> List[str]:
        """Ajusta un bloque de líneas crudas para el parser ICAO TD1 (30 caracteres)."""
        sanitized = []
        for l in raw_lines:
            cleaned = MRZTextCleaner.clean_mrz_line(l)
            if cleaned:
                # Asegurar longitud de 30 caracteres
                if len(cleaned) < 30:
                    cleaned = cleaned.ljust(30, "<")
                elif len(cleaned) > 30:
                    cleaned = cleaned[:30]
                sanitized.append(cleaned)
        return sanitized

    @staticmethod
    def sanitize_mrz_lines_td3(raw_lines: List[str]) -> List[str]:
        """Ajusta un bloque de líneas crudas para el parser ICAO TD3 (44 caracteres - Pasaportes)."""
        sanitized = []
        for l in raw_lines:
            cleaned = MRZTextCleaner.clean_mrz_line(l)
            if cleaned:
                if len(cleaned) < 44:
                    cleaned = cleaned.ljust(44, "<")
                elif len(cleaned) > 44:
                    cleaned = cleaned[:44]
                sanitized.append(cleaned)
        return sanitized


def parse_date_to_yymmdd(date_str: str) -> Optional[str]:
    """Convierte una fecha en formato DD/MM/YYYY o DD-MM-YYYY a YYMMDD (formato ICAO)."""
    cleaned = re.sub(r"[\s\.\-]+", "/", date_str.strip())
    parts = cleaned.split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        day, month, year = parts[0], parts[1], parts[2]
        if len(year) == 4:
            year_short = year[2:4]
            return f"{year_short}{int(month):02d}{int(day):02d}"
        elif len(year) == 2:
            return f"{year}{int(month):02d}{int(day):02d}"
    return None


def cross_verify_front_with_mrz(
    front: DocumentFrontData,
    mrz_result: Union[MRZTD1Result, MRZTD3Result]
) -> CrossVerificationResult:
    """Cruce determinista entre la información del anverso y la decodificada en la MRZ.
    
    Cualquier discrepancia entre el texto visible y la zona criptográfica/MRZ es
    un indicio crítico de alteración digital o montaje físico.
    """
    findings: List[ValidationResult] = []

    # 1. Comprobación del Número de Documento (DNI/NIE o número de pasaporte)
    # Comparación por igualdad exacta: en DNI/TIE el DNI/NIE está en los datos opcionales
    # (personal_number) y el campo "número de documento" es el soporte; en pasaportes es el número.
    clean_front_doc = re.sub(r"[\s\-<]", "", (front.document_number or "").upper())
    if clean_front_doc:
        mrz_candidates = [c for c in (mrz_result.personal_number, mrz_result.document_number.upper()) if c]
        match_doc = clean_front_doc in mrz_candidates
        shown_mrz = mrz_result.personal_number or mrz_result.document_number or "(vacío)"

        findings.append(ValidationResult(
            check_name="CROSS_CHECK_DOC_NUMBER",
            passed=match_doc,
            expected=clean_front_doc,
            actual=shown_mrz,
            details="Número de documento coincide entre frontal y MRZ." if match_doc else "DISCREPANCIA CRÍTICA: El número frontal no coincide con el codificado en la MRZ."
        ))

    # 1b. Número de soporte (IDESP) del anverso frente al campo de documento de la MRZ española
    clean_front_support = re.sub(r"[\s\-<]", "", (front.support_number or "").upper())
    if clean_front_support and mrz_result.support_number:
        match_support = clean_front_support == mrz_result.support_number
        findings.append(ValidationResult(
            check_name="CROSS_CHECK_SUPPORT_NUMBER",
            passed=match_support,
            expected=clean_front_support,
            actual=mrz_result.support_number,
            details="Número de soporte coincide entre frontal y MRZ." if match_support else "DISCREPANCIA: El número de soporte frontal no coincide con el de la MRZ."
        ))

    # 2. Comprobación de Fecha de Caducidad
    if front.expiry_date:
        expected_yymmdd = parse_date_to_yymmdd(front.expiry_date)
        if expected_yymmdd:
            match_expiry = (expected_yymmdd == mrz_result.expiry_date)
            findings.append(ValidationResult(
                check_name="CROSS_CHECK_EXPIRY_DATE",
                passed=match_expiry,
                expected=expected_yymmdd,
                actual=mrz_result.expiry_date,
                details="Fecha de caducidad coincide exactamente." if match_expiry else f"DISCREPANCIA: Caducidad frontal ({expected_yymmdd}) difiere de MRZ ({mrz_result.expiry_date})."
            ))

    # 3. Comprobación de Fecha de Nacimiento
    if front.birth_date:
        expected_birth_yymmdd = parse_date_to_yymmdd(front.birth_date)
        if expected_birth_yymmdd:
            match_birth = (expected_birth_yymmdd == mrz_result.birth_date)
            findings.append(ValidationResult(
                check_name="CROSS_CHECK_BIRTH_DATE",
                passed=match_birth,
                expected=expected_birth_yymmdd,
                actual=mrz_result.birth_date,
                details="Fecha de nacimiento coincide exactamente." if match_birth else f"DISCREPANCIA: Nacimiento frontal ({expected_birth_yymmdd}) difiere de MRZ ({mrz_result.birth_date})."
            ))

    # 4. Comprobación de Apellidos (normalizados a la transliteración ICAO, comparados por palabras)
    front_tokens = normalize_name_tokens(front.surname)
    mrz_tokens = normalize_name_tokens(mrz_result.surname)
    if front_tokens:
        front_joined = " ".join(front_tokens)
        mrz_joined = " ".join(mrz_tokens)
        # La línea de nombres de la MRZ se trunca si no cabe: termina sin relleno '<'
        name_line = mrz_result.raw_lines[-1] if isinstance(mrz_result, MRZTD1Result) and mrz_result.raw_lines else ""
        mrz_truncated = bool(name_line) and not name_line.endswith("<")

        if mrz_tokens and front_tokens == mrz_tokens:
            match_surname, detail = True, "Apellidos coincidentes."
        elif mrz_tokens and front_tokens == mrz_tokens[:len(front_tokens)]:
            match_surname, detail = True, (
                f"Coincidencia parcial: el anverso aporta '{front_joined}', la MRZ contiene '{mrz_joined}' "
                "(solo se comparó el primer apellido)."
            )
        elif mrz_tokens and front_tokens[:len(mrz_tokens)] == mrz_tokens:
            # El OCR del anverso arrastró texto tras los apellidos completos (p. ej. el nombre)
            match_surname, detail = True, (
                f"Apellidos coincidentes: el anverso empieza por los apellidos de la MRZ ('{mrz_joined}') y añade texto."
            )
        elif mrz_tokens and mrz_truncated and front_joined.replace(" ", "").startswith(mrz_joined.replace(" ", "")):
            match_surname, detail = True, "Apellidos coincidentes (MRZ truncada por longitud)."
        else:
            match_surname, detail = False, (
                f"DISCREPANCIA: Apellidos frontales ('{front_joined}') no coinciden con MRZ ('{mrz_joined or '(vacío)'}')."
            )

        findings.append(ValidationResult(
            check_name="CROSS_CHECK_SURNAME",
            passed=match_surname,
            expected=front_joined,
            actual=mrz_joined,
            details=detail,
        ))

    failed = [f for f in findings if not f.passed]
    passed = [f for f in findings if f.passed]

    has_discrepancies = len(failed) > 0
    if not has_discrepancies and len(findings) > 0:
        summary = "Todos los campos cruzados coinciden perfectamente entre anverso y reverso."
    elif has_discrepancies:
        summary = f"Se han detectado {len(failed)} discrepancia(s) entre el anverso y la zona MRZ."
    else:
        summary = "No se proporcionaron campos suficientes para cruce anverso/reverso."

    return CrossVerificationResult(
        has_discrepancies=has_discrepancies,
        passed_checks_count=len(passed),
        failed_checks_count=len(failed),
        findings=findings,
        summary=summary,
    )


@dataclass
class AutoDetectionResult:
    """Resultado del escaneo y extracción automática OCR sin intervención manual."""
    is_mrz_detected: bool = False
    is_front_detected: bool = False
    mrz_lines: List[str] = field(default_factory=list)
    mrz_result: Optional[Union[MRZTD1Result, MRZTD3Result]] = None
    detected_doc_number: Optional[str] = None
    detected_doc_type: Optional[str] = None
    detected_expiry_date: Optional[str] = None
    detected_birth_date: Optional[str] = None
    detected_surname: Optional[str] = None
    detected_given_names: Optional[str] = None
    detected_support_number: Optional[str] = None
    detected_nationality: Optional[str] = None
    raw_ocr_lines: List[str] = field(default_factory=list)
    # Toda corrección heurística aplicada al texto OCR queda registrada para el informe
    ocr_corrections: List[str] = field(default_factory=list)


class DocumentAutoDetector:
    """Motor de lectura e inferencia automática sobre imágenes de documentos."""

    _reader = None

    @classmethod
    def get_reader(cls):
        """Inicializa EasyOCR de forma perezosa y lo mantiene en memoria (Singleton).
        
        Soporta modo offline estricto mediante la variable de entorno HERMETIC_OFFLINE_MODE.
        """
        if cls._reader is None:
            try:
                import os
                from pathlib import Path
                import easyocr

                offline_mode = os.getenv("HERMETIC_OFFLINE_MODE", "false").lower() == "true"
                custom_model_dir = os.getenv("HERMETIC_MODELS_DIR")
                model_storage_dir = Path(custom_model_dir) if custom_model_dir else Path.home() / ".EasyOCR" / "model"

                # Si está en modo offline, verificar presencia de modelos requeridos para evitar excepciones silenciosas
                if offline_mode:
                    craft_exists = (model_storage_dir / "craft_mlt_25k.pth").exists()
                    latin_exists = (model_storage_dir / "latin_g2.pth").exists() or (model_storage_dir / "latin.pth").exists()
                    if not (craft_exists and latin_exists):
                        import logging
                        logging.getLogger(__name__).warning(
                            f"[Hermetic-ID] HERMETIC_OFFLINE_MODE=true pero faltan modelos en {model_storage_dir}."
                        )

                cls._reader = easyocr.Reader(
                    ["es", "en"],
                    gpu=False,
                    verbose=False,
                    download_enabled=not offline_mode,
                    model_storage_directory=str(model_storage_dir) if custom_model_dir else None
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"[Hermetic-ID] OCR local no disponible: {e}")
                cls._reader = None
        return cls._reader

    @classmethod
    def analyze_image_auto(cls, image_np_or_pil) -> AutoDetectionResult:
        """Lee la imagen con OCR local y extrae automáticamente los datos documentales."""
        import numpy as np
        from PIL import Image

        result = AutoDetectionResult()

        reader = cls.get_reader()
        if reader is None:
            return result

        # Normalizar imagen a array NumPy
        if isinstance(image_np_or_pil, Image.Image):
            img_np = np.array(image_np_or_pil.convert("RGB"))
        elif isinstance(image_np_or_pil, np.ndarray):
            img_np = image_np_or_pil
        else:
            return result

        try:
            detected_texts = reader.readtext(img_np, detail=0)
            result.raw_ocr_lines = [str(t).strip() for t in detected_texts if str(t).strip()]
        except Exception:
            return result

        # 1. Intentar localizar la tríada exacta de 3 líneas MRZ TD1
        # Filtrar líneas administrativas visibles que no forman parte de la MRZ
        admin_keywords = [
            "DATE OF ISSUE", "PLACE OF", "FECHA DE", "LUGAR DE",
            "DOMICILIO", "ADDRESS", "EXPEDICION", "RESIDENCIA",
            "FAMILIAR", "COMUNITARIO", "OBSERVACIONES"
        ]

        mrz_candidates = []
        for l in result.raw_ocr_lines:
            l_up = l.upper()
            if any(k in l_up for k in admin_keywords):
                continue
            cleaned = MRZTextCleaner.clean_mrz_line(l)
            if len(cleaned) >= 15:
                mrz_candidates.append(cleaned)

        # Prioridad 0: Buscar par de líneas MRZ TD3 (Pasaportes - ICAO Doc 9303 Parte 4)
        selected_2_td3 = None
        for i in range(len(mrz_candidates) - 1):
            cand1 = mrz_candidates[i]
            cand2 = mrz_candidates[i + 1]
            if cand1.startswith("P") and "<<" in cand1 and len(cand1) >= 30 and len(cand2) >= 30:
                selected_2_td3 = [cand1, cand2]
                break

        if selected_2_td3:
            for idx, line in enumerate(selected_2_td3, 1):
                if len(line) != 44:
                    result.ocr_corrections.append(f"Línea {idx}: longitud OCR {len(line)} ajustada a 44 (relleno o recorte).")
            sanitized_2 = MRZTextCleaner.sanitize_mrz_lines_td3(selected_2_td3)
            mrz_res_td3 = validate_mrz_td3(sanitized_2)
            result.is_mrz_detected = True
            result.mrz_lines = sanitized_2
            result.mrz_result = mrz_res_td3
            result.detected_doc_number = mrz_res_td3.document_number
            result.detected_doc_type = "Pasaporte (ICAO TD3)"
            result.detected_surname = mrz_res_td3.surname
            result.detected_given_names = mrz_res_td3.given_names
            result.detected_nationality = mrz_res_td3.nationality
            result.detected_expiry_date = mrz_res_td3.expiry_date
            result.detected_birth_date = mrz_res_td3.birth_date
            return result

        selected_3 = None

        # Prioridad 1: Buscar tríada contigua [L1, L2, L3] al pie del documento (TD1)
        for i in range(len(mrz_candidates) - 2):
            l1 = mrz_candidates[i]
            l2 = mrz_candidates[i + 1]
            l3 = mrz_candidates[i + 2]

            is_l1 = bool(re.match(r"^[I1][A-Z0-9<]{0,2}[A-Z]{3}", l1) and len(l1) >= 20)
            is_l2 = bool(re.match(r"^\d{6}", l2) and len(l2) >= 20)
            is_l3 = bool(not re.match(r"^[I10-9]", l3) and len(l3) >= 15)

            if is_l1 and is_l2 and is_l3:
                selected_3 = [l1, l2, l3]
                break

        # Prioridad 2: Si no hubo contigüidad estricta, buscar L1 y L2 por patrón y tomar la siguiente línea L3
        if not selected_3:
            for i, c in enumerate(mrz_candidates):
                if re.match(r"^[I1][A-Z0-9<]{0,2}[A-Z]{3}", c) and len(c) >= 20:
                    # Buscar L2 en las 2 líneas posteriores
                    for j in range(i + 1, min(i + 3, len(mrz_candidates))):
                        if re.match(r"^\d{6}", mrz_candidates[j]) and len(mrz_candidates[j]) >= 20:
                            if j + 1 < len(mrz_candidates):
                                selected_3 = [c, mrz_candidates[j], mrz_candidates[j + 1]]
                                break
                    if selected_3:
                        break

        # Si encontramos la tríada oficial
        if selected_3:
            corrections = result.ocr_corrections
            selected_3 = [s.upper() for s in selected_3]

            # Normalizar confusiones de OCR-B en posiciones estructurales (todas quedan registradas):
            # Línea 1: el código de documento empieza por 'I', nunca por '1'
            if selected_3[0].startswith("1"):
                selected_3[0] = "I" + selected_3[0][1:]
                corrections.append("Línea 1, pos. 0: '1' → 'I' (código de documento TD1).")

            # Líneas 1 y 3: el relleno final '<' se lee a veces como '6'
            for idx in (0, 2):
                fixed = re.sub(r"6+$", lambda m: "<" * len(m.group(0)), selected_3[idx])
                if fixed != selected_3[idx]:
                    corrections.append(f"Línea {idx + 1}: relleno final '6' → '<'.")
                    selected_3[idx] = fixed

            # Línea 2, pos. 7 (sexo): no tiene dígito de control, así que no se adivina.
            # Un carácter ilegible se marca como no especificado ('<').
            if len(selected_3[1]) >= 8 and selected_3[1][7] not in ("M", "F", "<"):
                corrections.append(f"Línea 2, pos. 7: sexo ilegible '{selected_3[1][7]}' → '<' (no verificable).")
                selected_3[1] = selected_3[1][:7] + "<" + selected_3[1][8:]

            for idx, line in enumerate(selected_3, 1):
                if len(line) != 30:
                    corrections.append(f"Línea {idx}: longitud OCR {len(line)} ajustada a 30 (relleno o recorte).")

            sanitized_3 = MRZTextCleaner.sanitize_mrz_lines(selected_3)
            mrz_res = validate_mrz_td1(sanitized_3)

            # Línea 1, pos. 15 (TIE): el OCR confunde el prefijo NIE (X/Y/Z) con 0/1/2/7.
            # Solo se corrige si la MRZ leída NO valida y la corrección hace que valide entera
            # (letra módulo 23 + dígito compuesto), así nunca se altera un DNI correcto.
            if not mrz_res.is_valid and len(sanitized_3[0]) == 30 and re.fullmatch(r"[0127]\d{7}[A-Z]", sanitized_3[0][15:24]):
                for prefix in ("X", "Y", "Z"):
                    candidate_l1 = sanitized_3[0][:15] + prefix + sanitized_3[0][16:]
                    candidate_res = validate_mrz_td1([candidate_l1, sanitized_3[1], sanitized_3[2]])
                    if candidate_res.is_valid:
                        corrections.append(
                            f"Línea 1, pos. 15: '{sanitized_3[0][15]}' → '{prefix}' (prefijo NIE; la MRZ corregida supera módulo 23 y dígito compuesto)."
                        )
                        sanitized_3[0] = candidate_l1
                        mrz_res = candidate_res
                        break

            result.is_mrz_detected = True
            result.mrz_lines = sanitized_3
            result.mrz_result = mrz_res
            personal_number = mrz_res.personal_number
            result.detected_doc_number = personal_number or mrz_res.document_number
            result.detected_support_number = mrz_res.support_number
            if personal_number and NIE_PATTERN.fullmatch(personal_number):
                result.detected_doc_type = "NIE / TIE (Extranjeros)"
            elif personal_number:
                result.detected_doc_type = "DNI 4.0 / 3.0"
            else:
                result.detected_doc_type = "Documento ICAO TD1"
            result.detected_surname = mrz_res.surname
            result.detected_given_names = mrz_res.given_names
            result.detected_nationality = mrz_res.nationality
            result.detected_expiry_date = mrz_res.expiry_date
            result.detected_birth_date = mrz_res.birth_date
            return result

        # 2. Si no es MRZ (es un anverso), buscar patrones de DNI / NIE en el texto
        full_text = " ".join(result.raw_ocr_lines).upper()
        result.is_front_detected = True

        # Buscar NIE: X, Y o Z seguido de 7 dígitos y una letra
        nie_match = re.search(r"\b([XYZ]\d{7}[A-Z])\b", full_text)
        if nie_match:
            result.detected_doc_number = nie_match.group(1)
            result.detected_doc_type = "NIE / TIE (Extranjeros)"

        # Si no hay NIE, buscar DNI: 8 dígitos y una letra
        if not result.detected_doc_number:
            dni_match = re.search(r"\b(\d{8}[A-Z])\b", full_text)
            if dni_match:
                result.detected_doc_number = dni_match.group(1)
                result.detected_doc_type = "DNI 4.0 / 3.0"

        # Buscar soporte (ej. AAA000000 o similar)
        support_match = re.search(r"\b([A-Z]{3}\d{6})\b", full_text)
        if support_match:
            result.detected_support_number = support_match.group(1)

        # Buscar titular o nombres
        for line in result.raw_ocr_lines:
            line_up = line.upper()
            if "TITULAR:" in line_up:
                result.detected_surname = line.split(":", 1)[1].strip()
            elif "NOMBRE:" in line_up:
                result.detected_given_names = line.split(":", 1)[1].strip()

        # Documentos reales: etiqueta en una línea y valor debajo (DNI 3.0/4.0, TIE)
        if not result.detected_surname or not result.detected_given_names:
            surname, given_names = extract_labeled_names(result.raw_ocr_lines)
            result.detected_surname = result.detected_surname or surname
            result.detected_given_names = result.detected_given_names or given_names

        # Buscar fechas típicas (DD/MM/AAAA o DD MM AAAA). El orden de lectura del OCR no es fiable,
        # así que se asignan por valor: nacimiento < expedición < caducidad.
        dates_found = re.findall(r"\b(\d{2}[\s/\.\-]\d{2}[\s/\.\-]\d{4})\b", full_text)
        parsed_dates = sorted(
            ((parsed, raw) for raw in dates_found if (parsed := parse_human_date_to_date(raw))),
            key=lambda pair: pair[0],
        )
        if len(parsed_dates) >= 2:
            result.detected_birth_date = parsed_dates[0][1]
            result.detected_expiry_date = parsed_dates[-1][1]
        elif len(parsed_dates) == 1 and parsed_dates[0][0] > date.today():
            # Una sola fecha futura solo puede ser la caducidad; una pasada es ambigua y no se asigna
            result.detected_expiry_date = parsed_dates[0][1]

        return result


def create_manager_summary(
    doc_number: Optional[str] = None,
    doc_type: Optional[str] = None,
    full_name: Optional[str] = None,
    surname: Optional[str] = None,
    given_names: Optional[str] = None,
    birth_date_str: Optional[str] = None,
    expiry_date_str: Optional[str] = None,
    nationality: Optional[str] = None,
    support_number: Optional[str] = None,
    reference_date: Optional[date] = None,
) -> Optional[object]:
    """Construye un resumen ejecutivo estructurado para el gestor / analista."""
    from src.reporting.report_generator import ManagerSummary

    if not (doc_number or surname or expiry_date_str or birth_date_str):
        return None

    if reference_date is None:
        reference_date = date.today()

    # Descartar falsos positivos de encabezados administrativos
    admin_terms = ["DATE OF ISSUE", "PLACE OF", "FECHA", "LUGAR", "EXPEDICION", "DOMICILIO"]
    if surname and any(t in surname.upper() for t in admin_terms):
        surname = None
    if given_names and any(t in given_names.upper() for t in admin_terms):
        given_names = None
    if full_name and any(t in full_name.upper() for t in admin_terms):
        full_name = None

    # 1. Determinar nombre completo
    if not full_name:
        parts = [p for p in [surname, given_names] if p]
        full_name = " ".join(parts).strip() if parts else "No especificado"

    # 2. Determinar tipo de documento
    clean_doc = (doc_number or "").strip().upper()
    if not doc_type:
        if clean_doc.startswith(("X", "Y", "Z")):
            doc_type = "NIE / TIE (Extranjeros)"
        elif any(c.isdigit() for c in clean_doc):
            doc_type = "DNI 4.0 / 3.0"
        else:
            doc_type = "Documento de Identidad"

    # 3. Procesar caducidad y vigencia
    expiry_formatted = "No indicada"
    days_rem = None
    is_expired = False
    val_label = "SIN FECHA"

    if expiry_date_str:
        clean_exp = expiry_date_str.strip()
        parsed_exp = None
        if len(clean_exp) == 6 and clean_exp.isdigit():
            parsed_exp = parse_icao_date_to_date(clean_exp, is_expiry=True, reference_date=reference_date)
        else:
            parsed_exp = parse_human_date_to_date(clean_exp)

        if parsed_exp:
            expiry_formatted = parsed_exp.strftime("%d/%m/%Y")
            days_rem, is_expired, val_label = evaluate_document_validity(parsed_exp, reference_date)
        else:
            expiry_formatted = clean_exp

    # 4. Procesar fecha de nacimiento y edad
    birth_formatted = "No indicada"
    age_years = None
    if birth_date_str:
        clean_birth = birth_date_str.strip()
        parsed_birth = None
        if len(clean_birth) == 6 and clean_birth.isdigit():
            parsed_birth = parse_icao_date_to_date(clean_birth, is_expiry=False, reference_date=reference_date)
        else:
            parsed_birth = parse_human_date_to_date(clean_birth)

        if parsed_birth:
            birth_formatted = parsed_birth.strftime("%d/%m/%Y")
            age_years = calculate_age(parsed_birth, reference_date)
        else:
            birth_formatted = clean_birth

    return ManagerSummary(
        full_name=full_name,
        doc_number=clean_doc or "No detectado",
        doc_type=doc_type,
        nationality=(nationality or "ESP").strip().upper(),
        birth_date=birth_formatted,
        age_years=age_years,
        expiry_date=expiry_formatted,
        days_until_expiry=days_rem,
        is_expired=is_expired,
        validity_status_label=val_label,
        support_number=support_number,
    )


