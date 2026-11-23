"""P-08 (B-153): en LaDe-P, `accept_gps_time` frente a `accept_time`.

En LaDe-D los dos campos GPS son copias de sus marcas (B-67, B-146). En
LaDe-P, `accept_gps_time` difiere de `accept_time` en parte de las filas
(B-153). Este script describe esas filas, sin interpretarlas:

1. cuantas filas tienen `accept_gps_time` no nulo y distinto de
   `accept_time`;
2. en esas filas, el porcentaje con segundos distintos de :00 en cada
   columna;
3. la distribucion de (`accept_gps_time` - `accept_time`) en segundos:
   minimo, p1, p25, mediana, p75, p99, maximo y porcentaje de negativos.

Las marcas se parsean con el loader, con el ano relativo al `ds` de la fila
(DAT-14); ninguna fila se excluye. Solo la ciudad elegida (B-162).

    python tools/reloj_de_aceptacion_recoleccion_lade.py --ciudad hz --salida p08.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from hoppit.preparacion_datos.puerto import (
    CIUDADES_LADE,
    SUBCONJUNTO_RECOLECCION,
    ArchivoLaDe,
    parsear_tiempo_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_LADE = pathlib.Path("datos/lade")

JORNADA = "ds"
ACEPTACION = "accept_time"
ACEPTACION_GPS = "accept_gps_time"

CUANTILES = {"p1": 0.01, "p25": 0.25, "mediana": 0.5, "p75": 0.75, "p99": 0.99}
SEGUNDOS_EN_PUNTO = 0
PORCENTAJE = 100.0
DECIMALES = 4

_LOG = logging.getLogger("hoppit.tools.reloj_de_aceptacion_recoleccion_lade")


def medir(ruta: pathlib.Path) -> dict[str, Any]:
    datos = pd.read_csv(ruta, usecols=[JORNADA, ACEPTACION, ACEPTACION_GPS], dtype=str)
    aceptacion = parsear_tiempo_lade(datos[ACEPTACION], datos[JORNADA])
    aceptacion_gps = parsear_tiempo_lade(datos[ACEPTACION_GPS], datos[JORNADA])
    distintas = aceptacion_gps.notna() & aceptacion.notna() & (aceptacion_gps != aceptacion)
    n = int(distintas.sum())
    segundos = (
        (aceptacion_gps[distintas] - aceptacion[distintas]).dt.total_seconds().to_numpy()
    )
    resultado: dict[str, Any] = {
        "filas": int(len(datos)),
        "accept_gps_time_nulo": int(datos[ACEPTACION_GPS].isna().sum()),
        "accept_gps_time_no_nulo_y_distinto": n,
        "porcentaje_distintas_sobre_filas": round(n / len(datos) * PORCENTAJE, DECIMALES),
        "en_las_distintas_porcentaje_con_segundos_distintos_de_00": {
            ACEPTACION: round(
                float((aceptacion[distintas].dt.second != SEGUNDOS_EN_PUNTO).mean() * PORCENTAJE),
                DECIMALES,
            ),
            ACEPTACION_GPS: round(
                float(
                    (aceptacion_gps[distintas].dt.second != SEGUNDOS_EN_PUNTO).mean() * PORCENTAJE
                ),
                DECIMALES,
            ),
        },
    }
    if n:
        resultado["diferencia_s"] = (
            {"minimo": float(segundos.min())}
            | {nombre: float(np.quantile(segundos, q)) for nombre, q in CUANTILES.items()}
            | {
                "maximo": float(segundos.max()),
                "porcentaje_negativos": round(float((segundos < 0).mean() * PORCENTAJE), DECIMALES),
            }
        )
    return resultado


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--ciudad", required=True, choices=sorted(CIUDADES_LADE))
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    args = parser.parse_args(argumentos)
    archivo = ArchivoLaDe(
        subconjunto=SUBCONJUNTO_RECOLECCION,
        codigo_ciudad=args.ciudad,
        ciudad=CIUDADES_LADE[args.ciudad],
    )
    resultado = {"ciudad": archivo.ciudad} | medir(
        args.directorio / SUBCONJUNTO_RECOLECCION / archivo.nombre
    )
    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
