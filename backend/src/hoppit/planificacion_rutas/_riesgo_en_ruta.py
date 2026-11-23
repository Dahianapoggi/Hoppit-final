"""Propuesta de reruteo por riesgo vial con aprobacion del operador (D31 del
encargo 2026-11-23E).

Cuando una Ruta en ejecucion recorre tramos con muchos hechos viales predichos
y hay una alternativa que baja de forma marcada su exposicion predicha con un
costo acotado en distancia, se genera una propuesta de reemplazo de CU-07 con
motivo `riesgo_vial`, que el operador aprueba o rechaza como la del desvio.

- **Cuando:** en la pasada periodica del desvio y en cada consulta de alertas,
  solo si hay tabla de riesgo verificada para el grafo cargado (sobre San
  Isidro no hay y no pasa nada). Cada Ruta se evalua una vez por proceso (D31:
  la tabla es anual y estatica); lo que se evalua es lo pendiente desde el
  origen de ese momento, que cambia con el avance, y eso no se reevalua
  (consejo del 23E, E4). Despues de un reemplazo, la Ruta nueva es otra y se
  evalua una vez.
- **Convivencia:** con otra propuesta pendiente de la Ruta (de cualquier
  motivo) no se evalua todavia; una propuesta por riesgo de la Ruta, en
  cualquier estado (pendiente, aprobada, rechazada o vencida por un desvio, un
  Incidente o un resultado declarado despues), no se repite.
- **mu de la propuesta:** el mu_1 del bloque A del 23E (la regla de D26 sobre la
  tabla 23B: mu x riesgo medio por arco = 10 % del largo medio por arco).

Rotulo: la exposicion es la suma de la frecuencia anual predicha de hechos
viales de los tramos; no es una deteccion en tiempo real ni un riesgo por
vehiculo-kilometro. El umbral (20 % y 5 %) es un supuesto sin dato de operacion.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Final

from hoppit.shared.ids import RutaId

from ._desvio import ReporteDePosicion
from ._reemplazo import (
    ANTIGUEDAD_MAXIMA_S,
    MOTIVO_RIESGO_VIAL,
    EstadoDePropuesta,
    PosicionDelVehiculo,
    PropuestaDeReemplazo,
    RutaNoEnEjecucion,
    RutaYaReemplazada,
    ServicioDeReemplazo,
    UmbralDeRiesgoVial,
)

MU_DE_LA_PROPUESTA_M: Final[float] = 28.352409339768933
"""mu_1 del bloque A del encargo 2026-11-23E (`a/mu.json`): metros por hecho
vial anual predicho, con la tabla 23B sobre el grafo de CABA."""
_LOG = logging.getLogger(__name__)


class VigilanteDeRiesgoVial:
    """Lo arma `crear_servicio` en `puerto.py`, sobre el servicio de reemplazo y
    con el candado del vigilante de desvio."""

    def __init__(self, reemplazo: ServicioDeReemplazo, reloj: Callable[[], dt.datetime],
                 candado: threading.Lock | None = None, mu: float = MU_DE_LA_PROPUESTA_M,
                 umbral: UmbralDeRiesgoVial = UmbralDeRiesgoVial()) -> None:
        self._reemplazo = reemplazo
        self._reloj = reloj
        self._candado = candado if candado is not None else threading.Lock()
        self.mu = mu
        self.umbral = umbral
        self._evaluadas: set[str] = set()

    def evaluar_si_corresponde(self, ruta_id: RutaId, reportes: Sequence[ReporteDePosicion] = (),
                               ahora: dt.datetime | None = None) -> PropuestaDeReemplazo | None:
        """La propuesta nueva, si esta pasada la genera; None si no."""
        with self._candado:
            if str(ruta_id) in self._evaluadas:
                return None
            previas = self._reemplazo.propuestas_de(ruta_id)
            if any(p.motivo == MOTIVO_RIESGO_VIAL for p in previas):
                self._evaluadas.add(str(ruta_id))
                return None
            if any(p.estado is EstadoDePropuesta.PENDIENTE for p in previas):
                return None
            try:
                self._reemplazo.avance(ruta_id)
            except RutaYaReemplazada:
                self._evaluadas.add(str(ruta_id))
                return None
            except RutaNoEnEjecucion:
                return None
            ahora = self._reloj() if ahora is None else ahora
            posicion = None
            recientes = [r for r in reportes if 0.0 <= (ahora - r.capturada_en).total_seconds() <= ANTIGUEDAD_MAXIMA_S]
            if recientes:
                ultimo = max(recientes, key=lambda r: r.capturada_en)
                posicion = PosicionDelVehiculo(ultimo.latitud, ultimo.longitud,
                                               (ahora - ultimo.capturada_en).total_seconds())
            propuesta = self._reemplazo.proponer_por_riesgo_vial(ruta_id, posicion, self.mu, self.umbral)
            if propuesta is None and any(p.estado is EstadoDePropuesta.PENDIENTE
                                         for p in self._reemplazo.propuestas_de(ruta_id)):
                # Aparecio otra pendiente mientras se calculaba (un Incidente por
                # la API): no se guardo y la Ruta se reevalua despues (consejo
                # del 23E, J7).
                return None
            self._evaluadas.add(str(ruta_id))
            return propuesta

    def revisar(self, rutas: Iterable[RutaId], lector: Callable[[str], Sequence[ReporteDePosicion]] | None = None,
                ahora: dt.datetime | None = None) -> list[PropuestaDeReemplazo]:
        """Una pasada: evalua las Rutas que corresponda y devuelve las
        propuestas por riesgo vial pendientes de `rutas`. Un error de
        telemetria o del recalculo deja esa Ruta para la proxima pasada,
        registrado, sin cortar las demas."""
        if self._reemplazo.capa_de_riesgo() is None:
            # Sin tabla de riesgo para el grafo cargado (San Isidro): inactiva.
            return []
        rutas = list(rutas)
        for ruta_id in rutas:
            try:
                reportes = lector(str(ruta_id)) if lector is not None else ()
                propuesta = self.evaluar_si_corresponde(ruta_id, reportes, ahora)
            except Exception as error:  # noqa: BLE001 - la vigilancia no corta las demas Rutas
                _LOG.warning("Sin evaluacion de riesgo vial para %s: %s", ruta_id, error)
                continue
            if propuesta is not None and propuesta.riesgo_vial is not None:
                c = propuesta.riesgo_vial
                _LOG.info("Riesgo vial en Ruta | %s | exposicion %.2f -> %.2f | distancia %.0f -> %.0f m | propuesta %s",
                          ruta_id, c.exposicion_vigente, c.exposicion_propuesta, c.distancia_vigente_m,
                          c.distancia_propuesta_m, propuesta.id)
        # Una pendiente calculada sobre otro avance (se declaro un resultado)
        # vence: no se podria aprobar. No se vuelve a evaluar la Ruta.
        pendientes = [self._reemplazo.vencer_si_cambio_el_avance(p)
                      for r in rutas for p in self._reemplazo.propuestas_de(r)
                      if p.motivo == MOTIVO_RIESGO_VIAL and p.estado is EstadoDePropuesta.PENDIENTE]
        return [p for p in pendientes if p.estado is EstadoDePropuesta.PENDIENTE]
