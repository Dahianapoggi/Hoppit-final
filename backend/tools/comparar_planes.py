"""Evalua los planes de comparacion con los mismos sorteos (B-185, punto 6;
B-178, punto 7).

Lee la tabla de B-176, la instancia canonica y los planes pedidos de esa
instancia: P0, el canonico exportado en datos/simulacion, y P0a, P1 y P1h,
los que arma tools/planes_de_comparacion.py en datos/tmp/planes_de_comparacion.
Cada plan se indica por parametro (`--plan`, `--plan-p0a`, `--plan-p1`,
`--plan-p1h`); por omision, los declarados en las herramientas que los arman,
nunca el ultimo de la carpeta.
Los evalua con N episodios, la misma semilla base y el modelo hibrido de
tiempos (B-196) a la velocidad pedida, un supuesto declarado. Con los sorteos
apareados por Orden del entorno, el episodio i de todos los planes le da a
cada Orden la misma probabilidad.

Informa, por plan, la distancia por el grafo con el regreso y p10, p50 y p90
de la suma y de la mayor de las duraciones de las Rutas, contadas hasta el
regreso al Deposito (B-201, punto 2); y, para cada par,
la diferencia apareada por episodio en la suma de las duraciones (el plan
pedido despues menos el pedido antes): media,
mediana, intervalo de confianza del 95 % de la media por bootstrap de
percentiles (remuestreo de episodios, con semilla con nombre) y fraccion de
episodios con diferencia negativa y nula.

Son cifras de una instancia, bajo una distribucion transferida de Hangzhou
(B-19). No son una decision sobre el agente.

    python tools/comparar_planes.py
    python tools/comparar_planes.py --planes P0 P0a --velocidad-km-h 19
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import pathlib
import sys
from collections.abc import Sequence

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import (
    SEMILLA_BASE_DE_EVALUACION,
    VELOCIDAD_DE_TRABAJO_KM_H,
    crear_servicio,
)
from tools.exportar_instancia_y_plan import INSTANCIA_CANONICA, PLAN_CANONICO
from tools.planes_de_comparacion import PLAN_P0A, PLAN_P1, PLAN_P1H
from tools.simular_plan import EPISODIOS, TABLA, Percentiles, percentiles

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.comparar_planes")

REMUESTRAS_DEL_BOOTSTRAP = 2000
SEMILLA_DEL_BOOTSTRAP = 20261006
NIVEL_DEL_INTERVALO = 0.95
_POR_CIENTO = 100
METROS_POR_KILOMETRO = 1000.0
PLANES = ("P0", "P0a", "P1", "P1h")
PLANES_POR_OMISION = ("P0", "P0a", "P1")


@dataclasses.dataclass(frozen=True)
class Evaluacion:
    plan: str
    distancia_km: float
    duracion_total_min: np.ndarray
    """Por episodio, en el orden de su indice."""

    duracion_maxima_min: np.ndarray


def evaluar(
    nombre: str,
    ruta_instancia: pathlib.Path,
    ruta_plan: pathlib.Path,
    episodios: int,
    semilla_base: int,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> Evaluacion:
    entorno = crear_servicio(TABLA, ruta_instancia, ruta_plan)
    resultados = entorno.evaluar_plan(
        episodios, semilla_base=semilla_base, velocidad_km_h=velocidad_km_h
    )
    if [r.indice for r in resultados] != list(range(episodios)):
        raise RuntimeError(f"{nombre}: los episodios no estan en el orden de su indice")
    _LOG.info(
        "%s | %s | %s | SHA-256 tabla %s, instancia %s, plan %s",
        nombre,
        entorno.plan.identificador,
        ruta_plan.name,
        entorno.huellas.tabla[:12],
        entorno.huellas.instancia[:12],
        entorno.huellas.plan[:12],
    )
    return Evaluacion(
        plan=nombre,
        distancia_km=entorno.plan.distancia_m / METROS_POR_KILOMETRO,
        duracion_total_min=np.array([r.duracion_total_min for r in resultados]),
        duracion_maxima_min=np.array([r.duracion_maxima_min for r in resultados]),
    )


def intervalo_bootstrap(diferencias: np.ndarray, generador: np.random.Generator) -> tuple[float, float]:
    """Intervalo de percentiles del 95 % para la media, remuestreando episodios."""
    indices = generador.integers(0, len(diferencias), (REMUESTRAS_DEL_BOOTSTRAP, len(diferencias)))
    medias = diferencias[indices].mean(axis=1)
    cola = (1 - NIVEL_DEL_INTERVALO) / 2 * _POR_CIENTO
    bajo, alto = np.percentile(medias, (cola, _POR_CIENTO - cola))
    return float(bajo), float(alto)


def _p(valores: Percentiles) -> str:
    return f"{valores.p10:.1f} | {valores.p50:.1f} | {valores.p90:.1f}"


def comparar(
    episodios: int,
    semilla_base: int,
    nombres: Sequence[str] = PLANES_POR_OMISION,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
    ruta_instancia: pathlib.Path = INSTANCIA_CANONICA,
    ruta_p0: pathlib.Path = PLAN_CANONICO,
    ruta_p0a: pathlib.Path = PLAN_P0A,
    ruta_p1: pathlib.Path = PLAN_P1,
    ruta_p1h: pathlib.Path = PLAN_P1H,
) -> None:
    """La instancia y cada plan se indican; por omision, los declarados en
    tools/exportar_instancia_y_plan.py y tools/planes_de_comparacion.py."""
    disponibles = {"P0": ruta_p0, "P0a": ruta_p0a, "P1": ruta_p1, "P1h": ruta_p1h}
    planes = {nombre: disponibles[nombre] for nombre in nombres}
    _LOG.info(
        "Episodios %d | semilla base %d | modelo hibrido, velocidad supuesta %g km/h | instancia %s",
        episodios,
        semilla_base,
        velocidad_km_h,
        ruta_instancia.name,
    )
    evaluaciones = {
        nombre: evaluar(nombre, ruta_instancia, ruta, episodios, semilla_base, velocidad_km_h)
        for nombre, ruta in planes.items()
    }
    _LOG.info("")
    _LOG.info(
        "| Plan | distancia por el grafo con regreso (km) | suma p10 | suma p50 | suma p90 | "
        "mayor p10 | mayor p50 | mayor p90 |"
    )
    _LOG.info("|---|---|---|---|---|---|---|---|")
    for nombre, evaluacion in evaluaciones.items():
        _LOG.info(
            "| %s | %.3f | %s | %s |",
            nombre,
            evaluacion.distancia_km,
            _p(percentiles(evaluacion.duracion_total_min.tolist())),
            _p(percentiles(evaluacion.duracion_maxima_min.tolist())),
        )
    _LOG.info("")
    _LOG.info(
        "Diferencia apareada por episodio en la suma de las duraciones (min); IC 95 %% de la media "
        "por bootstrap de percentiles, %d remuestras, semilla %d.",
        REMUESTRAS_DEL_BOOTSTRAP,
        SEMILLA_DEL_BOOTSTRAP,
    )
    _LOG.info("| Diferencia | media | mediana | IC 95 % de la media | fracción negativa | fracción nula |")
    _LOG.info("|---|---|---|---|---|---|")
    generador = np.random.default_rng(SEMILLA_DEL_BOOTSTRAP)
    pares = [(nombres[j], nombres[i]) for i in range(len(nombres)) for j in range(i + 1, len(nombres))]
    for uno, otro in pares:
        diferencias = evaluaciones[uno].duracion_total_min - evaluaciones[otro].duracion_total_min
        bajo, alto = intervalo_bootstrap(diferencias, generador)
        _LOG.info(
            "| %s - %s | %.2f | %.2f | [%.2f, %.2f] | %.3f | %.3f |",
            uno,
            otro,
            float(diferencias.mean()),
            float(np.median(diferencias)),
            bajo,
            alto,
            float((diferencias < 0).mean()),
            float((diferencias == 0).mean()),
        )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--episodios", type=int, default=EPISODIOS)
    parser.add_argument("--semilla-base", type=int, default=SEMILLA_BASE_DE_EVALUACION)
    parser.add_argument("--planes", nargs="+", choices=PLANES, default=list(PLANES_POR_OMISION))
    parser.add_argument(
        "--velocidad-km-h",
        type=float,
        default=VELOCIDAD_DE_TRABAJO_KM_H,
        help="Velocidad del viaje por la red: un supuesto declarado (B-196).",
    )
    parser.add_argument("--instancia", type=pathlib.Path, default=INSTANCIA_CANONICA)
    parser.add_argument("--plan", type=pathlib.Path, default=PLAN_CANONICO, help="El plan P0.")
    parser.add_argument("--plan-p0a", type=pathlib.Path, default=PLAN_P0A)
    parser.add_argument("--plan-p1", type=pathlib.Path, default=PLAN_P1)
    parser.add_argument("--plan-p1h", type=pathlib.Path, default=PLAN_P1H)
    args = parser.parse_args(argumentos)
    comparar(
        args.episodios,
        args.semilla_base,
        args.planes,
        args.velocidad_km_h,
        args.instancia,
        args.plan,
        args.plan_p0a,
        args.plan_p1,
        args.plan_p1h,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
