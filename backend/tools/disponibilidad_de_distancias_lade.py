"""B-156: si la distancia entre paradas consecutivas es derivable de LaDe-D.

Las dos propuestas de B-156 necesitan la distancia entre paradas
consecutivas de un mismo transportista en un mismo ds. Este script mide solo
si el dato esta disponible:

1. Si los archivos traen algun campo de distancia. El preprocesamiento
   oficial calcula `dis_to_last_package` con geopy a partir de lat y lng
   (route_prediction/data/preprocess.py, linea 231, commit 77ae6e7): es una
   columna derivada, no del archivo publicado.
2. Cuantos pares consecutivos tienen coordenadas completas en los dos
   extremos, tanto las de la parada (lng, lat) como las del punto GPS de la
   entrega (delivery_gps_lng, delivery_gps_lat).
3. Cuantos pares tienen la misma delivery_time en los dos extremos: en esos
   el orden de visita no lo fija el dato.

No calcula distancias, intervalos ni velocidades. Todo agregado sobre las
cinco ciudades (DAT-18). Determinista: el orden dentro de un mismo
delivery_time se desempata por order_id.

    python tools/disponibilidad_de_distancias_lade.py --directorio datos/lade --salida b156.json
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import pathlib
import sys
from typing import Any

import pandas as pd

from hoppit.preparacion_datos.puerto import (
    SUBCONJUNTO_ENTREGA,
    catalogo_lade,
    parsear_tiempo_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")

COLUMNA_ORDEN = "order_id"
COLUMNA_TRANSPORTISTA = "courier_id"
COLUMNA_JORNADA = "ds"
COLUMNA_ENTREGA = "delivery_time"
COORDENADAS = {
    "parada": ("lng", "lat"),
    "gps_de_la_entrega": ("delivery_gps_lng", "delivery_gps_lat"),
}
FRAGMENTOS_DE_NOMBRE_DE_DISTANCIA = ("dis", "dist", "km", "meter", "metro")
COORDENADA_NULA = 0.0
PORCENTAJE = 100.0
DECIMALES = 4

_LOG = logging.getLogger("hoppit.tools.disponibilidad_de_distancias_lade")


def _contar_archivo(ruta: pathlib.Path) -> tuple[list[str], collections.Counter[str]]:
    encabezado = list(pd.read_csv(ruta, nrows=0).columns)
    columnas = [COLUMNA_ORDEN, COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA, COLUMNA_ENTREGA] + [
        c for par in COORDENADAS.values() for c in par
    ]
    datos = pd.read_csv(ruta, usecols=columnas, dtype={COLUMNA_ORDEN: str, COLUMNA_TRANSPORTISTA: str, COLUMNA_JORNADA: str, COLUMNA_ENTREGA: str})
    datos[COLUMNA_ENTREGA] = parsear_tiempo_lade(datos[COLUMNA_ENTREGA], datos[COLUMNA_JORNADA])
    datos = datos.sort_values(
        [COLUMNA_TRANSPORTISTA, COLUMNA_JORNADA, COLUMNA_ENTREGA, COLUMNA_ORDEN], kind="mergesort"
    ).reset_index(drop=True)

    grupo = [datos[COLUMNA_TRANSPORTISTA], datos[COLUMNA_JORNADA]]
    mismo_grupo_que_el_anterior = (
        datos[COLUMNA_TRANSPORTISTA].eq(datos[COLUMNA_TRANSPORTISTA].shift())
        & datos[COLUMNA_JORNADA].eq(datos[COLUMNA_JORNADA].shift())
    )
    conteo: collections.Counter[str] = collections.Counter()
    conteo["filas"] = len(datos)
    conteo["grupos_transportista_ds"] = int(datos.groupby(grupo, sort=False).ngroups)
    conteo["pares_consecutivos"] = int(mismo_grupo_que_el_anterior.sum())
    conteo["pares_con_la_misma_delivery_time"] = int(
        (mismo_grupo_que_el_anterior & datos[COLUMNA_ENTREGA].eq(datos[COLUMNA_ENTREGA].shift())).sum()
    )
    for nombre, (lng, lat) in COORDENADAS.items():
        completa = datos[lng].notna() & datos[lat].notna()
        conteo[f"filas_con_{nombre}_completa"] = int(completa.sum())
        conteo[f"filas_con_{nombre}_en_cero"] = int(
            (completa & ((datos[lng] == COORDENADA_NULA) | (datos[lat] == COORDENADA_NULA))).sum()
        )
        conteo[f"pares_con_{nombre}_completa_en_ambos_extremos"] = int(
            (mismo_grupo_que_el_anterior & completa & completa.shift(fill_value=False)).sum()
        )
    return encabezado, conteo


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_PREDETERMINADO)
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    total: collections.Counter[str] = collections.Counter()
    encabezados: set[tuple[str, ...]] = set()
    for archivo in catalogo_lade((SUBCONJUNTO_ENTREGA,)):
        ruta = args.directorio / archivo.subconjunto / archivo.nombre
        encabezado, conteo = _contar_archivo(ruta)
        encabezados.add(tuple(encabezado))
        total.update(conteo)
        _LOG.info("B-156 | leido %s", ruta.name)

    columnas = sorted({c for e in encabezados for c in e})
    pares = total["pares_consecutivos"]
    resultado: dict[str, Any] = {
        "columnas_de_los_archivos": columnas,
        "columnas_con_nombre_de_distancia": [
            c for c in columnas if any(f in c.lower() for f in FRAGMENTOS_DE_NOMBRE_DE_DISTANCIA)
        ],
        "conteos": dict(total),
        "porcentaje_de_pares_con_coordenadas_completas": {
            nombre: round(total[f"pares_con_{nombre}_completa_en_ambos_extremos"] / pares * PORCENTAJE, DECIMALES)
            if pares
            else None
            for nombre in COORDENADAS
        },
        "porcentaje_de_pares_con_la_misma_delivery_time": round(
            total["pares_con_la_misma_delivery_time"] / pares * PORCENTAJE, DECIMALES
        )
        if pares
        else None,
    }
    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
