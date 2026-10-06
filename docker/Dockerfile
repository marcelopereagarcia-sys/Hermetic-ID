# Imagen base ligera de Python
FROM python:3.11-slim

# Evitar generación de bytecode y forzar salida inmediata en stdout
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV GRADIO_ANALYTICS_ENABLED=False

# Dentro del contenedor la app debe escuchar en todas las interfaces: si escuchara en 127.0.0.1
# el mapeo de puertos no llegaría a ella. La exposición real la limita docker-compose
# publicando el puerto solo en el loopback del host (127.0.0.1:7860:7860).
ENV HERMETIC_HOST=0.0.0.0
ENV HERMETIC_PORT=7860

# Modelos de OCR horneados en la imagen: en ejecución no hay descargas (modo offline estricto)
ENV HERMETIC_MODELS_DIR=/opt/easyocr
ENV HERMETIC_OFFLINE_MODE=true

# Dependencias del sistema requeridas por OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario sin privilegios por seguridad (Principio de mínimo privilegio)
RUN useradd -m -u 1000 appuser

WORKDIR /app

# PyTorch solo CPU (EasyOCR lo requiere): evita descargar las ruedas CUDA de varios GB
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Descargar los modelos de EasyOCR en tiempo de construcción
RUN mkdir -p "$HERMETIC_MODELS_DIR" \
    && python -c "import easyocr; easyocr.Reader(['es', 'en'], gpu=False, verbose=False, model_storage_directory='/opt/easyocr')" \
    && chown -R appuser:appuser "$HERMETIC_MODELS_DIR"

# Copiar el código de la aplicación (ver .dockerignore: sin .venv, .git ni imágenes locales)
COPY --chown=appuser:appuser . .

USER appuser

# Exponer el puerto de la interfaz web
EXPOSE 7860

# Comando de arranque
CMD ["python", "app.py"]
