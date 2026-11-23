"""Recrea las dos bases de la maquina, sin SSH (sesion 2026-11-03B; B-272):
por `az vm run-command`, que funciona tambien desde una red que no deja
salir el 22.

En la maquina, en este orden: respalda las dos bases con
`respaldar_bases.py`, detiene el nucleo y telemetria, recrea la base del
nucleo con `recrear_instancia.py --grafo caba` dentro de la imagen, aparta la
base de telemetria (queda en el directorio de respaldos con la fecha, y el
servicio arranca con una base vacia) y vuelve a levantar el Compose.

La base nueva no tiene usuarios: `recrear_instancia.py` los borra. Despues
hay que crearlos de nuevo (infra/README.md).

    python tools/recrear_bases_remotas.py --simular
    python tools/recrear_bases_remotas.py
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.apagar import GRUPO_PREDETERMINADO, MAQUINA_PREDETERMINADA
from tools.operacion_remota import (
    BASES_EN_LA_MAQUINA,
    COMPOSE_COMO_SERVICIO,
    DIRECTORIO_COMPOSE,
    DIRECTORIO_RESPALDOS,
    Orden,
    ejecutar,
    por_run_command,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.recrear_bases_remotas")

# La base del nucleo vista desde su contenedor (infra/compose.yaml).
BASE_DEL_NUCLEO_EN_EL_CONTENEDOR = "/var/lib/hoppit/nucleo/nucleo.sqlite3"
GRAFO_DEL_PRODUCTO = "caba"


def guion_de_recreacion() -> str:
    """El guion que corre en la maquina; se detiene en el primer error."""
    telemetria = BASES_EN_LA_MAQUINA["telemetria"]
    respaldos = " && ".join(
        f"python3 {DIRECTORIO_COMPOSE}/respaldar_bases.py --base {base} --directorio {DIRECTORIO_RESPALDOS}/{servicio}"
        for servicio, base in BASES_EN_LA_MAQUINA.items()
    )
    return " && ".join(
        [
            "set -e",
            f"cd {DIRECTORIO_COMPOSE}",
            respaldos,
            f"{COMPOSE_COMO_SERVICIO} stop nucleo telemetria",
            f"{COMPOSE_COMO_SERVICIO} run --rm --no-deps nucleo python tools/recrear_instancia.py "
            f"--grafo {GRAFO_DEL_PRODUCTO} --base-de-datos {BASE_DEL_NUCLEO_EN_EL_CONTENEDOR}",
            f"for f in {telemetria} {telemetria}-wal {telemetria}-shm; do "
            f'[ -e "$f" ] && mv "$f" {DIRECTORIO_RESPALDOS}/telemetria/apartada-$(date +%Y%m%dT%H%M%S)-$(basename "$f"); done; true',
            f"{COMPOSE_COMO_SERVICIO} up -d",
        ]
    )


def ordenes_de_recreacion(grupo: str, maquina: str) -> list[Orden]:
    return [por_run_command(grupo, maquina, guion_de_recreacion(), "recreacion de las dos bases en la maquina")]


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grupo", default=GRUPO_PREDETERMINADO)
    parser.add_argument("--maquina", default=MAQUINA_PREDETERMINADA)
    parser.add_argument("--simular", action="store_true", help="Imprime las ordenes sin ejecutarlas.")
    args = parser.parse_args(argumentos)
    ejecutar(ordenes_de_recreacion(args.grupo, args.maquina), simular=args.simular)
    if not args.simular:
        _LOG.info("Bases recreadas. Faltan los usuarios: la base nueva no tiene ninguno.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
