"""Desactiva un usuario (seccion 4.1 del diseno de Chat A).

No lo borra: sus aceptaciones de terminos quedan registradas. El nucleo
consulta `usuarios.activo` en cada pedido, asi que una sesion abierta del
usuario se rechaza desde el pedido siguiente, aunque su token no haya
vencido (seccion 4.3).

    python tools/desactivar_usuario.py --usuario <usuario>
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.frontera.identidad.puerto import UsuarioInexistente, crear_administrador_de_usuarios
from tools.argumentos_de_usuarios import agregar_argumentos_comunes

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.desactivar_usuario")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    agregar_argumentos_comunes(parser, con_contrasena=False)
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        usuario = crear_administrador_de_usuarios(args.base_de_datos).desactivar_usuario(args.usuario)
    except UsuarioInexistente as error:
        _LOG.error("%s", error)
        return 1
    _LOG.info("Usuario desactivado | %s | base %s", usuario.usuario, args.base_de_datos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
