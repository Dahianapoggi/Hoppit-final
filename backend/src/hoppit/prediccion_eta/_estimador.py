"""Estimador de ETA del MVP: la mediana de la acumulacion simulada, con banda
de p10 a p90 y semilla fija (B-193, punto 1).

Recorre la Ruta `SORTEOS_DEL_ETA` veces con el modelo hibrido de tiempos del
Entorno de Simulacion (`recorrer_ruta`, en shared/intervalo_por_celda.py;
B-196): para llegar a cada Parada, el reloj avanza el viaje por la red, la
distancia de llegada sobre la velocidad, sin azar; cada finalizacion lo
avanza en una componente de parada sorteada con `cuantil_interpolado` de la
celda de 0 a 100 m, en la franja del reloj. Por Parada toma, en cada
recorrido, la hora de su primera finalizacion, y devuelve la mediana, el p10
y el p90 de esas horas.

Lo que estima es la hora en que se completa la primera entrega de la Parada,
no la de llegada (B-193, punto 3, criterio a): la componente de parada es el
intervalo entre finalizaciones de LaDe a menos de 100 m (DAT-16), que
incluye la atencion y las esperas. La estimacion suma un viaje por la red a
una velocidad supuesta, no medida en el AMBA, y una componente de parada de
datos de referencia de otra ciudad, Hangzhou: no esta validada para el AMBA
(B-19).

Determinista (NFR-07): las probabilidades salen de un generador con semilla
fija que se crea de nuevo en cada llamada, asi que dos llamadas con la misma
entrada dan lo mismo. Fila por fila, cada recorrido consume sus
probabilidades en el orden en que `recorrer_ruta` pide los intervalos.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Sequence

import numpy as np

from hoppit.shared.intervalo_por_celda import (
    Celda,
    CeldaInexistente,
    CeldaInsuficiente,
    FranjaFueraDeLaJornada,
    ParadaARecorrer,
    TablaDeIntervalos,
    cuantil_interpolado,
    recorrer_ruta,
)

SORTEOS_DEL_ETA = 1000
"""Recorridos de la Ruta por estimacion."""

SEMILLA_DEL_ETA = 20261008
"""Semilla del generador de las probabilidades, fija para que el ETA
publicado no cambie entre dos lecturas de la misma Ruta."""

PERCENTIL_INFERIOR = 10
PERCENTIL_CENTRAL = 50
PERCENTIL_SUPERIOR = 90

# Los errores con que `recorrer_ruta` rechaza una Ruta: una franja fuera de la
# jornada simulada, una celda insuficiente o inexistente. No hay valor de
# reemplazo (B-193, punto 3, criterio f).
ERRORES_DE_ESTIMACION = (FranjaFueraDeLaJornada, CeldaInsuficiente, CeldaInexistente)


@dataclasses.dataclass(frozen=True)
class EtaDeParada:
    """La hora estimada en que se completa la primera entrega de una Parada,
    en minutos desde las 0:00 de la jornada."""

    mediana_min: float
    p10_min: float
    p90_min: float


def _sorteo_en_orden(probabilidades: Iterator[float]) -> Callable[[Celda], float]:
    def sorteo(celda: Celda) -> float:
        return cuantil_interpolado(celda, next(probabilidades))

    return sorteo


def estimar_primera_entrega_por_parada(
    tabla: TablaDeIntervalos,
    paradas: Sequence[ParadaARecorrer],
    salida_min: float,
    velocidad_km_h: float,
) -> tuple[EtaDeParada, ...]:
    """Por Parada, en el orden de la Ruta, la mediana, el p10 y el p90 de la
    hora en que se completa su primera entrega.

    Cada Parada trae la distancia por la red desde la previa, o desde el
    Deposito la primera, y su cantidad de Ordenes. `salida_min` es la hora
    de salida del Deposito, en minutos desde las 0:00 de la jornada, la misma
    escala del resultado; `velocidad_km_h`, la del viaje por la red, un
    supuesto declarado. Si algun recorrido necesita una franja fuera de la
    jornada o una celda insuficiente o inexistente, se levanta ese error, con
    el numero de recorrido: no hay valor de reemplazo.
    """
    if not paradas:
        return ()
    cantidad_de_finalizaciones = sum(parada.ordenes for parada in paradas)
    generador = np.random.default_rng(SEMILLA_DEL_ETA)
    probabilidades = generador.random((SORTEOS_DEL_ETA, cantidad_de_finalizaciones))
    primeras = np.empty((SORTEOS_DEL_ETA, len(paradas)))
    for indice, fila in enumerate(probabilidades):
        try:
            recorrido = recorrer_ruta(
                tabla, paradas, salida_min, velocidad_km_h, _sorteo_en_orden(iter(fila.tolist()))
            )
        except ERRORES_DE_ESTIMACION as error:
            raise type(error)(
                f"Recorrido {indice} de {SORTEOS_DEL_ETA} del ETA: {error}"
            ) from error
        primeras[indice] = [de_la_parada[0] for de_la_parada in recorrido]
    p10, mediana, p90 = np.percentile(
        primeras, (PERCENTIL_INFERIOR, PERCENTIL_CENTRAL, PERCENTIL_SUPERIOR), axis=0
    )
    return tuple(
        EtaDeParada(mediana_min=float(m), p10_min=float(bajo), p90_min=float(alto))
        for m, bajo, alto in zip(mediana, p10, p90)
    )
