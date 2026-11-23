"""Evaluacion de un Plan de Distribucion por episodios (B-178, puntos 7 a 9).

Un episodio es una jornada simulada del plan con el modelo hibrido de tiempos
(B-196): cada Vehiculo sale del Deposito a la hora de salida y recorre su
Ruta con la regla de `recorrer_ruta`. Para llegar a cada Parada, el reloj
avanza el viaje por la red, la distancia del artefacto de la instancia sobre
la velocidad; cada finalizacion de una Orden lo avanza en una componente de
parada sorteada de la celda de 0 a 100 m. Despues de la ultima
finalizacion, cada Ruta suma el regreso al Deposito: el viaje por la red
desde su ultima Parada, a la misma velocidad, sin componente de parada ni
azar. La duracion de una Ruta se cuenta hasta el regreso (B-201, punto 2,
que revisa el punto 9 de B-178). La velocidad es un supuesto declarado, no un
dato medido en el AMBA.

Reproducibilidad (punto 8): una semilla base con nombre y, por episodio, una
semilla derivada de ella y del indice con `numpy.random.SeedSequence`. Cada
resultado registra la semilla base, el indice, la velocidad y el SHA-256 de
los tres artefactos, que alcanza para repetir el episodio. La estocasticidad es del
entorno; la inferencia del motor sigue siendo determinista (NFR-07).

Sorteos apareados por Orden: el generador del episodio produce de una vez
una probabilidad por Orden de la instancia, en orden de identificador, y la
finalizacion de cada Orden se sortea con la suya, en cualquier plan. Dos
planes de la misma instancia evaluados con la misma semilla base le dan a
cada Orden la misma probabilidad en cada episodio, aunque la visiten en otra
posicion; lo que cambia es la celda en que se lee. Reemplaza al generador
compartido que se consumia en orden de recorrido, con el que los sorteos de
dos planes no quedaban apareados (B-181, punto 4).
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Mapping

import numpy as np

from hoppit.shared.intervalo_por_celda import (
    MINUTOS_POR_HORA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    CeldaInexistente,
    CeldaInsuficiente,
    Celda,
    FranjaFueraDeLaJornada,
    ParadaARecorrer,
    TablaDeIntervalos,
    cuantil_interpolado,
    recorrer_ruta,
    viaje_por_la_red_min,
)

from ._artefactos import Instancia, PlanAEvaluar

# Semilla base de la evaluacion de planes. Constante con nombre para que dos
# evaluaciones del mismo plan, o de dos planes que se comparan (punto 7), usen
# las mismas semillas.
SEMILLA_BASE_DE_EVALUACION = 20261003

# Hora de salida de los Vehiculos: las 8:00, la primera franja con todas las
# celdas suficientes (punto 9). Parametro de demostracion, no dato observado.
HORA_DE_SALIDA = 8
MINUTO_DE_SALIDA = HORA_DE_SALIDA * MINUTOS_POR_HORA
"""En minutos desde las 0:00 de la jornada simulada, como todo el reloj."""


@dataclasses.dataclass(frozen=True)
class HuellasDeLosArtefactos:
    """SHA-256 de los tres artefactos que definen un episodio."""

    tabla: str
    instancia: str
    plan: str


@dataclasses.dataclass(frozen=True)
class ResultadoDeEpisodio:
    """Una jornada simulada del plan. Horas en minutos desde las 0:00."""

    semilla_base: int
    indice: int
    velocidad_km_h: float
    huellas: HuellasDeLosArtefactos
    finalizacion_por_orden_min: Mapping[str, float]
    finalizacion_por_parada_min: Mapping[tuple[str, int], float]
    """Por (Vehiculo, secuencia), la ultima finalizacion de la Parada."""

    ultima_finalizacion_por_ruta_min: Mapping[str, float]
    """Por Vehiculo, la hora de la ultima finalizacion de su Ruta."""

    regreso_por_ruta_min: Mapping[str, float]
    """Por Vehiculo, la hora en que su Ruta vuelve al Deposito."""

    duracion_por_ruta_min: Mapping[str, float]
    """Por Vehiculo, de la salida al regreso al Deposito (B-201, punto 2)."""

    @property
    def duracion_total_min(self) -> float:
        """Suma de las duraciones de las Rutas, hasta el regreso."""
        return sum(self.duracion_por_ruta_min.values())

    @property
    def duracion_maxima_min(self) -> float:
        """La mayor de las duraciones de las Rutas, hasta el regreso."""
        return max(self.duracion_por_ruta_min.values())


def generador_del_episodio(semilla_base: int, indice: int) -> np.random.Generator:
    """El generador del episodio `indice`.

    `SeedSequence(semilla_base, spawn_key=(indice,))` es el hijo numero
    `indice` de `SeedSequence(semilla_base).spawn(...)`: cada episodio se
    puede repetir solo, sin generar los anteriores.
    """
    return np.random.default_rng(np.random.SeedSequence(semilla_base, spawn_key=(indice,)))


def probabilidades_por_orden(
    instancia: Instancia, generador: np.random.Generator
) -> dict[str, float]:
    """Una probabilidad uniforme por Orden de la instancia, sorteadas de una
    vez y asignadas en orden de identificador. No depende del plan."""
    identificadores = sorted(orden.identificador for orden in instancia.ordenes)
    return dict(zip(identificadores, generador.random(len(identificadores)).tolist()))


def _sorteo_en_orden(probabilidades: Iterator[float]) -> Callable[[Celda], float]:
    """El intervalo de cada finalizacion, con la probabilidad siguiente.

    `recorrer_ruta` pide un intervalo por finalizacion, Parada por Parada y,
    dentro de cada una, en el orden de sus Ordenes en el plan: las
    probabilidades llegan en ese mismo orden.
    """

    def sorteo(celda: Celda) -> float:
        return cuantil_interpolado(celda, next(probabilidades))

    return sorteo


def simular_episodio(
    tabla: TablaDeIntervalos,
    instancia: Instancia,
    plan: PlanAEvaluar,
    semilla_base: int,
    indice: int,
    salida_min: float = MINUTO_DE_SALIDA,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> ResultadoDeEpisodio:
    """Una jornada del plan.

    La componente de parada de cada Orden se sortea con su probabilidad del
    episodio (`probabilidades_por_orden`), en la franja que le toca en este
    plan. El regreso al Deposito no lee la tabla: es viaje por la red. Un
    error de la tabla (franja fuera de la jornada, celda insuficiente o
    inexistente) se levanta con el episodio y la Ruta en que ocurrio.
    """
    probabilidades = probabilidades_por_orden(
        instancia, generador_del_episodio(semilla_base, indice)
    )
    por_orden: dict[str, float] = {}
    por_parada: dict[tuple[str, int], float] = {}
    ultimas: dict[str, float] = {}
    regresos: dict[str, float] = {}
    duraciones: dict[str, float] = {}
    for ruta in plan.rutas:
        origenes = [instancia.deposito_nodo] + [parada.nodo for parada in ruta.paradas[:-1]]
        paradas = [
            ParadaARecorrer(
                distancia_por_la_red_m=instancia.distancia_por_la_red_m(origen, parada.nodo),
                ordenes=len(parada.ordenes),
            )
            for origen, parada in zip(origenes, ruta.paradas)
        ]
        sorteo = _sorteo_en_orden(
            iter([probabilidades[orden] for parada in ruta.paradas for orden in parada.ordenes])
        )
        try:
            recorrido = recorrer_ruta(tabla, paradas, salida_min, velocidad_km_h, sorteo)
        except (FranjaFueraDeLaJornada, CeldaInsuficiente, CeldaInexistente) as error:
            raise type(error)(
                f"Episodio {indice} de la semilla base {semilla_base}, Ruta de "
                f"{ruta.vehiculo}: {error}"
            ) from error
        for parada, finalizaciones in zip(ruta.paradas, recorrido):
            por_orden.update(zip(parada.ordenes, finalizaciones))
            por_parada[(ruta.vehiculo, parada.secuencia)] = finalizaciones[-1]
        ultimas[ruta.vehiculo] = recorrido[-1][-1]
        regresos[ruta.vehiculo] = ultimas[ruta.vehiculo] + viaje_por_la_red_min(
            instancia.distancia_por_la_red_m(ruta.paradas[-1].nodo, instancia.deposito_nodo),
            velocidad_km_h,
        )
        duraciones[ruta.vehiculo] = regresos[ruta.vehiculo] - salida_min
    return ResultadoDeEpisodio(
        semilla_base=semilla_base,
        indice=indice,
        velocidad_km_h=velocidad_km_h,
        huellas=HuellasDeLosArtefactos(
            tabla=tabla.sha256, instancia=instancia.sha256, plan=plan.sha256
        ),
        finalizacion_por_orden_min=por_orden,
        finalizacion_por_parada_min=por_parada,
        ultima_finalizacion_por_ruta_min=ultimas,
        regreso_por_ruta_min=regresos,
        duracion_por_ruta_min=duraciones,
    )


def evaluar_plan(
    tabla: TablaDeIntervalos,
    instancia: Instancia,
    plan: PlanAEvaluar,
    episodios: int,
    semilla_base: int = SEMILLA_BASE_DE_EVALUACION,
    salida_min: float = MINUTO_DE_SALIDA,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> tuple[ResultadoDeEpisodio, ...]:
    """Los episodios 0 a `episodios` - 1 del plan, con la semilla base dada."""
    return tuple(
        simular_episodio(tabla, instancia, plan, semilla_base, indice, salida_min, velocidad_km_h)
        for indice in range(episodios)
    )
