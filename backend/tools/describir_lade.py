"""Describe la copia local de LaDe y mide los deltas entre finalizaciones.

Solo mide y loggea: no transforma, no elige ciudad y no interpreta ningun
estadistico. La lectura de los deltas es DAT-05; la semantica del timestamp
de agregacion es DAT-06. Las dos estan abiertas.

Tres bloques por archivo: descripcion del esquema y los nulos, deltas entre
finalizaciones consecutivas, y aceptacion de la tarea frente a su
finalizacion.

    python tools/describir_lade.py --directorio datos/lade
    python tools/describir_lade.py --directorio datos/lade --subconjunto delivery --sin-deltas
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from hoppit.preparacion_datos.puerto import (
    SUBCONJUNTOS_LADE,
    analizar_aceptacion,
    catalogo_lade,
    deltas_entre_finalizaciones,
    loggear_analisis_de_aceptacion,
    loggear_deltas,
    loggear_resumen_lade,
    resumir_lade,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/lade")

_LOG = logging.getLogger("hoppit.tools.describir_lade")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directorio",
        type=pathlib.Path,
        default=DIRECTORIO_PREDETERMINADO,
        help="Copia local del dataset.",
    )
    parser.add_argument(
        "--subconjunto",
        action="append",
        choices=list(SUBCONJUNTOS_LADE),
        default=None,
        help="Subconjunto a describir; repetible. Sin esto, los dos.",
    )
    parser.add_argument(
        "--sin-deltas",
        action="store_true",
        help="Omite el calculo de deltas entre finalizaciones consecutivas.",
    )
    parser.add_argument(
        "--sin-aceptacion",
        action="store_true",
        help="Omite el analisis de accept_time frente a la finalizacion.",
    )
    parser.add_argument(
        "--sin-corte-de-jornada",
        action="store_true",
        help="Calcula los deltas sin cortar la secuencia en el limite de ds.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    subconjuntos = tuple(args.subconjunto) if args.subconjunto else SUBCONJUNTOS_LADE
    for archivo in catalogo_lade(subconjuntos):
        ruta = args.directorio / archivo.subconjunto / archivo.nombre
        if not ruta.exists():
            _LOG.warning("No esta en disco, se saltea | %s", ruta)
            continue

        _LOG.info("=== %s ===", ruta)
        loggear_resumen_lade(resumir_lade(ruta, archivo.subconjunto))

        if not args.sin_deltas:
            loggear_deltas(
                deltas_entre_finalizaciones(
                    ruta,
                    archivo.subconjunto,
                    segmentar_por_jornada=not args.sin_corte_de_jornada,
                )
            )

        if not args.sin_aceptacion:
            loggear_analisis_de_aceptacion(
                analizar_aceptacion(ruta, archivo.subconjunto)
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
