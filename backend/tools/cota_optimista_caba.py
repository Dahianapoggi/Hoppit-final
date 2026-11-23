"""Cota optimista por jornada sobre CABA (encargo 2026-11-19A, bloque 2,
punto 3): `entorno_simulacion._cota_optimista` en las jornadas y escenarios
pedidos; un JSON con una entrada por jornada. Rotulo: simulacion sobre
instancias sorteadas en CABA; no es validacion contra datos reales.

    python tools/cota_optimista_caba.py --insumos <...> --matrices <dir> --conjunto 1 \
        --escenarios R3_c08_p07 R3_c10_p07 --jornadas 50 --segundos 10 --salida <json>
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import sys
import time
from collections.abc import Sequence

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto_dinamico import cota_de_la_jornada  # noqa: E402
from tools import entrenar_dqn_etapa2 as e2  # noqa: E402

_LOG = logging.getLogger("hoppit.tools.cota_optimista_caba")


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lector.add_argument("--insumos", type=pathlib.Path, required=True)
    lector.add_argument("--matrices", type=pathlib.Path, required=True)
    lector.add_argument("--conjunto", type=int, default=1)
    lector.add_argument("--escenarios", nargs="+", required=True)
    lector.add_argument("--jornadas", type=int, default=50)
    lector.add_argument("--segundos", type=float, default=10.0)
    lector.add_argument("--tope-total-s", type=float, default=1800.0)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    contexto = e2.Contexto(args.insumos, {args.conjunto: args.matrices})
    inicio = time.perf_counter()
    res: dict[str, dict] = {}
    cortado = False
    for nombre in args.escenarios:
        res[nombre] = {"por_jornada": {}}
        for j in range(args.jornadas):
            if time.perf_counter() - inicio > args.tope_total_s:
                cortado = True
                break
            ordenes, matriz = contexto.jornada(args.conjunto, j, nombre)
            c = cota_de_la_jornada(ordenes, contexto.sorteador.deposito_nodo, matriz, contexto.tabla, contexto.parametros,
                                   args.segundos)
            res[nombre]["por_jornada"][str(j)] = dataclasses.asdict(c)
        _LOG.info("%s: %d jornadas", nombre, len(res[nombre]["por_jornada"]))
    args.salida.write_text(json.dumps({"rotulo": e2.ROTULO, "argumentos": {k: str(v) for k, v in vars(args).items()},
                                       "cortado_por_el_tope": cortado, "segundos": round(time.perf_counter() - inicio, 1),
                                       "escenarios": res}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"cortado": cortado, "sha256": hashlib.sha256(args.salida.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
