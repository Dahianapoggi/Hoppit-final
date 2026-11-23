# HOPPIT

HOPPIT planifica rutas de reparto de última milla en la Ciudad de Buenos Aires. Tiene una interfaz web para el operador, que registra Órdenes, planifica las Rutas sobre la red vial y sigue su ejecución, y una aplicación para el transportista, que recibe su Ruta, declara cada entrega e informa su posición. Es el Proyecto Final de Ingeniería en Informática de la UADE, 2026.

## Qué hay en el repositorio

| Carpeta | Contenido |
|---|---|
| `backend/` | El sistema en Python: el código (`src/hoppit/`), las pruebas (`tests/`), las herramientas de operación y de datos (`tools/`), el contrato OpenAPI (`contrato/`) y los artefactos de datos versionados (`datos/`). |
| `frontend/` | La interfaz web del operador: HTML, CSS y JavaScript sin framework ni empaquetador, con Leaflet y las tipografías dentro del repositorio. |
| `movil/` | La aplicación del transportista, en Expo y React Native, con una versión web que se sirve en `/app/`. |
| `infra/` | El despliegue: las imágenes de contenedores, el borde HTTP, los archivos de Docker Compose y la infraestructura de la nube en Terraform. |
| `.github/` | El flujo de GitHub Actions que corre la batería de pruebas. |

## Arquitectura

El backend es un monolito modular con arquitectura de puertos y adaptadores, partido por capacidad de negocio. Cada paquete de `backend/src/hoppit/` expone sólo su `puerto.py`, y los límites entre paquetes se verifican con `import-linter` y con `tools/check_boundaries.py`. Se despliega como dos servicios: el núcleo, con la API REST bajo `/v1`, y un servicio aparte de telemetría y seguimiento, que recibe las posiciones de la aplicación. Cada servicio tiene su propia base SQLite. Delante de los dos hay un borde HTTPS que reparte los pedidos y sirve la interfaz del operador y la versión web de la aplicación. Las tecnologías principales, según los archivos del repositorio:

| Parte | Tecnología |
|---|---|
| Backend (`backend/pyproject.toml`, versión 1.0.0) | Python 3.13, obligatorio (imagen `python:3.13.15-slim`, fijada por digest); FastAPI ≥ 0.115 y uvicorn; OR-Tools ≥ 9.10 para la resolución de rutas; OSMnx ≥ 2.0 y NetworkX ≥ 3.0 para la red vial; SQLite de la biblioteca estándar |
| Interfaz del operador | Leaflet 1.9.4, en `frontend/vendor/leaflet/` |
| Aplicación del transportista (`movil/package.json`) | Expo SDK 58 (`expo ~58.0.19`), React Native 0.88.0, React 19.3.0, TypeScript ~6.0.3 |
| Borde | Caddy 2.10.2 (`caddy:2.10.2-alpine`) |
| Contenedores | Docker Compose, con tres servicios: `borde`, `nucleo` y `telemetria` |

Las dependencias de Python están fijadas, con versión y hash, en `backend/requirements.txt` (servidor e imagen) y `backend/requirements-dev.txt` (lo anterior más las herramientas de verificación). `pip` rechaza cualquier paquete cuyo hash no coincida, así que todos los equipos, el CI y la imagen instalan exactamente lo mismo. Las de la aplicación están fijadas en `movil/package-lock.json`.

## Requisitos

- Para probar el sistema completo, en contenedores: Docker con Compose (opción A). No hace falta nada más.
- Para probarlo sin contenedores y correr las pruebas del backend: Python 3.13 (opción B).
- Para correr las pruebas de la aplicación: Node 24.

Todas las órdenes se dan desde la raíz del repositorio, salvo donde se indica.

## Opción A: el sistema completo, con Docker

Levanta los tres servicios, sobre la red vial de la Ciudad de Buenos Aires. Con Docker abierto, primero se definen las variables de la consola. La contraseña es la de los usuarios de prueba y se elige en el momento, de 12 caracteres o más.

Windows (PowerShell):

```powershell
$env:COMPOSE_FILE = "infra/compose.yaml;infra/compose.local.yaml"
$env:COMPOSE_ENV_FILES = "infra/entorno.local.ejemplo"
$env:HOPPIT_CONTRASENA_DEL_ENSAYO = "<una contraseña de 12 caracteres o más>"
```

Linux o macOS:

```bash
export COMPOSE_FILE=infra/compose.yaml:infra/compose.local.yaml
export COMPOSE_ENV_FILES=infra/entorno.local.ejemplo
export HOPPIT_CONTRASENA_DEL_ENSAYO="<una contraseña de 12 caracteres o más>"
```

Después, en la misma consola, las mismas órdenes en los dos casos:

1. Construir las imágenes (tarda varios minutos la primera vez):

   ```
   docker compose build
   ```

2. Generar las claves de firma de las sesiones, que no están en el repositorio:

   ```
   docker run --rm -u 0 -v "${PWD}/backend/datos/tmp/despliegue_local/secretos:/claves" local/hoppit-backend:local sh -c "python tools/generar_claves_de_firma.py --directorio /claves && chown 10001:10001 /claves/*"
   ```

3. Crear la base y los dos usuarios de prueba:

   ```
   docker compose run --rm --no-deps nucleo python tools/recrear_instancia.py --grafo caba --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3
   docker compose run --rm --no-deps -e HOPPIT_CONTRASENA_DEL_ENSAYO nucleo python tools/crear_usuario.py --usuario operador-ensayo --rol operador --nombre "Operador del ensayo" --contrasena-desde-variable HOPPIT_CONTRASENA_DEL_ENSAYO --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3
   docker compose run --rm --no-deps -e HOPPIT_CONTRASENA_DEL_ENSAYO nucleo python tools/crear_usuario.py --usuario transportista-ensayo --rol transportista --nombre "Transportista del ensayo" --contrasena-desde-variable HOPPIT_CONTRASENA_DEL_ENSAYO --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3
   ```

4. Levantar los servicios:

   ```
   docker compose up -d --wait
   ```

La interfaz del operador queda en `http://localhost:8080/` (usuario `operador-ensayo`) y la aplicación del transportista en `http://localhost:8080/app/` (usuario `transportista-ensayo`), con la contraseña elegida. En el primer ingreso se aceptan los términos. El sistema arranca con la Flota y sin Órdenes: se dan de alta desde la interfaz.

`docker compose down` detiene los servicios y conserva los datos; `docker compose down -v` también los borra.

**Comprobación automática.** Con los servicios levantados y el entorno de Python de la opción B instalado, este recorrido ingresa con los dos usuarios, da de alta Órdenes, planifica, despacha, declara un Incidente e informa posiciones, y termina con `16 de 16 comprobaciones OK`. Crea datos de prueba. En Windows:

```
.venv\Scripts\python.exe backend/tools/ensayo_guion_demo.py --base http://localhost:8080
```

En Linux o macOS, con `.venv/bin/python`.

## Opción B: sin contenedores

### Instalar (una sola vez)

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r backend/requirements-dev.txt
.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e ./backend
```

Linux o macOS:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e ./backend
```

### Levantar

En lo que sigue, en Linux o macOS se usa `../.venv/bin/python` en lugar de `..\.venv\Scripts\python.exe`. En una terminal:

```powershell
cd backend
..\.venv\Scripts\python.exe tools/generar_claves_de_firma.py
..\.venv\Scripts\python.exe tools/crear_usuario.py --usuario operador --rol operador --nombre "Operador"
..\.venv\Scripts\python.exe tools/servir_api.py --grafo caba
```

Las dos primeras órdenes se dan una sola vez por equipo. Las claves quedan en una carpeta del usuario, fuera del repositorio, y la contraseña se pide por consola (12 caracteres o más). El sistema arranca sobre la Ciudad de Buenos Aires, con la Flota y sin Órdenes: se dan de alta desde la interfaz.

En otra terminal, la interfaz:

```powershell
cd frontend
..\.venv\Scripts\python.exe -m http.server 5173 --bind 127.0.0.1
```

La interfaz queda en `http://127.0.0.1:5173/` y la documentación interactiva de la API en `http://127.0.0.1:8000/docs`. Para el seguimiento con telemetría y la aplicación del transportista, usar la opción A.

## Pruebas

**Backend,** desde `backend/`, con el entorno de la opción B. Es la misma secuencia que corre `.github/workflows/bateria.yml`:

```powershell
..\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
..\.venv\Scripts\python.exe -m mypy src/hoppit
..\.venv\Scripts\lint-imports.exe
..\.venv\Scripts\python.exe tools/check_boundaries.py
..\.venv\Scripts\python.exe tools/huella_instancia_canonica.py
```

(en Linux o macOS, `../.venv/bin/python` y `../.venv/bin/lint-imports`). `check_boundaries.py` y `lint-imports` verifican los límites entre paquetes. `huella_instancia_canonica.py` comprueba que la planificación de la instancia de referencia dé exactamente el mismo resultado, porque el motor es determinista. Esa instancia es un caso chico de San Isidro, que queda en el repositorio sólo para esta comprobación y para las pruebas: el sistema funciona sobre la Ciudad de Buenos Aires.

**Aplicación del transportista,** desde `movil/`, con Node 24:

```
npm ci
npm test
npm run tipos
```

`npm test` corre las pruebas con Jest, y `npm run tipos` verifica los tipos con `tsc --noEmit`.

## Referencias internas

El código, los datos y los documentos citan identificadores como `ARQ-`, `B-`, `D31` o `sesión 2026-11-23F`: son entradas de la bitácora del proyecto, que no está en este repositorio. Los identificadores de commit que aparecen en los archivos de procedencia corresponden al repositorio de trabajo, no a este.

## Despliegue

El despliegue en Azure, con Terraform, imágenes de contenedores y Docker Compose en una máquina virtual, está descrito en [`infra/README.md`](infra/README.md).

## Datos y atribuciones

- **Red vial y mapas:** datos de [OpenStreetMap](https://www.openstreetmap.org/copyright), © colaboradores de OpenStreetMap, con licencia ODbL. Los grafos de `backend/datos/` derivan de esos datos.
- **Teselas del fondo del mapa:** del servidor estándar de OpenStreetMap, usadas según la política de uso de su proveedor. La atribución se muestra en el propio mapa.
- **Búsqueda por dirección:** «Servicio Georef – argentina.gob.ar/georef», con licencia CC BY 4.0.
- **Hechos viales de la Ciudad:** datos del Observatorio de Movilidad y Seguridad Vial (BA Data, Gobierno de la Ciudad de Buenos Aires), con la licencia tal como la registra el sidecar de la descarga: «Creative Commons Attribution (cc-by), segun el portal (package_show de victimas-siniestros-viales)». La tabla de riesgo por arco de `backend/datos/riesgo_vial/` deriva de ellos.
- **Datos de estudio:** las tablas derivadas de LaDe y de Amazon Last Mile Routing Research Challenge 2021 que hay en `backend/datos/` tienen licencia CC BY-NC 4.0, de uso no comercial. Cada artefacto lleva un archivo `.procedencia.json` con su fuente y su licencia.
- **Componentes incluidos:**
  - Leaflet 1.9.4, con el aviso de copyright de sus autores en el encabezado de `frontend/vendor/leaflet/leaflet.js`. Tiene licencia BSD de 2 cláusulas, con su texto en `frontend/vendor/leaflet/LICENSE`;
  - IBM Plex y Chakra Petch, con SIL Open Font License 1.1, cuyos textos están en `frontend/vendor/fuentes/`.

## Licencia

© 2026, el equipo de HOPPIT. Todos los derechos reservados. El repositorio se publica para su evaluación académica: se puede leer y ejecutar con ese fin, y no se otorga licencia para otros usos. Los datos y los componentes de terceros conservan sus propias licencias (ver «Datos y atribuciones»).
