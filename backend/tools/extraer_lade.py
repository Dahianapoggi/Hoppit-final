"""Baja los CSV de LaDe desde Hugging Face y deja un sidecar por archivo.

Descarga las cinco ciudades del o los subconjuntos pedidos: elegir ciudad es
la decision abierta DAT-08 y no se toma desde una linea de comandos.

    python tools/extraer_lade.py --directorio-salida datos/lade
    python tools/extraer_lade.py --directorio-salida datos/lade --subconjunto delivery
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from hoppit.preparacion_datos.puerto import (
    SUBCONJUNTOS_LADE,
    extraer_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")
BYTES_POR_MB = 1_000_000

_LOG = logging.getLogger("hoppit.tools.extraer_lade")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directorio-salida",
        type=pathlib.Path,
        default=DIRECTORIO_PREDETERMINADO,
        help="Destino de la copia local del dataset.",
    )
    parser.add_argument(
        "--subconjunto",
        action="append",
        choices=list(SUBCONJUNTOS_LADE),
        default=None,
        help="Subconjunto a bajar; repetible. Sin esto, baja los dos.",
    )
    parser.add_argument(
        "--volver-a-descargar",
        action="store_true",
        help="Rehace la descarga aunque el archivo ya este en disco.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    subconjuntos = tuple(args.subconjunto) if args.subconjunto else SUBCONJUNTOS_LADE
    descargados = extraer_lade(
        args.directorio_salida,
        subconjuntos=subconjuntos,
        volver_a_descargar=args.volver_a_descargar,
    )

    total = sum(descarga.bytes_escritos for descarga in descargados)
    _LOG.info(
        "Descarga completa | archivos=%d total=%.1f MB destino=%s",
        len(descargados),
        total / BYTES_POR_MB,
        args.directorio_salida,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
