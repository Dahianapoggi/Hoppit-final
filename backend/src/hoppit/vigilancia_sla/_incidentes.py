"""Registro y consulta de Incidentes (FR-18; CU-07, paso 1; glosario, entidad
8 y relacion 6).

Solo el registro y la consulta: un Incidente no recalcula nada ni cambia
ninguna Ruta ni ninguna Orden (el recalculo es FR-05 y CU-07 desde el paso
2, y no esta construido). Al registrarse se publica `IncidenteDeclarado` en
el bus, todavia sin consumidores.

Atributos, elecciones provisorias de la sesion 2026-10-15A a confirmar por el
equipo:
- tipo, de los ejemplos del glosario: corte de calle, siniestro, desvio,
  cancelacion y condicion climatica adversa;
- alcance, total o parcial ("invalida total o parcialmente");
- descripcion libre;
- Rutas afectadas, cero o mas (relacion 6), que tienen que existir. La
  verificacion se inyecta desde el punto de ensamblado con el puerto de
  Planificacion de Rutas: este componente es una hoja y no lo importa;
- instante de declaracion, del mismo reloj que el historial de la Orden
  (`hoppit.shared.tiempo.instante_actual`), en UTC.

El diagrama del glosario lleva ademas `detectadoEn` y `arcoAfectado`, que
esta eleccion no incluye: el instante es el de la declaracion y no el de la
deteccion, y el arco afectado no se registra.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.eventos import IncidenteDeclarado
from hoppit.shared.ids import IncidenteId, RutaId
from hoppit.shared.tiempo import instante_actual


class TipoDeIncidente(enum.Enum):
    """Los ejemplos del glosario (entidad 8)."""

    CORTE_DE_CALLE = "corte_de_calle"
    SINIESTRO = "siniestro"
    DESVIO = "desvio"
    CANCELACION = "cancelacion"
    CONDICION_CLIMATICA_ADVERSA = "condicion_climatica_adversa"


class AlcanceDelIncidente(enum.Enum):
    """El Incidente invalida total o parcialmente el plan vigente."""

    TOTAL = "total"
    PARCIAL = "parcial"


class TipoDeIncidenteInvalido(ErrorDeDominio):
    """El tipo no es ninguno de los admitidos."""


class AlcanceDeIncidenteInvalido(ErrorDeDominio):
    """El alcance no es total ni parcial."""


class RutasAfectadasInexistentes(ErrorDeDominio):
    """Alguna de las Rutas declaradas como afectadas no existe."""

    def __init__(self, rutas: Sequence[RutaId]) -> None:
        self.rutas = tuple(rutas)
        super().__init__(
            f"Rutas afectadas que no existen: {', '.join(str(r) for r in self.rutas)}"
        )


class IncidenteNoEncontrado(EntidadNoEncontrada):
    """No hay Incidente con ese identificador."""


RADIO_MINIMO_DE_LA_ZONA_M = 20.0
RADIO_MAXIMO_DE_LA_ZONA_M = 1_000.0


class ZonaAfectadaInvalida(ErrorDeDominio):
    """El radio de la zona afectada esta fuera de 20 a 1.000 m, o el punto no
    es una coordenada."""


@dataclasses.dataclass(frozen=True)
class ZonaAfectada:
    """Donde esta el problema (CU-07; encargo 2026-11-23B, bloque K): un punto y
    un radio. El recalculo de este Incidente no usa los tramos que pasan por
    ahi."""

    lat: float
    lon: float
    radio_m: float

    def __post_init__(self) -> None:
        if not (RADIO_MINIMO_DE_LA_ZONA_M <= self.radio_m <= RADIO_MAXIMO_DE_LA_ZONA_M):
            raise ZonaAfectadaInvalida(
                f"El radio de la zona afectada tiene que estar entre {RADIO_MINIMO_DE_LA_ZONA_M:.0f} y "
                f"{RADIO_MAXIMO_DE_LA_ZONA_M:.0f} m: {self.radio_m}"
            )
        if not (-90.0 <= self.lat <= 90.0 and -180.0 <= self.lon <= 180.0):
            raise ZonaAfectadaInvalida(f"El punto de la zona afectada no es una coordenada: {self.lat}, {self.lon}")


@dataclasses.dataclass(frozen=True)
class Incidente:
    """Evento disruptivo que invalida total o parcialmente el plan vigente
    (glosario, entidad 8)."""

    id: IncidenteId
    tipo: TipoDeIncidente
    alcance: AlcanceDelIncidente
    descripcion: str
    rutas_afectadas: tuple[RutaId, ...]
    declarado_en: dt.datetime
    zona: ZonaAfectada | None = None
    """Opcional (bloque K, 2026-11-23B): sin zona, el recalculo es el de
    siempre."""


def tipo_de_incidente(valor: str) -> TipoDeIncidente:
    try:
        return TipoDeIncidente(valor)
    except ValueError as error:
        admitidos = ", ".join(t.value for t in TipoDeIncidente)
        raise TipoDeIncidenteInvalido(f"Tipo de Incidente '{valor}': no es ninguno de {admitidos}") from error


def alcance_del_incidente(valor: str) -> AlcanceDelIncidente:
    try:
        return AlcanceDelIncidente(valor)
    except ValueError as error:
        raise AlcanceDeIncidenteInvalido(f"Alcance '{valor}': tiene que ser total o parcial") from error


def sin_repetir(rutas: Iterable[str]) -> tuple[RutaId, ...]:
    """Las Rutas en el orden en que se declararon, sin repetir: declarar dos
    veces la misma Ruta no la afecta dos veces."""
    vistas: dict[str, None] = {}
    for ruta in rutas:
        vistas.setdefault(str(ruta), None)
    return tuple(RutaId(ruta) for ruta in vistas)


def nuevo_identificador() -> IncidenteId:
    """Un uuid, como en el diagrama del glosario."""
    return IncidenteId(str(uuid.uuid4()))


class RepositorioIncidentes(Protocol):
    def guardar(self, incidente: Incidente) -> None: ...
    def obtener(self, id_incidente: IncidenteId) -> Incidente: ...
    def listar(self) -> Sequence[Incidente]: ...


RutasInexistentes = Callable[[Sequence[RutaId]], Sequence[RutaId]]
"""Dadas unas Rutas, las que no existen. La provee el punto de ensamblado,
con el puerto de Planificacion de Rutas."""


class ServicioIncidentes:
    def __init__(
        self,
        repositorio: RepositorioIncidentes,
        bus: Bus,
        rutas_inexistentes: RutasInexistentes,
        reloj: Callable[[], dt.datetime] = instante_actual,
        generar_identificador: Callable[[], IncidenteId] = nuevo_identificador,
    ) -> None:
        self._repositorio = repositorio
        self._bus = bus
        self._rutas_inexistentes = rutas_inexistentes
        self._reloj = reloj
        self._generar_identificador = generar_identificador

    def declarar(
        self, tipo: str, alcance: str, descripcion: str, rutas_afectadas: Sequence[str],
        zona: ZonaAfectada | None = None,
    ) -> Incidente:
        """Registra un Incidente y publica `IncidenteDeclarado`. No toca
        ninguna Ruta ni ninguna Orden."""
        rutas = sin_repetir(rutas_afectadas)
        incidente = Incidente(
            id=self._generar_identificador(),
            tipo=tipo_de_incidente(tipo),
            alcance=alcance_del_incidente(alcance),
            descripcion=descripcion,
            rutas_afectadas=rutas,
            declarado_en=self._reloj(),
            zona=zona,
        )
        faltan = tuple(self._rutas_inexistentes(rutas)) if rutas else ()
        if faltan:
            raise RutasAfectadasInexistentes(faltan)
        self._repositorio.guardar(incidente)
        self._bus.publicar(
            IncidenteDeclarado(
                incidente_id=str(incidente.id),
                tipo_de_incidente=incidente.tipo.value,
                alcance=incidente.alcance.value,
                rutas_afectadas=tuple(str(r) for r in incidente.rutas_afectadas),
            )
        )
        return incidente

    def obtener(self, id_incidente: str) -> Incidente:
        return self._repositorio.obtener(IncidenteId(id_incidente))

    def listar(self) -> tuple[Incidente, ...]:
        """Por instante de declaracion y, a igual instante, por
        identificador."""
        return tuple(sorted(self._repositorio.listar(), key=lambda i: (i.declarado_en, i.id)))
