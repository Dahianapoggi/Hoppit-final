"""Reemplazo de una Ruta en ejecucion (CU-07, pasos 2 a 6 y flujos 3a y 5a;
glosario, ciclo de vida de la Ruta, estado 5 "Reemplazada"; decisiones D8 a
D13 del encargo 2026-11-20A).

Modelo (D8, el del glosario aprobado en B-240): la Ruta reemplazada se
conserva tal como se planifico, pasa a "Reemplazada" (estado terminal) y
conserva sus Paradas cumplidas; la Ruta nueva, para el mismo Vehiculo y en el
mismo Plan de Distribucion, nace En ejecucion con la Parada en curso fija y
primera y las pendientes recalculadas. Las Ordenes no cumplidas se mudan a
la Ruta nueva por el evento `RutaReemplazada`, sin cambiar su estado, con el
asiento de reasignacion en su historial (D9).

Solo se implementa el estado "Reemplazada": el resto del ciclo de vida de la
Ruta (Planificada, En ejecucion, Cerrada, Descartada) sigue sin implementar,
y que una Ruta este en ejecucion se sigue leyendo en sus Ordenes (B-221).
Lo nuevo se guarda en tablas propias (`propuestas_de_reemplazo`,
`reemplazos_de_ruta`), sin columnas nuevas en las de Rutas, para que una base
anterior siga abriendo y para que el cuerpo de una Ruta no reemplazada no
cambie (D11, NFR-07).

Definiciones de esta implementacion (decisiones de la sesion 2026-11-20A):
- **Parada cumplida:** todas sus Ordenes en Completada o Fallida y todavia en
  esta Ruta. **Pendiente:** todas sus Ordenes En transito y en esta Ruta.
- **Parada en curso:** la pendiente de menor secuencia (el Vehiculo va hacia
  la proxima Parada del plan) [supuesto].
- **Origen del recalculo (D12):** la posicion de telemetria anclada al nodo
  mas cercano de la mayor componente fuerte si su antiguedad esta entre 0 y
  `ANTIGUEDAD_MAXIMA_S`; si no, el nodo de la Parada en curso (la convencion
  del simulador y de la capa dinamica del 19A); si no hubiera Parada en
  curso, la ultima cumplida o el Deposito. Como sin Paradas pendientes no hay
  nada que recalcular, las dos ultimas solo informan.
- **Efecto del Incidente (D13):** el registro de Incidentes (FR-18) no modela
  ningun efecto sobre el grafo (no guarda el arco afectado), asi que el
  recalculo usa el grafo sin cambios. El Incidente queda como motivo.
- **Flujo 3a:** sin camino en el grafo entre el origen, las pendientes y el
  Deposito, no hay alternativa factible: se informa y el plan se conserva.

Rotulo: lo que este modulo decide son las Paradas que el Vehiculo todavia no
visito; ninguna cifra sale de datos reales.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from typing import Any, Final, Protocol, cast

from hoppit.cartografia.puerto import CaminoInexistente, MatrizDeCostos, ZonaCircular
from hoppit.gestion_ordenes.puerto import EstadoOrden, ServicioGestionOrdenes
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.eventos import AsignacionEnRuta, RutaReemplazada
from hoppit.shared.ids import OrdenId, RutaId

from ._ejecucion import NotificacionSaliente, RepositorioDeEjecucion
from ._replanificacion import costo_del_camino, secuenciar_pendientes
from ._repositorio import RepositorioRutas, RutaPersistida
from .motor.base import ParadaPlaneada, Ruta

ANTIGUEDAD_MAXIMA_S: Final[float] = 120.0
"""Posicion de telemetria aceptable como origen (D12): la aplicacion captura
cada 10 s y envia cada 15 s, y 120 s es la tolerancia de reloj de Seguimiento
de Flota [supuesto, el mismo del 19A]."""

MOTIVO_INCIDENTE: Final[str] = "incidente"
MOTIVO_CONTINGENCIA: Final[str] = "contingencia"
MOTIVO_DESVIO: Final[str] = "desvio"
"""Reruteo por desvio de la Ruta (D17 a D19 del encargo 2026-11-22A)."""
MOTIVO_RIESGO_VIAL: Final[str] = "riesgo_vial"
"""Reruteo por riesgo vial con aprobacion del operador (D31 del encargo 2026-11-23E)."""
TIPO_RUTA_REEMPLAZADA: Final[str] = "RutaReemplazada"
TIPO_REEMPLAZO: Final[str] = "reemplazo"
TIPO_CONTINGENCIA: Final[str] = "contingencia"
SECUENCIA_DEL_PRIMER_EVENTO: Final[int] = 1
_PREFIJO_RUTA: Final[str] = "ruta"
_DIGITOS_ID_RUTA: Final[int] = 12
_PREFIJO_PROPUESTA: Final[str] = "prop"
_CODIFICACION: Final[str] = "utf-8"
_TERMINADOS: Final[frozenset[EstadoOrden]] = frozenset({EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA})


class FuenteDelOrigen(enum.Enum):
    TELEMETRIA = "telemetria"
    PARADA_EN_CURSO = "parada_en_curso"
    ULTIMA_CUMPLIDA = "ultima_cumplida"
    DEPOSITO = "deposito"


class EstadoDePropuesta(enum.Enum):
    PENDIENTE = "pendiente"
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"
    VENCIDA = "vencida"
    """Una propuesta por riesgo vial pendiente cuando aparece la de un desvio
    o de un Incidente: prevalece la otra (D31, encargo 2026-11-23E)."""


# --- Errores -----------------------------------------------------------------------------


class RutaNoEnEjecucion(ErrorDeDominio):
    """La Ruta no esta en ejecucion: no se despacho, o ya no le queda ninguna
    Parada pendiente."""


class RutaYaReemplazada(ErrorDeDominio):
    """La Ruta ya fue reemplazada (estado terminal): se opera sobre la vigente."""

    def __init__(self, ruta_id: RutaId, vigente: RutaId) -> None:
        self.ruta_id, self.vigente = ruta_id, vigente
        super().__init__(f"La Ruta {ruta_id} fue reemplazada por {vigente}: opere sobre la vigente")


class SinAlternativaFactible(ErrorDeDominio):
    """CU-07, flujo 3a: no hay camino en el grafo para recorrer lo pendiente y
    volver al Deposito. El plan vigente se conserva."""


class ZonaContieneAlVehiculo(ErrorDeDominio):
    """CU-07 con zona afectada (D33, encargo 2026-11-23E): el origen del
    recalculo (la posicion del Vehiculo anclada, o la Parada en curso) esta
    dentro de la zona: algun arco que sale de el quedaria excluido. No es 3a:
    el punto declarado es el del Vehiculo, no el del corte."""


class PropuestaNoEncontrada(EntidadNoEncontrada):
    """No hay propuesta de reemplazo con ese identificador."""


class PropuestaYaResuelta(ErrorDeDominio):
    """La propuesta ya fue aprobada o rechazada."""


class PropuestaVencida(ErrorDeDominio):
    """El avance de la Ruta cambio desde la propuesta (se declaro un
    resultado, o la Ruta se reemplazo por otra via): hay que pedir otra."""


# --- Datos ---------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PosicionDelVehiculo:
    """La ultima posicion que informo telemetria, ya leida por quien llama."""

    latitud: float
    longitud: float
    antiguedad_s: float


@dataclasses.dataclass(frozen=True)
class AvanceDeRuta:
    """CU-07, paso 2: el estado de avance vigente de una Ruta en ejecucion."""

    ruta_id: RutaId
    cumplidas: tuple[int, ...]
    en_curso: int | None
    pendientes: tuple[int, ...]
    """Las pendientes sin la en curso, en el orden de su secuencia vigente."""

    ultima_cumplida: int | None

    def huella(self) -> str:
        canonico = json.dumps([str(self.ruta_id), self.cumplidas, self.en_curso, self.pendientes])
        return hashlib.sha256(canonico.encode(_CODIFICACION)).hexdigest()


@dataclasses.dataclass(frozen=True)
class OrigenDelRecalculo:
    nodo: int
    fuente: FuenteDelOrigen
    antiguedad_s: float | None = None
    distancia_del_anclaje_m: float | None = None


@dataclasses.dataclass(frozen=True)
class CompromisoDeRiesgoVial:
    """D31 (encargo 2026-11-23E): lo que la propuesta por riesgo vial gana y
    cuesta, medido sobre los caminos reales (largo, tiempo base y riesgo de los
    arcos elegidos) de lo pendiente, del origen al Deposito. La exposicion es
    la suma de la frecuencia anual predicha de hechos viales de esos tramos: un
    indicador del tramo, no del Vehiculo, y no una deteccion en tiempo real."""

    mu_m: float
    """El mu de la propuesta (metros por hecho anual predicho)."""
    distancia_vigente_m: float
    distancia_propuesta_m: float
    minutos_vigentes: float
    minutos_propuestos: float
    exposicion_vigente: float
    exposicion_propuesta: float
    baja_minima_de_exposicion: float
    suba_maxima_de_distancia: float
    tabla: str
    tabla_sha256: str

    @property
    def baja_de_exposicion(self) -> float:
        return 0.0 if self.exposicion_vigente <= 0 else 1.0 - self.exposicion_propuesta / self.exposicion_vigente

    @property
    def suba_de_distancia(self) -> float:
        return 0.0 if self.distancia_vigente_m <= 0 else self.distancia_propuesta_m / self.distancia_vigente_m - 1.0

    @property
    def cumple_el_umbral(self) -> bool:
        return (self.exposicion_vigente > 0 and self.baja_de_exposicion >= self.baja_minima_de_exposicion
                and self.suba_de_distancia <= self.suba_maxima_de_distancia)


@dataclasses.dataclass(frozen=True)
class UmbralDeRiesgoVial:
    """Supuesto declarado del encargo 2026-11-23E (D31), sin dato de operacion:
    se propone solo si la exposicion predicha de lo pendiente baja al menos un
    20 % y su distancia sube como mucho un 5 %."""

    baja_minima_de_exposicion: float = 0.20
    suba_maxima_de_distancia: float = 0.05


@dataclasses.dataclass(frozen=True)
class PropuestaDeReemplazo:
    """CU-07, paso 4: el plan resultante y lo necesario para mostrar su
    diferencia contra el vigente. Las secuencias son las de la Ruta vigente,
    en el orden de cada plan, con la Parada en curso primera."""

    id: str
    ruta_id: RutaId
    motivo: str
    referencia: str
    """El Incidente (CU-07) o la contingencia (CU-11) que la motiva."""

    creada_en: dt.datetime
    estado: EstadoDePropuesta
    origen: OrigenDelRecalculo
    cumplidas: tuple[int, ...]
    en_curso: int | None
    secuencia_vigente: tuple[int, ...]
    secuencia_propuesta: tuple[int, ...]
    distancia_vigente_m: float
    distancia_propuesta_m: float
    metodo: str
    segundos_de_calculo: float
    huella_del_avance: str
    resuelta_en: dt.datetime | None = None
    ruta_nueva: RutaId | None = None
    fija_en_curso: bool = True
    """False en el reruteo por desvio (D19): la Parada en curso tambien se
    recalcula desde la posicion."""
    riesgo_vial: CompromisoDeRiesgoVial | None = None
    """Solo con motivo `riesgo_vial` (D31, encargo 2026-11-23E)."""
    motivo_del_vencimiento: str | None = None
    """Por que vencio (estado `vencida`; D35, encargo 2026-11-23F). None en las demas."""
    mu_m: float | None = None
    """El mu con que se recalculo (D36, encargo 2026-11-23F): el de la propuesta por
    riesgo vial, o el de la Ruta si nacio de una; None con los caminos del proceso."""

    @property
    def diferencia_m(self) -> float:
        return self.distancia_propuesta_m - self.distancia_vigente_m

    @property
    def orden_sin_cambios(self) -> bool:
        """El recalculo dio la misma secuencia que la vigente (D19: la
        propuesta lo dice)."""
        return self.secuencia_propuesta == self.secuencia_vigente


@dataclasses.dataclass(frozen=True)
class ReemplazoDeRuta:
    """CU-07, paso 6: lo que queda asentado de la replanificacion."""

    ruta_reemplazada: RutaId
    ruta_nueva: RutaId
    motivo: str
    referencia: str
    propuesta_id: str | None
    instante: dt.datetime
    origen_nodo: int
    fuente_del_origen: FuenteDelOrigen
    paradas_cumplidas: tuple[int, ...]
    """Secuencias de la Ruta reemplazada cumplidas al reemplazarse: las que
    conserva (glosario, estado 5)."""
    mu_m: float | None = None
    """El mu de los caminos de la Ruta nueva (D36, encargo 2026-11-23F): el de la
    propuesta por riesgo vial que la origino, o el heredado de la reemplazada;
    None con los caminos del proceso."""


# --- Serializacion (para SQLite) -----------------------------------------------------------


def _propuesta_a_json(p: PropuestaDeReemplazo) -> str:
    d = dataclasses.asdict(p)
    d["estado"] = p.estado.value
    d["origen"]["fuente"] = p.origen.fuente.value
    for clave in ("creada_en", "resuelta_en"):
        d[clave] = None if d[clave] is None else d[clave].isoformat()
    return json.dumps(d, sort_keys=True)


def _propuesta_desde_dict(d: dict[str, Any]) -> PropuestaDeReemplazo:
    o = d["origen"]
    return PropuestaDeReemplazo(
        id=d["id"], ruta_id=RutaId(d["ruta_id"]), motivo=d["motivo"], referencia=d["referencia"],
        creada_en=dt.datetime.fromisoformat(d["creada_en"]), estado=EstadoDePropuesta(d["estado"]),
        origen=OrigenDelRecalculo(o["nodo"], FuenteDelOrigen(o["fuente"]), o["antiguedad_s"],
                                  o["distancia_del_anclaje_m"]),
        cumplidas=tuple(d["cumplidas"]), en_curso=d["en_curso"], secuencia_vigente=tuple(d["secuencia_vigente"]),
        secuencia_propuesta=tuple(d["secuencia_propuesta"]), distancia_vigente_m=d["distancia_vigente_m"],
        distancia_propuesta_m=d["distancia_propuesta_m"], metodo=d["metodo"],
        segundos_de_calculo=d["segundos_de_calculo"], huella_del_avance=d["huella_del_avance"],
        resuelta_en=None if d["resuelta_en"] is None else dt.datetime.fromisoformat(d["resuelta_en"]),
        ruta_nueva=None if d["ruta_nueva"] is None else RutaId(d["ruta_nueva"]),
        fija_en_curso=d.get("fija_en_curso", True),
        riesgo_vial=None if d.get("riesgo_vial") is None else CompromisoDeRiesgoVial(**d["riesgo_vial"]),
        motivo_del_vencimiento=d.get("motivo_del_vencimiento"),
        mu_m=d.get("mu_m"),
    )


def _reemplazo_a_json(r: ReemplazoDeRuta) -> str:
    d = dataclasses.asdict(r)
    d["instante"] = r.instante.isoformat()
    d["fuente_del_origen"] = r.fuente_del_origen.value
    return json.dumps(d, sort_keys=True)


def _reemplazo_desde_dict(d: dict[str, Any]) -> ReemplazoDeRuta:
    return ReemplazoDeRuta(
        ruta_reemplazada=RutaId(d["ruta_reemplazada"]), ruta_nueva=RutaId(d["ruta_nueva"]), motivo=d["motivo"],
        referencia=d["referencia"], propuesta_id=d["propuesta_id"],
        instante=dt.datetime.fromisoformat(d["instante"]), origen_nodo=d["origen_nodo"],
        fuente_del_origen=FuenteDelOrigen(d["fuente_del_origen"]), paradas_cumplidas=tuple(d["paradas_cumplidas"]),
        mu_m=d.get("mu_m"),
    )


# --- Repositorios --------------------------------------------------------------------------


class RepositorioDeReemplazos(Protocol):
    def guardar_propuesta(self, propuesta: PropuestaDeReemplazo) -> None: ...
    def propuesta(self, propuesta_id: str) -> PropuestaDeReemplazo: ...
    def propuestas_de(self, ruta_id: RutaId) -> tuple[PropuestaDeReemplazo, ...]: ...
    def guardar_reemplazo(self, reemplazo: ReemplazoDeRuta) -> None: ...
    def reemplazo_de_la_reemplazada(self, ruta_id: RutaId) -> ReemplazoDeRuta | None: ...
    def reemplazo_de_la_nueva(self, ruta_id: RutaId) -> ReemplazoDeRuta | None: ...


class RepositorioDeReemplazosEnMemoria:
    def __init__(self) -> None:
        self._propuestas: dict[str, PropuestaDeReemplazo] = {}
        self._por_reemplazada: dict[str, ReemplazoDeRuta] = {}
        self._por_nueva: dict[str, ReemplazoDeRuta] = {}
        self._cerrojo = threading.Lock()

    def guardar_propuesta(self, propuesta: PropuestaDeReemplazo) -> None:
        with self._cerrojo:
            self._propuestas[propuesta.id] = propuesta

    def propuesta(self, propuesta_id: str) -> PropuestaDeReemplazo:
        try:
            return self._propuestas[propuesta_id]
        except KeyError as error:
            raise PropuestaNoEncontrada(f"Propuesta {propuesta_id} no existe") from error

    def propuestas_de(self, ruta_id: RutaId) -> tuple[PropuestaDeReemplazo, ...]:
        return tuple(sorted((p for p in self._propuestas.values() if p.ruta_id == ruta_id),
                            key=lambda p: (p.creada_en, p.id)))

    def guardar_reemplazo(self, reemplazo: ReemplazoDeRuta) -> None:
        with self._cerrojo:
            self._por_reemplazada[str(reemplazo.ruta_reemplazada)] = reemplazo
            self._por_nueva[str(reemplazo.ruta_nueva)] = reemplazo

    def reemplazo_de_la_reemplazada(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._por_reemplazada.get(str(ruta_id))

    def reemplazo_de_la_nueva(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._por_nueva.get(str(ruta_id))


_ESQUEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS propuestas_de_reemplazo (
    id         TEXT PRIMARY KEY,
    ruta_id    TEXT NOT NULL,
    creada_en  TEXT NOT NULL,
    contenido  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reemplazos_de_ruta (
    ruta_reemplazada  TEXT PRIMARY KEY,
    ruta_nueva        TEXT NOT NULL UNIQUE,
    contenido         TEXT NOT NULL
);
"""


class RepositorioDeReemplazosSQLite:
    """Dos tablas del componente de Rutas (ARQ-09), con el contenido en JSON:
    son registros que se escriben una vez y se leen enteros."""

    def __init__(self, conexion: sqlite3.Connection) -> None:
        self._conexion = conexion
        self._cerrojo = threading.Lock()
        with self._cerrojo:
            self._conexion.executescript(_ESQUEMA)
            self._conexion.commit()

    def guardar_propuesta(self, propuesta: PropuestaDeReemplazo) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO propuestas_de_reemplazo (id, ruta_id, creada_en, contenido) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET contenido = excluded.contenido",
                (propuesta.id, str(propuesta.ruta_id), propuesta.creada_en.isoformat(), _propuesta_a_json(propuesta)),
            )

    def propuesta(self, propuesta_id: str) -> PropuestaDeReemplazo:
        with self._cerrojo:
            fila = self._conexion.execute(
                "SELECT contenido FROM propuestas_de_reemplazo WHERE id = ?", (propuesta_id,)
            ).fetchone()
        if fila is None:
            raise PropuestaNoEncontrada(f"Propuesta {propuesta_id} no existe")
        return _propuesta_desde_dict(json.loads(fila[0]))

    def propuestas_de(self, ruta_id: RutaId) -> tuple[PropuestaDeReemplazo, ...]:
        with self._cerrojo:
            filas = self._conexion.execute(
                "SELECT contenido FROM propuestas_de_reemplazo WHERE ruta_id = ? ORDER BY creada_en, id",
                (str(ruta_id),),
            ).fetchall()
        return tuple(_propuesta_desde_dict(json.loads(f[0])) for f in filas)

    def guardar_reemplazo(self, reemplazo: ReemplazoDeRuta) -> None:
        with self._cerrojo, self._conexion:
            self._conexion.execute(
                "INSERT INTO reemplazos_de_ruta (ruta_reemplazada, ruta_nueva, contenido) VALUES (?, ?, ?)",
                (str(reemplazo.ruta_reemplazada), str(reemplazo.ruta_nueva), _reemplazo_a_json(reemplazo)),
            )

    def _uno(self, columna: str, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        with self._cerrojo:
            fila = self._conexion.execute(
                f"SELECT contenido FROM reemplazos_de_ruta WHERE {columna} = ?", (str(ruta_id),)
            ).fetchone()
        return None if fila is None else _reemplazo_desde_dict(json.loads(fila[0]))

    def reemplazo_de_la_reemplazada(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._uno("ruta_reemplazada", ruta_id)

    def reemplazo_de_la_nueva(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._uno("ruta_nueva", ruta_id)


# --- Notificacion hacia telemetria (D10) ---------------------------------------------------


def notificacion_de_reemplazo(
    ruta_nueva: RutaId, ruta_reemplazada: RutaId, vehiculo_id: str, transportista_id: str | None,
    instante: dt.datetime,
) -> NotificacionSaliente:
    """Primer evento de la Ruta nueva hacia telemetria: abre su ejecucion,
    como el despacho, y le dice a que Ruta reemplaza, para que los reportes
    que sigan llegando con la Ruta reemplazada se atribuyan a la vigente."""
    return NotificacionSaliente(
        evento_id=str(uuid.uuid4()),
        tipo=TIPO_RUTA_REEMPLAZADA,
        agregado_id=str(ruta_nueva),
        secuencia_del_agregado=SECUENCIA_DEL_PRIMER_EVENTO,
        cuerpo={
            "ruta_id": str(ruta_nueva),
            "reemplaza_a": str(ruta_reemplazada),
            "vehiculo_id": vehiculo_id,
            "transportista_id": transportista_id,
            "despachada_en": instante.isoformat(),
        },
        creado_en=instante,
        proximo_intento_en=instante,
    )


# --- Servicio --------------------------------------------------------------------------------


class Contexto(Protocol):
    """Lo que el recalculo le pide a Cartografia (por el adaptador de contexto)."""

    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos: ...
    def matriz_de_costos_evitando(self, nodos: Sequence[int], zona: ZonaCircular) -> MatrizDeCostos: ...
    def matriz_de_costos_con_riesgo(self, nodos: Sequence[int], mu: float,
                                    zona: ZonaCircular | None = None) -> MatrizDeCostos: ...
    def arcos_en_la_zona(self, zona: ZonaCircular) -> frozenset[tuple[int, int, int]]: ...
    def arcos_de_salida(self, nodo: int) -> frozenset[tuple[int, int, int]]: ...
    def capa_de_riesgo(self) -> tuple[float, str, str] | None: ...
    def anclar_en_la_componente(self, lat: float, lon: float) -> tuple[int, float]: ...


def identificador_derivado(original: RutaId, tipo: str, contador: int) -> RutaId:
    """Derivado, no sorteado (NFR-07; bloque 1 del encargo 2026-11-22A): de la
    Ruta original de la cadena, del tipo (`reemplazo` o `contingencia`) y de un
    contador, con el mismo formato que las del motor. Dos corridas con la
    misma entrada dan los mismos identificadores, aunque el Incidente o la
    contingencia que las motivo tengan otro."""
    canonico = json.dumps({"ruta_original": str(original), "tipo": tipo, "contador": contador}, sort_keys=True)
    return RutaId(f"{_PREFIJO_RUTA}-{hashlib.sha256(canonico.encode(_CODIFICACION)).hexdigest()[:_DIGITOS_ID_RUTA]}")


class ServicioDeReemplazo:
    """CU-07 desde el paso 2 (y la activacion de CU-11, que reusa el
    reemplazo). Lo arma `crear_servicio` en `puerto.py`."""

    def __init__(
        self,
        rutas: RepositorioRutas,
        ordenes: ServicioGestionOrdenes,
        contexto: Contexto,
        bus: Bus,
        ejecucion: RepositorioDeEjecucion,
        repositorio: RepositorioDeReemplazos,
        reloj: Callable[[], dt.datetime],
        generar_identificador: Callable[[], str] = lambda: f"{_PREFIJO_PROPUESTA}-{uuid.uuid4().hex[:12]}",
    ) -> None:
        self._rutas = rutas
        self._ordenes = ordenes
        self._contexto = contexto
        self._bus = bus
        self._ejecucion = ejecucion
        self._repositorio = repositorio
        self._reloj = reloj
        self._generar_identificador = generar_identificador
        # Revisar las pendientes de una Ruta y guardar una propuesta es una sola
        # operacion: una por riesgo vial no se guarda si ya hay otra pendiente,
        # y la de un desvio o un Incidente vence a la de riesgo (D31, 23E).
        self._candado_de_propuestas = threading.RLock()
        # D36 (2026-11-23F): el mu de las Rutas que no nacen de un reemplazo (las
        # de contingencia); lo asigna el ensamblado.
        self.mu_de_otras: Callable[[RutaId], float | None] | None = None

    # lectura ------------------------------------------------------------------------------
    def reemplazo_de_la_reemplazada(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._repositorio.reemplazo_de_la_reemplazada(ruta_id)

    def reemplazo_de_la_nueva(self, ruta_id: RutaId) -> ReemplazoDeRuta | None:
        return self._repositorio.reemplazo_de_la_nueva(ruta_id)

    def original(self, ruta_id: RutaId) -> tuple[RutaId, int]:
        """La Ruta original de la cadena de reemplazos de `ruta_id` y cuantos
        reemplazos la separan de ella."""
        actual, pasos = ruta_id, 0
        while (r := self._repositorio.reemplazo_de_la_nueva(actual)) is not None:
            actual, pasos = r.ruta_reemplazada, pasos + 1
        return actual, pasos

    def vigente(self, ruta_id: RutaId) -> RutaId:
        """La Ruta vigente del Vehiculo de `ruta_id`, siguiendo los reemplazos."""
        actual = ruta_id
        while (r := self._repositorio.reemplazo_de_la_reemplazada(actual)) is not None:
            actual = r.ruta_nueva
        return actual

    def propuesta(self, propuesta_id: str) -> PropuestaDeReemplazo:
        return self._repositorio.propuesta(propuesta_id)

    def propuestas_de(self, ruta_id: RutaId) -> tuple[PropuestaDeReemplazo, ...]:
        return self._repositorio.propuestas_de(ruta_id)

    # CU-07, paso 2 ---------------------------------------------------------------------------
    def avance(self, ruta_id: RutaId) -> AvanceDeRuta:
        persistida = self._rutas.obtener(ruta_id)
        reemplazo = self._repositorio.reemplazo_de_la_reemplazada(ruta_id)
        if reemplazo is not None:
            raise RutaYaReemplazada(ruta_id, self.vigente(ruta_id))
        cumplidas: list[int] = []
        pendientes: list[int] = []
        ultima: tuple[dt.datetime, int] | None = None
        for parada in persistida.ruta.paradas:
            ordenes = [self._ordenes.consultar(o) for o in parada.ordenes]
            en_esta = [o for o in ordenes if o.ruta_id == ruta_id]
            if not en_esta:
                continue
            if all(o.estado in _TERMINADOS for o in en_esta):
                cumplidas.append(parada.secuencia)
                marca = max(o.historial[-1].marca_temporal for o in en_esta)
                if ultima is None or marca > ultima[0]:
                    ultima = (marca, parada.secuencia)
            elif any(o.estado is EstadoOrden.EN_TRANSITO for o in en_esta):
                # Una Parada a medio cumplir (una Orden cerrada por un reporte,
                # bloque 1 del encargo 2026-11-22A) sigue pendiente por las otras.
                pendientes.append(parada.secuencia)
        if not pendientes:
            raise RutaNoEnEjecucion(
                f"La Ruta {ruta_id} no tiene Paradas pendientes en ejecucion: no se despacho o ya se cumplio"
            )
        return AvanceDeRuta(ruta_id, tuple(cumplidas), pendientes[0], tuple(pendientes[1:]),
                            None if ultima is None else ultima[1])

    def _origen(self, persistida: RutaPersistida, avance: AvanceDeRuta,
                posicion: PosicionDelVehiculo | None) -> OrigenDelRecalculo:
        if posicion is not None and 0.0 <= posicion.antiguedad_s <= ANTIGUEDAD_MAXIMA_S:
            nodo, distancia = self._contexto.anclar_en_la_componente(posicion.latitud, posicion.longitud)
            return OrigenDelRecalculo(nodo, FuenteDelOrigen.TELEMETRIA, posicion.antiguedad_s, distancia)
        antiguedad = None if posicion is None else posicion.antiguedad_s
        paradas = persistida.ruta.paradas
        if avance.en_curso is not None:
            return OrigenDelRecalculo(paradas[avance.en_curso - 1].nodo, FuenteDelOrigen.PARADA_EN_CURSO, antiguedad)
        if avance.ultima_cumplida is not None:  # pragma: no cover  (sin pendientes no se llega)
            return OrigenDelRecalculo(paradas[avance.ultima_cumplida - 1].nodo, FuenteDelOrigen.ULTIMA_CUMPLIDA,
                                      antiguedad)
        return OrigenDelRecalculo(persistida.deposito, FuenteDelOrigen.DEPOSITO, antiguedad)  # pragma: no cover

    # CU-07, pasos 3 y 4 ----------------------------------------------------------------------
    def proponer(self, ruta_id: RutaId, motivo: str, referencia: str,
                 posicion: PosicionDelVehiculo | None, fijar_en_curso: bool = True,
                 evitando: ZonaCircular | None = None) -> PropuestaDeReemplazo:
        """Recalcula las pendientes desde el origen y guarda la propuesta, sin
        tocar nada vigente. Levanta `SinAlternativaFactible` (3a). Con
        `fijar_en_curso` falso (reruteo por desvio, D19) la Parada en curso
        tambien se recalcula: el Vehiculo ya no va hacia ella."""
        antes = time.perf_counter()
        if motivo == MOTIVO_INCIDENTE:
            # D35 (2026-11-23F): un Incidente sobre la Ruta vence la propuesta por
            # riesgo vial pendiente, termine o no en 3a.
            self.vencer_las_de_riesgo(ruta_id, f"Incidente {referencia} sobre la Ruta")
        persistida = self._rutas.obtener(ruta_id)
        avance = self.avance(ruta_id)
        origen = self._origen(persistida, avance, posicion)
        paradas = persistida.ruta.paradas
        nodo = {p.secuencia: p.nodo for p in paradas}
        assert avance.en_curso is not None
        fija = nodo[avance.en_curso]
        pendientes = [nodo[s] for s in avance.pendientes]
        nodos = list(dict.fromkeys([origen.nodo, fija, *pendientes, persistida.deposito]))
        # D36 (2026-11-23F): una Ruta nacida de una propuesta por riesgo vial se
        # recalcula con el mismo costo generalizado (su mu).
        mu = self.mu_del_trazado(ruta_id)
        try:
            matriz = (self._contexto.matriz_de_costos(nodos) if mu is None
                      else self._contexto.matriz_de_costos_con_riesgo(nodos, mu))
        except CaminoInexistente as error:
            raise SinAlternativaFactible(
                f"No hay camino en el grafo para recorrer lo pendiente de la Ruta {ruta_id} y volver al "
                f"Deposito: se conserva el plan vigente ({error})"
            ) from error

        def costo_vigente(a: int, b: int) -> float:
            return 0.0 if a == b else matriz.distancia_m(a, b)

        vigente_m = costo_del_camino(origen.nodo, fija, pendientes, persistida.deposito, costo_vigente)

        def generalizado(a: int, b: int) -> float:
            return 0.0 if a == b else matriz.costo_m(a, b)

        costo = costo_vigente if mu is None else generalizado
        para_medir = matriz
        if evitando is not None:
            # CU-07 con zona afectada (encargo 2026-11-23B, bloque K): la
            # propuesta se calcula sin los arcos de la zona; la distancia
            # vigente sigue siendo la de la red completa, asi la diferencia
            # muestra el rodeo. Sin camino para una pendiente o el regreso: 3a.
            # D33 (2026-11-23E), corregida por D34 (2026-11-23F): la zona contiene
            # al Vehiculo solo si corta todos los arcos que salen del origen (no
            # puede salir); si queda alguno, la propuesta se calcula sin la zona.
            salidas = self._contexto.arcos_de_salida(origen.nodo)
            if salidas and salidas <= self._contexto.arcos_en_la_zona(evitando):
                if origen.fuente is FuenteDelOrigen.TELEMETRIA:
                    raise ZonaContieneAlVehiculo(
                        "La zona afectada contiene la posición del Vehículo: declarar el punto del corte, no la "
                        "posición del Vehículo")
                # Sin posicion reciente el origen es la Parada en curso (consejo del 23E, J18).
                raise ZonaContieneAlVehiculo(
                    "La zona afectada contiene el origen del recálculo (la Parada en curso: no hay posición "
                    "reciente del Vehículo): declarar el punto del corte")
            try:
                sin_zona = (self._contexto.matriz_de_costos_evitando(nodos, evitando) if mu is None
                            else self._contexto.matriz_de_costos_con_riesgo(nodos, mu, evitando))
            except CaminoInexistente as error:
                raise SinAlternativaFactible(
                    f"Sin los tramos de la zona afectada no hay camino para recorrer lo pendiente de la Ruta "
                    f"{ruta_id} y volver al Deposito: se conserva el plan vigente ({error})"
                ) from error

            def costo_sin_zona(a: int, b: int) -> float:
                return 0.0 if a == b else (sin_zona.distancia_m(a, b) if mu is None else sin_zona.costo_m(a, b))

            costo = costo_sin_zona
            para_medir = sin_zona
        propuesta_seq, recalculada = self._secuenciar(origen, avance, nodo, persistida.deposito, costo,
                                                      fijar_en_curso)
        distancia_propuesta = recalculada.costo_m
        if mu is not None:
            # El costo optimizado es el generalizado; la distancia es la de los caminos.
            recorrido = [origen.nodo, *(nodo[s] for s in propuesta_seq), persistida.deposito]
            distancia_propuesta = sum(para_medir.distancia_m(a, b) for a, b in zip(recorrido, recorrido[1:]) if a != b)
        propuesta = PropuestaDeReemplazo(
            id=self._generar_identificador(), ruta_id=ruta_id, motivo=motivo, referencia=referencia,
            creada_en=self._reloj(), estado=EstadoDePropuesta.PENDIENTE, origen=origen,
            cumplidas=avance.cumplidas, en_curso=avance.en_curso,
            secuencia_vigente=(avance.en_curso, *avance.pendientes),
            secuencia_propuesta=propuesta_seq,
            distancia_vigente_m=vigente_m, distancia_propuesta_m=distancia_propuesta, metodo=recalculada.metodo,
            segundos_de_calculo=time.perf_counter() - antes, huella_del_avance=avance.huella(),
            fija_en_curso=fijar_en_curso, mu_m=mu,
        )
        with self._candado_de_propuestas:
            # D31 (2026-11-23E): un desvio o un Incidente vencen la propuesta
            # por riesgo vial pendiente de la misma Ruta; prevalece la otra.
            if motivo != MOTIVO_RIESGO_VIAL:
                self.vencer_las_de_riesgo(ruta_id, f"prevalece la propuesta {propuesta.id} ({motivo} {referencia})")
            self._repositorio.guardar_propuesta(propuesta)
        return propuesta

    def _secuenciar(self, origen: OrigenDelRecalculo, avance: AvanceDeRuta, nodo: dict[int, int], deposito: int,
                    costo: Callable[[int, int], float], fijar_en_curso: bool) -> tuple[tuple[int, ...], Any]:
        """(secuencias propuestas, recalculo) de lo pendiente desde el origen."""
        assert avance.en_curso is not None
        fija = nodo[avance.en_curso]
        pendientes = [nodo[s] for s in avance.pendientes]
        if fijar_en_curso:
            recalculada = secuenciar_pendientes(origen.nodo, fija, pendientes, deposito, costo)
            por_nodo = {nodo[s]: s for s in avance.pendientes}
            return (avance.en_curso, *(por_nodo[n] for n in recalculada.pendientes)), recalculada
        # Sin Parada fija: el origen hace de fija, a costo cero; si el origen
        # es el nodo de una pendiente, esa va primera (con la desigualdad
        # triangular de los caminos minimos, empezar por ella es optimo).
        todos = [fija, *pendientes]
        if origen.nodo in todos:
            resto = [n for n in todos if n != origen.nodo]
            recalculada = secuenciar_pendientes(origen.nodo, origen.nodo, resto, deposito, costo)
            orden_de_nodos: tuple[int, ...] = (origen.nodo, *recalculada.pendientes)
        else:
            recalculada = secuenciar_pendientes(origen.nodo, origen.nodo, todos, deposito, costo)
            orden_de_nodos = tuple(recalculada.pendientes)
        por_nodo = {nodo[s]: s for s in (avance.en_curso, *avance.pendientes)}
        return tuple(por_nodo[n] for n in orden_de_nodos), recalculada

    # D31 (encargo 2026-11-23E) ---------------------------------------------------------------
    def proponer_por_riesgo_vial(self, ruta_id: RutaId, posicion: PosicionDelVehiculo | None, mu: float,
                                 umbral: UmbralDeRiesgoVial = UmbralDeRiesgoVial()) -> PropuestaDeReemplazo | None:
        """La propuesta de reemplazo por riesgo vial, o None si no cumple el
        umbral, si no hay tabla de riesgo para el grafo, si no hay camino o si
        la Ruta ya tiene una propuesta pendiente de cualquier motivo.

        Es el recalculo del desvio (D19: desde la posicion, sin la Parada en
        curso fija) con costo `largo + mu x riesgo` sobre las pendientes y el
        regreso. Lo vigente se mide con los caminos que dibuja la Ruta (los del
        proceso, o los de su mu si nacio de otra propuesta por riesgo vial) y lo
        propuesto con los de `mu`; en los dos, distancia, tiempo y exposicion
        son las de los arcos de esos caminos."""
        capa = self.capa_de_riesgo()
        if capa is None:
            return None
        mu_del_proceso, tabla, tabla_sha256 = capa
        # Lo vigente se mide con los caminos que la Ruta dibuja: los de su mu si
        # nacio de otra propuesta por riesgo vial (visto en las pruebas: medida
        # con los del proceso, la Ruta nueva volvia a proponer lo mismo).
        mu_de_la_ruta = self.mu_del_trazado(ruta_id)
        mu_vigente = mu_del_proceso if mu_de_la_ruta is None else mu_de_la_ruta
        antes = time.perf_counter()
        persistida = self._rutas.obtener(ruta_id)
        avance = self.avance(ruta_id)
        origen = self._origen(persistida, avance, posicion)
        nodo = {p.secuencia: p.nodo for p in persistida.ruta.paradas}
        assert avance.en_curso is not None
        vigentes = [nodo[avance.en_curso], *(nodo[s] for s in avance.pendientes)]
        nodos = list(dict.fromkeys([origen.nodo, *vigentes, persistida.deposito]))
        try:
            con_mu = self._contexto.matriz_de_costos_con_riesgo(nodos, mu)
            del_proceso = self._contexto.matriz_de_costos_con_riesgo(nodos, mu_vigente)
        except CaminoInexistente:
            return None

        def costo(a: int, b: int) -> float:
            return 0.0 if a == b else con_mu.costo_m(a, b)

        secuencias, recalculada = self._secuenciar(origen, avance, nodo, persistida.deposito, costo, False)

        def medir(matriz: MatrizDeCostos, recorrido: Sequence[int]) -> tuple[float, float, float]:
            largo = tiempo = riesgo = 0.0
            for a, b in zip(recorrido, recorrido[1:]):
                if a == b:
                    continue
                camino = matriz.camino(a, b)
                largo += camino.distancia_m
                tiempo += camino.tiempo_base_s
                riesgo += camino.riesgo_hechos_por_anio or 0.0
            return largo, tiempo / 60.0, riesgo

        d_v, t_v, e_v = medir(del_proceso, [origen.nodo, *vigentes, persistida.deposito])
        d_p, t_p, e_p = medir(con_mu, [origen.nodo, *(nodo[s] for s in secuencias), persistida.deposito])
        compromiso = CompromisoDeRiesgoVial(
            mu_m=mu, distancia_vigente_m=d_v, distancia_propuesta_m=d_p, minutos_vigentes=t_v, minutos_propuestos=t_p,
            exposicion_vigente=e_v, exposicion_propuesta=e_p,
            baja_minima_de_exposicion=umbral.baja_minima_de_exposicion,
            suba_maxima_de_distancia=umbral.suba_maxima_de_distancia, tabla=tabla, tabla_sha256=tabla_sha256,
        )
        if not compromiso.cumple_el_umbral:
            return None
        propuesta = PropuestaDeReemplazo(
            id=self._generar_identificador(), ruta_id=ruta_id, motivo=MOTIVO_RIESGO_VIAL,
            referencia=f"riesgo-vial-{ruta_id}", creada_en=self._reloj(), estado=EstadoDePropuesta.PENDIENTE,
            origen=origen, cumplidas=avance.cumplidas, en_curso=avance.en_curso,
            secuencia_vigente=(avance.en_curso, *avance.pendientes), secuencia_propuesta=secuencias,
            distancia_vigente_m=d_v, distancia_propuesta_m=d_p, metodo=recalculada.metodo,
            segundos_de_calculo=time.perf_counter() - antes, huella_del_avance=avance.huella(),
            fija_en_curso=False, riesgo_vial=compromiso, mu_m=mu,
        )
        with self._candado_de_propuestas:
            if any(x.estado is EstadoDePropuesta.PENDIENTE for x in self._repositorio.propuestas_de(ruta_id)):
                return None
            self._repositorio.guardar_propuesta(propuesta)
        return propuesta

    def vencer_si_cambio_el_avance(self, propuesta: PropuestaDeReemplazo) -> PropuestaDeReemplazo:
        """Una propuesta pendiente cuyo avance ya no es el de la Ruta (se declaro
        un resultado, o la Ruta se reemplazo por otra via) queda vencida: no se
        podria aprobar (`PropuestaVencida`) y no tiene que seguir a la vista
        (D31, 2026-11-23E). Devuelve la propuesta como quedo."""
        if propuesta.estado is not EstadoDePropuesta.PENDIENTE:
            return propuesta
        try:
            vigente = self.avance(propuesta.ruta_id).huella() == propuesta.huella_del_avance
        except (RutaYaReemplazada, RutaNoEnEjecucion):
            vigente = False
        if vigente:
            return propuesta
        with self._candado_de_propuestas:
            # Se relee dentro del candado (consejo del 23E, E3): una aprobacion
            # o un rechazo entre la lectura y aca no se pisan.
            actual = self._repositorio.propuesta(propuesta.id)
            if actual.estado is not EstadoDePropuesta.PENDIENTE:
                return actual
            vencida = dataclasses.replace(actual, estado=EstadoDePropuesta.VENCIDA, resuelta_en=self._reloj(),
                                          motivo_del_vencimiento="cambio el avance de la Ruta")
            self._repositorio.guardar_propuesta(vencida)
        return vencida

    # D35 (encargo 2026-11-23F) ---------------------------------------------------------------
    def vencer_las_de_riesgo(self, ruta_id: RutaId, motivo_del_vencimiento: str) -> tuple[PropuestaDeReemplazo, ...]:
        """Vence las propuestas por riesgo vial pendientes de la Ruta, con su motivo."""
        vencidas = []
        with self._candado_de_propuestas:
            for previa in self._repositorio.propuestas_de(ruta_id):
                if previa.motivo == MOTIVO_RIESGO_VIAL and previa.estado is EstadoDePropuesta.PENDIENTE:
                    vencida = dataclasses.replace(previa, estado=EstadoDePropuesta.VENCIDA, resuelta_en=self._reloj(),
                                                  motivo_del_vencimiento=motivo_del_vencimiento)
                    self._repositorio.guardar_propuesta(vencida)
                    vencidas.append(vencida)
        return tuple(vencidas)

    def arcos_propuestos(self, propuesta: PropuestaDeReemplazo) -> frozenset[tuple[int, int]]:
        """Los arcos (origen, destino) del camino propuesto por riesgo vial:
        del origen por las Paradas en el orden propuesto al Deposito, con los
        caminos de su mu (D35)."""
        if propuesta.riesgo_vial is None:
            return frozenset()
        persistida = self._rutas.obtener(propuesta.ruta_id)
        nodo = {p.secuencia: p.nodo for p in persistida.ruta.paradas}
        recorrido = [propuesta.origen.nodo, *(nodo[s] for s in propuesta.secuencia_propuesta), persistida.deposito]
        matriz = self._contexto.matriz_de_costos_con_riesgo(list(dict.fromkeys(recorrido)), propuesta.riesgo_vial.mu_m)
        return frozenset((a.origen, a.destino) for x, y in zip(recorrido, recorrido[1:]) if x != y
                         for a in matriz.camino(x, y).arcos)

    def vencer_si_cruza_zonas(self, ruta_id: RutaId,
                              zonas: Sequence[tuple[str, ZonaCircular]]) -> tuple[PropuestaDeReemplazo, ...]:
        """D35: vence la propuesta por riesgo vial pendiente de la Ruta cuyo
        camino propuesto cruza la zona de un Incidente vigente, de esta Ruta o
        de otra. `zonas`: (Incidente, zona)."""
        if not zonas:
            return ()
        vencidas = []
        for propuesta in self._repositorio.propuestas_de(ruta_id):
            if propuesta.motivo != MOTIVO_RIESGO_VIAL or propuesta.estado is not EstadoDePropuesta.PENDIENTE:
                continue
            arcos = self.arcos_propuestos(propuesta)
            for incidente, zona in zonas:
                if any((u, v) in arcos for u, v, _ in self._contexto.arcos_en_la_zona(zona)):
                    with self._candado_de_propuestas:
                        actual = self._repositorio.propuesta(propuesta.id)
                        if actual.estado is EstadoDePropuesta.PENDIENTE:
                            vencida = dataclasses.replace(
                                actual, estado=EstadoDePropuesta.VENCIDA, resuelta_en=self._reloj(),
                                motivo_del_vencimiento=f"el camino propuesto cruza la zona del Incidente {incidente}")
                            self._repositorio.guardar_propuesta(vencida)
                            vencidas.append(vencida)
                    break
        return tuple(vencidas)

    def capa_de_riesgo(self) -> tuple[float, str, str] | None:
        """(mu del proceso, tabla, SHA-256) si hay tabla de riesgo vial
        verificada para el grafo cargado; None si no, o si el contexto no la
        informa (D31, 2026-11-23E)."""
        consultar = getattr(self._contexto, "capa_de_riesgo", None)
        return None if consultar is None else cast("tuple[float, str, str] | None", consultar())

    def mu_del_trazado(self, ruta_id: RutaId) -> float | None:
        """El mu de los caminos de una Ruta nacida de una propuesta por riesgo
        vial aprobada (D31, 2026-11-23E); None en las demas, que se dibujan con
        los caminos del proceso."""
        reemplazo = self._repositorio.reemplazo_de_la_nueva(ruta_id)
        if reemplazo is None:
            # Una Ruta de contingencia preparada desde una con mu (D36).
            return None if self.mu_de_otras is None else self.mu_de_otras(ruta_id)
        if reemplazo.mu_m is not None:
            return reemplazo.mu_m
        # Reemplazos guardados antes de D36: el mu de la propuesta por riesgo.
        if reemplazo.motivo != MOTIVO_RIESGO_VIAL or reemplazo.propuesta_id is None:
            return None
        compromiso = self._repositorio.propuesta(reemplazo.propuesta_id).riesgo_vial
        return None if compromiso is None else compromiso.mu_m

    # CU-07, flujo 5a ---------------------------------------------------------------------------
    def rechazar(self, propuesta_id: str) -> PropuestaDeReemplazo:
        # Bajo el candado de las propuestas (consejo del 23E, E2): no se cruza
        # con el vencimiento de una propuesta por riesgo vial.
        with self._candado_de_propuestas:
            propuesta = self._pendiente(propuesta_id)
            rechazada = dataclasses.replace(propuesta, estado=EstadoDePropuesta.RECHAZADA, resuelta_en=self._reloj())
            self._repositorio.guardar_propuesta(rechazada)
            return rechazada

    def _pendiente(self, propuesta_id: str) -> PropuestaDeReemplazo:
        propuesta = self._repositorio.propuesta(propuesta_id)
        if propuesta.estado is not EstadoDePropuesta.PENDIENTE:
            raise PropuestaYaResuelta(f"La propuesta {propuesta_id} ya fue {propuesta.estado.value}")
        return propuesta

    # CU-07, pasos 5 y 6 ------------------------------------------------------------------------
    def aprobar(self, propuesta_id: str) -> ReemplazoDeRuta:
        # Bajo el candado de las propuestas (consejo del 23E, E2): una propuesta
        # por riesgo vial que un desvio o un Incidente vencen no se aplica igual.
        with self._candado_de_propuestas:
            return self._aprobar(propuesta_id)

    def _aprobar(self, propuesta_id: str) -> ReemplazoDeRuta:
        propuesta = self._pendiente(propuesta_id)
        try:
            avance = self.avance(propuesta.ruta_id)
        except (RutaYaReemplazada, RutaNoEnEjecucion) as error:
            raise PropuestaVencida(f"La propuesta {propuesta_id} vencio: {error}") from error
        if avance.huella() != propuesta.huella_del_avance:
            raise PropuestaVencida(
                f"La propuesta {propuesta_id} vencio: el avance de la Ruta {propuesta.ruta_id} cambio desde que se "
                f"calculo. Pida otra."
            )
        persistida = self._rutas.obtener(propuesta.ruta_id)
        reemplazo = self.reemplazar(persistida, propuesta.secuencia_propuesta, propuesta.distancia_propuesta_m,
                                    propuesta.motivo, propuesta.referencia, propuesta.id, propuesta.origen,
                                    avance.cumplidas, mu_m=propuesta.mu_m)
        self._repositorio.guardar_propuesta(dataclasses.replace(
            propuesta, estado=EstadoDePropuesta.APROBADA, resuelta_en=reemplazo.instante,
            ruta_nueva=reemplazo.ruta_nueva))
        return reemplazo

    def reemplazar(self, persistida: RutaPersistida, secuencias: Sequence[int], distancia_m: float, motivo: str,
                   referencia: str, propuesta_id: str | None, origen: OrigenDelRecalculo,
                   cumplidas: Sequence[int], vehiculo_id: str | None = None,
                   capacidad: int | None = None, mu_m: float | None = None) -> ReemplazoDeRuta:
        """Crea la Ruta nueva con las Paradas de `secuencias` (de la Ruta
        reemplazada, en ese orden), la persiste en el mismo Plan, muda las
        Ordenes, abre su ejecucion con el transportista de la reemplazada y
        avisa a telemetria. CU-11 la usa con otro Vehiculo (el de reserva)."""
        vieja = persistida.ruta
        instante = self._reloj()
        por_secuencia = {p.secuencia: p for p in vieja.paradas}
        # Solo viajan las Ordenes En transito de esta Ruta: las ya cerradas de
        # una Parada a medio cumplir se quedan en la reemplazada (bloque 1).
        provisorias = [
            dataclasses.replace(por_secuencia[s], ordenes=tuple(
                o for o in por_secuencia[s].ordenes
                if (x := self._ordenes.consultar(o)).ruta_id == vieja.id and x.estado is EstadoOrden.EN_TRANSITO))
            for s in secuencias
        ]
        raiz, pasos = self.original(vieja.id)
        nueva_id = identificador_derivado(raiz, TIPO_REEMPLAZO, pasos + 1)
        paradas = tuple(
            ParadaPlaneada(ruta_id=nueva_id, secuencia=i, nodo=p.nodo, ordenes=p.ordenes, eta_estimado_s=None)
            for i, p in enumerate(provisorias, start=1)
        )
        demanda = sum(self._ordenes.consultar(o).demanda for p in paradas for o in p.ordenes)
        vehiculo = vehiculo_id if vehiculo_id is not None else str(vieja.vehiculo_id)
        nueva = Ruta(id=nueva_id, vehiculo_id=vehiculo, paradas=paradas,  # type: ignore[arg-type]
                     distancia_m=distancia_m, carga=demanda,
                     capacidad_vehiculo=capacidad if capacidad is not None else vieja.capacidad_vehiculo)
        self._rutas.persistir(nueva, persistida.fecha_planificacion, persistida.deposito, persistida.plan_id)
        evento = RutaReemplazada(
            ruta_reemplazada=str(vieja.id), ruta_nueva=str(nueva_id),
            asignaciones=tuple(AsignacionEnRuta(orden_id=OrdenId(str(o)), secuencia=p.secuencia)
                               for p in paradas for o in p.ordenes),
            motivo=motivo,
        )
        try:
            self._bus.publicar(evento)
        except Exception:
            self._rutas.eliminar(nueva_id)
            raise
        reemplazo = ReemplazoDeRuta(
            ruta_reemplazada=vieja.id, ruta_nueva=nueva_id, motivo=motivo, referencia=referencia,
            propuesta_id=propuesta_id, instante=instante, origen_nodo=origen.nodo,
            fuente_del_origen=origen.fuente, paradas_cumplidas=tuple(cumplidas), mu_m=mu_m,
        )
        self._repositorio.guardar_reemplazo(reemplazo)
        transportista = self._ejecucion.transportista_de(vieja.id)
        self._ejecucion.registrar_despacho(
            nueva_id, transportista,
            notificacion_de_reemplazo(nueva_id, vieja.id, vehiculo, transportista, instante),
        )
        return reemplazo
