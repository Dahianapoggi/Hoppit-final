"""Restablece la contrasena de un usuario y levanta su bloqueo, si lo tenia.

Es lo que la pantalla de ingreso le indica a quien olvido su contrasena:
pedirle al administrador que la restablezca. La contrasena nueva se pide
dos veces sin mostrarla, o se lee de una variable de entorno con
`--contrasena-desde-variable`.

    python tools/restablecer_contrasena.py --usuario <usuario>
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.frontera.identidad.puerto import (
    ContrasenaInvalida,
    UsuarioInexistente,
    crear_administrador_de_usuarios,
)
from tools.argumentos_de_usuarios import (
    ContrasenaNoObtenida,
    agregar_argumentos_comunes,
    obtener_contrasena,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.restablecer_contrasena")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    agregar_argumentos_comunes(parser, con_contrasena=True)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        contrasena = obtener_contrasena(args.contrasena_desde_variable)
        usuario = crear_administrador_de_usuarios(args.base_de_datos).restablecer_contrasena(
            args.usuario, contrasena
        )
    except (ContrasenaNoObtenida, ContrasenaInvalida, UsuarioInexistente) as error:
        _LOG.error("%s", error)
        return 1
    _LOG.info("Contrasena restablecida | %s | base %s", usuario.usuario, args.base_de_datos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
