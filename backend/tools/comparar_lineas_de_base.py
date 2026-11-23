"""Comparacion de las lineas de base del simulador dinamico (encargo
2026-11-18A, bloques 4 y 6). No depende de la teoria de colas.

- `resumir`: por escenario (un CSV de `simulador_dinamico_caba.py correr`),
  medianas por politica y diferencias pareadas por jornada de la medida
  principal y de los diferidos, con `comparar_dqn_etapa1.estadistica_pareada`
  sin cambios (Wilcoxon, IC 95 % por bootstrap de la mediana con 10.000
  remuestreos, diferencia minima detectable 2,80 x desvio / raiz de n). El
  generador de cada comparacion es SeedSequence(20261145, spawn_key=(escenario,
  comparacion)).
- `potencia`: con el desvio de las diferencias pareadas medido, las jornadas
  necesarias para detectar una diferencia delta con potencia 0,8, por la
  aproximacion normal de dos colas, n = ((z_{1-a/2} + z_{0,8}) desvio / delta)^2,
  con a = 0,05 y con el primer paso de Holm para k comparaciones (a/k), y la
  correccion por la eficiencia relativa asintotica del test de Wilcoxon bajo
  normalidad (3/pi).

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import pathlib
import statistics
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"
SEMILLA_BOOTSTRAP_LINEAS_DE_BASE: Final[int] = 20261145
POLITICAS: Final[tuple[str, ...]] = ("a_vehiculo_mas_cercano", "b_insercion_mas_barata", "c_aleatoria",
                                     "d_mas_cercano_con_insercion", "e_vecino_mas_cercano")
COMPARACIONES: Final[tuple[tuple[str, str], ...]] = (
    ("b_insercion_mas_barata", "a_vehiculo_mas_cercano"),
    ("c_aleatoria", "a_vehiculo_mas_cercano"),
    ("c_aleatoria", "b_insercion_mas_barata"),
    # encargo 2026-11-18B, bloque 5: (b - d), (e - b), (d - a), (e - a)
    ("b_insercion_mas_barata", "d_mas_cercano_con_insercion"),
    ("e_vecino_mas_cercano", "b_insercion_mas_barata"),
    ("d_mas_cercano_con_insercion", "a_vehiculo_mas_cercano"),
    ("e_vecino_mas_cercano", "a_vehiculo_mas_cercano"),
)
"""El indice de cada comparacion es la segunda clave del generador del
bootstrap: las tres primeras conservan los indices del 18A."""
MEDIDA_PRINCIPAL: Final[str] = "medida_principal_min"
EFICIENCIA_DE_WILCOXON: Final[float] = 3.0 / math.pi


def leer(ruta: pathlib.Path) -> list[dict[str, str]]:
    with ruta.open(encoding="utf-8") as archivo:
        return list(csv.DictReader(archivo))


def por_jornada(filas: Sequence[Mapping[str, str]], politica: str, columna: str) -> dict[int, float]:
    return {int(f["jornada"]): float(f[columna]) for f in filas if f["politica"] == politica}


def diferencias(filas: Sequence[Mapping[str, str]], x: str, y: str, columna: str) -> list[float]:
    a, b = por_jornada(filas, x, columna), por_jornada(filas, y, columna)
    if set(a) != set(b):
        raise ValueError(f"las jornadas de {x} y {y} no coinciden")
    return [a[j] - b[j] for j in sorted(a)]


def lectura(intervalo: Sequence[float], x: str) -> str:
    bajo, alto = intervalo
    if alto < 0:
        return f"{x[0]} mejor"
    if bajo > 0:
        return f"{x[0]} peor"
    return "no se distingue"


def comparar(d: Sequence[float], generador: np.random.Generator, x: str) -> dict[str, Any]:
    from tools.comparar_dqn_etapa1 import estadistica_pareada

    e = estadistica_pareada(d, generador)
    return {"mediana_de_las_diferencias": e.mediana_de_las_diferencias, "intervalo": list(e.intervalo), "wilcoxon_p": e.wilcoxon_p,
            "wilcoxon_estadistico": e.wilcoxon_estadistico, "diferencia_minima_detectable": e.diferencia_minima_detectable,
            "desvio": e.desvio, "media_de_las_diferencias": float(np.mean(d)), "lectura": lectura(e.intervalo, x),
            "jornadas": len(d)}


def resumen_de_politica(filas: Sequence[Mapping[str, str]], politica: str) -> dict[str, Any]:
    sel = [f for f in filas if f["politica"] == politica]

    def med(c: str) -> float:
        return float(statistics.median(float(f[c]) for f in sel))

    cortes = sorted(c for c in sel[0] if c.startswith("pendientes_") and c[-4:].isdigit())
    radial = [float(f["radial_min"]) / float(f["viaje_min"]) for f in sel if float(f["viaje_min"]) > 0]
    return {
        "jornadas": len(sel),
        "medida_principal_mediana_min": med(MEDIDA_PRINCIPAL), "duracion_total_mediana_min": med("duracion_total_min"),
        "diferidos_mediana": med("diferidos"), "diferidos_media": float(np.mean([float(f["diferidos"]) for f in sel])),
        "no_entran_total": int(sum(float(f["no_entran"]) for f in sel)), "atraso_mediana_min": med("atraso_min"),
        "entregadas_mediana": med("entregadas"), "tiempo_en_el_sistema_medio_mediana_min": med("tiempo_en_el_sistema_medio_min"),
        "fraccion_radial_del_viaje_mediana": float(np.median(radial)),
        "fraccion_radial_del_tiempo_de_flota_mediana": float(np.median(
            [float(f["radial_min"]) / float(f["duracion_total_min"]) for f in sel])),
        "ordenes_por_carga_mediana": med("ordenes_por_carga_media"), "regresos_mediana": med("regresos"),
        "pendientes_mediana": {c[-4:]: med(c) for c in cortes},
        "decision_mediana_entre_jornadas_de_la_mediana_s": med("decision_mediana_s"),
        "decision_mediana_entre_jornadas_del_p95_s": med("decision_p95_s"),
        "decision_maximo_del_p95_s": max(float(f["decision_p95_s"]) for f in sel),
        "grado_de_dinamismo_mediana": med("grado_de_dinamismo"),
        "grado_efectivo_de_dinamismo_mediana": med("grado_efectivo_de_dinamismo"),
        "ordenes": int(float(sel[0]["ordenes"])), "proporcion_dinamica": float(sel[0]["proporcion_dinamica"]),
        "carga_por_regreso_min": float(sel[0]["carga_por_regreso_min"]),
    }


def resumir(rutas: Sequence[pathlib.Path]) -> dict[str, Any]:
    resultado: dict[str, Any] = {"rotulo": ROTULO, "semilla_bootstrap": SEMILLA_BOOTSTRAP_LINEAS_DE_BASE, "escenarios": {}}
    for e, ruta in enumerate(rutas):
        filas = leer(ruta)
        nombre = filas[0]["escenario"]
        presentes = {f["politica"] for f in filas}
        bloque: dict[str, Any] = {"archivo": ruta.name, "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(),
                                  "indice": e, "politicas": {p: resumen_de_politica(filas, p) for p in POLITICAS
                                                             if p in presentes},
                                  "comparaciones": {}, "comparaciones_de_diferidos": {}}
        for c, (x, y) in enumerate(COMPARACIONES):
            if x not in presentes or y not in presentes:
                continue
            clave = f"{x[0]}_menos_{y[0]}"
            g = np.random.default_rng(np.random.SeedSequence(SEMILLA_BOOTSTRAP_LINEAS_DE_BASE, spawn_key=(e, c)))
            bloque["comparaciones"][clave] = comparar(diferencias(filas, x, y, MEDIDA_PRINCIPAL), g, x)
            g = np.random.default_rng(np.random.SeedSequence(SEMILLA_BOOTSTRAP_LINEAS_DE_BASE, spawn_key=(e, c, 1)))
            bloque["comparaciones_de_diferidos"][clave] = comparar(diferencias(filas, x, y, "diferidos"), g, x)
        resultado["escenarios"][nombre] = bloque
    return resultado


def z(probabilidad: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(probabilidad))


def jornadas_necesarias(desvio: float, delta: float, alfa: float = 0.05, potencia: float = 0.8) -> float:
    return ((z(1 - alfa / 2) + z(potencia)) * desvio / delta) ** 2


def _jornadas_por_delta(sd: float, d: float, comparaciones_holm: int) -> dict[str, int]:
    return {
        "alfa_0_05": math.ceil(jornadas_necesarias(sd, d)),
        "alfa_0_05_wilcoxon": math.ceil(jornadas_necesarias(sd, d) / EFICIENCIA_DE_WILCOXON),
        "holm_primer_paso": math.ceil(jornadas_necesarias(sd, d, 0.05 / comparaciones_holm)),
        "holm_primer_paso_wilcoxon": math.ceil(jornadas_necesarias(sd, d, 0.05 / comparaciones_holm) / EFICIENCIA_DE_WILCOXON),
    }


def potencia(resumen: Mapping[str, Any], escenarios: Sequence[str], deltas: Sequence[float], comparaciones_holm: int,
             comparacion: str = "b_menos_a", deltas_relativos: Sequence[float] = (),
             politica_de_referencia: str = "b_insercion_mas_barata") -> dict[str, Any]:
    """`deltas_relativos` (encargo 2026-11-18B, bloque 6): delta como fraccion
    de la medida principal mediana de `politica_de_referencia` en cada escenario."""
    filas = {}
    for nombre in escenarios:
        c = resumen["escenarios"][nombre]["comparaciones"][comparacion]
        sd = c["desvio"]
        medida = (resumen["escenarios"][nombre]["politicas"][politica_de_referencia]["medida_principal_mediana_min"]
                  if deltas_relativos else None)
        filas[nombre] = {
            "desvio_min": sd, "mediana_de_las_diferencias": c["mediana_de_las_diferencias"],
            "diferencia_minima_detectable_con_50": c["diferencia_minima_detectable"],
            "por_delta": {str(d): {
                "alfa_0_05": math.ceil(jornadas_necesarias(sd, d)),
                "alfa_0_05_wilcoxon": math.ceil(jornadas_necesarias(sd, d) / EFICIENCIA_DE_WILCOXON),
                "holm_primer_paso": math.ceil(jornadas_necesarias(sd, d, 0.05 / comparaciones_holm)),
                "holm_primer_paso_wilcoxon": math.ceil(jornadas_necesarias(sd, d, 0.05 / comparaciones_holm)
                                                       / EFICIENCIA_DE_WILCOXON),
            } for d in deltas},
            "medida_de_referencia_min": medida,
            "por_delta_relativo": {str(f): {"delta_min": f * medida, **_jornadas_por_delta(sd, f * medida, comparaciones_holm)}
                                   for f in deltas_relativos} if medida is not None else {},
        }
    return {"rotulo": ROTULO, "comparacion": comparacion, "potencia": 0.8, "alfa": 0.05,
            "comparaciones_holm": comparaciones_holm, "z": {"0,975": z(0.975), "0,8": z(0.8),
                                                            "holm": z(1 - 0.05 / comparaciones_holm / 2)},
            "eficiencia_de_wilcoxon": EFICIENCIA_DE_WILCOXON, "escenarios": filas}


def _escribir(ruta: pathlib.Path, contenido: Mapping[str, Any]) -> None:
    if ruta.exists():
        raise SystemExit(f"{ruta} ya existe: no se pisa")
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(ruta.name, hashlib.sha256(ruta.read_bytes()).hexdigest())


def main(argumentos: Sequence[str]) -> int:
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(dest="comando", required=True)
    r = sub.add_parser("resumir")
    r.add_argument("--escenarios", type=pathlib.Path, nargs="+", required=True)
    r.add_argument("--salida", type=pathlib.Path, required=True)
    p = sub.add_parser("potencia")
    p.add_argument("--resumen", type=pathlib.Path, required=True)
    p.add_argument("--escenarios", nargs="+", required=True)
    p.add_argument("--deltas", type=float, nargs="+", default=[2.0, 5.0, 10.0])
    p.add_argument("--comparaciones-holm", type=int, default=9)
    p.add_argument("--comparacion", default="b_menos_a",
                   choices=[f"{x[0]}_menos_{y[0]}" for x, y in COMPARACIONES])
    p.add_argument("--deltas-relativos", type=float, nargs="*", default=[])
    p.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.comando == "resumir":
        _escribir(args.salida, resumir(args.escenarios))
    else:
        resumen = json.loads(args.resumen.read_text(encoding="utf-8"))
        _escribir(args.salida, potencia(resumen, args.escenarios, args.deltas, args.comparaciones_holm,
                                           args.comparacion, args.deltas_relativos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
