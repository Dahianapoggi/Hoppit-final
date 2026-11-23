"""Puerto de telemetria (NFR-04, NFR-05; ARQ-52; 2026-10-27B, T).

Adaptador HTTP de entrada del servicio de telemetria y seguimiento, detras
de la capacidad Seguimiento de Flota. MQTT queda como segundo adaptador
futuro, detras de la misma capacidad (B-233, punto 8).
"""
from __future__ import annotations

from ._aplicacion import (
    CONSENTIMIENTOS_DEL_LOTE,
    PREFIJO,
    EstadoDeTelemetria,
    LoteClasificado,
    LoteSolicitado,
    UltimaPosicionPublicada,
    crear_app_de_telemetria,
)

__all__ = [
    "CONSENTIMIENTOS_DEL_LOTE",
    "PREFIJO",
    "EstadoDeTelemetria",
    "LoteClasificado",
    "LoteSolicitado",
    "UltimaPosicionPublicada",
    "crear_app_de_telemetria",
]
