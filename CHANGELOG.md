# Registro de Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).

---

## [1.2.0] — 2026-10-06 · Cruce de apellidos con documentos reales

### Añadido
- **Extracción de apellidos y nombre del anverso real** a partir de sus etiquetas impresas: `PRIMER APELLIDO` / `SEGUNDO APELLIDO` (DNI 3.0) y `APELLIDOS / SURNAMES`, `NOMBRE / NAME` (DNI 4.0 y TIE), con el valor en la línea siguiente o pegado a la etiqueta. Las líneas con dígitos o con otras etiquetas cortan el valor, para no arrastrar fechas, sexo ni nacionalidad.
- Hasta ahora solo se reconocía la etiqueta `TITULAR:` de las muestras sintéticas: con una TIE real el apellido no se cruzaba, así que **un cambio de apellido en el anverso no se detectaba**. Ahora sí (`CROSS_CHECK_SURNAME` en rojo).

### Cambiado
- Cruce de apellidos: si el anverso empieza por los apellidos completos de la MRZ y el OCR añade texto detrás (por ejemplo, el nombre), ya no se marca como discrepancia.
- README: badge de CI real en lugar del recuento fijo de tests; URL de clonado del repositorio público; titular de la licencia.

**Resultado:** 91/91 tests (8 nuevos) y cobertura del 87 %.

---

## [1.1.0] — 2026-10-06 · Hermetic-ID

### Cambiado
- **Nuevo nombre: Hermetic-ID** (antes DocShield-ES, un nombre muy usado). Cambian el paquete (`hermetic-id`), el servicio y el contenedor de Docker, la carpeta de subidas temporales (`hermetic_id_uploads`) y las variables de entorno: `DOCSHIELD_*` → `HERMETIC_*` (`HERMETIC_HOST`, `HERMETIC_PORT`, `HERMETIC_OFFLINE_MODE`, `HERMETIC_MODELS_DIR`, `HERMETIC_API_KEY`, `HERMETIC_CORS_ORIGINS`).
- **Interfaz más limpia**: los botones de muestras sintéticas pasan a un desplegable plegado ("Muestras de demostración"). El botón "Limpiar" queda junto al de análisis, siempre visible porque también borra las copias temporales de las subidas. La etiqueta "Enterprise" pasa a "Beta".

### Privacidad del repositorio
- `.gitignore` y `.dockerignore` sin excepciones para imágenes: ningún formato de imagen o PDF puede llegar al repositorio ni a la imagen Docker. Las muestras de los tests se generan en código.
- Nueva carpeta local `private/` (ignorada) para documentos reales, notas y borradores. Los informes exportados (`informes/`, `reports/`, `*_informe*.json`) y los archivos `.env` también se ignoran.
- La documentación interna de desarrollo (plan de fases y guion de difusión) sale del repositorio público; el roadmap queda resumido en el README.

### Docker (verificado)
- Imagen construida y probada: arranca en ~6 s, responde en `127.0.0.1` y **no** es accesible desde la IP de red local; OCR operativo sin descargas en ejecución; usuario sin privilegios; dentro de la imagen no hay `.venv`, `.git`, `private/` ni imágenes.
- `docker-compose.yml`: nuevo `HERMETIC_HOST_PORT` para usar otro puerto del host si el 7860 está ocupado. Eliminada la clave `version`, obsoleta en Docker Compose v2.

---

## [1.0.1] — 2026-10-06 · Revisión de correctitud, privacidad y despliegue

Revisión completa del código hecha con Claude Code. Los 55 tests existentes pasaban, pero el generador sintético usaba una estructura de MRZ que no es la del DNI real, así que los fallos con documentos reales quedaban ocultos. Cada fallo corregido tiene una prueba en `tests/test_regressions.py`.

**Resultado:** 83/83 tests pasan (55 existentes adaptados + 28 de regresión). Probado también en el navegador con las muestras sintéticas y con una TIE real:

| Prueba con TIE real | Veredicto | Detalle |
| :-- | :-- | :-- |
| Original sin modificar | 🟢 Sin inconsistencias (13/13 OK) | Sin falsos positivos. ELA uniforme (desviación 0,1) |
| Caducidad del anverso alterada (reverso intacto) | 🔴 Inconsistencias (1 fallo) | Detectada por `CROSS_CHECK_EXPIRY_DATE`. La vigencia muestra la fecha real de la MRZ. ELA: aviso en el umbral (Z-score 5,0) |

### 🔴 Corregido — Fallos graves

| # | Fallo | Causa | Archivo |
| :-- | :-- | :-- | :-- |
| 1 | Los DNI cuyo número empieza por 0, 1 o 2 salían como **"Inconsistencias críticas"** | La "corrección" del prefijo NIE se aplicaba a cualquier documento y convertía `12345678Z` en `Y2345678Z`, rompiendo el dígito compuesto | `src/detectors/ocr_engine.py` |
| 2 | Los tests no detectaban los fallos con DNI reales | El generador ponía el DNI en el campo de documento y el texto literal `IDESP` en los datos opcionales. En el DNI real el campo de documento es el **número de soporte** y el DNI va en los **datos opcionales** | `tests/fixtures/synthetic_generator.py` |
| 3 | Subir solo el reverso daba siempre rojo | Se validaba como DNI el número de soporte (`BAA000001`) y fallaba `DNI_FORMAT` | `src/app/ui.py`, `src/api/main.py` |
| 4 | Tildes y Ñ provocaban discrepancias falsas (`GARCÍA` ≠ `GARCIA`, `MUÑOZ` ≠ `MUNOZ`) | Se eliminaban los caracteres no A-Z en lugar de transliterarlos como hace ICAO | `src/detectors/ocr_engine.py` |
| 5 | Docker no era accesible | La app escuchaba en `127.0.0.1` dentro del contenedor, inalcanzable desde el mapeo de puertos | `src/app/ui.py`, `docker/Dockerfile` |
| 6 | Sin anverso, el cruce comparaba la MRZ **consigo misma** y salía "OK" | Los datos de respaldo del reverso se usaban como si fueran del anverso | `src/app/ui.py` |
| 7 | Con un anverso manipulado (caducidad 2035 sobre una MRZ de 2030), el panel mostraba la **fecha falsa como "EN VIGOR"** | La vigencia se calculaba con la fecha del anverso. Ahora manda la de la MRZ si su dígito de control es correcto | `src/app/ui.py`, `src/api/main.py` |
| 8 | En el navegador, el ELA **no detectaba el parche** de la muestra manipulada (en los tests sí) | `gr.Image` guarda por defecto en WebP con pérdida, que borra los artefactos JPEG. Además, con `image_mode="RGB"` Gradio re-codifica las JPEG en gris y las PNG con transparencia, perdiendo EXIF y compresión original. Ahora `format="png"` e `image_mode=None` | `src/app/ui.py` |

### 🟠 Corregido — Privacidad y seguridad

- **Subidas de Gradio en disco**: el README decía "cero persistencia en disco", pero Gradio guarda cada subida en una carpeta temporal. Ahora se confinan en una carpeta dedicada (`GRADIO_TEMP_DIR`) que se purga cada minuto (subidas de más de 5 min), al arrancar, al cerrar y con "Nuevo Expediente". La documentación lo explica tal cual.
- **Subidas de la API en disco**: Starlette volcaba a un temporal en disco toda subida de más de 1 MB. Se eleva `MultiPartParser.spool_max_size` por encima del máximo admitido, y las peticiones con `Content-Length` excesivo se rechazan (413) antes de leer el cuerpo.
- **CORS abierto**: `allow_origins=["*"]` con `allow_credentials=True`. Ahora está cerrado por defecto y se configura con `HERMETIC_CORS_ORIGINS`.
- **API sin autenticación**: nueva clave opcional `HERMETIC_API_KEY` (cabecera `X-API-Key`, comparación en tiempo constante) para los endpoints `/api/v1/audit/*`.
- **HTML sin escapar**: los datos leídos por OCR (nombre, número, mensajes de error) se insertaban sin escapar en el HTML del panel. Ahora se escapan.
- **Modelos OCR descargados en ejecución**: la imagen Docker descarga los modelos de EasyOCR al construirse y arranca con `HERMETIC_OFFLINE_MODE=true`.
- **`.dockerignore` inexistente**: `COPY . .` metía en la imagen la `.venv` y cualquier imagen local del proyecto. Ahora quedan excluidas.

### 🟡 Corregido — Controles que no funcionaban

- **Borde de la foto**: faltaba `import cv2` en `ui.py`. El `except` ocultaba el `NameError`, así que el control no se ejecutaba nunca en la interfaz.
- **EXIF**: la interfaz volvía a codificar la imagen como JPEG antes del análisis, lo que eliminaba los metadatos. Ahora se analizan los bytes originales del archivo subido.
- **ELA**: se calculaba sobre la imagen reducida a 1920 px, y una foto de móvil siempre supera ese tamaño. Redimensionar destruye la rejilla de compresión JPEG que el ELA analiza. Ahora se calcula a resolución original (con tope anti-OOM de 25 MP) y solo se reduce el mapa de visualización.
- **Caracteres no válidos en la MRZ**: un espacio o una letra acentuada lanzaba `ValueError` (error 500 en `/api/v1/audit/mrz`). Ahora devuelve un resultado inválido con el control `MRZ_LINE_n_CHARSET`.
- **CSS de la interfaz**: en Gradio 6, `css` ya no se acepta en `gr.Blocks()` y se ignoraba. Ahora se pasa a `launch()`.

### 🔧 Cambiado — Criterio y trazabilidad

- **Severidad del veredicto**: los indicios heurísticos (ELA, EXIF, borde de foto) pasan de FALLO a **ADVERTENCIA**. El rojo queda para fallos deterministas (dígitos de control, módulo 23, cruce anverso/MRZ, caducidad). Motivo: sus umbrales no están calibrados y dan falsos positivos en fotos de móvil.
- **Correcciones del OCR registradas**: toda corrección heurística queda en `ocr_corrections` (informe JSON y pestaña de trazabilidad). El prefijo NIE solo se corrige si la MRZ resultante supera a la vez la letra módulo 23 y el dígito compuesto.
- **Sexo ilegible**: ya no se adivina (`7`/`1` → `F`). Ese campo no tiene dígito de control; se marca como no especificado (`<`).
- **Tipo de documento**: el desplegable "Tipo de Soporte" tenía "DNI 4.0 / 3.0" por defecto y pisaba la detección (una TIE salía como DNI). Nueva opción por defecto "Auto (detectar)": el tipo se deduce de la MRZ o del número salvo que el operador elija uno.
- **Detección de TIE**: se decidía buscando el texto `"ARG"` (la nacionalidad de una imagen de prueba). Ahora se deduce de si los datos opcionales contienen un NIE o un DNI.
- **Comparaciones exactas**: número de documento por igualdad (antes por subcadena, y una cadena vacía siempre "coincidía"). Apellidos comparados por palabras, con "coincidencia parcial" si solo se aporta el primer apellido.
- **Nuevos controles**: letra módulo 23 del DNI/NIE dentro de la MRZ (`MRZ_PERSONAL_NUMBER_CHECKSUM`) y cruce del número de soporte (`CROSS_CHECK_SUPPORT_NUMBER`).
- **Fechas del anverso**: se asignan por valor (nacimiento = la más antigua, caducidad = la más reciente) en lugar de por el orden de lectura del OCR.
- **OCR una sola vez por imagen**: el resultado de la detección de cara (anverso/reverso) se reutiliza en lugar de repetir EasyOCR.
- **Leyenda del mapa ELA**: el halo naranja ya no se presenta como "posible empalme digital", porque también marca texto impreso sólido.
- **Muestra sintética válida**: el borde de la foto se suaviza para que no dispare el control de borde, ahora activo.

### 📦 Dependencias, CI y empaquetado

- Eliminada la dependencia `pytesseract` (no se usaba en el código) y la instalación de Tesseract en CI.
- CI: `libgl1-mesa-glx` ya no existe en Ubuntu 24.04 (`ubuntu-latest`) y rompía la instalación. Se sustituye por `libgl1 libglib2.0-0`. PyTorch se instala en su versión solo CPU.
- `requirements.txt` incluye `fastapi`, `uvicorn` y `python-multipart`, que la API necesita.
- `gradio>=6.0.0` (la interfaz usa la API de Gradio 6).
- `pyproject.toml`: el estado pasa de `Production/Stable` a `Beta` y el tema de `Cryptography` (no hay criptografía) a `Security`.

### 📝 Documentación

- README: eliminadas las afirmaciones sin respaldo ("cero persistencia en disco", "sin dependencias en la nube", "filtra el 25–35 % del fraude", coste por documento, tiempos del ELA). Añadidas la política de severidad, la estructura real de la MRZ, la configuración de la API y una sección de limitaciones conocidas.
- Nueva sección de roadmap en el README.

### ⏳ Pendiente (no resuelto en esta revisión)

- **Calibrar ELA y borde de foto** con un conjunto de imágenes etiquetado (auténticas y manipuladas, fotos de móvil y escaneos) y publicar las tasas medidas.
- **Recuadro de la foto**: localizarlo detectando el documento en lugar de usar proporciones fijas.
- **OCR del anverso con documentos reales**: la extracción por etiquetas (1.2.0) está probada con lecturas OCR simuladas; falta validarla con anversos reales.
- **ELA según el formato de origen**: avisar o desactivarlo cuando la imagen no es JPEG.
- **Fixtures fuera de `tests/`**: la interfaz importa el generador de muestras desde `tests.fixtures`, y el código de producción no debería depender de `tests/`.
- **Purga al arrancar compartida**: dos instancias de la interfaz abiertas a la vez comparten la carpeta de subidas, así que arrancar una borra las subidas de la otra. Si hace falta, usar un `GRADIO_TEMP_DIR` distinto por instancia.
