"""Pruebas unitarias para el motor de análisis forense digital."""

import io
import numpy as np
import pytest
from PIL import Image

from src.core.forensics import (
    compute_ela,
    inspect_image_exif,
    analyze_photo_boundary_sharpness,
)
from tests.fixtures.synthetic_generator import create_specimen_card_image


class TestForensicEngine:
    """Verificación de herramientas de análisis forense digital en memoria."""

    def test_compute_ela_clean_image(self):
        # Crear imagen sintética no alterada y guardarla en memoria JPEG calidad 90
        clean_img = create_specimen_card_image(tampered=False)
        buf = io.BytesIO()
        clean_img.save(buf, format="JPEG", quality=90)
        img_bytes = buf.getvalue()

        result = compute_ela(img_bytes, quality=90)
        assert result.mean_error >= 0.0
        assert result.ela_image_bytes is not None
        assert len(result.ela_image_bytes) > 0
        # Una imagen guardada a calidad 90 y reanalizada a calidad 90 no debe disparar anomalía
        assert result.anomaly_detected is False

    def test_compute_ela_tampered_image_difference(self):
        # Imagen con parche pegado de compresión extremadamente baja
        clean_img = create_specimen_card_image(tampered=False)
        tampered_img = create_specimen_card_image(tampered=True)

        res_clean = compute_ela(clean_img, quality=90)
        res_tampered = compute_ela(tampered_img, quality=90)

        # La imagen manipulada debe reflejar mayor dispersión o error máximo
        assert res_tampered.max_error >= res_clean.max_error

    def test_inspect_image_exif_clean(self):
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        
        result = inspect_image_exif(buf.getvalue())
        assert result.is_suspicious_software is False

    def test_analyze_photo_boundary_natural_vs_artificial(self):
        import cv2

        # 1. Caso Natural: Imagen con textura y región de fotografía con transición suave (difuminada)
        canvas_natural = np.random.randint(190, 210, (400, 600, 3), dtype=np.uint8)
        cv2.circle(canvas_natural, (150, 150), 60, (140, 140, 140), -1)
        canvas_natural = cv2.GaussianBlur(canvas_natural, (7, 7), 2)
        res_natural = analyze_photo_boundary_sharpness(canvas_natural, (50, 50, 200, 200))
        assert res_natural.is_suspicious_edge is False

        # 2. Caso Artificial: Pegado con salto abrupto de 0 a 250 sin suavizado
        canvas_sharp = np.ones((400, 600, 3), dtype=np.uint8) * 10
        canvas_sharp[50:250, 50:250] = 250  # Contraste brutal no difuminado
        res_sharp = analyze_photo_boundary_sharpness(canvas_sharp, (50, 50, 200, 200))
        assert res_sharp.is_suspicious_edge is True
