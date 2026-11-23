"""P-04b (DAT-19, B-159): donde se aceptan las ordenes aceptadas despues de la
primera entrega del dia.

DAT-19 deja abiertas dos lecturas de esas ordenes: insercion real durante la
jornada, con regreso al punto de carga, o aceptacion administrativa de
paquetes ya cargados, sobre el recorrido. Este script mide dos distancias,
sin elegir entre las lecturas:

1. entre la ubicacion GPS de aceptacion de la orden y la referencia de la
   manana de su transportista en ese ds, que es la mediana de
   `accept_gps_lng` y `accept_gps_lat` de las aceptaciones previas a la
   primera entrega (indicador del punto de carga);
2. entre la ubicacion GPS de aceptacion de la orden y su propia ubicacion GPS
   de entrega (`delivery_gps_lng`, `delivery_gps_lat`).

Criterio de grupo, el mismo de P-04 y del codigo oficial: una orden es
posterior si su `accept_time` es mayor que la primera `delivery_time` de su
transportista en su ds. Las marcas se parsean con el loader (DAT-14). Se
leen todas las filas de la ciudad, sin alcance ni filtro de atipicos, como
en P-04. Las coordenadas nulas o en cero se excluyen de cada distancia y se
cuentan. Distancia geodesica WGS-84, la misma de la transformacion.

Solo la ciudad elegida (B-162); nada por ciudad fuera de ella (DAT-18).

    python tools/ubicacion_de_aceptacion_lade.py --ciudad hz --salida p04b.json
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
    SUBCONJUNTO_ENTREGA,
    ArchivoLaDe,
    distancia_geodesica_m,
    parsear_tiempo_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_LADE = pathlib.Path("datos/lade")

TRANSPORTISTA = "courier_id"
JORNADA = "ds"
ACEPTACION = "accept_time"
ENTREGA = "delivery_time"
ACEPTACION_GPS = ("accept_gps_lng", "accept_gps_lat")
ENTREGA_GPS = ("delivery_gps_lng", "delivery_gps_lat")
REFERENCIA = ("referencia_lng", "referencia_lat")
COLUMNAS = (TRANSPORTISTA, JORNADA, ACEPTACION, ENTREGA) + ACEPTACION_GPS + ENTREGA_GPS

COORDENADA_NULA = 0.0
CUANTILES = {"p5": 0.05, "p25": 0.25, "mediana": 0.5, "p75": 0.75, "p95": 0.95}
DECIMALES = 1

_LOG = logging.getLogger("hoppit.tools.ubicacion_de_aceptacion_lade")


def _valida(datos: pd.DataFrame, par: tuple[str, str]) -> pd.Series:
    lng, lat = par
    return (
        datos[lng].notna()
        & datos[lat].notna()
        & (datos[lng] != COORDENADA_NULA)
        & (datos[lat] != COORDENADA_NULA)
    )


def _distribucion(metros: np.ndarray) -> dict[str, Any]:
    if metros.size == 0:
        return {"n": 0}
    return {"n": int(metros.size)} | {
        nombre: round(float(np.quantile(metros, q)), DECIMALES) for nombre, q in CUANTILES.items()
    }


def _distancias(
    datos: pd.DataFrame, origen: tuple[str, str], destino: tuple[str, str]
) -> tuple[dict[str, Any], int]:
    validas = _valida(datos, origen) & _valida(datos, destino)
    elegidas = datos[validas]
    metros = distancia_geodesica_m(
        elegidas[origen[0]].to_numpy(),
        elegidas[origen[1]].to_numpy(),
        elegidas[destino[0]].to_numpy(),
        elegidas[destino[1]].to_numpy(),
    )
    return _distribucion(metros), int((~validas).sum())


def medir(ruta: pathlib.Path) -> dict[str, Any]:
    datos = pd.read_csv(ruta, usecols=list(COLUMNAS), dtype={TRANSPORTISTA: str, JORNADA: str, ACEPTACION: str, ENTREGA: str})
    for columna in (ACEPTACION, ENTREGA):
        datos[columna] = parsear_tiempo_lade(datos[columna], datos[JORNADA])
    claves = [TRANSPORTISTA, JORNADA]
    primera = datos.groupby(claves)[ENTREGA].transform("min")
    posterior = datos[ACEPTACION] > primera

    anteriores = datos[~posterior & _valida(datos, ACEPTACION_GPS)]
    referencia = (
        anteriores.groupby(claves)[list(ACEPTACION_GPS)]
        .median()
        .rename(columns=dict(zip(ACEPTACION_GPS, REFERENCIA)))
    )
    posteriores = datos[posterior].join(referencia, on=claves)
    sin_referencia = int(posteriores[REFERENCIA[0]].isna().sum())

    a_la_referencia, excluidas_referencia = _distancias(posteriores, ACEPTACION_GPS, REFERENCIA)
    a_la_entrega, excluidas_entrega = _distancias(posteriores, ACEPTACION_GPS, ENTREGA_GPS)
    return {
        "filas": int(len(datos)),
        "ordenes_posteriores_a_la_primera_entrega": int(posterior.sum()),
        "posteriores_sin_referencia_de_la_manana": sin_referencia,
        "aceptacion_a_referencia_de_la_manana_m": a_la_referencia,
        "excluidas_de_la_primera_distancia": excluidas_referencia,
        "aceptacion_a_su_entrega_m": a_la_entrega,
        "excluidas_de_la_segunda_distancia": excluidas_entrega,
        "coordenadas_nulas_o_en_cero_en_la_ciudad": {
            "aceptacion_gps": int((~_valida(datos, ACEPTACION_GPS)).sum()),
            "entrega_gps": int((~_valida(datos, ENTREGA_GPS)).sum()),
        },
    }


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directorio", type=pathlib.Path, default=DIRECTORIO_LADE)
    parser.add_argument("--ciudad", required=True, choices=sorted(CIUDADES_LADE))
    parser.add_argument("--salida", type=pathlib.Path, default=None)
    args = parser.parse_args(argumentos)
    archivo = ArchivoLaDe(
        subconjunto=SUBCONJUNTO_ENTREGA, codigo_ciudad=args.ciudad, ciudad=CIUDADES_LADE[args.ciudad]
    )
    resultado = {"ciudad": archivo.ciudad} | medir(args.directorio / SUBCONJUNTO_ENTREGA / archivo.nombre)
    texto = json.dumps(resultado, indent=2, ensure_ascii=False)
    _LOG.info("Resultado:\n%s", texto)
    if args.salida is not None:
        args.salida.write_text(texto, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
