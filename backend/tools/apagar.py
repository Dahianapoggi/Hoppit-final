"""Apaga la maquina sin perder datos (seccion 1.10 de ARQ_Pedido2;
2026-10-27B, D): respaldo de las dos bases, `docker compose stop` (cierre
limpio de SQLite) y `az vm deallocate`, que deja de cobrar el computo.

El grupo de recursos y la maquina son los que publica Terraform en sus
salidas; el destino SSH no tiene valor por omision.

    python tools/apagar.py --destino <usuario>@<ip> --simular
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.operacion_remota import (
    COMPOSE,
    DIRECTORIO_COMPOSE,
    VARIABLE_DESTINO_SSH,
    FaltaDato,
    Orden,
    dato_requerido,
    ejecutar,
    ordenes_de_respaldo,
    por_ssh,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.apagar")

# Los nombres que da infra/terraform con el prefijo por omision.
GRUPO_PREDETERMINADO = "hoppit-demostracion"
MAQUINA_PREDETERMINADA = "hoppit-maquina"


def ordenes_de_apagado(destino: str, grupo: str, maquina: str) -> list[Orden]:
    return [
        *ordenes_de_respaldo(destino),
        por_ssh(destino, f"cd {DIRECTORIO_COMPOSE} && {' '.join(COMPOSE)} stop", "cierre limpio de los servicios"),
        Orden(
            ("az", "vm", "deallocate", "--resource-group", grupo, "--name", maquina),
            "desasignacion de la maquina",
        ),
    ]


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--destino", default=None, help=f"usuario@ip de la maquina, o {VARIABLE_DESTINO_SSH}.")
    parser.add_argument("--grupo", default=GRUPO_PREDETERMINADO)
    parser.add_argument("--maquina", default=MAQUINA_PREDETERMINADA)
    parser.add_argument("--simular", action="store_true", help="Imprime las ordenes sin ejecutarlas.")
    args = parser.parse_args(argumentos)
    try:
        destino = dato_requerido(args.destino, VARIABLE_DESTINO_SSH, "el destino SSH")
    except FaltaDato as error:
        _LOG.error("%s", error)
        return 1
    ejecutar(ordenes_de_apagado(destino, args.grupo, args.maquina), simular=args.simular)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
