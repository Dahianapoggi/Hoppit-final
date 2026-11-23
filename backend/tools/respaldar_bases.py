"""Respaldo consistente de una base SQLite (seccion 1.7 de ARQ_Pedido2;
2026-10-27B, D).

Copia con `sqlite3.Connection.backup()`, que es consistente con WAL y con la
base abierta por otro proceso. Cada copia lleva la fecha UTC en el nombre y
un sidecar con su SHA-256, y se conservan las `RETENCION` mas recientes. Solo
usa la biblioteca estandar: se copia a la maquina y corre con su Python, sin
el paquete del proyecto.

    python tools/respaldar_bases.py --base datos/hoppit.sqlite3 --directorio datos/tmp/respaldos
    python tools/respaldar_bases.py --base /srv/hoppit/datos/nucleo/nucleo.sqlite3 \\
        --directorio /srv/hoppit/respaldos/nucleo --simular
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import logging
import pathlib
import sqlite3
import sys

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.respaldar_bases")

# Las 7 copias mas recientes por base (seccion 1.7).
RETENCION = 7
FORMATO_DE_FECHA = "%Y%m%dT%H%M%SZ"
SUFIJO_SIDECAR = ".sha256.json"


def nombre_de_la_copia(base: pathlib.Path, instante: dt.datetime) -> str:
    return f"{base.stem}_{instante.strftime(FORMATO_DE_FECHA)}{base.suffix}"


def respaldar(
    base: pathlib.Path,
    directorio: pathlib.Path,
    instante: dt.datetime | None = None,
    retencion: int = RETENCION,
) -> pathlib.Path:
    """Copia la base al directorio y borra las copias que exceden la retencion."""
    if not base.is_file():
        raise FileNotFoundError(f"No existe la base {base}")
    momento = instante or dt.datetime.now(dt.timezone.utc)
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / nombre_de_la_copia(base, momento)
    with contextlib.closing(sqlite3.connect(base)) as origen, contextlib.closing(
        sqlite3.connect(destino)
    ) as copia:
        origen.backup(copia)
    resumen = hashlib.sha256(destino.read_bytes()).hexdigest()
    (destino.parent / (destino.name + SUFIJO_SIDECAR)).write_text(
        json.dumps(
            {"base": base.name, "momento_utc": momento.isoformat(timespec="seconds"), "sha256": resumen},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _LOG.info("Respaldo de %s en %s | sha256 %s", base, destino, resumen)
    _aplicar_retencion(base, directorio, retencion)
    return destino


def _aplicar_retencion(base: pathlib.Path, directorio: pathlib.Path, retencion: int) -> None:
    copias = sorted(directorio.glob(f"{base.stem}_*{base.suffix}"))
    for vieja in copias[: max(len(copias) - retencion, 0)]:
        vieja.unlink()
        (vieja.parent / (vieja.name + SUFIJO_SIDECAR)).unlink(missing_ok=True)
        _LOG.info("Copia fuera de la retencion, borrada: %s", vieja.name)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=pathlib.Path, required=True)
    parser.add_argument("--directorio", type=pathlib.Path, required=True)
    parser.add_argument("--retencion", type=int, default=RETENCION)
    parser.add_argument("--simular", action="store_true", help="Dice que copiaria, sin copiar.")
    args = parser.parse_args(argumentos)
    if args.simular:
        _LOG.info(
            "Copiaria %s en %s y conservaria las %d copias mas recientes",
            args.base,
            args.directorio / nombre_de_la_copia(args.base, dt.datetime.now(dt.timezone.utc)),
            args.retencion,
        )
        return 0
    respaldar(args.base, args.directorio, retencion=args.retencion)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
