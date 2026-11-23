"""Esquema fisico de la base, por introspeccion (B-205, punto 4).

Crea una base SQLite temporal con el mismo ensamblado que `tools/servir_api.py`
(`construir_contexto`, sin escenario) y la lee con `sqlite_master`,
`PRAGMA table_info`, `PRAGMA foreign_key_list`, `PRAGMA index_list` e
`PRAGMA index_info`. No toca `src/` ni la base de desarrollo.

Desde 2026-10-27C (V) describe tambien la base del servicio de telemetria
y seguimiento (`telemetria.sqlite3`, de `seguimiento_flota`), que es otra
base en otro proceso: sus referencias a la Ruta y al usuario cruzan de
servicio y de archivo, y ninguna tiene integridad referencial.

Que sale de donde:

- **De la base:** tablas, columnas con tipo, nulabilidad y valor por defecto,
  clave primaria, claves foraneas fisicas e indices.
- **Del codigo, ejecutandolo:** el componente dueno de cada tabla. Cada
  repositorio SQLite del ensamblado se instancia solo, sobre una base vacia
  propia, y se anotan las tablas que crea.
- **Del codigo, leyendolo:** a que entidad apunta cada columna que guarda un
  identificador sin clave foranea (`REFERENCIAS_SEGUN_EL_CODIGO`, con la
  fuente de cada una). La herramienta verifica contra la base que la columna
  exista y que no tenga clave foranea. Una columna con forma de identificador
  que no este en ese mapa se informa como pregunta, sin atribuirle destino.

Una referencia entre componentes sin integridad referencial es la forma que
toma ARQ-09: ninguna clave foranea cruza de un componente a otro.

Salidas, fuera de git, en `referencia/diagramas/`, una pareja por base
(`esquema_fisico_base` y `esquema_fisico_telemetria`): un `erDiagram` de
Mermaid (`.mmd`), con las claves foraneas fisicas en linea continua y las
referencias sin clave foranea en linea punteada, y una descripcion en
Markdown (`.md`).

    python tools/esquema_fisico_base.py
    python tools/esquema_fisico_base.py --directorio <carpeta>
"""
from __future__ import annotations

import argparse
import dataclasses
import gc
import logging
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.identidad.puerto import RepositorioIdentidadSQLite
from hoppit.gestion_ordenes.puerto import RepositorioOrdenesSQLite
from hoppit.planificacion_rutas.puerto import RepositorioDeEjecucionSQLite, RepositorioRutasSQLite
from hoppit.seguimiento_flota.puerto import RepositorioDeSeguimientoSQLite
from hoppit.shared.almacen import conectar
from hoppit.vigilancia_sla.puerto import RepositorioIncidentesSQLite
from tools.acceso_en_proceso import clave_efimera
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.esquema_fisico_base")

RAIZ_DEL_REPOSITORIO = pathlib.Path(__file__).resolve().parents[2]
DIRECTORIO_DE_SALIDA = RAIZ_DEL_REPOSITORIO / "referencia" / "diagramas"
NOMBRE_DE_LAS_SALIDAS = "esquema_fisico_base"
NOMBRE_DE_LAS_SALIDAS_DE_TELEMETRIA = "esquema_fisico_telemetria"
NOMBRE_DE_LA_BASE = "esquema.sqlite3"
NOMBRE_DE_LA_BASE_DE_TELEMETRIA = "telemetria.sqlite3"
PREFIJO_DE_LAS_TABLAS_INTERNAS = "sqlite_"

# Los repositorios SQLite que arma `construir_contexto` cuando recibe una
# ruta de base (tools/servir_api.py): uno por componente que persiste.
REPOSITORIOS_DEL_ENSAMBLADO: tuple[tuple[str, Callable[[sqlite3.Connection], object]], ...] = (
    ("gestion_ordenes", RepositorioOrdenesSQLite),
    ("planificacion_rutas", RepositorioRutasSQLite),
    # 2026-10-27B, T: el despacho con transportista y la salida transaccional.
    ("planificacion_rutas", RepositorioDeEjecucionSQLite),
    # 2026-10-20A, V.1; 2026-10-20B, V1: usuarios y aceptaciones; desde
    # 2026-10-20C, R, tambien invitaciones.
    ("frontera.identidad", RepositorioIdentidadSQLite),
    # Incidentes (FR-18): `construir_contexto` los arma desde 2026-10-19A.
    ("vigilancia_sla", RepositorioIncidentesSQLite),
)

# Los repositorios SQLite de componentes que persisten y que
# `construir_contexto` no arma; la herramienta los crearia sobre la misma
# base. Desde 2026-10-27C no queda ninguno.
REPOSITORIOS_FUERA_DEL_ENSAMBLADO: tuple[tuple[str, Callable[[sqlite3.Connection], object]], ...] = ()
REPOSITORIOS = REPOSITORIOS_DEL_ENSAMBLADO + REPOSITORIOS_FUERA_DEL_ENSAMBLADO

# La base del servicio de telemetria y seguimiento, que arma
# `tools/servir_telemetria.py` (2026-10-27B, T).
REPOSITORIOS_DE_TELEMETRIA: tuple[tuple[str, Callable[[sqlite3.Connection], object]], ...] = (
    ("seguimiento_flota", RepositorioDeSeguimientoSQLite),
)

# Columnas con forma de identificador que son la identidad de su propia fila
# y no apuntan a otra entidad.
IDENTIFICADORES_PROPIOS = frozenset({("notificaciones_salientes", "evento_id")})

# Columnas que, por su nombre, parecen guardar un identificador: las que
# terminan en `_id` y las que guardan un nodo del grafo vial.
SUFIJO_DE_IDENTIFICADOR = "_id"
COLUMNAS_DE_NODO = ("nodo", "destino", "deposito")

ENTRE_COMPONENTES = "referencia entre componentes, sin integridad referencial"
ENTRE_SERVICIOS = "referencia a otro servicio y a otra base, sin integridad referencial"
DEL_CLIENTE = "referencia a un dato que informa el cliente, sin tabla"
INTERNA = "referencia interna del componente, sin clave foranea"


@dataclasses.dataclass(frozen=True)
class Referencia:
    """Una referencia que el codigo declara y la base no hace cumplir."""

    columnas: tuple[str, ...]
    destino: str
    """La entidad a la que apunta, con el nombre del glosario."""

    donde_vive: str
    """Donde esta esa entidad: tabla de otro componente, memoria o artefacto."""

    clase: str
    fuente: str
    """Donde lo dice el codigo."""

    tabla_destino: str | None = None
    """La tabla del destino, si el destino es una tabla de esta base."""


REFERENCIAS_SEGUN_EL_CODIGO: dict[str, tuple[Referencia, ...]] = {
    "aceptaciones": (
        Referencia(
            ("dispositivo_id",),
            "Dispositivo de la aplicacion",
            "no persiste en la base: UUID generado al instalar la aplicacion del transportista",
            DEL_CLIENTE,
            "frontera/identidad/_repositorio.py, esquema de aceptaciones; diseno de acceso de Chat A, "
            "seccion 4.4 (2026-10-20B, V1)",
        ),
    ),
    "ordenes": (
        Referencia(
            ("ruta_id", "secuencia_parada"),
            "Parada de una Ruta",
            "tabla paradas_de_ruta, de planificacion_rutas",
            ENTRE_COMPONENTES,
            "gestion_ordenes/_repositorio.py (docstring del modulo, ARQ-28); "
            "gestion_ordenes/_dominio.py, Orden.ruta_id y Orden.secuencia_parada",
            "paradas_de_ruta",
        ),
        Referencia(
            ("destino",),
            "nodo del Grafo vial",
            "artefacto GraphML, cargado en memoria por cartografia",
            ENTRE_COMPONENTES,
            "gestion_ordenes/_dominio.py, Orden.destino; tools/servir_api.py, "
            "construir_contexto (crear_cartografia)",
        ),
    ),
    "transiciones_de_orden": (
        Referencia(
            ("declarante_usuario_id",),
            "Usuario que declaro el resultado de Parada",
            "tabla usuarios, de frontera.identidad",
            ENTRE_COMPONENTES,
            "gestion_ordenes/_repositorio.py, _COLUMNAS_DE_TRANSICIONES; ARQ-65 "
            "(2026-10-20C, B)",
            "usuarios",
        ),
    ),
    # CU-07 (2026-11-20A): reasignacion de Ordenes y reemplazo de Rutas.
    "reasignaciones_de_orden": (
        Referencia(
            ("ruta_anterior", "ruta_nueva"),
            "Ruta",
            "tabla rutas, de planificacion_rutas",
            ENTRE_COMPONENTES,
            "gestion_ordenes/_repositorio.py, reasignaciones_de_orden; evento RutaReemplazada (D9)",
            "rutas",
        ),
    ),
    "propuestas_de_reemplazo": (
        Referencia(
            ("ruta_id",),
            "Ruta",
            "tabla rutas, del mismo componente",
            INTERNA,
            "planificacion_rutas/_reemplazo.py, RepositorioDeReemplazosSQLite",
            "rutas",
        ),
    ),
    "reemplazos_de_ruta": (
        Referencia(
            ("ruta_reemplazada", "ruta_nueva"),
            "Ruta",
            "tabla rutas, del mismo componente",
            INTERNA,
            "planificacion_rutas/_reemplazo.py, RepositorioDeReemplazosSQLite (glosario, estado Reemplazada)",
            "rutas",
        ),
    ),
    "contingencias": (
        Referencia(
            ("ruta_id",),
            "Ruta",
            "tabla rutas, del mismo componente",
            INTERNA,
            "planificacion_rutas/_contingencias.py, RepositorioDeContingenciasSQLite (CU-11)",
            "rutas",
        ),
    ),
    "planes": (
        Referencia(
            ("deposito",),
            "Deposito (un nodo del Grafo vial)",
            "artefacto JSON del Deposito, leido al arrancar",
            ENTRE_COMPONENTES,
            "tools/servir_api.py, construir_contexto (leer_deposito)",
        ),
    ),
    "vehiculos_de_plan": (
        Referencia(
            ("vehiculo_id",),
            "Vehiculo de la Flota",
            "memoria del servicio de planificacion, armada al arrancar",
            ENTRE_COMPONENTES,
            "tools/servir_api.py, construir_contexto (capacidad_por_vehiculo); "
            "planificacion_rutas/puerto.py, parametro flota de crear_servicio",
        ),
    ),
    "rutas": (
        Referencia(
            ("plan_id",),
            "Plan de Distribucion",
            "tabla planes, del mismo componente",
            INTERNA,
            "planificacion_rutas/_repositorio.py, RutaPersistida.plan_id",
            "planes",
        ),
        Referencia(
            ("vehiculo_id",),
            "Vehiculo de la Flota",
            "memoria del servicio de planificacion, armada al arrancar",
            ENTRE_COMPONENTES,
            "tools/servir_api.py, construir_contexto (capacidad_por_vehiculo)",
        ),
        Referencia(
            ("deposito",),
            "Deposito (un nodo del Grafo vial)",
            "artefacto JSON del Deposito, leido al arrancar",
            ENTRE_COMPONENTES,
            "planificacion_rutas/_repositorio.py, RutaPersistida.deposito",
        ),
    ),
    "paradas_de_ruta": (
        Referencia(
            ("nodo",),
            "nodo del Grafo vial",
            "artefacto GraphML, cargado en memoria por cartografia",
            ENTRE_COMPONENTES,
            "planificacion_rutas/_repositorio.py, esquema de paradas_de_ruta",
        ),
    ),
    "ordenes_no_planificadas_de_plan": (
        Referencia(
            ("orden_id",),
            "Orden",
            "tabla ordenes, de gestion_ordenes",
            ENTRE_COMPONENTES,
            "planificacion_rutas/_repositorio.py, esquema de ordenes_no_planificadas_de_plan "
            "(OrdenNoPlanificada.orden)",
            "ordenes",
        ),
    ),
    "rutas_afectadas_por_incidente": (
        Referencia(
            ("ruta_id",),
            "Ruta",
            "tabla rutas, de planificacion_rutas",
            ENTRE_COMPONENTES,
            "vigilancia_sla/_repositorio.py (esquema); la existencia se verifica al registrar, por el "
            "puerto de Planificacion de Rutas (bootstrap.rutas_inexistentes_segun)",
            "rutas",
        ),
    ),
    "ordenes_de_parada": (
        Referencia(
            ("orden_id",),
            "Orden",
            "tabla ordenes, de gestion_ordenes",
            ENTRE_COMPONENTES,
            "planificacion_rutas/_repositorio.py (comentario del esquema, ARQ-28)",
            "ordenes",
        ),
    ),
    "transportistas_de_ruta": (
        Referencia(
            ("ruta_id",),
            "Ruta",
            "tabla rutas, del mismo componente",
            INTERNA,
            "planificacion_rutas/_ejecucion.py, esquema de transportistas_de_ruta (2026-10-27B, T)",
            "rutas",
        ),
        Referencia(
            ("transportista_id",),
            "Usuario con rol transportista",
            "tabla usuarios, de frontera.identidad",
            ENTRE_COMPONENTES,
            "planificacion_rutas/_ejecucion.py, sin clave foranea al usuario (ARQ-28; 2026-10-27B, T)",
            "usuarios",
        ),
    ),
    "notificaciones_salientes": (
        Referencia(
            ("agregado_id",),
            "Ruta",
            "tabla rutas, del mismo componente: el agregado de RutaDespachada",
            INTERNA,
            "planificacion_rutas/_ejecucion.py, salida transaccional (ARQ-59; 2026-10-27B, T)",
            "rutas",
        ),
    ),
}

# Las referencias de la base de telemetria (2026-10-27C, V). Todo lo que
# apunta al nucleo cruza de servicio: llega por el evento RutaDespachada o
# por el token de sesion, nunca leyendo la base del nucleo (seccion 4.3).
REFERENCIAS_DE_TELEMETRIA: dict[str, tuple[Referencia, ...]] = {
    "ejecuciones": (
        Referencia(
            ("ruta_id",),
            "Ruta (base del nucleo)",
            "tabla rutas de la base del nucleo, en otro proceso: llega por el evento RutaDespachada",
            ENTRE_SERVICIOS,
            "seguimiento_flota/_seguimiento.py, ServicioDeSeguimiento.recibir_evento (ARQ-59)",
        ),
        Referencia(
            ("vehiculo_id",),
            "Vehiculo de la Flota",
            "memoria del servicio de planificacion del nucleo; llega en el cuerpo del evento",
            ENTRE_SERVICIOS,
            "seguimiento_flota/_seguimiento.py, recibir_evento (vehiculo_id del cuerpo)",
        ),
        Referencia(
            ("transportista_id",),
            "Usuario (base del nucleo)",
            "tabla usuarios de la base del nucleo, en otro proceso; llega en el cuerpo del evento",
            ENTRE_SERVICIOS,
            "seguimiento_flota/_seguimiento.py, recibir_evento (transportista_id del cuerpo)",
        ),
    ),
    "posiciones": (
        Referencia(
            ("ruta_id",),
            "Ejecucion de la Ruta",
            "tabla ejecuciones, del mismo componente",
            INTERNA,
            "seguimiento_flota/_seguimiento.py, recibir_lote (se clasifica contra la ejecucion)",
            "ejecuciones",
        ),
        Referencia(
            ("transportista_id",),
            "Usuario (base del nucleo)",
            "tabla usuarios de la base del nucleo, en otro proceso: sale del token de sesion",
            ENTRE_SERVICIOS,
            "frontera/telemetria/_aplicacion.py, _reclamos (seccion 4.3)",
        ),
        Referencia(
            ("dispositivo_id",),
            "Dispositivo de la aplicacion",
            "no persiste en ninguna base: UUID version 4 que genera la aplicacion del transportista",
            DEL_CLIENTE,
            "seguimiento_flota/_seguimiento.py, es_uuid_v4_canonico (ARQ-52; 2026-10-27C, F2)",
        ),
    ),
    "reemplazos_de_ejecucion": (
        Referencia(
            ("ruta_reemplazada", "ruta_nueva"),
            "Ejecucion de la Ruta",
            "tabla ejecuciones, del mismo componente; llega por el evento RutaReemplazada",
            INTERNA,
            "seguimiento_flota/_seguimiento.py, recibir_evento y vigente (CU-07, D10; 2026-11-20A)",
            "ejecuciones",
        ),
    ),
    "eventos_recibidos": (
        Referencia(
            ("evento_id",),
            "Notificacion saliente del nucleo",
            "tabla notificaciones_salientes de la base del nucleo, en otro proceso",
            ENTRE_SERVICIOS,
            "seguimiento_flota/_seguimiento.py, recibir_evento (idempotencia, seccion 6.3)",
        ),
    ),
}

# Entidades fuera de la base a las que apuntan referencias: se dibujan como
# entidades sin atributos, para que la linea punteada tenga destino.
ENTIDADES_FUERA_DE_LA_BASE = {
    "Dispositivo de la aplicacion": "DISPOSITIVO_DE_LA_APLICACION",
    "Ruta (base del nucleo)": "RUTA_EN_LA_BASE_DEL_NUCLEO",
    "Usuario (base del nucleo)": "USUARIO_EN_LA_BASE_DEL_NUCLEO",
    "Notificacion saliente del nucleo": "NOTIFICACION_SALIENTE_EN_LA_BASE_DEL_NUCLEO",
    "Vehiculo de la Flota": "VEHICULO_EN_MEMORIA",
    "Deposito (un nodo del Grafo vial)": "DEPOSITO_ARTEFACTO",
    "nodo del Grafo vial": "NODO_DEL_GRAFO_VIAL_ARTEFACTO",
}


@dataclasses.dataclass(frozen=True)
class Columna:
    nombre: str
    tipo: str
    no_nula: bool
    defecto: str | None
    posicion_en_la_clave: int
    """0 si no es parte de la clave primaria; si no, su posicion en ella."""


@dataclasses.dataclass(frozen=True)
class ClaveForanea:
    columnas: tuple[str, ...]
    tabla: str
    columnas_destino: tuple[str, ...]
    al_borrar: str


@dataclasses.dataclass(frozen=True)
class Indice:
    nombre: str
    unico: bool
    origen: str
    """`c`: CREATE INDEX; `pk`: de la clave primaria; `u`: de un UNIQUE."""

    columnas: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Tabla:
    nombre: str
    dueno: str | None
    columnas: tuple[Columna, ...]
    claves_foraneas: tuple[ClaveForanea, ...]
    indices: tuple[Indice, ...]

    @property
    def clave_primaria(self) -> tuple[str, ...]:
        en_la_clave = [c for c in self.columnas if c.posicion_en_la_clave]
        return tuple(c.nombre for c in sorted(en_la_clave, key=lambda c: c.posicion_en_la_clave))


def _tablas(conexion: sqlite3.Connection) -> list[str]:
    filas = conexion.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE ? ORDER BY name",
        (PREFIJO_DE_LAS_TABLAS_INTERNAS + "%",),
    )
    return [str(fila[0]) for fila in filas]


def _leer_tabla(conexion: sqlite3.Connection, nombre: str, dueno: str | None) -> Tabla:
    columnas = tuple(
        Columna(
            nombre=str(fila[1]),
            tipo=str(fila[2]),
            no_nula=bool(fila[3]),
            defecto=None if fila[4] is None else str(fila[4]),
            posicion_en_la_clave=int(fila[5]),
        )
        for fila in conexion.execute(f"PRAGMA table_info({nombre})")
    )
    por_id: dict[int, list[tuple[object, ...]]] = {}
    for fila in conexion.execute(f"PRAGMA foreign_key_list({nombre})"):
        por_id.setdefault(int(fila[0]), []).append(fila)
    claves = tuple(
        ClaveForanea(
            columnas=tuple(str(f[3]) for f in sorted(filas, key=lambda f: int(f[1]))),
            tabla=str(filas[0][2]),
            columnas_destino=tuple(str(f[4]) for f in sorted(filas, key=lambda f: int(f[1]))),
            al_borrar=str(filas[0][6]),
        )
        for _, filas in sorted(por_id.items())
    )
    indices = tuple(
        Indice(
            nombre=str(fila[1]),
            unico=bool(fila[2]),
            origen=str(fila[3]),
            columnas=tuple(
                str(info[2]) for info in conexion.execute(f"PRAGMA index_info('{fila[1]}')")
            ),
        )
        for fila in sorted(conexion.execute(f"PRAGMA index_list({nombre})"), key=lambda f: str(f[1]))
    )
    return Tabla(nombre, dueno, columnas, claves, indices)


def duenos_por_tabla(
    temporal: pathlib.Path,
    repositorios: Sequence[tuple[str, Callable[[sqlite3.Connection], object]]] = REPOSITORIOS,
) -> dict[str, str]:
    """Las tablas que crea cada repositorio, instanciado solo sobre una base
    vacia propia. Un componente puede tener mas de un repositorio."""
    duenos: dict[str, str] = {}
    temporal.mkdir(parents=True, exist_ok=True)
    for posicion, (componente, repositorio) in enumerate(repositorios):
        ruta = temporal / f"solo_{posicion}_{componente}.sqlite3"
        conexion = conectar(ruta)
        repositorio(conexion)
        for tabla in _tablas(conexion):
            if tabla in duenos:
                raise RuntimeError(f"La tabla {tabla} la crean {duenos[tabla]} y {componente}")
            duenos[tabla] = componente
        conexion.close()
    return duenos


def introspeccionar(temporal: pathlib.Path) -> list[Tabla]:
    """El esquema de una base recien creada por `construir_contexto`, con las
    tablas de `REPOSITORIOS_FUERA_DEL_ENSAMBLADO`."""
    base = temporal / NOMBRE_DE_LA_BASE
    construir_contexto(
        base_de_datos=base,
        con_escenario=False,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    for _, repositorio in REPOSITORIOS_FUERA_DEL_ENSAMBLADO:
        conexion_propia = conectar(base)
        repositorio(conexion_propia)
        conexion_propia.close()
    duenos = duenos_por_tabla(temporal)
    conexion = sqlite3.connect(base)
    try:
        return [_leer_tabla(conexion, nombre, duenos.get(nombre)) for nombre in _tablas(conexion)]
    finally:
        conexion.close()


def introspeccionar_telemetria(temporal: pathlib.Path) -> list[Tabla]:
    """El esquema de una base de telemetria recien creada por su repositorio,
    como la arma `tools/servir_telemetria.py`."""
    base = temporal / NOMBRE_DE_LA_BASE_DE_TELEMETRIA
    for _, repositorio in REPOSITORIOS_DE_TELEMETRIA:
        conexion_propia = conectar(base)
        repositorio(conexion_propia)
        conexion_propia.close()
    duenos = duenos_por_tabla(temporal / "duenos_de_telemetria", REPOSITORIOS_DE_TELEMETRIA)
    conexion = sqlite3.connect(base)
    try:
        return [_leer_tabla(conexion, nombre, duenos.get(nombre)) for nombre in _tablas(conexion)]
    finally:
        conexion.close()


def _parece_identificador(columna: str) -> bool:
    return columna.endswith(SUFIJO_DE_IDENTIFICADOR) or columna in COLUMNAS_DE_NODO


@dataclasses.dataclass(frozen=True)
class Verificacion:
    referencias: dict[str, tuple[Referencia, ...]]
    sin_clasificar: list[tuple[str, str]]
    """(tabla, columna) con forma de identificador, sin clave foranea y sin
    destino en el codigo declarado: van como pregunta."""

    problemas: list[str]


def verificar_referencias(
    tablas: Sequence[Tabla], mapa: dict[str, tuple[Referencia, ...]] = REFERENCIAS_SEGUN_EL_CODIGO
) -> Verificacion:
    """Contrasta el mapa del codigo con la base: cada columna tiene que existir
    y no estar en una clave foranea."""
    problemas: list[str] = []
    sin_clasificar: list[tuple[str, str]] = []
    por_nombre = {tabla.nombre: tabla for tabla in tablas}
    for nombre, referencias in mapa.items():
        tabla = por_nombre.get(nombre)
        if tabla is None:
            problemas.append(f"el mapa nombra la tabla {nombre}, que la base no tiene")
            continue
        presentes = {c.nombre for c in tabla.columnas}
        con_clave = {c for clave in tabla.claves_foraneas for c in clave.columnas}
        for referencia in referencias:
            for columna in referencia.columnas:
                if columna not in presentes:
                    problemas.append(f"{nombre}.{columna} no existe en la base")
                if columna in con_clave:
                    problemas.append(f"{nombre}.{columna} tiene clave foranea fisica")
    for tabla in tablas:
        con_clave = {c for clave in tabla.claves_foraneas for c in clave.columnas}
        declaradas = {
            c for referencia in mapa.get(tabla.nombre, ()) for c in referencia.columnas
        }
        for columna in tabla.columnas:
            if columna.posicion_en_la_clave and columna.nombre == "id":
                continue
            if (tabla.nombre, columna.nombre) in IDENTIFICADORES_PROPIOS:
                continue
            if (
                _parece_identificador(columna.nombre)
                and columna.nombre not in con_clave
                and columna.nombre not in declaradas
            ):
                sin_clasificar.append((tabla.nombre, columna.nombre))
    return Verificacion(mapa, sin_clasificar, problemas)


def _tipo_mermaid(tipo: str) -> str:
    return tipo.replace(" ", "_") or "SIN_TIPO"


def _cardinalidad_de_la_referencia(tabla: Tabla, referencia: Referencia) -> str:
    nulable = any(
        not c.no_nula for c in tabla.columnas if c.nombre in referencia.columnas
    )
    return "|o" if nulable else "||"


def mermaid(tablas: Sequence[Tabla], verificacion: Verificacion) -> str:
    lineas = ["erDiagram"]
    for tabla in tablas:
        con_clave = {c for clave in tabla.claves_foraneas for c in clave.columnas}
        referidas = {
            c for r in verificacion.referencias.get(tabla.nombre, ()) for c in r.columnas
        }
        lineas.append(f"    {tabla.nombre} {{")
        for columna in tabla.columnas:
            claves = []
            if columna.posicion_en_la_clave:
                claves.append("PK")
            if columna.nombre in con_clave:
                claves.append("FK")
            comentario = ' "referencia sin FK"' if columna.nombre in referidas else ""
            sufijo = f" {', '.join(claves)}" if claves else ""
            lineas.append(f"        {_tipo_mermaid(columna.tipo)} {columna.nombre}{sufijo}{comentario}")
        lineas.append("    }")
    referidas_fuera = {
        ENTIDADES_FUERA_DE_LA_BASE[r.destino]
        for referencias in verificacion.referencias.values()
        for r in referencias
        if r.tabla_destino is None
    }
    for entidad in sorted(referidas_fuera):
        lineas.append(f"    {entidad} {{")
        lineas.append('        fuera_de_la_base nota "no es tabla"')
        lineas.append("    }")
    lineas.append("")
    lineas.append("    %% Claves foraneas fisicas: linea continua.")
    for tabla in tablas:
        for clave in tabla.claves_foraneas:
            etiqueta = ", ".join(clave.columnas)
            lineas.append(f'    {clave.tabla} ||--o{{ {tabla.nombre} : "FK {etiqueta}"')
    lineas.append("")
    lineas.append("    %% Referencias sin clave foranea (ARQ-09 y una interna): linea punteada.")
    for tabla in tablas:
        for referencia in verificacion.referencias.get(tabla.nombre, ()):
            destino = referencia.tabla_destino or ENTIDADES_FUERA_DE_LA_BASE[referencia.destino]
            etiqueta = ", ".join(referencia.columnas)
            lineas.append(
                f"    {destino} {_cardinalidad_de_la_referencia(tabla, referencia)}..o{{ "
                f'{tabla.nombre} : "{etiqueta}"'
            )
    return "\n".join(lineas) + "\n"


def _commit() -> str:
    try:
        salida = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=RAIZ_DEL_REPOSITORIO,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "desconocido"
    return salida.stdout.strip()


def _si_no(valor: bool) -> str:
    return "sí" if valor else "no"


def _encabezado_del_nucleo(tablas: Sequence[Tabla], commit: str) -> list[str]:
    fuera = (
        f", más las tablas de {', '.join(f'`{c}`' for c, _ in REPOSITORIOS_FUERA_DEL_ENSAMBLADO)}, creadas por la herramienta con su repositorio"
        if REPOSITORIOS_FUERA_DEL_ENSAMBLADO
        else ""
    )
    return [
        "# Esquema físico de la base del núcleo",
        "",
        f"Generado por `tools/esquema_fisico_base.py` sobre el commit `{commit}`, por introspección de una base SQLite recién creada con `construir_contexto` (el ensamblado de `tools/servir_api.py`), sin escenario{fuera}. Las columnas, las claves y los índices salen de la base. El componente dueño sale de instanciar cada repositorio solo sobre una base vacía. El destino de las referencias sin clave foránea sale del código, con la fuente citada en cada una.",
        "",
        "## Qué persiste y qué no",
        "",
        "Persisten en la base, cada uno en sus propias tablas y por su propia conexión al mismo archivo (ARQ-09): "
        + "; ".join(
            f"`{componente}` ({', '.join(sorted(t.nombre for t in tablas if t.dueno == componente))})"
            for componente in dict.fromkeys(c for c, _ in REPOSITORIOS)
        )
        + ". "
        + " ".join(
            f"Las tablas de `{componente}` no las crea `construir_contexto`, que todavía no arma ese componente: "
            "la herramienta las crea sobre la misma base con su repositorio SQLite."
            for componente, _ in REPOSITORIOS_FUERA_DEL_ENSAMBLADO
        )
        + " Según `construir_contexto`, no persisten en la base: la Flota, un diccionario de matrícula a capacidad que se arma al arrancar desde la instancia de demostración y se pasa al servicio de planificación; el Depósito, un nodo leído del artefacto JSON del Depósito; el Grafo vial, que cartografía carga del artefacto GraphML; el bus de eventos, en memoria; y el ETA, que la frontera calcula al responder y guarda en memoria por Ruta (la columna `paradas_de_ruta.eta_estimado_s` existe y el motor la deja en nulo). Las posiciones del Vehículo no están en esta base: son del servicio de telemetría, con base propia (`esquema_fisico_telemetria`).",
        "",
    ]


def _encabezado_de_telemetria(commit: str) -> list[str]:
    return [
        "# Esquema físico de la base de telemetría",
        "",
        f"Generado por `tools/esquema_fisico_base.py` sobre el commit `{commit}`, por introspección de una base SQLite recién creada por el repositorio de `seguimiento_flota`, como la arma `tools/servir_telemetria.py` (`telemetria.sqlite3`). Es otra base, en otro proceso (ARQ-52, ARQ-53; 2026-10-27B, T): ninguna de sus tablas tiene clave foránea hacia la base del núcleo, y lo que necesita de él llega por el evento `RutaDespachada` (salida transaccional, ARQ-59) o por el token de sesión, verificado con la clave pública sin abrir la base del núcleo (sección 4.3).",
        "",
        "Retención (sección 3.6): 30 días desde la última posición recibida, porque la Ruta no tiene cierre; se aplica al arrancar y cada 6 horas.",
        "",
    ]


def markdown(
    tablas: Sequence[Tabla], verificacion: Verificacion, commit: str, encabezado: Sequence[str] | None = None
) -> str:
    lineas: list[str] = list(_encabezado_del_nucleo(tablas, commit) if encabezado is None else encabezado)
    if verificacion.problemas:
        lineas += ["## Problemas del mapa de referencias", ""] + [f"- {p}" for p in verificacion.problemas] + [""]
    for tabla in tablas:
        referencias = {c: r for r in verificacion.referencias.get(tabla.nombre, ()) for c in r.columnas}
        con_clave = {c: k for k in tabla.claves_foraneas for c in k.columnas}
        lineas += [
            f"## `{tabla.nombre}`",
            "",
            f"- **Componente dueño:** `{tabla.dueno}`" if tabla.dueno else "- **Componente dueño:** sin identificar",
            f"- **Clave primaria:** ({', '.join(tabla.clave_primaria)})",
            "",
            "| Columna | Tipo | Nula | Por defecto | Clave foránea física | Referencia sin clave foránea |",
            "|---|---|---|---|---|---|",
        ]
        for c in tabla.columnas:
            clave = con_clave.get(c.nombre)
            fk = f"{clave.tabla} ({', '.join(clave.columnas_destino)}), al borrar {clave.al_borrar}" if clave else ""
            ref = referencias.get(c.nombre)
            texto_ref = f"{ref.clase}: {ref.destino}, en {ref.donde_vive} (fuente: {ref.fuente})" if ref else ""
            if (tabla.nombre, c.nombre) in verificacion.sin_clasificar:
                texto_ref = "pregunta: parece un identificador y el código revisado no dice a qué apunta"
            lineas.append(
                f"| `{c.nombre}`{' (PK)' if c.posicion_en_la_clave else ''} | {c.tipo} | {_si_no(not c.no_nula)} | {c.defecto or ''} | {fk} | {texto_ref} |"
            )
        lineas.append("")
        if tabla.indices:
            lineas += ["| Índice | Único | Origen | Columnas |", "|---|---|---|---|"]
            for i in tabla.indices:
                lineas.append(f"| `{i.nombre}` | {_si_no(i.unico)} | {i.origen} | {', '.join(i.columnas)} |")
            lineas.append("")
    return "\n".join(lineas)


def escribir(
    directorio: pathlib.Path,
    tablas: Sequence[Tabla],
    verificacion: Verificacion,
    nombre: str = NOMBRE_DE_LAS_SALIDAS,
    encabezado: Sequence[str] | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    directorio.mkdir(parents=True, exist_ok=True)
    ruta_mmd = directorio / f"{nombre}.mmd"
    ruta_md = directorio / f"{nombre}.md"
    ruta_mmd.write_bytes(mermaid(tablas, verificacion).encode("utf-8"))
    ruta_md.write_bytes(markdown(tablas, verificacion, _commit(), encabezado).encode("utf-8"))
    return ruta_mmd, ruta_md


def _informar(tablas: Sequence[Tabla], verificacion: Verificacion) -> None:
    for tabla in tablas:
        _LOG.info(
            "%s | dueno %s | %d columnas | PK (%s) | %d FK | %d indices",
            tabla.nombre,
            tabla.dueno,
            len(tabla.columnas),
            ", ".join(tabla.clave_primaria),
            len(tabla.claves_foraneas),
            len(tabla.indices),
        )
    for problema in verificacion.problemas:
        _LOG.warning("Mapa de referencias: %s", problema)
    for tabla_con_pregunta, columna in verificacion.sin_clasificar:
        _LOG.warning(
            "Pregunta: %s.%s parece un identificador sin destino en el codigo declarado", tabla_con_pregunta, columna
        )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit.cartografia", "hoppit.tools.servir_api", "hoppit.planificacion_rutas"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_DE_SALIDA)
    args = parser.parse_args(argumentos)
    temporal = pathlib.Path(tempfile.mkdtemp())
    tablas = introspeccionar(temporal)
    verificacion = verificar_referencias(tablas)
    _LOG.info("--- Base del nucleo")
    _informar(tablas, verificacion)
    de_telemetria = introspeccionar_telemetria(temporal / "telemetria")
    verificacion_de_telemetria = verificar_referencias(de_telemetria, REFERENCIAS_DE_TELEMETRIA)
    _LOG.info("--- Base de telemetria")
    _informar(de_telemetria, verificacion_de_telemetria)
    escritas = [
        escribir(args.directorio, tablas, verificacion),
        escribir(
            args.directorio,
            de_telemetria,
            verificacion_de_telemetria,
            NOMBRE_DE_LAS_SALIDAS_DE_TELEMETRIA,
            _encabezado_de_telemetria(_commit()),
        ),
    ]
    for ruta_mmd, ruta_md in escritas:
        _LOG.info("Escritos %s y %s", ruta_mmd, ruta_md)
        if shutil.which("mmdc") is None:
            _LOG.info("mmdc no esta disponible: no se genera el SVG")
        else:
            svg = ruta_mmd.with_suffix(".svg")
            subprocess.run(["mmdc", "-i", str(ruta_mmd), "-o", str(svg)], check=True)
            _LOG.info("Escrito %s", svg)
    # Las conexiones del ensamblado no se cierran (B-150): el recolector las
    # suelta antes de intentar borrar el temporal.
    gc.collect()
    shutil.rmtree(temporal, ignore_errors=True)
    if temporal.exists():
        _LOG.info("El temporal %s no se pudo borrar (B-150).", temporal)
    return 1 if verificacion.problemas or verificacion_de_telemetria.problemas else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
