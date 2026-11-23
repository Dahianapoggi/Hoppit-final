"""Mide una planificacion real sobre la instancia de demostracion.

Primer insumo empirico para NFR-09, que hoy no tiene umbral definido
justamente por falta de medicion. Este tool no propone el umbral: mide y
loggea. Separa el tiempo del solver del tiempo de armado de la instancia,
porque son dos costos distintos y solo el primero es el que crece con el
tamano del problema combinatorio.

    python tools/medir_planificacion.py
    python tools/medir_planificacion.py --repeticiones 5 --semilla 20260817
"""
from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import statistics
import sys
import time

# Corrido como script, sys.path arranca en tools/ y no en la raiz, asi que el
# paquete `tools` no se resuelve. Las pruebas importan este mismo modulo por
# `tools.escenario_demostracion`; dejar una sola forma de importarlo evita que
# el escenario exista dos veces bajo dos identidades distintas.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import (
    OrigenRotulo,
    ProveedorGrafoVial,
    crear_servicio as crear_cartografia,
)
from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
from hoppit.planificacion_rutas.puerto import (
    SIN_MARGEN,
    InstanciaProblema,
    SolucionRuteo,
    SolverVRPDeterminista,
    crear_servicio as crear_planificacion,
)
from hoppit.preparacion_datos.puerto import leer_deposito, seleccionar_deposito
from hoppit.shared.bus import Bus
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.escenario_demostracion import capacidad_por_vehiculo, construir_ordenes
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.medir_planificacion")

DIRECTORIO_INSTANCIA = pathlib.Path("datos/instancia")
REPETICIONES_PREDETERMINADAS = 3
MILISEGUNDOS_POR_SEGUNDO = 1000.0
METROS_POR_KM = 1000.0
PORCENTAJE = 100.0


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeticiones",
        type=int,
        default=REPETICIONES_PREDETERMINADAS,
        help="Corridas del solver sobre la misma instancia (verifica NFR-07).",
    )
    parser.add_argument("--semilla", type=int, default=SEMILLA_DE_INSTANCIA)
    parser.add_argument(
        "--directorio-instancia", type=pathlib.Path, default=DIRECTORIO_INSTANCIA
    )
    return parser.parse_args(argumentos)


def armar_instancia(
    semilla: int, directorio: pathlib.Path
) -> tuple[InstanciaProblema, float, ProveedorGrafoVial]:
    """Instancia de demostracion, con el tiempo que llevo armarla."""
    grafo = crear_cartografia(GRAFO_PREDETERMINADO)
    grafo.construir()
    deposito = leer_deposito(seleccionar_deposito(directorio, ARTEFACTO_DEPOSITO_PREDETERMINADO))

    bus = Bus()
    ordenes = crear_ordenes(bus)
    generadas = construir_ordenes(grafo.nodos_de_mayor_componente_fuerte(), semilla=semilla)
    for orden in generadas:
        ordenes.dar_de_alta(orden)

    servicio = crear_planificacion(bus, grafo, ordenes, margen_de_capacidad=SIN_MARGEN)
    # Se mide el camino real del servicio, no una copia del armado: si se
    # replicara aca, lo medido podria dejar de ser lo que corre.
    comienzo = time.perf_counter()
    instancia = servicio.ensamblar_instancia(deposito, capacidad_por_vehiculo(generadas))
    transcurrido = time.perf_counter() - comienzo
    return instancia, transcurrido, grafo


def loggear_solucion(instancia: InstanciaProblema, solucion: SolucionRuteo) -> None:
    """Distancia, Paradas y ocupacion de capacidad, Ruta por Ruta."""
    _LOG.info(
        "Plan | rutas=%d distancia_total=%.1f m (%.2f km) bultos=%d/%d",
        len(solucion.rutas),
        solucion.distancia_total_m,
        solucion.distancia_total_m / METROS_POR_KM,
        instancia.demanda_total,
        instancia.capacidad_total,
    )
    for ruta in solucion.rutas:
        capacidad = instancia.capacidad_por_vehiculo[ruta.vehiculo_id]
        _LOG.info(
            "  %s | %s paradas=%2d ordenes=%2d distancia=%8.1f m (%.2f km) "
            "carga=%2d/%2d bultos (ocupacion %5.1f%%)",
            ruta.id,
            ruta.vehiculo_id,
            len(ruta.paradas),
            len(ruta.ordenes),
            ruta.distancia_m,
            ruta.distancia_m / METROS_POR_KM,
            ruta.carga,
            capacidad,
            ruta.carga / capacidad * PORCENTAJE,
        )
    _LOG.info(
        "  Ordenes por Ruta: %s",
        {
            str(ruta.id): [str(orden) for orden in ruta.ordenes]
            for ruta in solucion.rutas
        },
    )
    agrupadas = [
        (parada.nodo, parada.ordenes)
        for ruta in solucion.rutas
        for parada in ruta.paradas
        if len(parada.ordenes) > 1
    ]
    _LOG.info(
        "  Paradas que agrupan mas de una Orden (RB-04): %d de %d | %s",
        len(agrupadas),
        len(instancia.paradas),
        {nodo: [str(o) for o in ordenes] for nodo, ordenes in agrupadas},
    )


def loggear_rotulos(grafo: ProveedorGrafoVial, instancia: InstanciaProblema) -> None:
    """Cuantas Paradas se nombran por cruce de calles y cuantas degradan
    (RB-02).

    Va junto a la medicion de la planificacion porque es la misma instancia:
    reportar la cobertura de rotulos contra un conjunto de nodos distinto del
    que se rutea no diria nada sobre lo que la interfaz va a mostrar.
    """
    nodos = instancia.depositos + tuple(parada.nodo for parada in instancia.paradas)
    conteo: collections.Counter[OrigenRotulo] = collections.Counter(
        grafo.rotulo(nodo).origen for nodo in nodos
    )
    derivados = sum(
        cantidad
        for origen, cantidad in conteo.items()
        if origen is not OrigenRotulo.COORDENADAS
    )
    _LOG.info(
        "Rotulos (RB-02) | %d de %d con nombre real: cruces=%d via_unica=%d | "
        "degradadas a coordenadas=%d",
        derivados,
        len(nodos),
        conteo[OrigenRotulo.CRUCE_DE_CALLES],
        conteo[OrigenRotulo.VIA_UNICA],
        conteo[OrigenRotulo.COORDENADAS],
    )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    instancia, tiempo_armado, grafo = armar_instancia(
        args.semilla, args.directorio_instancia
    )
    _LOG.info(
        "Instancia | ordenes=%d vehiculos=%d nodos_en_matriz=%d "
        "demanda_total=%d capacidad_total=%d tiempo_de_armado_s=%.3f",
        len(instancia.ordenes),
        len(instancia.capacidad_por_vehiculo),
        len(instancia.matriz.nodos),
        instancia.demanda_total,
        instancia.capacidad_total,
        tiempo_armado,
    )

    solver = SolverVRPDeterminista()
    tiempos: list[float] = []
    soluciones: list[SolucionRuteo] = []
    for _ in range(args.repeticiones):
        comienzo = time.perf_counter()
        soluciones.append(solver.resolver(instancia))
        tiempos.append(time.perf_counter() - comienzo)

    _LOG.info(
        "Tiempo del solver | corridas=%d min=%.1f ms mediana=%.1f ms max=%.1f ms",
        len(tiempos),
        min(tiempos) * MILISEGUNDOS_POR_SEGUNDO,
        statistics.median(tiempos) * MILISEGUNDOS_POR_SEGUNDO,
        max(tiempos) * MILISEGUNDOS_POR_SEGUNDO,
    )
    identicas = all(s == soluciones[0] for s in soluciones[1:])
    _LOG.info(
        "Determinismo (NFR-07) | %d corridas sobre la misma instancia: "
        "soluciones identicas=%s",
        len(soluciones),
        identicas,
    )
    loggear_solucion(instancia, soluciones[0])
    loggear_rotulos(grafo, instancia)
    return 0 if identicas else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
