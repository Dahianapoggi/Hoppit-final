"""Cota optimista por jornada (encargo 2026-11-19A, bloque 2, punto 3).

Relajacion de la jornada dinamica: todas las Ordenes del dia conocidas a las
8, sin horas de llegada ni fin de jornada, resueltas como un problema
estatico con capacidad y varios viajes. Como no hay ventanas ni fin de
jornada, que los viajes los haga uno u otro de los 3 Vehiculos no cambia la
duracion total: el problema es un ruteo con capacidad con tantas copias del
Vehiculo como haga falta, y cada copia usada paga una carga (10 min). La
duracion de la relajacion es viaje + Parada + carga, con el tiempo de Parada
de cada Orden al minimo entre las franjas de la jornada con su
probabilidad sorteada, que no puede ser mayor que el que le toca en la
simulacion.

Dos numeros por jornada:

- `cota_min`: el mejor valor que encuentra OR-Tools para la relajacion
  (camino mas barato y busqueda local guiada con limite de tiempo). Es la
  relajacion resuelta por una heuristica: **no es una cota garantizada**
  (el optimo de la relajacion puede ser menor), pero ninguna politica
  realizable que entregue todo puede bajar del optimo de la relajacion.
- `cota_garantizada_min`: una cota inferior valida de la relajacion, y por
  lo tanto de cualquier politica que entregue todo: viaje >= suma sobre las
  Ordenes de demanda x (ida + vuelta al Deposito) / capacidad (cada viaje
  recorre al menos la ida y vuelta a su Orden mas lejana), cargas >= techo de
  la demanda total sobre la capacidad, mas el tiempo de Parada minimo.

No es el envoltorio del producto (`planificacion_rutas/motor`): es un
modulo propio del simulador. Rotulo: simulacion sobre instancias sorteadas en
CABA; no es validacion contra datos reales.
"""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence

from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    TablaDeIntervalos,
    cuantil_interpolado,
)

from ._jornadas_dinamicas import OrdenDinamica
from ._simulador_dinamico import MatrizDeDistancias, ParametrosDelSimulador

CENTESIMAS_POR_MINUTO: int = 6_000
"""Los costos de OR-Tools son enteros: centesimas de segundo."""


@dataclasses.dataclass(frozen=True)
class CotaDeLaJornada:
    ordenes: int
    viajes: int
    viaje_min: float
    parada_min: float
    carga_min: float
    cota_min: float
    cota_garantizada_min: float
    estado_del_solver: int


def parada_minima(orden: OrdenDinamica, tabla: TablaDeIntervalos, factor: float = 1.0) -> float:
    return min(cuantil_interpolado(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, f), orden.probabilidad_de_parada)
               for f in range(PRIMERA_FRANJA_DE_LA_JORNADA, ULTIMA_FRANJA_DE_LA_JORNADA + 1)) * factor


def cota_de_la_jornada(ordenes: Sequence[OrdenDinamica], deposito: int, distancias: MatrizDeDistancias,
                       tabla: TablaDeIntervalos, parametros: ParametrosDelSimulador, segundos: float) -> CotaDeLaJornada:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    entran = [o for o in ordenes if o.demanda <= parametros.capacidad]
    metros_por_min = parametros.velocidad_km_h * 1000.0 / 60.0
    nodos = [deposito] + [o.nodo for o in entran]

    def minutos(a: int, b: int) -> float:
        return distancias.m(nodos[a], nodos[b]) / metros_por_min

    demanda_total = sum(o.demanda for o in entran)
    copias = max(1, math.ceil(demanda_total / parametros.capacidad)) + 5
    gestor = pywrapcp.RoutingIndexManager(len(nodos), copias, 0)
    modelo = pywrapcp.RoutingModel(gestor)

    def costo(i: int, j: int) -> int:
        return int(round(minutos(gestor.IndexToNode(i), gestor.IndexToNode(j)) * CENTESIMAS_POR_MINUTO))

    arco = modelo.RegisterTransitCallback(costo)
    modelo.SetArcCostEvaluatorOfAllVehicles(arco)
    modelo.SetFixedCostOfAllVehicles(int(round(parametros.carga_min * CENTESIMAS_POR_MINUTO)))
    demandas = [0] + [o.demanda for o in entran]
    indice_demanda = modelo.RegisterUnaryTransitCallback(lambda i: demandas[gestor.IndexToNode(i)])
    modelo.AddDimensionWithVehicleCapacity(indice_demanda, 0, [parametros.capacidad] * copias, True, "carga")
    busqueda = pywrapcp.DefaultRoutingSearchParameters()
    busqueda.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    busqueda.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    busqueda.time_limit.FromMilliseconds(int(segundos * 1000))
    solucion = modelo.SolveWithParameters(busqueda)
    if solucion is None:
        raise RuntimeError("OR-Tools no encontro solucion de la relajacion")
    viaje = 0.0
    viajes = 0
    for k in range(copias):
        i = modelo.Start(k)
        if modelo.IsEnd(solucion.Value(modelo.NextVar(i))):
            continue
        viajes += 1
        while not modelo.IsEnd(i):
            j = solucion.Value(modelo.NextVar(i))
            viaje += minutos(gestor.IndexToNode(i), gestor.IndexToNode(j))
            i = j
    parada = sum(parada_minima(o, tabla, parametros.factor_de_parada) for o in entran)
    carga = viajes * parametros.carga_min
    radial = sum(o.demanda * (minutos(0, k + 1) + minutos(k + 1, 0)) for k, o in enumerate(entran)) / parametros.capacidad
    garantizada = radial + math.ceil(demanda_total / parametros.capacidad) * parametros.carga_min + parada
    return CotaDeLaJornada(len(entran), viajes, viaje, parada, carga, viaje + parada + carga, garantizada,
                           int(modelo.status()))
