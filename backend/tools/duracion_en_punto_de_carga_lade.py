"""P-10: duracion en el punto de carga (sesion 2026-10-19A, bloque P10;
DAT-41, insumo de la recarga de la etapa 2). Solo lectura: mide, no propone
ningun valor para el producto.

En LaDe-D de Hangzhou, alcance de B-162, dentro de los intervalos excluidos
por regreso (B-175): los intervalos de R1 que el corte por aceptacion saca de
R2 (B-187), es decir, los que contienen una aceptacion del mismo transportista
en el mismo `ds`, con las aceptaciones tomadas de todas sus filas. Por
intervalo, con el grupo de aceptaciones que caen entre su finalizacion previa
y la siguiente:

- (a) minutos entre la primera y la ultima aceptacion del grupo (cota
  inferior de la permanencia en el punto de carga);
- (b) minutos entre la finalizacion previa y la primera aceptacion (incluye el
  viaje de regreso, que LaDe no separa);
- (c) cantidad de aceptaciones del grupo.

Todo por cuantiles, sin filtro y con el filtro de distancia: el grupo entra
solo si todas sus aceptaciones tienen GPS valido a no mas de
`UMBRAL_DE_DISTANCIA_A_LA_REFERENCIA_M` de la referencia de la manana de su
transportista y `ds` (B-169: mediana de las coordenadas GPS de las
aceptaciones previas a la primera entrega; `referencia_de_la_manana` de
`tools/primer_tramo_lade.py`). El encargo no fija el umbral: se toma el p95 de
la distancia de B-169 (13,5 m), elegido en esta sesion antes de correr.

Reservas de DAT-41, punto 3: el GPS de aceptacion puede registrar la
estacion sin que el transportista este ahi (B-183, punto 3); (b) incluye el
viaje de regreso; es la carga de un courier en una estacion de Hangzhou, no la
de un furgon en un Deposito de CABA.

Cifras: [datos reales de otra ciudad (LaDe, Hangzhou); no es validacion en
CABA] (DAT-37). Cargar LaDe exige 4 GB libres, comprobados en una orden
aparte.

    python tools/duracion_en_punto_de_carga_lade.py --salida datos/tmp/metricas/p10_duracion_en_punto_de_carga_2026-10-19A.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, Final

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import (
    acotar_alcance,
    aplicar_cortes,
    distancia_geodesica_m,
    escribir_sidecar,
    intervalos_entre_finalizaciones,
    leer_entregas,
    registrar,
)
from tools.primer_tramo_lade import (
    ACEPTACION,
    ACEPTACION_GPS,
    CLAVES,
    COORDENADA_NULA,
    DIRECTORIO_LADE,
    ESCALA_DEL_GRUPO_S,
    FINALIZACION,
    FINALIZACION_PREVIA,
    REFERENCIA,
    codigos_de_grupo,
    referencia_de_la_manana,
    segundos,
)
from tools.sensibilidad_corte_regreso_lade import ID, ruta_de_lade

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.duracion_en_punto_de_carga_lade")

UMBRAL_DE_DISTANCIA_A_LA_REFERENCIA_M: Final[float] = 13.5
CUANTILES: Final[dict[str, float]] = {
    "p5": 5, "p10": 10, "p25": 25, "p50": 50, "p75": 75, "p90": 90, "p95": 95,
}
SEGUNDOS_POR_MINUTO: Final[float] = 60.0
DECIMALES: Final[int] = 2
ROTULO: Final[str] = "datos reales de otra ciudad (LaDe, Hangzhou); no es validacion en CABA"
RESERVAS: Final[tuple[str, ...]] = (
    "el GPS de aceptacion puede registrar la estacion sin que el transportista este ahi (B-183, punto 3)",
    "(b) incluye el viaje de regreso, que LaDe no separa",
    "es la carga de un courier en una estacion de Hangzhou, no la de un furgon en un Deposito de CABA",
)


def cuantiles(valores: np.ndarray) -> dict[str, float]:
    if len(valores) == 0:
        return {}
    return {nombre: round(float(np.percentile(valores, p)), DECIMALES) for nombre, p in CUANTILES.items()}


def grupos_de_aceptaciones(
    aceptaciones_s: np.ndarray, previa_s: np.ndarray, siguiente_s: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Para cada intervalo, el rango [desde, hasta) de las aceptaciones
    ordenadas que caen en (previa, siguiente], con los instantes ya
    desplazados por el codigo de grupo."""
    desde = np.searchsorted(aceptaciones_s, previa_s, side="right")
    hasta = np.searchsorted(aceptaciones_s, siguiente_s, side="right")
    return desde, hasta


def medidas_del_grupo(
    aceptaciones_s: np.ndarray, desde: np.ndarray, hasta: np.ndarray, previa_s: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(a), (b) en minutos y (c) por intervalo; cada intervalo tiene al menos
    una aceptacion."""
    cantidad = hasta - desde
    if (cantidad < 1).any():
        raise ValueError("un intervalo excluido por regreso no tiene aceptacion adentro")
    primera = aceptaciones_s[desde]
    ultima = aceptaciones_s[hasta - 1]
    return (ultima - primera) / SEGUNDOS_POR_MINUTO, (primera - previa_s) / SEGUNDOS_POR_MINUTO, cantidad


def todas_cerca(lejos_o_invalidas: np.ndarray, desde: np.ndarray, hasta: np.ndarray) -> np.ndarray:
    """Si ninguna aceptacion del rango esta lejos o sin GPS valido."""
    acumuladas = np.concatenate([[0], np.cumsum(lejos_o_invalidas.astype(np.int64))])
    return (acumuladas[hasta] - acumuladas[desde]) == 0


def main(argumentos: Sequence[str]) -> int:
    from tools.amazon_2021 import pico_del_proceso_mb

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(list(argumentos))
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")

    ruta = ruta_de_lade(args.directorio)
    entregas = leer_entregas(ruta)
    gps = pd.read_csv(ruta, usecols=list(ACEPTACION_GPS))
    for columna in ACEPTACION_GPS:
        entregas[columna] = pd.to_numeric(gps[columna], errors="coerce").to_numpy()
    del gps
    referencia, _ = referencia_de_la_manana(entregas)

    en_alcance, _ = acotar_alcance(entregas)
    intervalos = intervalos_entre_finalizaciones(en_alcance)
    del en_alcance
    intervalos[ID] = np.arange(len(intervalos))
    r2, cortes_r2 = aplicar_cortes(intervalos, entregas)
    sin_aceptaciones = entregas[ACEPTACION].where(entregas[ACEPTACION].isna())
    r1, cortes_r1 = aplicar_cortes(intervalos, entregas.assign(**{ACEPTACION: sin_aceptaciones}))
    del intervalos
    excluidos = r1[~r1[ID].isin(r2[ID])]
    del r1, r2

    con_aceptacion = entregas[entregas[ACEPTACION].notna()].join(referencia, on=CLAVES)
    grupo_aceptacion, grupo_intervalo = codigos_de_grupo(con_aceptacion, excluidos)
    instantes = grupo_aceptacion * ESCALA_DEL_GRUPO_S + segundos(con_aceptacion[ACEPTACION])
    orden = np.argsort(instantes, kind="stable")
    instantes = instantes[orden]
    lng, lat = (con_aceptacion[c].to_numpy(dtype=float)[orden] for c in ACEPTACION_GPS)
    ref_lng, ref_lat = (con_aceptacion[c].to_numpy(dtype=float)[orden] for c in REFERENCIA)
    validas = np.isfinite(lng) & np.isfinite(lat) & (lng != COORDENADA_NULA) & (lat != COORDENADA_NULA) & np.isfinite(ref_lng)
    distancia = np.full(len(instantes), np.nan)
    distancia[validas] = np.asarray(
        distancia_geodesica_m(ref_lng[validas], ref_lat[validas], lng[validas], lat[validas]), dtype=float
    )
    lejos_o_invalidas = ~validas | (distancia > UMBRAL_DE_DISTANCIA_A_LA_REFERENCIA_M)

    base = grupo_intervalo * ESCALA_DEL_GRUPO_S
    previa = segundos(excluidos[FINALIZACION_PREVIA])
    siguiente = segundos(excluidos[FINALIZACION])
    desde, hasta = grupos_de_aceptaciones(instantes, base + previa, base + siguiente)
    a, b, c = medidas_del_grupo(instantes, desde, hasta, base + previa)
    cerca = todas_cerca(lejos_o_invalidas, desde, hasta)
    distancias_en_los_grupos = np.concatenate([distancia[d:h] for d, h in zip(desde, hasta)]) if len(desde) else np.array([])

    resultado: dict[str, Any] = {
        "rotulo": ROTULO,
        "reservas": list(RESERVAS),
        "umbral_de_distancia_a_la_referencia_m": UMBRAL_DE_DISTANCIA_A_LA_REFERENCIA_M,
        "umbral_origen": "p95 de la distancia de B-169; elegido en la sesion 2026-10-19A antes de correr",
        "intervalos_r1": cortes_r1.retenidos,
        "intervalos_r2": cortes_r2.retenidos,
        "intervalos_excluidos_por_regreso": int(len(excluidos)),
        "sin_filtro": {
            "intervalos_usados": int(len(a)),
            "a_primera_a_ultima_aceptacion_min": cuantiles(a),
            "b_finalizacion_previa_a_primera_aceptacion_min": cuantiles(b),
            "c_aceptaciones_del_grupo": cuantiles(c.astype(float)),
            "c_media": round(float(c.mean()), DECIMALES),
            "a_igual_a_cero_fraccion": round(float((a == 0).mean()), 4),
        },
        "con_filtro": {
            "intervalos_usados": int(cerca.sum()),
            "intervalos_descartados": int((~cerca).sum()),
            "a_primera_a_ultima_aceptacion_min": cuantiles(a[cerca]),
            "b_finalizacion_previa_a_primera_aceptacion_min": cuantiles(b[cerca]),
            "c_aceptaciones_del_grupo": cuantiles(c[cerca].astype(float)),
            "c_media": round(float(c[cerca].mean()), DECIMALES) if cerca.any() else None,
            "a_igual_a_cero_fraccion": round(float((a[cerca] == 0).mean()), 4) if cerca.any() else None,
        },
        "distancia_de_las_aceptaciones_de_los_grupos_a_la_referencia_m": cuantiles(
            distancias_en_los_grupos[np.isfinite(distancias_en_los_grupos)]),
        "aceptaciones_de_los_grupos_sin_gps_valido_o_sin_referencia": int((~np.isfinite(distancias_en_los_grupos)).sum()),
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(resultado, ensure_ascii=False, indent=1) + "\n"
    args.salida.write_bytes(texto.encode("utf-8"))
    escribir_sidecar(
        registrar(artefacto="p10_duracion_en_punto_de_carga", ambito="Hangzhou", fuente="LaDe-D, delivery_hz.csv",
                  herramienta="tools/duracion_en_punto_de_carga_lade.py",
                  parametros={"sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(), "rotulo": ROTULO,
                              "umbral_m": UMBRAL_DE_DISTANCIA_A_LA_REFERENCIA_M,
                              "versiones": {"numpy": np.__version__, "pandas": pd.__version__}}),
        args.salida,
    )
    _LOG.info("P10 | %s", json.dumps({k: v for k, v in resultado.items() if k not in ("reservas",)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
