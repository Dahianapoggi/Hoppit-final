"""Diagnostico de las instancias de CVRPLIB que no terminan con la opcion de k
Vehiculos encendida (sesion 2026-10-13C, bloque C, punto e). Fuera de la
metrica.

Pregunta: la busqueda llega a una primera solucion? El motor del producto no
expone sus soluciones intermedias, y cambiar su configuracion (por ejemplo,
`log_search`) esta prohibido. Esta herramienta arma una replica del modelo de
`SolverVRPDeterminista.resolver`, con la opcion de k encendida y apagada (mismos
callbacks de costo y demanda, misma dimension de capacidad, la misma
restriccion `NextVar(Start(v)) != End(v)` si la opcion esta encendida, y los
mismos parametros de busqueda,
tomados de `parametros_de_busqueda` del motor) y le agrega solo
`AddAtSolutionCallback`, que anota cada solucion aceptada (momento y costo)
en un archivo, sin cambiar la busqueda.

Fidelidad: antes del diagnostico, la replica tiene que dar las mismas Rutas
que el motor en instancias que si terminan; si no, no se informa nada.

`parametros_de_busqueda` se importa del puerto del motor, que la exporta
desde la sesion 2026-10-13F (B-220, punto 4), para no reescribir esa
configuracion a mano.

`armar_modelo` y `leer_solucion` exponen las dos mitades de la replica para
tools/terminacion_del_motor.py (sesion 2026-10-13D, bloque T): el modelo, y
la traduccion de la asignacion a `SolucionRuteo` con los tipos publicos del
puerto, como lo hace `_leer_solucion` del motor (identificador de Ruta,
Paradas numeradas desde 1, distancia sobre la matriz en metros), para que el
SHA-256 de la solucion se pueda comparar con el del motor.

    python tools/diagnostico_k_vehiculos.py A-n61-k9 [OTRAS ...]
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import multiprocessing
import pathlib
import sys
import time
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ortools.constraint_solver import pywrapcp

from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    InstanciaProblema,
    ParadaDeInstancia,
    ParadaPlaneada,
    Ruta,
    SolucionRuteo,
    SolverVRPDeterminista,
    parametros_de_busqueda,
)
from hoppit.shared.ids import RutaId, VehiculoId
from tools.cvrplib import DIRECTORIO as DIRECTORIO_CVRPLIB, como_externa, leer_vrp
from tools.metricas_cvrplib import LIMITE_POR_RESOLUCION_S
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.diagnostico_k_vehiculos")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "datos" / "tmp" / "metricas" / "diagnostico_k_vehiculos_2026-10-13C"
INSTANCIAS_DE_FIDELIDAD = ("A-n32-k5", "B-n31-k5", "P-n16-k8")
CODIFICACION = "utf-8"


def _problema(nombre: str) -> InstanciaProblema:
    ruta = next(DIRECTORIO_CVRPLIB.glob(f"*/{nombre}.vrp"))
    return instancia_del_problema(como_externa(leer_vrp(ruta)))


@dataclasses.dataclass(frozen=True)
class Modelo:
    gestor: pywrapcp.RoutingIndexManager
    modelo: pywrapcp.RoutingModel
    vehiculos: tuple[VehiculoId, ...]
    # Los callbacks registrados en OR-Tools tienen que seguir referenciados
    # mientras viva el modelo.
    callbacks: tuple[object, ...]


def armar_modelo(instancia: InstanciaProblema, usar_todos: bool) -> Modelo:
    """El modelo de `SolverVRPDeterminista.resolver`, sin resolver."""
    vehiculos = tuple(sorted(instancia.capacidad_por_vehiculo))
    capacidades = [instancia.capacidad_por_vehiculo[v] for v in vehiculos]
    cantidad_nodos = len(instancia.matriz.nodos)
    parada_por_indice = instancia.parada_por_indice
    demanda_por_indice = [
        0 if indice == instancia.indice_deposito else parada_por_indice[indice].demanda
        for indice in range(cantidad_nodos)
    ]
    distancias = instancia.matriz.como_matriz()
    gestor = pywrapcp.RoutingIndexManager(cantidad_nodos, len(vehiculos), instancia.indice_deposito)
    modelo = pywrapcp.RoutingModel(gestor)

    def costo_de_transito(desde: int, hasta: int) -> int:
        return int(round(distancias[gestor.IndexToNode(desde)][gestor.IndexToNode(hasta)] * 1))

    modelo.SetArcCostEvaluatorOfAllVehicles(modelo.RegisterTransitCallback(costo_de_transito))

    def demanda(desde: int) -> int:
        return demanda_por_indice[gestor.IndexToNode(desde)]

    modelo.AddDimensionWithVehicleCapacity(
        modelo.RegisterUnaryTransitCallback(demanda), 0, capacidades, True, "capacidad_en_bultos"
    )
    if usar_todos:
        for posicion in range(len(vehiculos)):
            modelo.solver().Add(modelo.NextVar(modelo.Start(posicion)) != modelo.End(posicion))
    return Modelo(gestor, modelo, vehiculos, (costo_de_transito, demanda))


def _resumen_de_instancia(instancia: InstanciaProblema) -> dict[str, object]:
    return {
        "paradas": [{"nodo": p.nodo, "demanda": p.demanda, "ordenes": list(p.ordenes)} for p in instancia.paradas],
        "nodos": list(instancia.matriz.nodos),
        "depositos": list(instancia.depositos),
        "capacidades": {str(v): c for v, c in sorted(instancia.capacidad_por_vehiculo.items())},
        "fecha_planificacion": instancia.fecha_planificacion.isoformat(),
    }


def _identificador_de_ruta(
    resumen: dict[str, object], vehiculo: VehiculoId, visitadas: tuple[ParadaDeInstancia, ...]
) -> RutaId:
    canonico = json.dumps(
        {
            "instancia": resumen,
            "vehiculo": str(vehiculo),
            "recorrido": [{"nodo": p.nodo, "demanda": p.demanda, "ordenes": list(p.ordenes)} for p in visitadas],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return RutaId(f"ruta-{hashlib.sha256(canonico.encode(CODIFICACION)).hexdigest()[:12]}")


def leer_solucion(instancia: InstanciaProblema, armado: Modelo, asignacion: pywrapcp.Assignment) -> SolucionRuteo:
    """La asignacion como `SolucionRuteo`, igual que `_leer_solucion` del
    motor: una Ruta por Vehiculo usado, Paradas numeradas desde 1 y distancia
    sobre la matriz en metros."""
    gestor, modelo = armado.gestor, armado.modelo
    resumen = _resumen_de_instancia(instancia)
    rutas = []
    for posicion, vehiculo in enumerate(armado.vehiculos):
        indice = modelo.Start(posicion)
        visitadas: list[ParadaDeInstancia] = []
        distancia = 0.0
        carga = 0
        while not modelo.IsEnd(indice):
            nodo = gestor.IndexToNode(indice)
            siguiente = asignacion.Value(modelo.NextVar(indice))
            distancia += instancia.matriz.distancia_m(
                instancia.matriz.nodos[nodo], instancia.matriz.nodos[gestor.IndexToNode(siguiente)]
            )
            if nodo != instancia.indice_deposito:
                parada = instancia.parada_por_indice[nodo]
                carga += parada.demanda
                visitadas.append(parada)
            indice = siguiente
        if not visitadas:
            continue
        ruta_id = _identificador_de_ruta(resumen, vehiculo, tuple(visitadas))
        rutas.append(
            Ruta(
                id=ruta_id,
                vehiculo_id=vehiculo,
                paradas=tuple(
                    ParadaPlaneada(ruta_id=ruta_id, secuencia=s, nodo=p.nodo, ordenes=p.ordenes, eta_estimado_s=None)
                    for s, p in enumerate(visitadas, start=1)
                ),
                distancia_m=distancia,
                carga=carga,
                capacidad_vehiculo=instancia.capacidad_por_vehiculo[vehiculo],
            )
        )
    return SolucionRuteo(rutas=tuple(rutas))


def replica(instancia: InstanciaProblema, anotar: pathlib.Path | None, usar_todos: bool = True) -> list[list[int]] | None:
    """El modelo de `SolverVRPDeterminista.resolver`, mas un callback que
    anota cada solucion."""
    armado = armar_modelo(instancia, usar_todos)
    gestor, modelo, vehiculos = armado.gestor, armado.modelo, armado.vehiculos

    inicio = time.perf_counter()
    contador = [0]

    def al_encontrar_una_solucion() -> None:
        contador[0] += 1
        if anotar is not None:
            with anotar.open("a", encoding=CODIFICACION) as archivo:
                archivo.write(json.dumps({"solucion": contador[0], "s": time.perf_counter() - inicio, "costo": modelo.CostVar().Value()}) + "\n")

    modelo.AddAtSolutionCallback(al_encontrar_una_solucion)
    asignacion = modelo.SolveWithParameters(parametros_de_busqueda(LIMITE_DE_SOLUCIONES))
    if asignacion is None:
        return None
    rutas = []
    for posicion in range(len(vehiculos)):
        indice = modelo.Start(posicion)
        nodos = []
        while not modelo.IsEnd(indice):
            nodo = gestor.IndexToNode(indice)
            if nodo != instancia.indice_deposito:
                nodos.append(instancia.matriz.nodos[nodo])
            indice = asignacion.Value(modelo.NextVar(indice))
        if nodos:
            rutas.append(nodos)
    return rutas


def _en_un_proceso(nombre: str, anotar: str, usar_todos: bool) -> None:
    replica(_problema(nombre), pathlib.Path(anotar), usar_todos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    SALIDA.mkdir(parents=True, exist_ok=True)
    for usar_todos in (True, False):
        motor = SolverVRPDeterminista(usar_todos_los_vehiculos=usar_todos)
        for nombre in INSTANCIAS_DE_FIDELIDAD:
            problema = _problema(nombre)
            del_motor = [[p.nodo for p in r.paradas] for r in motor.resolver(problema).rutas]
            de_la_replica = replica(problema, None, usar_todos)
            igual = del_motor == de_la_replica
            _LOG.info("Fidelidad | opcion de k %s | %s | replica igual al motor: %s", "encendida" if usar_todos else "apagada", nombre, "si" if igual else "NO")
            if not igual:
                _LOG.info("La replica no reproduce al motor: no se informa el diagnostico.")
                return 1

    for nombre, usar_todos in [(n, opcion) for n in argumentos for opcion in (True, False)]:
        anotar = SALIDA / f"{nombre}_{'k_encendida' if usar_todos else 'k_apagada'}_soluciones.jsonl"
        anotar.unlink(missing_ok=True)
        proceso = multiprocessing.Process(target=_en_un_proceso, args=(nombre, str(anotar), usar_todos), daemon=True)
        inicio = time.monotonic()
        proceso.start()
        proceso.join(LIMITE_POR_RESOLUCION_S)
        termino = not proceso.is_alive()
        if not termino:
            proceso.terminate()
            proceso.join()
        soluciones: list[dict[str, Any]] = (
            [json.loads(linea) for linea in anotar.read_text(encoding=CODIFICACION).splitlines()] if anotar.exists() else []
        )
        _LOG.info(
            "%s | opcion de k %s | %s en %.0f s | soluciones aceptadas: %d | primera: %s | ultima: %s",
            nombre, "encendida" if usar_todos else "apagada", "termino" if termino else "cortada por el tope de 5 min", time.monotonic() - inicio,
            len(soluciones), soluciones[0] if soluciones else "ninguna", soluciones[-1] if soluciones else "ninguna",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
