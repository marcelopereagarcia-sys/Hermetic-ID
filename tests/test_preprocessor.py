"""Pruebas unitarias para el módulo de control de calidad y preprocesamiento."""

import cv2
import numpy as np
import pytest
from PIL import Image

from src.core.preprocessor import assess_image_quality, preprocess_mrz_region
from tests.fixtures.synthetic_generator import create_specimen_card_image


class TestImageQualityAssessment:
    """Verificación de los filtros de calidad previa (borrosidad, reflejos, resolución)."""

    def test_sharp_specimen_image_is_acceptable(self):
        img = create_specimen_card_image(tampered=False)
        result = assess_image_quality(img)
        assert result.is_acceptable is True
        assert result.is_blurred is False
        assert result.has_excessive_glare is False
        assert result.has_sufficient_resolution is True

    def test_blurred_image_is_detected(self):
        img = create_specimen_card_image(tampered=False)
        img_np = np.array(img)
        # Aplicar desenfoque severo simulando foto movida
        blurred_np = cv2.GaussianBlur(img_np, (51, 51), 15)
        
        result = assess_image_quality(blurred_np, blur_threshold=80.0)
        assert result.is_blurred is True
        assert result.is_acceptable is False
        assert "desenfocada" in result.summary.lower()

    def test_excessive_glare_is_detected(self):
        # Crear imagen con parche de destello de flash (>250 sobreexpuesto en más del 25% del área)
        canvas = np.ones((400, 600, 3), dtype=np.uint8) * 150
        canvas[50:350, 50:450] = 255  # Gran zona blanca quemada
        
        result = assess_image_quality(canvas, glare_max_percent=10.0)
        assert result.has_excessive_glare is True
        assert result.is_acceptable is False

    def test_low_resolution_image_is_rejected(self):
        # Miniatura de 200x150 px
        thumb = Image.new("RGB", (200, 150), color=(200, 200, 200))
        result = assess_image_quality(thumb, min_resolution=(600, 380))
        assert result.has_sufficient_resolution is False
        assert result.is_acceptable is False

    def test_monochrome_photocopy_detected(self):
        # Fotocopia nítida en escala de grises convertida a RGB (saturación = 0)
        gray_card = create_specimen_card_image(tampered=False).convert("L").convert("RGB")
        result = assess_image_quality(gray_card)
        assert result.is_monochrome is True
        assert result.saturation_mean == 0.0
        # Con allow_monochrome=True por defecto, no bloquea el análisis
        assert result.is_acceptable is True

    def test_monochrome_rejection_when_disallowed(self):
        gray_card = create_specimen_card_image(tampered=False).convert("L").convert("RGB")
        result = assess_image_quality(gray_card, allow_monochrome=False)
        assert result.is_monochrome is True
        assert result.is_acceptable is False
        assert "monocromático" in result.summary.lower()


class TestMRZPreprocessing:
    """Verificación de binarización morfológica para la zona OCR-B."""

    def test_preprocess_mrz_region_returns_binary_image(self):
        crop = np.random.randint(100, 200, (60, 400, 3), dtype=np.uint8)
        processed = preprocess_mrz_region(crop)
        
        assert processed.shape == (60, 400)
        # Debe contener solo valores binarios (0 y 255)
        unique_vals = set(np.unique(processed))
        assert unique_vals.issubset({0, 255})
