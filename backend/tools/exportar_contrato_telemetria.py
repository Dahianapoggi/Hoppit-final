"""Exporta el esquema OpenAPI del servicio de telemetria y seguimiento, en un
archivo aparte del del nucleo (2026-10-27B, T; regla 14 del encargo).

El servicio tiene su propia aplicacion y su propia version de contrato; su
esquema se regenera desde la aplicacion y se valida con
`openapi-spec-validator`, como el del nucleo (B-102).

    python tools/exportar_contrato_telemetria.py
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from openapi_spec_validator import validate

from hoppit.frontera.telemetria.puerto import crear_app_de_telemetria
from hoppit.seguimiento_flota.puerto import crear_servicio
from tools.acceso_en_proceso import clave_efimera

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.exportar_contrato_telemetria")

DESTINO = pathlib.Path(__file__).resolve().parents[1] / "contrato" / "telemetria" / "openapi.json"


def esquema() -> dict:  # type: ignore[type-arg]
    """El esquema, generado desde la aplicacion con una clave efimera: la
    clave no aparece en el contrato."""
    return crear_app_de_telemetria(crear_servicio(), clave_efimera().public_key()).openapi()


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    contenido = esquema()
    validate(contenido)
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    operaciones = sum(
        1 for metodos in contenido["paths"].values() for m in metodos if m in {"get", "post", "put", "delete", "patch"}
    )
    _LOG.info("Esquema de telemetria en %s | %d operaciones | valido", DESTINO, operaciones)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
