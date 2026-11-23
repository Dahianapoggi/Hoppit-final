"""Puerto publico de Prediccion de ETA (FR-06).

Construido: el estimador del MVP (B-193, punto 1), la mediana de la
acumulacion simulada con el modelo hibrido de tiempos (B-196), con banda de
p10 a p90 y semilla fija. Recibe la tabla, la secuencia de Paradas con su
distancia de llegada por la red y su cantidad de Ordenes, la hora de salida
y la velocidad, y devuelve por Parada la hora estimada en que se completa su
primera entrega. Es el unico estimador publico: la suma de medianas de B-177
dejo de publicarse. El modelo supervisado se evaluo y no se adopto (B-177).

El servicio (`crear_servicio`) lee la tabla por ruta, verificada contra su
sidecar, y la frontera lo invoca al responder una Ruta (B-193, punto 3,
criterios d y e). El ETA no se persiste.

Suma un viaje por la red a una velocidad supuesta, que no es un dato medido
en el AMBA, y una componente de parada tomada de datos de referencia de otra
ciudad, Hangzhou. No esta validada para el AMBA (B-19): no es una
prediccion validada ni depende del transito.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from hoppit.shared.intervalo_por_celda import (
    VELOCIDAD_DE_TRABAJO_KM_H,
    ParadaARecorrer,
    TablaDeIntervalos,
    leer_tabla_de_intervalos,
)

from ._estimador import (
    ERRORES_DE_ESTIMACION,
    SEMILLA_DEL_ETA,
    SORTEOS_DEL_ETA,
    EtaDeParada,
    estimar_primera_entrega_por_parada,
)


class ServicioPrediccionEta:
    """El estimador sobre una tabla ya leida y verificada."""

    def __init__(self, tabla: TablaDeIntervalos) -> None:
        self._tabla = tabla

    @property
    def sha256_de_la_tabla(self) -> str:
        return self._tabla.sha256

    def estimar(
        self,
        paradas: Sequence[ParadaARecorrer],
        salida_min: float,
        velocidad_km_h: float,
    ) -> tuple[EtaDeParada, ...]:
        """Por Parada, la hora estimada de su primera entrega; ver
        `estimar_primera_entrega_por_parada`."""
        return estimar_primera_entrega_por_parada(
            self._tabla, paradas, salida_min, velocidad_km_h
        )


def crear_servicio(ruta_tabla: Path) -> ServicioPrediccionEta:
    """Lee la tabla, la verifica contra el SHA-256 de su sidecar y arma el
    servicio. Una tabla sin verificar no se usa: levanta el error de la
    lectura unica de shared/intervalo_por_celda.py."""
    return ServicioPrediccionEta(leer_tabla_de_intervalos(ruta_tabla))


__all__ = [
    "crear_servicio",
    "ServicioPrediccionEta",
    "estimar_primera_entrega_por_parada",
    "EtaDeParada",
    "ERRORES_DE_ESTIMACION",
    "SORTEOS_DEL_ETA",
    "SEMILLA_DEL_ETA",
    "VELOCIDAD_DE_TRABAJO_KM_H",
    "ParadaARecorrer",
]
