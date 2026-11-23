"""Formato del artefacto del grafo de CABA y memoria de Cartografia con el
(sesion 2026-10-15A, bloques C1 y C2; B-220, punto 7). Clase: diagnostico;
insumo del tamano de la maquina. Corre en un proceso aparte, sin otra carga.

C1. Verifica que el GraphML tenga la procedencia incrustada (atributos de
grafo `procedencia_*`, como el de San Isidro, B-44) y su sidecar, con el
SHA-256 que registra el sidecar igual al del archivo; y que Cartografia lo
cargue eligiendolo de forma explicita (`crear_servicio(ruta)`, que pasa por
`seleccionar_artefacto`).

C2. Pico del conjunto de trabajo del proceso (`PeakWorkingSetSize`):
antes de nada, despues de construir Cartografia con el grafo y despues de
una matriz de 21 nodos, 20 destinos y un nodo cualquiera, sorteados con
`SEMILLA_DE_LOS_NODOS` entre los nodos de la mayor componente fuertemente
conexa ordenados por identificador. El tiempo de construir y el de la matriz
son orientativos: valen solo si se cumplen las condiciones de medicion, que
se registran.

    python tools/memoria_cartografia_caba.py --grafo GRAFO --salida SALIDA.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import random
import sys
import time
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.amazon_2021 import pico_del_proceso_mb
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.memoria_cartografia_caba")

PREFIJO_PROCEDENCIA: Final[str] = "procedencia_"
SUFIJO_SIDECAR: Final[str] = ".procedencia.json"
SEMILLA_DE_LOS_NODOS: Final[int] = 20261015
DESTINOS: Final[int] = 20
BLOQUE_HASH: Final[int] = 1 << 20
CODIFICACION: Final[str] = "utf-8"
BYTES_POR_MB: Final[int] = 2**20


def _sha256(ruta: pathlib.Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE_HASH), b""):
            h.update(bloque)
    return h.hexdigest()


def nodos_de_la_matriz(componente: list[int], destinos: int = DESTINOS, semilla: int = SEMILLA_DE_LOS_NODOS) -> list[int]:
    """`destinos` + 1 nodos distintos de la componente, sorteados con la
    semilla sobre los nodos ordenados por identificador."""
    return random.Random(semilla).sample(sorted(componente), destinos + 1)


def main(argumentos: list[str]) -> int:
    import networkx as nx

    from hoppit.cartografia.puerto import crear_servicio

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.cartografia").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--grafo", type=pathlib.Path, required=True)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    _, libre = memoria_fisica_gb()
    escribir_condiciones(args.salida.with_name(args.salida.stem + "_inicio.json"))
    pico_inicial = pico_del_proceso_mb()

    sidecar = args.grafo.with_name(args.grafo.name + SUFIJO_SIDECAR)
    procedencia_del_sidecar = json.loads(sidecar.read_text(encoding=CODIFICACION)) if sidecar.exists() else None
    sha_del_archivo = _sha256(args.grafo)

    with sin_suspension():
        cartografia = crear_servicio(args.grafo)
        inicio = time.perf_counter()
        cartografia.construir()
        construir_s = time.perf_counter() - inicio
        pico_cartografia = pico_del_proceso_mb()
        grafo = cartografia._grafo_construido()  # noqa: SLF001 - solo lectura, para la componente y la procedencia
        procedencia = sorted(k for k in grafo.graph if str(k).startswith(PREFIJO_PROCEDENCIA))
        componente = [int(n) for n in max(nx.strongly_connected_components(grafo), key=len)]
        nodos = nodos_de_la_matriz(componente)
        inicio = time.perf_counter()
        matriz = cartografia.matriz_de_costos(nodos)
        matriz_s = time.perf_counter() - inicio
        pico_matriz = pico_del_proceso_mb()

    resultado: dict[str, Any] = {
        "clase": "diagnostico; insumo del tamano de la maquina",
        "grafo": args.grafo.as_posix(),
        "tamano_mb": round(args.grafo.stat().st_size / BYTES_POR_MB, 1),
        "sha256": sha_del_archivo,
        "C1": {
            "atributos_de_procedencia_incrustados": procedencia,
            "sidecar": sidecar.name if procedencia_del_sidecar else None,
            "sha256_del_sidecar_igual_al_del_archivo": (
                procedencia_del_sidecar is not None
                and procedencia_del_sidecar.get("parametros", {}).get("sha256") == sha_del_archivo
            ),
            "carga_eligiendolo_de_forma_explicita": True,
            "nodos": grafo.number_of_nodes(),
            "arcos": grafo.number_of_edges(),
            "mayor_componente_fuerte": len(componente),
        },
        "C2": {
            "memoria_libre_al_empezar_gb": libre,
            "pico_del_proceso_mb_al_empezar": pico_inicial,
            "pico_del_proceso_mb_despues_de_construir": pico_cartografia,
            "pico_del_proceso_mb_despues_de_la_matriz": pico_matriz,
            "nodos_de_la_matriz": nodos,
            "semilla_de_los_nodos": SEMILLA_DE_LOS_NODOS,
            "pares_de_la_matriz": len(matriz.como_matriz()) ** 2,
            "construir_s_orientativo": construir_s,
            "matriz_s_orientativo": matriz_s,
        },
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding=CODIFICACION)
    escribir_condiciones(args.salida)
    _LOG.info("%s", json.dumps(resultado, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
