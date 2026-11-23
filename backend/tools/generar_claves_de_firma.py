"""Genera el par de claves Ed25519 con que el nucleo firma las sesiones.

Las escribe en la carpeta local del usuario (`%LOCALAPPDATA%\\hoppit\\claves` en
Windows, `~/.local/share/hoppit/claves` en los demas), fuera del repositorio y
de OneDrive, o en el directorio de la
variable de entorno `HOPPIT_CLAVES_DE_FIRMA`, o en el que se indique con
`--directorio`. No reemplaza claves existentes: una clave nueva invalida
todas las sesiones emitidas con la anterior, asi que rotarlas es borrar los
dos archivos a mano y volver a correr esto.

`tools/servir_api.py` lee la clave privada de ese directorio al arrancar y,
si no esta, falla con un mensaje que remite a esta herramienta: el servidor
nunca genera una clave por su cuenta.

    python tools/generar_claves_de_firma.py
    python tools/generar_claves_de_firma.py --directorio <carpeta>
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.frontera.identidad.puerto import (
    ClavesDeFirmaExistentes,
    directorio_de_claves,
    escribir_par_de_claves,
    generar_clave_privada,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.generar_claves_de_firma")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directorio",
        type=pathlib.Path,
        default=None,
        help="Donde escribir el par. Sin indicarlo, el de HOPPIT_CLAVES_DE_FIRMA o la carpeta local del usuario.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    directorio = args.directorio if args.directorio is not None else directorio_de_claves()
    try:
        privada, publica = escribir_par_de_claves(generar_clave_privada(), directorio)
    except ClavesDeFirmaExistentes as error:
        _LOG.error("%s", error)
        return 1
    _LOG.info("Clave privada: %s", privada)
    _LOG.info("Clave publica: %s", publica)
    _LOG.info("La clave privada no se versiona ni se comparte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
