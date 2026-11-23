"""Reruteo por desvio de la Ruta (decisiones D17 a D19 del encargo 2026-11-22A;
el disparador por desvio de B-280, punto 5).

- **Deteccion (D18):** la posicion de telemetria, anclada al nodo mas cercano
  de la mayor componente fuerte, esta a mas de `umbral_m` del trazado de la
  Ruta vigente en el tramo que se esta recorriendo (los arcos del recorrido
  planificado entre la ultima Parada cumplida, o el origen de la Ruta, y la
  Parada en curso) en `consecutivos` reportes consecutivos; un reporte con mas
  de `antiguedad_maxima_s` no cuenta. El desvio esta vigente mientras los
  ultimos `consecutivos` reportes que cuentan esten afuera [decision de la
  sesion: una salida y vuelta que ya termino no dispara].
- **Evaluacion:** es una funcion de los reportes recientes, sin estado: el
  nucleo no recibe las posiciones (las guarda telemetria, en otro proceso), y
  las lee al evaluar las alertas (decision de la sesion, declarada).
- **Propuesta (D19):** al detectarse, una propuesta de reemplazo de CU-07 con
  todas las pendientes recalculadas desde la posicion anclada, sin la Parada
  en curso fija. Una por desvio: no se genera otra mientras haya una pendiente
  de la misma Ruta, ni hasta `espera_tras_una_propuesta_s` despues de la
  ultima [supuesto], para no proponer en cada consulta.

Rotulo: el umbral de 300 m y los dos reportes son un supuesto sin dato de CABA.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import math
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Final, Protocol

from ._reemplazo import (
    ANTIGUEDAD_MAXIMA_S,
    MOTIVO_DESVIO,
    PosicionDelVehiculo,
    PropuestaDeReemplazo,
    RutaNoEnEjecucion,
    RutaYaReemplazada,
    ServicioDeReemplazo,
)
from ._repositorio import RepositorioRutas
from hoppit.shared.ids import RutaId

UMBRAL_DE_DESVIO_M: Final[float] = 300.0
REPORTES_CONSECUTIVOS: Final[int] = 2
ESPERA_TRAS_UNA_PROPUESTA_S: Final[float] = 600.0
INTERVALO_DE_VIGILANCIA_S: Final[float] = 15.0
"""Cada cuanto el proceso del nucleo revisa las Rutas de la jornada, el envio de la
aplicacion (D18)."""
_LOG = logging.getLogger(__name__)
RADIO_TERRESTRE_M: Final[float] = 6_371_008.8
"""Radio medio (IUGG); la distancia a un trazado de pocos kilometros se mide
en una proyeccion equirectangular local, con error despreciable a esa escala."""


@dataclasses.dataclass(frozen=True)
class ParametrosDeDesvio:
    umbral_m: float = UMBRAL_DE_DESVIO_M
    consecutivos: int = REPORTES_CONSECUTIVOS
    antiguedad_maxima_s: float = ANTIGUEDAD_MAXIMA_S
    espera_tras_una_propuesta_s: float = ESPERA_TRAS_UNA_PROPUESTA_S


@dataclasses.dataclass(frozen=True)
class ReporteDePosicion:
    latitud: float
    longitud: float
    capturada_en: dt.datetime


@dataclasses.dataclass(frozen=True)
class DesvioDetectado:
    ruta_id: RutaId
    vehiculo_id: str
    distancia_m: float
    """Del ultimo reporte anclado al trazado del tramo."""

    desde: dt.datetime
    """La captura del primero de los reportes consecutivos afuera."""

    ultimo: ReporteDePosicion


class ContextoDeDesvio(Protocol):
    def anclar_en_la_componente(self, lat: float, lon: float) -> tuple[int, float]: ...
    def coordenadas_de(self, nodo: int) -> tuple[float, float]: ...
    def trazado_de(self, nodos: Sequence[int]) -> tuple[tuple[float, float], ...]: ...
    def trazado_con_riesgo_de(self, nodos: Sequence[int], mu: float) -> tuple[tuple[float, float], ...]: ...


def distancia_a_polilinea_m(punto: tuple[float, float], polilinea: Sequence[tuple[float, float]]) -> float:
    """Distancia minima de `punto` a la polilinea (pares latitud, longitud)."""
    lat0 = math.radians(punto[0])

    def plano(p: tuple[float, float]) -> tuple[float, float]:
        return (math.radians(p[1] - punto[1]) * math.cos(lat0) * RADIO_TERRESTRE_M,
                math.radians(p[0] - punto[0]) * RADIO_TERRESTRE_M)

    puntos = [plano(p) for p in polilinea]
    if len(puntos) == 1:
        return math.hypot(*puntos[0])
    mejor = math.inf
    for (ax, ay), (bx, by) in zip(puntos, puntos[1:]):
        dx, dy = bx - ax, by - ay
        largo2 = dx * dx + dy * dy
        t = 0.0 if largo2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / largo2))
        mejor = min(mejor, math.hypot(ax + t * dx, ay + t * dy))
    return mejor


class VigilanteDeDesvio:
    """Lo arma `crear_servicio` en `puerto.py`, sobre el servicio de reemplazo."""

    def __init__(self, rutas: RepositorioRutas, reemplazo: ServicioDeReemplazo, contexto: ContextoDeDesvio,
                 reloj: Callable[[], dt.datetime], parametros: ParametrosDeDesvio = ParametrosDeDesvio()) -> None:
        self._rutas = rutas
        self._reemplazo = reemplazo
        self._contexto = contexto
        self._reloj = reloj
        self.parametros = parametros
        # Revisar si hay una pendiente y proponer es una sola operacion: dos
        # consultas de alertas a la vez (la pantalla al recargar y su consulta
        # periodica, o la pasada de `servir_api`) creaban dos propuestas para el
        # mismo desvio (2026-11-23A, bloque B, visto en el navegador).
        self._candado = threading.Lock()

    @property
    def candado(self) -> threading.Lock:
        """El mismo candado lo usa la propuesta por riesgo vial (D31, 23E)."""
        return self._candado

    def tramo_vigente(self, ruta_id: RutaId) -> tuple[int, int]:
        """(desde, hasta): la ultima Parada cumplida (o el origen de la Ruta) y
        la Parada en curso. Levanta `RutaNoEnEjecucion` o `RutaYaReemplazada`."""
        avance = self._reemplazo.avance(ruta_id)
        persistida = self._rutas.obtener(ruta_id)
        nodo = {p.secuencia: p.nodo for p in persistida.ruta.paradas}
        assert avance.en_curso is not None
        if avance.ultima_cumplida is not None:
            desde = nodo[avance.ultima_cumplida]
        else:
            origen = self._reemplazo.reemplazo_de_la_nueva(ruta_id)
            desde = persistida.deposito if origen is None else origen.origen_nodo
        return desde, nodo[avance.en_curso]

    def evaluar(self, ruta_id: RutaId, reportes: Sequence[ReporteDePosicion],
                ahora: dt.datetime | None = None) -> DesvioDetectado | None:
        """D18 sobre los reportes recientes; None si no hay desvio vigente o si
        la Ruta no esta en ejecucion."""
        ahora = self._reloj() if ahora is None else ahora
        p = self.parametros
        cuentan = sorted((r for r in reportes if 0.0 <= (ahora - r.capturada_en).total_seconds() <= p.antiguedad_maxima_s),
                         key=lambda r: r.capturada_en)
        if len(cuentan) < p.consecutivos:
            return None
        try:
            desde, hasta = self.tramo_vigente(ruta_id)
        except (RutaNoEnEjecucion, RutaYaReemplazada):
            return None
        # Una Ruta nacida de una propuesta por riesgo vial recorre los caminos
        # con su mu (D31, 2026-11-23E): el tramo se mide contra esos.
        mu = self._reemplazo.mu_del_trazado(ruta_id)
        if desde == hasta:
            trazado: Sequence[tuple[float, float]] = (self._contexto.coordenadas_de(desde),)
        elif mu is None:
            trazado = self._contexto.trazado_de([desde, hasta])
        else:
            trazado = self._contexto.trazado_con_riesgo_de([desde, hasta], mu)
        distancias = []
        for r in cuentan:
            anclado, _ = self._contexto.anclar_en_la_componente(r.latitud, r.longitud)
            distancias.append(distancia_a_polilinea_m(self._contexto.coordenadas_de(anclado), trazado))
        if not all(d > p.umbral_m for d in distancias[-p.consecutivos:]):
            return None
        inicio = len(distancias) - p.consecutivos
        while inicio > 0 and distancias[inicio - 1] > p.umbral_m:
            inicio -= 1
        vehiculo = str(self._rutas.obtener(ruta_id).ruta.vehiculo_id)
        return DesvioDetectado(ruta_id, vehiculo, distancias[-1], cuentan[inicio].capturada_en, cuentan[-1])

    def proponer_si_corresponde(self, desvio: DesvioDetectado,
                                ahora: dt.datetime | None = None) -> PropuestaDeReemplazo | None:
        """La propuesta de D19 para el desvio, o la pendiente que ya hay; None
        si hubo otra hace menos de la espera."""
        with self._candado:
            return self._proponer_si_corresponde(desvio, ahora)

    def _proponer_si_corresponde(self, desvio: DesvioDetectado,
                                 ahora: dt.datetime | None) -> PropuestaDeReemplazo | None:
        ahora = self._reloj() if ahora is None else ahora
        previas = [x for x in self._reemplazo.propuestas_de(desvio.ruta_id) if x.motivo == MOTIVO_DESVIO]
        pendiente = next((x for x in previas if x.resuelta_en is None), None)
        if pendiente is not None:
            return pendiente
        if any((ahora - x.creada_en).total_seconds() < self.parametros.espera_tras_una_propuesta_s for x in previas):
            return None
        posicion = PosicionDelVehiculo(desvio.ultimo.latitud, desvio.ultimo.longitud,
                                       (ahora - desvio.ultimo.capturada_en).total_seconds())
        referencia = f"desvio-{desvio.ruta_id}-{desvio.desde.astimezone(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        return self._reemplazo.proponer(desvio.ruta_id, MOTIVO_DESVIO, referencia, posicion, fijar_en_curso=False)

    def revisar(self, rutas: Iterable[RutaId], lector: Callable[[str], Sequence[ReporteDePosicion]],
                ahora: dt.datetime | None = None) -> list[tuple[DesvioDetectado, PropuestaDeReemplazo | None]]:
        """Una pasada sobre `rutas`: el desvio vigente de cada una y su
        propuesta (una por desvio). Un error de telemetria o del recalculo deja
        esa Ruta sin vigilar en esta pasada, registrado, sin cortar las demas."""
        ahora = self._reloj() if ahora is None else ahora
        resultado: list[tuple[DesvioDetectado, PropuestaDeReemplazo | None]] = []
        for ruta_id in rutas:
            try:
                desvio = self.evaluar(ruta_id, lector(str(ruta_id)), ahora)
                if desvio is None:
                    continue
                propuesta = self.proponer_si_corresponde(desvio, ahora)
            except Exception as error:  # noqa: BLE001 - la vigilancia no corta las demas Rutas
                _LOG.warning("Sin vigilancia de desvio para %s: %s", ruta_id, error)
                continue
            if propuesta is not None:
                _LOG.info("Desvio de Ruta | %s | %.0f m | propuesta %s", ruta_id, desvio.distancia_m, propuesta.id)
            resultado.append((desvio, propuesta))
        return resultado
