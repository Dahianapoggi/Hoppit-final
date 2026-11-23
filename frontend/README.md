# HOPPIT — frontend

Aplicación cliente del plan de rutas. **JavaScript sin framework** (módulos
ES nativos), HTML y CSS, con [Leaflet](https://leafletjs.com/) vendorizado en
`vendor/` para el mapa. No hay paso de compilación ni gestor de paquetes: los
archivos se sirven tal como están.

## Pantallas

| Archivo | Pantalla | Caso de uso |
|---|---|---|
| `index.html` | G1-A — plan de rutas sobre el mapa | CU-03 |
| `planificacion.html` | G1-B — composición de la instancia y disparo | CU-02 |
| `orden.html` | G1-D — alta de Orden sobre el grafo vial | CU-01 |
| `ingreso.html` | Ingreso con usuario y contraseña (DFE-09) | — |
| `terminos.html` | Aceptación de términos y consentimientos (DFE-10) | — |
| `inicio.html` | Inicio de la jornada (DFE-12) | — |
| `incidentes.html` | Incidentes: lista, detalle y declaración (DFE-17) | CU-07, paso 1 |
| `acerca.html` | Versión, procedencia de los datos y licencias (DFE-13) | — |
| `transportista.html` | Aviso para el rol transportista en la web (DFE-15) | — |

G1-C no tiene archivo propio: los estados vacíos y de error son estados de
las pantallas, no una pantalla más.

## Acceso (backend 0.13.0)

Toda pantalla salvo el ingreso, el registro y "Acerca de" exige una sesión.
El token vive en `sessionStorage`: se pierde al cerrar la pestaña. Sin
sesión, la pantalla lleva al ingreso; con términos pendientes, a los
términos; con rol transportista, a su aviso. Los usuarios se crean con
`backend/tools/crear_usuario.py` o, desde 0.15.0, en `registro.html` con un
código de invitación de un solo uso que genera
`backend/tools/crear_invitacion.py` (DFE-28). El servidor necesita antes su
par de claves (`backend/tools/generar_claves_de_firma.py`).

## Módulos

- `js/api.js` — **única superficie de contacto con el backend.** Todas las
  llamadas pasan por acá; ninguna pantalla arma un `fetch` por su cuenta.
  Valida al arrancar que el contrato servido sea el esperado.
- `js/app.js`, `js/planificacion.js`, `js/orden.js`, `js/ingreso.js`,
  `js/terminos.js`, `js/inicio.js`, `js/incidentes.js`, `js/acerca.js`,
  `js/transportista.js` — una por pantalla.
- `js/sesion.js` — la sesión de la pestaña y a qué pantalla va cada quien.
- `js/encabezado.js` — la franja común con las secciones, el usuario y
  "Cerrar sesión".
- `js/modal.js` — el modal de avisos y errores, las confirmaciones, el aviso
  breve de éxito y el de sin conexión.
- `js/construccion.js` — la animación de entrada de G1-A: revela el trazado
  desde el Depósito hacia la última Parada siguiendo la geometría real de los
  arcos de calle. Es una transición, no un estado: al terminar deja
  exactamente lo que se habría dibujado sin animar.
- `js/tokens.js` — lo compartido entre pantallas: la paleta por Vehículo y el
  disco de secuencia. Las Rutas se distinguen por color **y** por número,
  nunca sólo por color.

## Levantarlo

Necesita **el backend corriendo** en `http://127.0.0.1:8000` — cómo prenderlo
está en el [README de la raíz](../README.md), que también cubre la
preparación del entorno y qué hacer cuando algo no arranca.

El frontend en sí **no necesita el entorno virtual**: se sirve con el
servidor de archivos que trae Python, sin dependencias. En una segunda
terminal, sin bajar la del backend:

**Linux / macOS**

```bash
cd frontend
python3 -m http.server 5173 --bind 127.0.0.1
```

**Windows (PowerShell)**

```powershell
cd frontend
py -3 -m http.server 5173 --bind 127.0.0.1
```

Y abrir **`http://127.0.0.1:5173`**.

Dos cosas que conviene tener claras:

- **Hay que servirlo por HTTP.** Como usa módulos ES, abrir el archivo con
  `file://` —doble clic en `index.html`, o la vista previa del editor— los
  bloquea por política de origen y la pantalla queda en blanco.
- `--bind 127.0.0.1` lo deja accesible sólo desde la máquina. Sin esa opción
  Python sirve en `0.0.0.0`, es decir a toda la red local.

El backend tiene CORS abierto, así que la diferencia de origen entre los dos
puertos no es problema.

Si el backend sirve una versión de contrato distinta de la esperada, la
aplicación lo dice al arrancar en vez de dejar una pantalla vacía sin causa
visible: `api.js` consulta `GET /v1/estado` antes que nada.

### Volver al estado de demostración

Para volver al estado conocido —la Flota y el Depósito, sin Órdenes ni plan— se corre
`tools/recrear_instancia.py --grafo caba` del backend, con el servidor **bajado**: rehace
la base y no puede borrarla mientras alguien la tenga abierta.

Para limpiar sólo el plan y conservar el historial alcanza con la acción
**Descartar el plan** de G1-B, o con `tools/descartar_planes.py`, que sí
funcionan con el servidor levantado.

## Cómo consume el backend

Exclusivamente por el contrato REST bajo `/v1`, que es lo que NFR-10 exige:
la capa de visualización no accede a la implementación interna del motor de
cálculo. Las decisiones se toman contra el **`codigo`** de cada error, nunca
contra el texto del `mensaje`, que puede cambiar sin romper compatibilidad.
El contrato y sus respuestas de ejemplo están en `backend/contrato/`.

Desde 2026-10-19B la interfaz usa las operaciones del contrato salvo
`GET /v1/ordenes/{id}`, `GET /v1/planes/{id}`, `GET /v1/jornadas` y `/estado`
(usa `/v1/estado`).

Dos criterios que atraviesan las tres pantallas:

- **Lo que no llega no se inventa.** Si un dato falta, se declara el hueco en
  pantalla. Ninguna cifra se deriva de un supuesto no declarado.
- **La capacidad y el Depósito de una Ruta publicada viajan con ella**, no se
  leen de `GET /v1/flota` ni de `GET /v1/depositos`. La Ruta se calculó contra
  aquéllos, y un cambio posterior de la Flota o del Depósito no debe alterar
  retroactivamente un plan ya emitido. Los dos endpoints son para el estado
  **previo** al cálculo, donde todavía no hay Rutas de las cuales derivarlos.

### ETA de las Paradas (5 oct 2026)

Desde el 5 de octubre de 2026, el detalle de una Ruta (`GET /v1/rutas/{id}`) publica `hora_de_salida` y, en cada Parada, `eta_estimado_s`, `eta_p10_s` y `eta_p90_s`: la mediana y la banda de p10 a p90 de la hora en que se completa la **primera entrega** de la Parada, en segundos desde la salida. Ese día la versión del backend no cambió; desde el 6 de octubre de 2026 es la 0.10.0 (B-198), y el contrato sigue en `v1`. Los estima el backend por simulación (B-193). Desde el 6 de octubre de 2026 usa el modelo híbrido de B-196: un viaje por la red a una velocidad supuesta, 19 km/h por omisión y no medida en el AMBA, más tiempos de parada tomados de datos de LaDe en Hangzhou, de otra ciudad. Entre el 7 y el 12 de octubre de 2026 cada tramo llevó además la dispersión de esos datos según su distancia en línea recta (B-201); el 12 de octubre se volvió al modelo sin esa dispersión (B-205). La banda es la del modelo y no está medida en el AMBA. No están validados para el AMBA ni dependen del tránsito.

G1-A los muestra como horas del día en la espina de la Ruta seleccionada y en la ficha de Parada, con un rótulo visible que dice de dónde sale la estimación. Si la API no los publica —el servidor arrancó sin el servicio de estimación, o la Ruta necesita una franja horaria que la tabla no cubre— no se muestra nada en su lugar. El listado de Rutas y las respuestas de Plan no traen Paradas, así que no traen ETA.
