"""Estimador de ETA del MVP aplicado al Plan de Distribucion canonico
(B-193, punto 1).

Aplica `estimar_primera_entrega_por_parada` a cada Ruta del plan que deja
tools/exportar_instancia_y_plan.py, con salida a las 8:00 y el modelo hibrido
de tiempos (B-196) a la velocidad pedida, 19 km/h por omision, que es un
supuesto declarado. Las distancias por la red salen del artefacto de la
instancia. Pone por Parada
la mediana, el p10 y el p90 de la hora en que se completa su primera entrega,
con la mediana tambien en segundos desde la salida, la unidad de
`eta_estimado_s`. Lee los artefactos, no la API: sirve para cotejar lo que
la API publica. La tabla sale como filas Markdown, para el
informe.

Son cifras de una instancia (B-84), estimadas con datos de referencia de otra
ciudad y no validadas para el AMBA (B-19).

    python tools/estimar_eta_del_plan.py
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import MINUTO_DE_SALIDA
from hoppit.prediccion_eta.puerto import (
    SEMILLA_DEL_ETA,
    SORTEOS_DEL_ETA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    ParadaARecorrer,
    crear_servicio,
)
from tools.simular_plan import TABLA, crear_entorno, hora

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.estimar_eta_del_plan")

SEGUNDOS_POR_MINUTO = 60


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tabla", type=pathlib.Path, default=TABLA)
    parser.add_argument("--instancia", type=pathlib.Path, default=None)
    parser.add_argument("--plan", type=pathlib.Path, default=None)
    parser.add_argument("--velocidad-km-h", type=float, default=VELOCIDAD_DE_TRABAJO_KM_H)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    servicio = crear_servicio(args.tabla)
    entorno = crear_entorno(args.tabla, args.instancia, args.plan)
    _LOG.info(
        "Plan %s | salida %s | velocidad supuesta %g km/h | tabla %s | instancia %s | "
        "%d sorteos, semilla %d",
        entorno.plan.identificador,
        hora(MINUTO_DE_SALIDA),
        args.velocidad_km_h,
        servicio.sha256_de_la_tabla,
        entorno.instancia.sha256,
        SORTEOS_DEL_ETA,
        SEMILLA_DEL_ETA,
    )
    _LOG.info("")
    _LOG.info("| Vehiculo | Parada | Ordenes | p10 | mediana | p90 | mediana (s desde la salida) |")
    _LOG.info("|---|---|---|---|---|---|---|")
    instancia = entorno.instancia
    for ruta in entorno.plan.rutas:
        origenes = [instancia.deposito_nodo] + [parada.nodo for parada in ruta.paradas[:-1]]
        paradas = [
            ParadaARecorrer(
                distancia_por_la_red_m=instancia.distancia_por_la_red_m(origen, parada.nodo),
                ordenes=len(parada.ordenes),
            )
            for origen, parada in zip(origenes, ruta.paradas)
        ]
        estimadas = servicio.estimar(paradas, MINUTO_DE_SALIDA, args.velocidad_km_h)
        for parada, eta in zip(ruta.paradas, estimadas):
            _LOG.info(
                "| %s | %d | %s | %s | %s | %s | %.1f |",
                ruta.vehiculo,
                parada.secuencia,
                ", ".join(parada.ordenes),
                hora(eta.p10_min),
                hora(eta.mediana_min),
                hora(eta.p90_min),
                (eta.mediana_min - MINUTO_DE_SALIDA) * SEGUNDOS_POR_MINUTO,
            )
    _LOG.info("")
    _LOG.info("Horas del dia redondeadas al minuto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
