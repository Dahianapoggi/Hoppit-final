"""Motor del producto sobre una instancia externa (encargo del 13 de octubre,
M1; B-208, punto 8).

Resuelve un CVRP dado como matriz de distancias, demandas, capacidad,
cantidad de Vehiculos y Deposito con el mismo motor y la misma configuracion
del producto: `SolverVRPDeterminista` con su limite de soluciones por
omision (`solution_limit = 200`), GUIDED_LOCAL_SEARCH, sin limite de tiempo y
con los operadores LNS desactivados (`parametros_de_busqueda`). No toca
`src/` ni pasa por el servicio de planificacion, que arma la instancia desde
las Ordenes y el grafo vial y no admite una matriz externa: arma una
`InstanciaProblema` y llama a `resolver`.

Como lo hace el producto, el motor redondea cada costo al entero mas cercano
para buscar, y la distancia de cada Ruta se suma sobre la matriz original. Un
Vehiculo sin Paradas no produce Ruta. Por omision el motor no obliga a usar
todos los Vehiculos, de modo que con k Vehiculos usa k o menos; con
`usar_todos_los_vehiculos` usa exactamente k o falla con
`SinSolucionConTodosLosVehiculos`, sin devolver una solucion con menos
(B-211, punto 1).

`SolverVRPDeterminista` se toma del puerto de Planificacion de Rutas, que lo
exporta desde el 13 de octubre (B-211, punto 4).

    python tools/motor_instancia_externa.py canonica
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import sys
from collections.abc import Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.cartografia.puerto import CaminoEntreNodos, MatrizDeCostos
from hoppit.planificacion_rutas.puerto import (
    InstanciaProblema,
    ParadaDeInstancia,
    SolucionRuteo,
    SolverVRPDeterminista,
)
from hoppit.shared.ids import OrdenId, VehiculoId
from tools.exportar_instancia_y_plan import INSTANCIA_CANONICA, PLAN_CANONICO

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.motor_instancia_externa")

# La jornada solo entra en el resumen del que se derivan los identificadores
# de Ruta; no cambia el reparto. Se fija para que dos corridas den lo mismo.
JORNADA_DE_LA_CORRIDA = dt.date(2026, 9, 27)
PREFIJO_DE_VEHICULO = "v"
PREFIJO_DE_ORDEN = "cliente-"
DISTANCIA_CANONICA_M = 42056.28
TOLERANCIA_DE_DISTANCIA_M = 0.005


@dataclasses.dataclass(frozen=True)
class InstanciaExterna:
    """Un CVRP con Deposito unico, en indices 0..n-1.

    `nodos` da un identificador a cada indice; si falta, el identificador es
    el indice. `ordenes` da las Ordenes de cada indice de cliente; si falta,
    una por cliente."""

    nombre: str
    matriz: Sequence[Sequence[float]]
    demandas: Sequence[int]
    indice_deposito: int
    capacidad_por_vehiculo: dict[str, int]
    nodos: Sequence[int] | None = None
    ordenes: dict[int, tuple[str, ...]] | None = None


def con_k_vehiculos(k: int, capacidad: int) -> dict[str, int]:
    """k Vehiculos iguales, con identificadores que ordenan como su numero."""
    digitos = len(str(k))
    return {f"{PREFIJO_DE_VEHICULO}{numero:0{digitos}d}": capacidad for numero in range(1, k + 1)}


def instancia_del_problema(externa: InstanciaExterna) -> InstanciaProblema:
    n = len(externa.matriz)
    if any(len(fila) != n for fila in externa.matriz) or len(externa.demandas) != n:
        raise ValueError(f"{externa.nombre}: la matriz y las demandas no son de {n} nodos")
    nodos = tuple(externa.nodos) if externa.nodos is not None else tuple(range(n))
    if len(set(nodos)) != n:
        raise ValueError(f"{externa.nombre}: los identificadores de nodo se repiten")
    caminos = {
        (nodos[i], nodos[j]): CaminoEntreNodos(nodos[i], nodos[j], float(externa.matriz[i][j]), ())
        for i in range(n)
        for j in range(n)
    }
    paradas = tuple(
        ParadaDeInstancia(
            nodo=nodos[i],
            indice=i,
            demanda=int(externa.demandas[i]),
            ordenes=tuple(
                OrdenId(orden)
                for orden in (
                    externa.ordenes[i] if externa.ordenes is not None else (f"{PREFIJO_DE_ORDEN}{i}",)
                )
            ),
        )
        for i in range(n)
        if i != externa.indice_deposito
    )
    return InstanciaProblema(
        paradas=paradas,
        capacidad_por_vehiculo={VehiculoId(v): c for v, c in externa.capacidad_por_vehiculo.items()},
        depositos=(nodos[externa.indice_deposito],),
        matriz=MatrizDeCostos(nodos=nodos, caminos=caminos),
        indice_deposito=externa.indice_deposito,
        fecha_planificacion=JORNADA_DE_LA_CORRIDA,
    )


def resolver(externa: InstanciaExterna, usar_todos_los_vehiculos: bool = False) -> SolucionRuteo:
    motor = SolverVRPDeterminista(usar_todos_los_vehiculos=usar_todos_los_vehiculos)
    return motor.resolver(instancia_del_problema(externa))


def instancia_canonica(
    ruta_instancia: pathlib.Path = INSTANCIA_CANONICA, ruta_plan: pathlib.Path = PLAN_CANONICO
) -> tuple[InstanciaExterna, dict[str, object]]:
    """La instancia exportada y su plan, como instancia externa; por omision,
    los canonicos declarados en tools/exportar_instancia_y_plan.py."""
    instancia = json.loads(ruta_instancia.read_text(encoding="utf-8"))
    plan = json.loads(ruta_plan.read_text(encoding="utf-8"))
    red = instancia["distancias_por_la_red_m"]
    nodos = [int(nodo) for nodo in red["nodos"]]
    indice = {nodo: i for i, nodo in enumerate(nodos)}
    demandas = [0] * len(nodos)
    ordenes: dict[int, list[str]] = {}
    for orden in instancia["ordenes"]:
        i = indice[int(orden["nodo"])]
        demandas[i] += int(orden["demanda"])
        ordenes.setdefault(i, []).append(str(orden["identificador"]))
    externa = InstanciaExterna(
        nombre=ruta_instancia.name,
        matriz=red["metros"],
        demandas=demandas,
        indice_deposito=indice[int(instancia["deposito"]["nodo"])],
        capacidad_por_vehiculo={str(v["identificador"]): int(v["capacidad"]) for v in instancia["flota"]},
        nodos=nodos,
        ordenes={i: tuple(sorted(o)) for i, o in ordenes.items()},
    )
    return externa, plan


def verificar_canonica() -> int:
    """El adaptador sobre la matriz exportada tiene que dar el reparto y la
    distancia del plan canonico."""
    externa, plan = instancia_canonica()
    solucion = resolver(externa)
    obtenido = {
        str(r.vehiculo_id): [(p.nodo, list(p.ordenes)) for p in r.paradas] for r in solucion.rutas
    }
    esperado = {
        str(r["vehiculo"]): [(int(p["nodo"]), [str(o) for o in p["ordenes"]]) for p in r["paradas"]]
        for r in plan["rutas"]
    }
    for ruta in solucion.rutas:
        _LOG.info(
            "  %s | %d Paradas | %d Ordenes | %.1f m | %d de %d bultos",
            ruta.vehiculo_id, len(ruta.paradas), len(ruta.ordenes), ruta.distancia_m,
            ruta.carga, ruta.capacidad_vehiculo,
        )
    distancia = solucion.distancia_total_m
    mismo_reparto = obtenido == esperado
    misma_distancia = abs(distancia - DISTANCIA_CANONICA_M) <= TOLERANCIA_DE_DISTANCIA_M
    _LOG.info(
        "Plan del artefacto %s | reparto y secuencias %s | distancia %.2f m, %s con %.2f m",
        plan["identificador_del_plan"],
        "identicos" if mismo_reparto else "DISTINTOS",
        distancia,
        "coincide" if misma_distancia else "NO coincide",
        DISTANCIA_CANONICA_M,
    )
    return 0 if mismo_reparto and misma_distancia else 1


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    subcomandos.add_parser("canonica", help="Verifica el adaptador contra el plan canonico exportado.")
    args = parser.parse_args(argumentos)
    if args.subcomando == "canonica":
        return verificar_canonica()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
