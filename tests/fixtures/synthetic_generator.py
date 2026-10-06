"""Generador de muestras sintéticas de prueba y fixtures para Hermetic-ID.

IMPORTANTE / COMPLIANCE:
Este módulo genera exclusivamente datos abstractos con marca prominente 'SPECIMEN / PRUEBA'.
NO utiliza datos reales de personas físicas ni replica fondos de seguridad oficiales,
cumpliendo estrictamente con el RGPD y evitando riesgos legales de falsificación.
"""

from typing import Dict, List, Tuple
import io
import random
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.core.algorithms import (
    calculate_dni_letter,
    calculate_icao_check_digit,
)


def generate_synthetic_dni(number: int = 12345678) -> Tuple[str, str]:
    """Genera un DNI sintético válido (número + letra calculada)."""
    num_str = f"{number:08d}"
    letter = calculate_dni_letter(number)
    return num_str, letter


def generate_synthetic_nie(prefix: str = "Y", number: int = 1234567) -> Tuple[str, str]:
    """Genera un NIE sintético válido con prefijo X, Y o Z."""
    prefix_map = {"X": "0", "Y": "1", "Z": "2"}
    num_str = f"{number:07d}"
    numeric_equiv = prefix_map[prefix] + num_str
    letter = calculate_dni_letter(int(numeric_equiv))
    return f"{prefix}{num_str}", letter


def generate_synthetic_mrz_td1(
    personal_number: str = "12345678Z",
    support_number: str = "BAA000001",
    birth_date: str = "850101",
    expiry_date: str = "300101",
    surname: str = "MUESTRA",
    given_names: str = "ESPECIMEN",
    nationality: str = "ESP",
    sex: str = "F",
) -> List[str]:
    """Genera 3 líneas MRZ TD1 matemáticamente perfectas según ICAO 9303.

    Reproduce la estructura real del DNI 3.0/4.0 y la TIE españoles:
    - Campo "número de documento" (pos. 5-13): número de SOPORTE (IDESP), p. ej. 'BAA000001'.
    - Datos opcionales 1 (pos. 15-29): número personal DNI/NIE, p. ej. '12345678Z'.
    """
    # Línea 1: ID + país(3) + soporte(9) + check(1) + opt1(15) = 30 chars
    support_padded = support_number.ljust(9, "<")[:9]
    doc_check = calculate_icao_check_digit(support_padded)
    opt1 = personal_number.ljust(15, "<")[:15]
    l1 = f"ID{nationality}{support_padded}{doc_check}{opt1}"

    # Línea 2: birth(6) + check(1) + sex(1) + expiry(6) + check(1) + nat(3) + opt2(11) + composite(1) = 30 chars
    birth_check = calculate_icao_check_digit(birth_date)
    expiry_check = calculate_icao_check_digit(expiry_date)
    opt2 = "".ljust(11, "<")
    
    # Composite: l1[5:30] + l2[0:7] + l2[8:15] + l2[18:29]
    comp_data = l1[5:30] + birth_date + birth_check + expiry_date + expiry_check + opt2
    comp_check = calculate_icao_check_digit(comp_data)
    
    l2 = f"{birth_date}{birth_check}{sex}{expiry_date}{expiry_check}{nationality}{opt2}{comp_check}"

    # Línea 3: APELLIDOS<<NOMBRES padded to 30 chars
    names_field = f"{surname.upper()}<<{given_names.upper()}".replace(" ", "<")
    l3 = names_field.ljust(30, "<")[:30]

    return [l1, l2, l3]


def create_specimen_card_image(
    doc_number: str = "12345678Z",
    name: str = "ESPECIMEN DE PRUEBA",
    tampered: bool = False,
) -> Image.Image:
    """Crea una imagen sintética abstracta con marca prominente SPECIMEN para pruebas.
    
    Si `tampered=True`, simula una manipulación digital pegando un parche con diferente compresión.
    """
    width, height = 800, 500
    # Fondo neutro claro con textura geométrica simple
    img = Image.new("RGB", (width, height), color=(240, 243, 246))
    draw = ImageDraw.Draw(img)

    # Marco exterior
    draw.rectangle([10, 10, width - 10, height - 10], outline=(180, 190, 205), width=3)

    # Marca de agua diagonal SPECIMEN
    draw.line([0, height, width, 0], fill=(225, 230, 238), width=8)
    draw.text((150, 220), "SPECIMEN - SOLO PRUEBAS", fill=(210, 215, 225))

    # Recuadro representativo de fotografía
    photo_box = [40, 60, 220, 300]
    draw.rectangle(photo_box, fill=(200, 210, 225), outline=(130, 145, 165), width=2)
    draw.text((70, 160), "FOTO MOCK", fill=(100, 115, 135))

    # Campos de texto ficticios
    draw.text((260, 70), "DOCUMENTO FICTICIO DE PRUEBA", fill=(60, 70, 85))
    draw.text((260, 120), f"NUMERO: {doc_number}", fill=(40, 45, 55))
    draw.text((260, 160), f"TITULAR: {name}", fill=(40, 45, 55))
    draw.text((260, 200), "VALIDEZ: 01/01/2030", fill=(40, 45, 55))
    draw.text((260, 240), "SOPORTE: BAA000001", fill=(40, 45, 55))

    # Banda MRZ abstracta inferior
    draw.rectangle([30, 370, width - 30, 470], fill=(228, 232, 238), outline=(170, 180, 195))
    for offset, mrz_line in zip((385, 415, 445), generate_synthetic_mrz_td1(personal_number=doc_number)):
        draw.text((45, offset), mrz_line, fill=(50, 50, 50))

    if tampered:
        # Simular empalme digital (splicing): crear un parche recomprimido a calidad muy baja y pegarlo
        patch = Image.new("RGB", (200, 50), color=(255, 255, 200))
        p_draw = ImageDraw.Draw(patch)
        p_draw.text((10, 15), "ALTERADO_99999", fill=(200, 0, 0))
        
        patch_buf = io.BytesIO()
        patch.save(patch_buf, format="JPEG", quality=20)
        patch_buf.seek(0)
        low_q_patch = Image.open(patch_buf)
        
        img.paste(low_q_patch, (260, 115))

    return img


def create_specimen_front_image(
    doc_number: str = "12345678Z",
    name: str = "ESPECIMEN DE PRUEBA",
    expiry_date: str = "01/01/2030",
    birth_date: str = "01/01/1985",
    support_number: str = "BAA000001",
    tampered: bool = False,
) -> Image.Image:
    """Crea una imagen sintética del ANVERSO (con fotografía y datos legibles)."""
    width, height = 800, 500
    img = Image.new("RGB", (width, height), color=(242, 245, 248))
    draw = ImageDraw.Draw(img)

    # Marco
    draw.rectangle([10, 10, width - 10, height - 10], outline=(175, 185, 200), width=3)
    draw.line([0, height, width, 0], fill=(230, 235, 242), width=8)
    draw.text((160, 230), "SPECIMEN - ANVERSO DE PRUEBA", fill=(210, 215, 225))

    # Foto
    draw.rectangle([40, 60, 220, 310], fill=(205, 215, 228), outline=(130, 145, 165), width=2)
    draw.text((70, 175), "FOTO MOCK", fill=(100, 115, 135))

    # Una foto real impresa en el soporte no tiene un corte de un píxel: se suaviza el contorno
    # para que la muestra válida no dispare el control heurístico de borde de fotografía.
    photo_band = (30, 50, 232, 322)
    img.paste(img.crop(photo_band).filter(ImageFilter.GaussianBlur(radius=3)), photo_band[:2])
    draw = ImageDraw.Draw(img)

    # Textos frontales
    draw.text((260, 60), "REINO DE ESPAÑA - DOCUMENTO NACIONAL DE IDENTIDAD", fill=(50, 60, 75))
    draw.text((260, 110), f"NUMERO: {doc_number}", fill=(35, 40, 50))
    draw.text((260, 150), f"TITULAR: {name}", fill=(35, 40, 50))
    draw.text((260, 190), f"FECHA NACIMIENTO: {birth_date}", fill=(35, 40, 50))
    draw.text((260, 230), f"VALIDEZ: {expiry_date}", fill=(35, 40, 50))
    draw.text((260, 270), f"SOPORTE: {support_number}", fill=(35, 40, 50))

    if tampered:
        patch = Image.new("RGB", (200, 45), color=(255, 255, 210))
        p_draw = ImageDraw.Draw(patch)
        p_draw.text((10, 12), "ALTERADO_88888", fill=(200, 0, 0))
        patch_buf = io.BytesIO()
        patch.save(patch_buf, format="JPEG", quality=20)
        patch_buf.seek(0)
        img.paste(Image.open(patch_buf), (260, 105))

    return img


def create_specimen_back_image(
    doc_number: str = "12345678Z",
    support_number: str = "BAA000001",
    birth_date: str = "850101",
    expiry_date: str = "300101",
    surname: str = "MUESTRA",
    given_names: str = "ESPECIMEN",
    nationality: str = "ESP",
    sex: str = "F",
) -> Image.Image:
    """Crea una imagen sintética del REVERSO (con banda MRZ TD1 ICAO 9303)."""
    width, height = 800, 500
    img = Image.new("RGB", (width, height), color=(240, 242, 245))
    draw = ImageDraw.Draw(img)

    draw.rectangle([10, 10, width - 10, height - 10], outline=(175, 185, 200), width=3)
    draw.line([0, height, width, 0], fill=(230, 235, 242), width=8)
    draw.text((160, 150), "SPECIMEN - REVERSO DE PRUEBA", fill=(210, 215, 225))

    draw.text((50, 40), "DATOS DE FILIACIÓN / DIRECCIÓN (MOCK)", fill=(80, 90, 105))
    draw.text((50, 75), f"EQUIPO: EXPEDICIÓN MADRID 01", fill=(100, 110, 125))

    mrz_lines = generate_synthetic_mrz_td1(
        personal_number=doc_number,
        support_number=support_number,
        birth_date=birth_date,
        expiry_date=expiry_date,
        surname=surname,
        given_names=given_names,
        nationality=nationality,
        sex=sex,
    )

    draw.rectangle([30, 320, width - 30, 470], fill=(225, 230, 238), outline=(160, 170, 185), width=2)
    draw.text((45, 340), mrz_lines[0], fill=(30, 30, 30))
    draw.text((45, 380), mrz_lines[1], fill=(30, 30, 30))
    draw.text((45, 420), mrz_lines[2], fill=(30, 30, 30))

    return img

