"""Lo comun a las herramientas de usuarios: la base y la contrasena.

La contrasena se pide dos veces con `getpass`, que no la muestra. Para uso
no interactivo, `--contrasena-desde-variable NOMBRE` la lee de esa variable
de entorno. En ningun caso se registra ni se escribe en un archivo.
"""
from __future__ import annotations

import argparse
import getpass
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.servir_api import BASE_PREDETERMINADA

PEDIDO_DE_CONTRASENA = "Contrasena: "
PEDIDO_DE_CONFIRMACION = "Repetir la contrasena: "


class ContrasenaNoObtenida(Exception):
    """No se pudo obtener la contrasena: no coinciden, o falta la variable."""


def agregar_argumentos_comunes(parser: argparse.ArgumentParser, *, con_contrasena: bool) -> None:
    parser.add_argument("--usuario", required=True)
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        default=BASE_PREDETERMINADA,
        help="Base SQLite del nucleo. La ruta es siempre parametro.",
    )
    if con_contrasena:
        parser.add_argument(
            "--contrasena-desde-variable",
            metavar="NOMBRE_DE_VARIABLE",
            default=None,
            help="Lee la contrasena de esa variable de entorno, sin pedirla ni registrarla.",
        )


def obtener_contrasena(variable: str | None) -> str:
    if variable is not None:
        valor = os.environ.get(variable)
        if not valor:
            raise ContrasenaNoObtenida(f"La variable de entorno {variable} no esta definida o esta vacia")
        return valor
    primera = getpass.getpass(PEDIDO_DE_CONTRASENA)
    if getpass.getpass(PEDIDO_DE_CONFIRMACION) != primera:
        raise ContrasenaNoObtenida("Las dos contrasenas no coinciden")
    return primera
