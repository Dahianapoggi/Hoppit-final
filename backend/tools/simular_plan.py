"""Simula el Plan de Distribucion canonico en el Entorno de Simulacion (B-178).

Lee la tabla de B-176, y la instancia y el plan que deja
tools/exportar_instancia_y_plan.py; corre N episodios con la semilla base de
la evaluacion, con el modelo hibrido de tiempos (B-196) a la velocidad
pedida, un supuesto declarado (19 km/h por omision), y resume:

- por Vehiculo, y para la suma y la mayor de las duraciones de las Rutas,
  p10, p50 y p90 de la duracion en minutos, contada hasta el regreso al
  Deposito (B-201, punto 2);
- por Vehiculo, el viaje de regreso, que es determinista, y p10, p50 y p90
  de la hora de regreso;
- por Parada, p10, p50 y p90 de la hora de su ultima finalizacion;
- la distancia del plan;
- cuantos episodios tienen alguna finalizacion despues de las 22:00, y cuantos
  despues de las 23:00, fin de la ultima franja de la jornada.

Un episodio que necesite sortear fuera de la jornada levanta el error
explicito del entorno y la corrida se detiene: no se descartan episodios.
Son cifras de una instancia y no se comparan con las de otra (B-84). Las
tablas salen como filas Markdown, para el informe.

    python tools/simular_plan.py
    python tools/simular_plan.py --episodios 1000 --semilla-base 20261003
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
    HuellasDeLosArtefactos,
    ResultadoDeEpisodio,
    ServicioEntornoSimulacion,
    crear_servicio,
)
from hoppit.shared.intervalo_por_celda import (
    MINUTOS_POR_HORA,
    ULTIMA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    viaje_por_la_red_min,
)
from tools.exportar_instancia_y_plan import (
    INSTANCIA_CANONICA,
    PLAN_CANONICO,
)
from tools.servir_api import TABLA_DE_INTERVALOS_VIGENTE

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.simular_plan")

# La tabla vigente de B-176, la misma con que la API estima el ETA.
TABLA = TABLA_DE_INTERVALOS_VIGENTE
EPISODIOS = 1000
PERCENTILES_DEL_RESUMEN = (10, 50, 90)

# El encargo pide contar los episodios que pasan de las 22 h, que es donde
# empieza la ultima franja de la jornada; se cuentan tambien los que pasan de
# las 23 h, donde termina.
INICIO_DE_LA_ULTIMA_FRANJA_MIN = ULTIMA_FRANJA_DE_LA_JORNADA * MINUTOS_POR_HORA
FIN_DE_LA_JORNADA_MIN = (ULTIMA_FRANJA_DE_LA_JORNADA + 1) * MINUTOS_POR_HORA


@dataclasses.dataclass(frozen=True)
class Percentiles:
    p10: float
    p50: float
    p90: float


def percentiles(valores: Sequence[float]) -> Percentiles:
    """p10, p50 y p90 con la interpolacion lineal de numpy, la de la tabla."""
    p10, p50, p90 = np.percentile(np.asarray(valores, dtype=float), PERCENTILES_DEL_RESUMEN)
    return Percentiles(float(p10), float(p50), float(p90))


@dataclasses.dataclass(frozen=True)
class ResumenDeLaEvaluacion:
    episodios: int
    semilla_base: int
    huellas: HuellasDeLosArtefactos
    duracion_por_ruta_min: dict[str, Percentiles]
    regreso_por_ruta_min: dict[str, Percentiles]
    duracion_total_min: Percentiles
    duracion_maxima_min: Percentiles
    finalizacion_por_parada_min: dict[tuple[str, int], Percentiles]
    episodios_despues_de_las_22: int
    episodios_despues_de_las_23: int
    ultima_finalizacion_min: float


def resumir(resultados: Sequence[ResultadoDeEpisodio]) -> ResumenDeLaEvaluacion:
    primero = resultados[0]
    vehiculos = sorted(primero.duracion_por_ruta_min)
    paradas = sorted(primero.finalizacion_por_parada_min)
    ultimas = [max(r.finalizacion_por_parada_min.values()) for r in resultados]
    return ResumenDeLaEvaluacion(
        episodios=len(resultados),
        semilla_base=primero.semilla_base,
        huellas=primero.huellas,
        duracion_por_ruta_min={
            v: percentiles([r.duracion_por_ruta_min[v] for r in resultados]) for v in vehiculos
        },
        regreso_por_ruta_min={
            v: percentiles([r.regreso_por_ruta_min[v] for r in resultados]) for v in vehiculos
        },
        duracion_total_min=percentiles([r.duracion_total_min for r in resultados]),
        duracion_maxima_min=percentiles([r.duracion_maxima_min for r in resultados]),
        finalizacion_por_parada_min={
            p: percentiles([r.finalizacion_por_parada_min[p] for r in resultados]) for p in paradas
        },
        episodios_despues_de_las_22=sum(u > INICIO_DE_LA_ULTIMA_FRANJA_MIN for u in ultimas),
        episodios_despues_de_las_23=sum(u >= FIN_DE_LA_JORNADA_MIN for u in ultimas),
        ultima_finalizacion_min=max(ultimas),
    )


def hora(minutos: float) -> str:
    """HH:MM de un instante del reloj simulado, redondeado al minuto."""
    redondeado = round(minutos)
    return f"{redondeado // MINUTOS_POR_HORA:02d}:{redondeado % MINUTOS_POR_HORA:02d}"


def evaluar(
    entorno: ServicioEntornoSimulacion,
    episodios: int,
    semilla_base: int,
    velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
) -> ResumenDeLaEvaluacion:
    resultados = entorno.evaluar_plan(
        episodios, semilla_base=semilla_base, velocidad_km_h=velocidad_km_h
    )
    huellas = {r.huellas for r in resultados}
    if huellas != {entorno.huellas}:
        raise RuntimeError(f"Los episodios registran otras huellas: {huellas}")
    return resumir(resultados)


def _informar(
    entorno: ServicioEntornoSimulacion, resumen: ResumenDeLaEvaluacion, velocidad_km_h: float
) -> None:
    plan = entorno.plan
    _LOG.info(
        "Plan %s | distancia %.2f m | modelo hibrido, velocidad supuesta %g km/h",
        plan.identificador,
        plan.distancia_m,
        velocidad_km_h,
    )
    _LOG.info(
        "Episodios %d | semilla base %d | SHA-256 tabla %s | instancia %s | plan %s",
        resumen.episodios,
        resumen.semilla_base,
        resumen.huellas.tabla,
        resumen.huellas.instancia,
        resumen.huellas.plan,
    )
    _LOG.info("")
    _LOG.info("| Duracion (min) | p10 | p50 | p90 |")
    _LOG.info("|---|---|---|---|")
    for vehiculo, p in resumen.duracion_por_ruta_min.items():
        _LOG.info("| %s | %.1f | %.1f | %.1f |", vehiculo, p.p10, p.p50, p.p90)
    for nombre, p in (
        ("Suma de las Rutas", resumen.duracion_total_min),
        ("Mayor de las Rutas", resumen.duracion_maxima_min),
    ):
        _LOG.info("| %s | %.1f | %.1f | %.1f |", nombre, p.p10, p.p50, p.p90)
    _LOG.info("")
    _LOG.info("| Vehiculo | regreso por la red (m) | viaje de regreso (min) | hora de regreso p10 | p50 | p90 |")
    _LOG.info("|---|---|---|---|---|---|")
    instancia = entorno.instancia
    for ruta in plan.rutas:
        regreso_m = instancia.distancia_por_la_red_m(ruta.paradas[-1].nodo, instancia.deposito_nodo)
        p = resumen.regreso_por_ruta_min[ruta.vehiculo]
        _LOG.info(
            "| %s | %.1f | %.1f | %s | %s | %s |",
            ruta.vehiculo,
            regreso_m,
            viaje_por_la_red_min(regreso_m, velocidad_km_h),
            hora(p.p10),
            hora(p.p50),
            hora(p.p90),
        )
    _LOG.info("")
    _LOG.info("| Vehiculo | Parada | Ordenes | p10 | p50 | p90 |")
    _LOG.info("|---|---|---|---|---|---|")
    for ruta in plan.rutas:
        for parada in ruta.paradas:
            p = resumen.finalizacion_por_parada_min[(ruta.vehiculo, parada.secuencia)]
            _LOG.info(
                "| %s | %d | %s | %s | %s | %s |",
                ruta.vehiculo,
                parada.secuencia,
                ", ".join(parada.ordenes),
                hora(p.p10),
                hora(p.p50),
                hora(p.p90),
            )
    _LOG.info("")
    _LOG.info(
        "Episodios con alguna finalizacion despues de las 22:00: %d; despues de las "
        "23:00: %d | ultima finalizacion observada: %s (%.1f min)",
        resumen.episodios_despues_de_las_22,
        resumen.episodios_despues_de_las_23,
        hora(resumen.ultima_finalizacion_min),
        resumen.ultima_finalizacion_min,
    )


def crear_entorno(
    tabla: pathlib.Path,
    instancia: pathlib.Path = INSTANCIA_CANONICA,
    plan: pathlib.Path = PLAN_CANONICO,
) -> ServicioEntornoSimulacion:
    """El entorno sobre los artefactos indicados; por omision, la instancia
    canonica y su plan."""
    return crear_servicio(tabla, instancia, plan)


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tabla", type=pathlib.Path, default=TABLA)
    parser.add_argument("--instancia", type=pathlib.Path, default=INSTANCIA_CANONICA)
    parser.add_argument("--plan", type=pathlib.Path, default=PLAN_CANONICO)
    parser.add_argument("--episodios", type=int, default=EPISODIOS)
    parser.add_argument("--semilla-base", type=int, default=SEMILLA_BASE_DE_EVALUACION)
    parser.add_argument(
        "--velocidad-km-h",
        type=float,
        default=VELOCIDAD_DE_TRABAJO_KM_H,
        help="Velocidad del viaje por la red: un supuesto declarado (B-196).",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    entorno = crear_entorno(args.tabla, args.instancia, args.plan)
    _informar(
        entorno,
        evaluar(entorno, args.episodios, args.semilla_base, args.velocidad_km_h),
        args.velocidad_km_h,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
