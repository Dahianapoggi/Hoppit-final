"""Contratos de eventos de dominio que cruzan containers via el bus.

Viven aca -- no dentro de cada container -- porque son, por definicion, el
contrato compartido entre quien publica y quien consume. Cada evento nuevo
que cruce el bus se agrega aca; nunca se redefine de forma independiente en
cada extremo, porque eso es exactamente lo que produce que un lado y otro
se desincronicen sin que ningun test lo detecte.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from hoppit.shared.ids import OrdenId


@dataclasses.dataclass(frozen=True)
class AsignacionEnRuta:
    """Donde quedo una Orden dentro de la Ruta que la incluye (RB-09).

    La secuencia viaja con la Orden y no aparte porque es lo que hace
    utilizable el evento: "Planificada" significa, segun el glosario, que la
    Orden quedo asignada a una Parada dentro de una Ruta, y un evento que
    nombrara solo la Ruta obligaria a quien lo consume a volver a buscar en
    que Parada cayo cada una.
    """

    orden_id: OrdenId
    secuencia: int
    """Numero de Parada dentro de la Ruta, empezando en 1 (ARQ-05)."""


@dataclasses.dataclass(frozen=True)
class RutaPublicada:
    """Publicado por Planificacion de Rutas (arista e16, ARQ-N1). Consumido
    por Gestion de Ordenes (ARQ-31) para ejecutar la transicion
    Creada -> Planificada (FR-03, paso 18 de ARQ-P1).

    Nombra una sola Ruta, la de un Vehiculo: una planificacion de N Vehiculos
    publica N eventos. `ruta_id` resuelve contra el Repositorio de Rutas, y
    `ordenes_incluidas` son las Ordenes que quedaron en esa Ruta -- no las de
    toda la corrida."""

    tipo: str = dataclasses.field(default="ruta_publicada", init=False)
    ruta_id: str = ""
    asignaciones: tuple[AsignacionEnRuta, ...] = ()
    """Las Ordenes de esta Ruta con la Parada en la que quedo cada una."""

    @property
    def ordenes_incluidas(self) -> tuple[OrdenId, ...]:
        """Solo las Ordenes, derivadas de las asignaciones.

        Derivada y no un segundo campo: dos listas de lo mismo pueden
        discrepar, y cual de las dos seria la verdadera no tendria respuesta.
        """
        return tuple(asignacion.orden_id for asignacion in self.asignaciones)


@dataclasses.dataclass(frozen=True)
class RutaInvalidada:
    """Publicado por Planificacion de Rutas cuando una Ruta deja de existir.

    Simetrico de `RutaPublicada` y con la misma direccion: Planificacion
    publica, Gestion de Ordenes consume y ajusta el estado de sus Ordenes
    (ARQ-31). Que la desasignacion viaje por el bus y no por una llamada
    directa es lo que mantiene a Gestion de Ordenes como unica propietaria
    del agregado Orden.

    Nombra todas las Ordenes que la Ruta contenia, no solo la que motivo la
    invalidacion: al desaparecer la Ruta, ninguna de ellas sigue asignada.
    """

    tipo: str = dataclasses.field(default="ruta_invalidada", init=False)
    ruta_id: str = ""
    ordenes_liberadas: tuple[OrdenId, ...] = ()


@dataclasses.dataclass(frozen=True)
class RutaDespachada:
    """Publicado por Planificacion de Rutas cuando el operador despacha una
    Ruta (FR-16): empieza su ejecucion. Gestion de Ordenes lo consume y lleva
    cada Orden de la Ruta de Planificada a En transito (ARQ-31).

    La Ruta no tiene estados (glosario; B-96, abierta): que una Ruta este
    despachada se lee en el estado de sus Ordenes, y el momento del despacho
    queda en el historial de cada una."""

    tipo: str = dataclasses.field(default="ruta_despachada", init=False)
    ruta_id: str = ""
    ordenes: tuple[OrdenId, ...] = ()


class ResultadoDeParada(enum.Enum):
    """Lo que el transportista declara al intentar una Parada (CU-06)."""

    CUMPLIDA = "cumplida"
    FALLIDA = "fallida"


class MedioDeDeclaracion(enum.Enum):
    """Por donde llego la declaracion de un resultado (ARQ-65). Hoy solo hay
    `web`: la mesa de control. `aplicacion` es la del transportista, que no
    esta construida."""

    WEB = "web"
    APLICACION = "aplicacion"


@dataclasses.dataclass(frozen=True)
class Declarante:
    """Quien declaro un resultado de Parada (ARQ-65; B-243, punto 1).

    El usuario sale del token, en la frontera. El rol se guarda tal como era
    al declarar, porque el del usuario puede cambiar despues. Son valores y no
    tipos de Identidad: este contrato es de `shared` y no importa a nadie."""

    usuario_id: str
    rol: str
    medio: MedioDeDeclaracion = MedioDeDeclaracion.WEB


@dataclasses.dataclass(frozen=True)
class ResultadoDeParadaDeclarado:
    """Publicado por Planificacion de Rutas cuando se declara el resultado de
    una Parada (CU-06, pasos 1 a 3). Gestion de Ordenes lo consume y lleva
    cada Orden de la Parada de En transito a Completada o a Fallida, con el
    declarante en la transicion.

    `declarante` es None solo si quien declara no paso por la frontera (las
    pruebas del nucleo y `bootstrap`)."""

    tipo: str = dataclasses.field(default="resultado_de_parada_declarado", init=False)
    ruta_id: str = ""
    secuencia: int = 0
    ordenes: tuple[OrdenId, ...] = ()
    resultado: ResultadoDeParada = ResultadoDeParada.CUMPLIDA
    declarante: Declarante | None = None
    declarado_en: dt.datetime | None = None
    """CU-06, 4a (encargo 2026-11-23B, D29): la hora que envio la aplicacion.
    Con ella, Gestion de Ordenes asienta la hora del hecho y la de recepcion,
    y aplica la prioridad del transportista; sin ella, todo es como antes."""

    recibido_en: dt.datetime | None = None
    sin_conexion: bool = False


@dataclasses.dataclass(frozen=True)
class IncidenteDeclarado:
    """Publicado por Vigilancia de SLA e Incidentes cuando el operador
    registra un Incidente (FR-18; CU-07, paso 1). Todavia sin consumidores:
    el recalculo de las Rutas afectadas (FR-05, CU-07 desde el paso 2) no
    esta construido.

    `tipo` es el del evento, como en los demas; el del Incidente va en
    `tipo_de_incidente`."""

    tipo: str = dataclasses.field(default="incidente_declarado", init=False)
    incidente_id: str = ""
    tipo_de_incidente: str = ""
    alcance: str = ""
    rutas_afectadas: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class RutaReemplazada:
    """Publicado por Planificacion de Rutas cuando el operador aprueba el
    recalculo de una Ruta en ejecucion (CU-07, paso 6; glosario, ciclo de vida
    de la Ruta, estado 5). Gestion de Ordenes lo consume y muda cada Orden no
    cumplida de la Ruta reemplazada a la nueva, **sin cambiar su estado**
    (siguen En transito), con un asiento de reasignacion en su historial
    (CU-11, paso 6; decision D9 del encargo 2026-11-20A).

    `asignaciones` son las Ordenes que se mudan, con la Parada de la Ruta nueva
    en la que quedo cada una. Las Ordenes de las Paradas ya cumplidas no
    viajan: se quedan en la Ruta reemplazada, que las conserva."""

    tipo: str = dataclasses.field(default="ruta_reemplazada", init=False)
    ruta_reemplazada: str = ""
    ruta_nueva: str = ""
    asignaciones: tuple[AsignacionEnRuta, ...] = ()
    motivo: str = ""
    """Por que se reemplazo: `incidente` (CU-07) o `contingencia` (CU-11)."""


@dataclasses.dataclass(frozen=True)
class ParadasTrasladadas:
    """Publicado por Planificacion de Rutas al activarse una contingencia
    (CU-11, paso 6): las Ordenes de las Paradas comprometidas pasan de la Ruta
    de origen a la Ruta de contingencia del Vehiculo de reserva, **sin cambiar
    su estado**, con el mismo asiento de reasignacion que el reemplazo (D9 del
    encargo 2026-11-20A). La Ruta de origen no se reemplaza: sigue con las
    Paradas que le quedan."""

    tipo: str = dataclasses.field(default="paradas_trasladadas", init=False)
    ruta_origen: str = ""
    ruta_destino: str = ""
    asignaciones: tuple[AsignacionEnRuta, ...] = ()
    motivo: str = ""
