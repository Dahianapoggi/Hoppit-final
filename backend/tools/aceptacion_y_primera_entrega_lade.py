"""P-04: delivery_time - accept_time segun la aceptacion sea anterior o
posterior a la primera entrega del dia (resuelve DAT-19).

DAT-19 deja dos lecturas de las ordenes aceptadas despues de la primera
entrega del dia de su transportista: una insercion real de ordenes durante la
jornada, o una aceptacion administrativa de paquetes ya cargados. Este script
mide la distribucion de delivery_time - accept_time en los dos grupos, sin
elegir entre las dos lecturas.

Criterio de grupo, el mismo del loader (analizar_aceptacion) y del codigo
oficial (time_prediction/data/dataset_delivery.py, linea 199): una orden es
"posterior" si su accept_time es mayor que la primera delivery_time de su
transportista en su ds; si no, es "anterior". La primera entrega se toma
dentro de cada archivo, porque courier_id se numera por ciudad.

Las marcas se parsean con el loader (DAT-14, DAT-15): ano relativo al ds de
la fila. Ninguna fila se excluye y no hay filtro de atipicos: el criterio no
esta definido (DAT-14). Todo agregado sobre las cinco ciudades (DAT-18).
Determinista: todas las filas, cuantiles sobre la serie completa.

    python tools/aceptacion_y_primera_entrega_lade.py --directorio datos/lade --salida p04.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

import numpy as np
import pandas as pd

from hoppit.preparacion_datos.puerto import (
    SUBCONJUNTO_ENTREGA,
    catalogo_lade,
    parsear_tiempo_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")

COLUMNA_TRANSPORTISTA = "courier_id"
COLUMNA_JORNADA = "ds"
COLUMNA_ACEPTACION = "accept_time"
COLUMNA_ENTREGA = "delivery_time"

CUANTILES = {"p5": 0.05, "p25": 0.25, "mediana": 0.5, "p75": 0.75, "p95": 0.95}
SEGUNDOS_POR_MINUTO = 60.0
NANOSEGUNDOS_POR_SEGUNDO = 1_000_000_000
DECIMALES = 2

_LOG = logging.getLogger("hoppit.tools.aceptacion_y_primera_entrega_lade")


def minutos_por_grupo(ruta: pathlib.Path) -> tuple[np.ndarray, np.ndarray]:
    """delivery_time - accept_time en minutos, para anteriores y posteriores."""
    columnas = [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA, COLUMNA_ACEPTACION, COLUMNA_ENTREGA]
    datos = pd.read_csv(ruta, usecols=columnas, dtype=str)
    aceptacion = parsear_tiempo_lade(datos[COLUMNA_ACEPTACION], datos[COLUMNA_JORNADA])
    entrega = parsear_tiempo_lade(datos[COLUMNA_ENTREGA], datos[COLUMNA_JORNADA])
    primera = entrega.groupby(
        [datos[COLUMNA_TRANSPORTISTA], datos[COLUMNA_JORNADA]], sort=False
    ).transform("min")
    posterior = (aceptacion > primera).to_numpy()
    minutos = (
        (entrega - aceptacion).to_numpy(dtype="timedelta64[ns]").astype(np.int64)
        / NANOSEGUNDOS_POR_SEGUNDO
        / SEGUNDOS_POR_MINUTO
    )
    return minutos[~posterior], minutos[posterior]


def estadisticos(minutos: np.ndarray) -> dict[str, Any]:
    """n, minimo, cuantiles pedidos y maximo, en minutos."""
    if minutos.size == 0:
        return {"n": 0}
    resultado: dict[str, Any] = {"n": int(minutos.size), "minimo": round(float(minutos.min()), DECIMALES)}
    for nombre, cuantil in CUANTILES.items():
        resultado[nombre] = round(float(np.quantile(minutos, cuantil)), DECIMALES)
    resultado["maximo"] = round(float(minutos.max()), DECIMALES)
    return resultado


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_PREDETERMINADO)
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    anteriores: list[np.ndarray] = []
    posteriores: list[np.ndarray] = []
    for archivo in catalogo_lade((SUBCONJUNTO_ENTREGA,)):
        ruta = args.directorio / archivo.subconjunto / archivo.nombre
        antes, despues = minutos_por_grupo(ruta)
        anteriores.append(antes)
        posteriores.append(despues)
        _LOG.info("P-04 | leido %s", ruta.name)

    resultado = {
        "aceptadas_antes_de_la_primera_entrega": estadisticos(np.concatenate(anteriores)),
        "aceptadas_despues_de_la_primera_entrega": estadisticos(np.concatenate(posteriores)),
    }
    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
