"""Da de alta un usuario (seccion 4.1 del diseno de Chat A; D5 del 18/10).

Sin alta por interfaz: los usuarios se crean con esta herramienta, sobre la
base del nucleo. Se guarda lo minimo --usuario, nombre visible, rol y el hash
Argon2id de la contrasena--, ni correo ni telefono. La contrasena se pide dos
veces sin mostrarla, o se lee de una variable de entorno con
`--contrasena-desde-variable`; tiene que tener al menos 12 caracteres, sin
reglas de composicion.

Se puede correr con el servidor levantado: escribe en la misma base y el
servidor lee `usuarios` en cada pedido.

    python tools/crear_usuario.py --usuario <usuario> --rol operador --nombre "<nombre visible>"
    python tools/crear_usuario.py --usuario <usuario> --rol transportista --nombre "<nombre>" \\
        --contrasena-desde-variable NOMBRE_DE_VARIABLE
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.frontera.identidad.puerto import (
    ContrasenaInvalida,
    Rol,
    UsuarioExistente,
    crear_administrador_de_usuarios,
)
from tools.argumentos_de_usuarios import (
    ContrasenaNoObtenida,
    agregar_argumentos_comunes,
    obtener_contrasena,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.crear_usuario")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    agregar_argumentos_comunes(parser, con_contrasena=True)
    parser.add_argument("--rol", required=True, choices=[rol.value for rol in Rol])
    parser.add_argument("--nombre", required=True, help="Nombre visible en la interfaz.")
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        contrasena = obtener_contrasena(args.contrasena_desde_variable)
        usuario = crear_administrador_de_usuarios(args.base_de_datos).crear_usuario(
            args.usuario, args.nombre, Rol(args.rol), contrasena
        )
    except (ContrasenaNoObtenida, ContrasenaInvalida, UsuarioExistente) as error:
        _LOG.error("%s", error)
        return 1
    _LOG.info(
        "Usuario creado | %s | rol %s | %s | base %s",
        usuario.usuario,
        usuario.rol.value,
        usuario.id,
        args.base_de_datos,
    )
    _LOG.info("Al primer ingreso tiene que aceptar los terminos vigentes de su rol.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
