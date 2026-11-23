"""Entregador de la salida transaccional (ARQ-59; seccion 6.3 de
ARQ_Pedido2; 2026-10-27B, T).

Corre dentro del proceso del nucleo. En cada pasada intenta, por Ruta, la
primera notificacion sin entregar cuyo proximo intento ya llego, y solo si
se entrega sigue con la siguiente de esa Ruta: el orden por Ruta se conserva
aunque una falle. Ante un fallo espera de forma exponencial, de 5 a 300 s.
Como enviar es "al menos una vez", el receptor descarta repetidas por
`evento_id`.

Como transportarla (HTTP, con token de servicio) no es asunto de este
componente: lo inyecta el ensamblado.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from collections.abc import Callable

from hoppit.shared.tiempo import instante_actual

from ._ejecucion import NotificacionSaliente, RepositorioDeEjecucion

_LOG = logging.getLogger(__name__)

ESPERA_INICIAL_S = 5.0
ESPERA_MAXIMA_S = 300.0
# Cada cuanto mira si hay algo para entregar el hilo de fondo.
INTERVALO_DE_PASADA_S = 5.0


def espera_tras_fallos(intentos: int) -> dt.timedelta:
    """5, 10, 20, 40… segundos, hasta 300."""
    return dt.timedelta(seconds=min(ESPERA_INICIAL_S * 2 ** max(intentos - 1, 0), ESPERA_MAXIMA_S))


class EntregadorDeNotificaciones:
    def __init__(
        self,
        repositorio: RepositorioDeEjecucion,
        enviar: Callable[[NotificacionSaliente], None],
        reloj: Callable[[], dt.datetime] = instante_actual,
    ) -> None:
        self._repositorio = repositorio
        self._enviar = enviar
        self._reloj = reloj
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

    def entregar_pendientes(self) -> int:
        """Una pasada. Devuelve cuantas se entregaron."""
        ahora = self._reloj()
        entregadas = 0
        trabadas: set[str] = set()
        for notificacion in self._repositorio.sin_entregar():
            if notificacion.agregado_id in trabadas:
                continue
            vence = notificacion.proximo_intento_en
            if vence is not None and vence > ahora:
                trabadas.add(notificacion.agregado_id)
                continue
            try:
                self._enviar(notificacion)
            except Exception as error:  # cualquier falla del transporte se reintenta
                proximo = ahora + espera_tras_fallos(notificacion.intentos + 1)
                self._repositorio.registrar_fallo(notificacion.evento_id, proximo)
                trabadas.add(notificacion.agregado_id)
                _LOG.warning(
                    "Notificacion %s (%s de %s) no entregada: %s; reintento a las %s",
                    notificacion.evento_id,
                    notificacion.tipo,
                    notificacion.agregado_id,
                    error,
                    proximo.isoformat(timespec="seconds"),
                )
                continue
            self._repositorio.marcar_entregada(notificacion.evento_id, ahora)
            entregadas += 1
        return entregadas

    def pendientes(self) -> int:
        """Verificacion de salud para `/estado`, no metrica de desempeno (ARQ-13)."""
        return len(self._repositorio.sin_entregar())

    def iniciar(self, intervalo_s: float = INTERVALO_DE_PASADA_S) -> None:
        """Pasadas periodicas en un hilo de fondo, hasta `detener`."""

        def ciclo() -> None:
            while not self._detener.wait(intervalo_s):
                try:
                    self.entregar_pendientes()
                except Exception:  # el hilo no muere por una pasada fallida
                    _LOG.exception("Pasada del entregador fallida")

        self._hilo = threading.Thread(target=ciclo, name="entregador-de-notificaciones", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
