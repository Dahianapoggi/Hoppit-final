"""Enciende la maquina y espera a que el servicio responda (seccion 1.10 de
ARQ_Pedido2; 2026-10-27B, D): `az vm start`, `docker compose up -d` en la
maquina y espera de `/estado`. `apagar.py` detiene el Compose antes de
desasignar, y con `restart: unless-stopped` los servicios detenidos no
vuelven solos al arrancar: hay que levantarlos (2026-11-02B). Va por
`az vm run-command`, sin SSH (2026-11-03B).

El tiempo de arranque con el grafo de CABA no esta medido: por eso la espera
tiene plazo propio y, si se vence, lo dice en vez de suponer que anduvo.

    python tools/encender.py --dominio <dominio> --simular
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.apagar import GRUPO_PREDETERMINADO, MAQUINA_PREDETERMINADA
from tools.operacion_remota import (
    COMPOSE_COMO_SERVICIO,
    DIRECTORIO_COMPOSE,
    PLAZO_DE_ESPERA_S,
    VARIABLE_DOMINIO,
    FaltaDato,
    Orden,
    dato_requerido,
    ejecutar,
    esperar_estado,
    por_run_command,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.encender")


def ordenes_de_encendido(grupo: str, maquina: str) -> list[Orden]:
    return [
        Orden(("az", "vm", "start", "--resource-group", grupo, "--name", maquina), "arranque de la maquina"),
        por_run_command(
            grupo, maquina, f"cd {DIRECTORIO_COMPOSE} && {COMPOSE_COMO_SERVICIO} up -d", "arranque de los servicios"
        ),
    ]


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dominio", default=None, help=f"Dominio del sitio, o {VARIABLE_DOMINIO}.")
    parser.add_argument("--grupo", default=GRUPO_PREDETERMINADO)
    parser.add_argument("--maquina", default=MAQUINA_PREDETERMINADA)
    parser.add_argument("--plazo-s", type=float, default=PLAZO_DE_ESPERA_S)
    parser.add_argument("--simular", action="store_true", help="Imprime las ordenes sin ejecutarlas.")
    args = parser.parse_args(argumentos)
    try:
        dominio = dato_requerido(args.dominio, VARIABLE_DOMINIO, "el dominio")
    except FaltaDato as error:
        _LOG.error("%s", error)
        return 1
    url = f"https://{dominio}/estado"
    ejecutar(ordenes_de_encendido(args.grupo, args.maquina), simular=args.simular)
    if args.simular:
        _LOG.info("Despues esperaria hasta %.0f s a que responda %s", args.plazo_s, url)
        return 0
    if esperar_estado(url, None, plazo_s=args.plazo_s):
        return 0
    _LOG.error("%s no respondio en %.0f s: revisar la maquina antes de la demostracion", url, args.plazo_s)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
