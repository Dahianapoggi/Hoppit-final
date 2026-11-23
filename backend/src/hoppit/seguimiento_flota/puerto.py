"""Puerto publico de Seguimiento de Flota (FR-13, FR-17; NFR-04, NFR-05;
2026-10-27B, T).

La capacidad del servicio de telemetria y seguimiento, que corre en su propio
proceso (`tools/servir_telemetria.py`) con su propia base. Recibe posiciones
por lotes del dispositivo del transportista y los despachos del nucleo por la
salida transaccional, y publica la ultima posicion de una Ruta con su
antiguedad. El despacho y el resultado de una Parada siguen en Planificacion
de Rutas, duena de la Ruta.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from hoppit.shared.tiempo import instante_actual

from ._repositorio import RepositorioDeSeguimientoEnMemoria, RepositorioDeSeguimientoSQLite
from ._seguimiento import (
    MAX_BYTES_DEL_LOTE,
    MAX_POSICIONES_POR_LOTE,
    REINTENTABLES,
    RETENCION_DE_POSICIONES_DIAS,
    TIPO_RUTA_DESPACHADA,
    TIPO_RUTA_REEMPLAZADA,
    TOLERANCIA_DE_RELOJ_S,
    DispositivoInvalido,
    Ejecucion,
    EventoDesconocido,
    MotivoDeRechazo,
    Posicion,
    PosicionRecibida,
    Rechazo,
    RepositorioDeSeguimiento,
    ResultadoDelLote,
    SecuenciaFueraDeOrden,
    ServicioDeSeguimiento,
    SinPosiciones,
    UltimaPosicion,
    clasificar,
    es_uuid_v4_canonico,
)


def crear_servicio(
    repositorio: RepositorioDeSeguimiento | None = None,
    reloj: Callable[[], dt.datetime] = instante_actual,
) -> ServicioDeSeguimiento:
    """Punto de ensamblado; sin repositorio, el de memoria."""
    return ServicioDeSeguimiento(
        repositorio if repositorio is not None else RepositorioDeSeguimientoEnMemoria(), reloj
    )


__all__ = [
    "crear_servicio",
    "MAX_BYTES_DEL_LOTE",
    "MAX_POSICIONES_POR_LOTE",
    "REINTENTABLES",
    "RETENCION_DE_POSICIONES_DIAS",
    "TIPO_RUTA_DESPACHADA",
    "TIPO_RUTA_REEMPLAZADA",
    "TOLERANCIA_DE_RELOJ_S",
    "DispositivoInvalido",
    "Ejecucion",
    "EventoDesconocido",
    "MotivoDeRechazo",
    "Posicion",
    "PosicionRecibida",
    "Rechazo",
    "RepositorioDeSeguimiento",
    "RepositorioDeSeguimientoEnMemoria",
    "RepositorioDeSeguimientoSQLite",
    "ResultadoDelLote",
    "SecuenciaFueraDeOrden",
    "ServicioDeSeguimiento",
    "SinPosiciones",
    "UltimaPosicion",
    "clasificar",
    "es_uuid_v4_canonico",
]
