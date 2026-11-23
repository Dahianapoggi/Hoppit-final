"""Genera un codigo de invitacion para registrarse por la interfaz (B-246,
B-248; 2026-10-20C, bloque R).

El codigo es de un solo uso, vence (72 h por omision) y fija el rol de la
cuenta que se cree con el: quien se registra no elige rol. Se muestra una
sola vez, aca; en la base se guarda solo su SHA-256, de modo que un codigo
perdido no se recupera: se genera otro.

Se puede correr con el servidor levantado: escribe en la misma base y el
servidor lee `invitaciones` en cada registro.

    python tools/crear_invitacion.py --rol operador
    python tools/crear_invitacion.py --rol transportista --vence-en-horas 24
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.frontera.identidad.puerto import (
    VENCIMIENTO_MAXIMO_H,
    VENCIMIENTO_POR_OMISION_H,
    Rol,
    VencimientoInvalido,
    crear_administrador_de_usuarios,
)
from tools.servir_api import BASE_PREDETERMINADA

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.crear_invitacion")


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rol", required=True, choices=[rol.value for rol in Rol])
    parser.add_argument(
        "--vence-en-horas",
        type=int,
        default=VENCIMIENTO_POR_OMISION_H,
        help=f"Vencimiento de la invitacion, de 1 a {VENCIMIENTO_MAXIMO_H} horas.",
    )
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        default=BASE_PREDETERMINADA,
        help="Base SQLite del nucleo. La ruta es siempre parametro.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        codigo, invitacion = crear_administrador_de_usuarios(args.base_de_datos).crear_invitacion(
            Rol(args.rol), args.vence_en_horas
        )
    except VencimientoInvalido as error:
        _LOG.error("%s", error)
        return 1
    _LOG.info("Invitacion creada | rol %s | vence %s (UTC) | base %s",
              invitacion.rol.value, invitacion.vence_en.isoformat(timespec="minutes"), args.base_de_datos)
    _LOG.info("Codigo (se muestra solo esta vez; es de un solo uso): %s", codigo)
    _LOG.info("Se usa en la pantalla de ingreso, en \"Crear cuenta con una invitacion\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
