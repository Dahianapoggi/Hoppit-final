"""Control de que el motor del producto sigue dando, en instancias de
CVRPLIB, el mismo SHA-256 que en C1 (sesion 2026-10-13C), en un solo proceso
y sin cronometrar (sesion 2026-10-15A, bloque M8). Clase: validacion contra
instancias de referencia generadas.

Resuelve cada instancia con `SolverVRPDeterminista(usar_todos_los_vehiculos=True)`,
como C1, en serie, y compara con `tools/aceptacion_p6_cvrplib.py`: igual a C1
si el SHA-256 es el unico que registro C1 para la instancia. Por omision, las
5 primeras en orden alfabetico de las resueltas en C1. Las herramientas de
CVRPLIB no aplican margen de capacidad: arman el motor con la capacidad del
archivo `.vrp`, sin pasar por el ensamblado de Planificacion de Rutas.

    python tools/control_cvrplib_contra_c1.py --referencia-c1 datos/tmp/metricas/cvrplib_2026-10-13C.json
    python tools/control_cvrplib_contra_c1.py --referencia-c1 C1.json A-n32-k5 P-n16-k8
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from tools.aceptacion_p6_cvrplib import iguales_y_distintas, shas_de_c1
from tools.cvrplib import DIRECTORIO, como_externa, leer_vrp
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.motor_instancia_externa import instancia_del_problema

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.control_cvrplib_contra_c1")

INSTANCIAS_POR_OMISION: Final[int] = 5
DIGITOS_DEL_SHA: Final[int] = 12
CODIFICACION: Final[str] = "utf-8"


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--referencia-c1", type=pathlib.Path, required=True)
    lector.add_argument("instancias", nargs="*", help="por omision, las 5 primeras de C1 en orden alfabetico")
    args = lector.parse_args(argumentos)
    sha_c1 = shas_de_c1(json.loads(args.referencia_c1.read_text(encoding=CODIFICACION)))
    nombres = args.instancias or sorted(sha_c1)[:INSTANCIAS_POR_OMISION]
    motor = SolverVRPDeterminista(usar_todos_los_vehiculos=True)
    medidos: dict[str, str | None] = {}
    for nombre in nombres:
        problema = instancia_del_problema(como_externa(leer_vrp(next(DIRECTORIO.glob(f"*/{nombre}.vrp")))))
        medidos[nombre] = sha256_de_la_solucion(motor.resolver(problema))
        _LOG.info(
            "%s | %s | C1 %s", nombre, (medidos[nombre] or "")[:DIGITOS_DEL_SHA],
            ", ".join(s[:DIGITOS_DEL_SHA] for s in sorted(sha_c1[nombre])),
        )
    iguales, distintas = iguales_y_distintas(nombres, sha_c1, medidos)
    _LOG.info("Iguales a C1: %d de %d | distintas: %s", len(iguales), len(nombres), distintas or "ninguna")
    return 0 if not distintas else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
