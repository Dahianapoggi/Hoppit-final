# Imagen del backend de HOPPIT (ARQ-47, seccion 1.2; 2026-10-27B, D).
#
# Una sola imagen con dos ordenes de arranque: el nucleo (por omision) y el
# servicio de telemetria y seguimiento (`command` en el Compose). La misma
# version menor de Python con que se verifica localmente, 3.13. Sin secretos:
# las claves de firma llegan como secretos de Compose en /run/secrets.
#
# El grafo de CABA y su Deposito viajan adentro, porque estan versionados en
# git (B-244, punto 2; B-259, punto 2). La base no: vive en un directorio
# montado desde el disco de datos.
FROM python:3.13.15-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HOPPIT_CLAVES_DE_FIRMA=/run/secrets

WORKDIR /app

# Instalacion editable: Cartografia y el arranque resuelven los artefactos
# relativos a la carpeta del backend (datos/grafos, datos/instancia), y una
# instalacion comun dejaria el paquete en site-packages, lejos de ellos.
#
# Las dependencias salen de requirements.txt (pip-compile, con hashes): la
# imagen instala siempre las mismas versiones, y pip rechaza cualquier
# archivo cuyo hash no coincida. El lockfile incluye setuptools y wheel (las
# dependencias de construccion): el paquete propio va aparte, sin dependencias
# y sin aislamiento de construccion, para que pip no descargue ni resuelva
# nada fuera del lockfile.
COPY requirements.txt pyproject.toml ./
RUN pip install --require-hashes -r requirements.txt
COPY src ./src
RUN pip install --no-build-isolation --no-deps -e .

COPY tools ./tools
COPY datos/grafos ./datos/grafos
COPY datos/instancia ./datos/instancia
COPY datos/transformado/hangzhou ./datos/transformado/hangzhou
COPY datos/riesgo_vial ./datos/riesgo_vial

# Usuario sin privilegios, dueno solo de los directorios de las bases. Los dos
# existen en la imagen con ese dueno: un volumen con nombre nuevo (el
# despliegue local, 2026-10-27C, DK) copia el dueno del directorio de la
# imagen, y sin el directorio quedaria de root y el usuario no podria escribir.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin hoppit \
    && mkdir -p /var/lib/hoppit/nucleo /var/lib/hoppit/telemetria \
    && chown -R hoppit /var/lib/hoppit
USER hoppit

EXPOSE 8000 8001

# Nucleo, un solo worker de uvicorn (seccion 1.2): cada worker cargaria su
# propia copia del grafo. El despliegue conserva el bloqueo tras cinco
# intentos fallidos, que servir_api.py deja apagado por omision en la etapa
# de prueba (2026-11-03B); infra/compose.yaml repite este arranque.
CMD ["python", "tools/servir_api.py", "--host", "0.0.0.0", "--puerto", "8000", \
     "--base-de-datos", "/var/lib/hoppit/nucleo/nucleo.sqlite3", "--con-bloqueo-por-intentos"]
