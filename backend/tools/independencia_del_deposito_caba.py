"""Cuantas Ordenes cambian entre las instancias de CABA de un mismo indice
generadas con Depositos distintos (sesion 2026-10-19A, bloque H2; B-231,
B-236 punto 11).

Con el generador anterior, quitar el nodo del Deposito de los candidatos de
su comuna corria el sorteo por indice en esa comuna: la instancia de indice
i con dos Depositos distintos diferia en 1 a 3 Ordenes. Esta herramienta
genera las primeras N instancias de un conjunto con cada Deposito pedido y
cuenta, por par de Depositos, las Ordenes que difieren en nodo o en demanda
(la misma posicion en las dos instancias). Se corre antes y despues de
corregir el generador; registra la version del generador que encontro.

Carga el grafo de CABA (pico de unos 0,5 GB en 2026-10-15A); comprobar la
memoria libre en una orden aparte antes de lanzarla.

    python tools/independencia_del_deposito_caba.py --grafo G --comunas C --pesos P --demanda A9.json --instancias 20 --deposito D4=312105875 D5=232636987 D6=206109384 --salida datos/tmp/metricas/independencia_del_deposito_caba_antes.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import pathlib
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion import puerto as entorno
from hoppit.entorno_simulacion.puerto import (
    CONJUNTO_COMPARACION,
    DepositoDeLaInstancia,
    Huellas,
    distribucion_de_demanda,
    generar_instancia,
    validar_pesos,
)
from tools.generar_instancias_caba import (
    CAPACIDAD_B70,
    TOTAL_CENSO_2022_CABA,
    _sha256,
    asignar_nodos,
    descriptor_del_deposito,
    nodos_de_la_componente_mayor,
)

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.independencia_del_deposito_caba")

CODIFICACION: Final[str] = "utf-8"
SIN_CONSTANTE_DE_VERSION: Final[str] = "sin constante de version (generador anterior a 2026-10-19A)"
NOMBRE_CONSTANTE_DE_VERSION: Final[str] = "VERSION_DEL_GENERADOR_CABA"


def version_del_generador() -> str:
    """La constante de version del generador, o un rotulo si no existe."""
    return str(getattr(entorno, NOMBRE_CONSTANTE_DE_VERSION, SIN_CONSTANTE_DE_VERSION))


def ordenes_distintas(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[int]:
    """Posiciones de las Ordenes que difieren en nodo o en demanda entre dos
    instancias del mismo indice."""
    if len(a["ordenes"]) != len(b["ordenes"]):
        raise ValueError("las instancias no tienen la misma cantidad de Ordenes")
    return [
        posicion
        for posicion, (oa, ob) in enumerate(zip(a["ordenes"], b["ordenes"]))
        if (oa["nodo"], oa["demanda"]) != (ob["nodo"], ob["demanda"])
    ]


def comparar_por_deposito(instancias: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    """Para cada par de Depositos (en el orden dado), las Ordenes que cambian
    en cada indice y el total. `instancias` va de la etiqueta del Deposito a
    la lista de instancias por indice."""
    etiquetas = list(instancias)
    cantidades = {len(v) for v in instancias.values()}
    if len(cantidades) != 1:
        raise ValueError("cada Deposito tiene que tener la misma cantidad de instancias")
    pares: dict[str, Any] = {}
    for x, y in itertools.combinations(etiquetas, 2):
        por_indice = [len(ordenes_distintas(a, b)) for a, b in zip(instancias[x], instancias[y])]
        pares[f"{x}-{y}"] = {
            "ordenes_distintas_por_indice": por_indice,
            "total": sum(por_indice),
            "instancias_con_alguna_diferencia": sum(1 for n in por_indice if n),
        }
    return pares


def ordenes_sobre_algun_deposito(
    instancias: Mapping[str, Sequence[Mapping[str, Any]]], nodos_de_deposito: Sequence[int]
) -> int:
    """Ordenes cuyo nodo es el de alguno de los Depositos comparados, sobre
    todas las instancias generadas."""
    conjunto = set(nodos_de_deposito)
    return sum(1 for lista in instancias.values() for i in lista for o in i["ordenes"] if o["nodo"] in conjunto)


def _commit() -> str:
    resultado = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return resultado.stdout.strip() or "desconocido"


def _arbol_limpio_en_src() -> bool:
    resultado = subprocess.run(
        ["git", "status", "--porcelain", "--", "src"], capture_output=True, text=True, check=False
    )
    return resultado.returncode == 0 and not resultado.stdout.strip()


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for nombre in ("--grafo", "--comunas", "--pesos", "--demanda", "--salida"):
        lector.add_argument(nombre, type=pathlib.Path, required=True)
    lector.add_argument("--conjunto", default=CONJUNTO_COMPARACION)
    lector.add_argument("--instancias", type=int, required=True)
    lector.add_argument("--cantidad-ordenes", type=int, default=20)
    lector.add_argument("--cantidad-vehiculos", type=int, default=3)
    lector.add_argument("--capacidad", default=CAPACIDAD_B70)
    lector.add_argument("--deposito", nargs="+", required=True, help="ETIQUETA=NODO, dos o mas")
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")

    from tools.amazon_2021 import pico_del_proceso_mb

    nodos = nodos_de_la_componente_mayor(args.grafo)
    por_comuna, afuera = asignar_nodos(nodos, json.loads(args.comunas.read_text(encoding=CODIFICACION)))
    tabla = json.loads(args.pesos.read_text(encoding=CODIFICACION))
    pesos = {int(c): int(v) for c, v in tabla["pesos"].items()}
    validar_pesos(pesos, total_esperado=TOTAL_CENSO_2022_CABA)
    p09 = json.loads(args.demanda.read_text(encoding=CODIFICACION))
    demanda = distribucion_de_demanda({e: r["masa_por_valor"] for e, r in p09["por_estrato"].items()})
    huellas = Huellas(tabla_de_pesos=_sha256(args.pesos), grafo=_sha256(args.grafo), distribucion_de_demanda=_sha256(args.demanda))
    capacidad = None if args.capacidad == CAPACIDAD_B70 else int(args.capacidad)

    depositos = {}
    for pedido in args.deposito:
        etiqueta, _, nodo_texto = pedido.partition("=")
        depositos[etiqueta] = int(nodo_texto)
    instancias: dict[str, list[dict[str, Any]]] = {}
    for etiqueta, nodo in depositos.items():
        _, sha_deposito = descriptor_del_deposito(etiqueta, nodo)
        instancias[etiqueta] = [
            generar_instancia(
                conjunto=args.conjunto,
                indice=indice,
                cantidad_ordenes=args.cantidad_ordenes,
                deposito=DepositoDeLaInstancia(nodo=nodo, sha256=sha_deposito),
                cantidad_vehiculos=args.cantidad_vehiculos,
                capacidad=capacidad,
                pesos=pesos,
                nodos_por_comuna=por_comuna,
                demanda=demanda,
                huellas=huellas,
            )
            for indice in range(args.instancias)
        ]
    contenido = {
        "version_del_generador": version_del_generador(),
        "commit": _commit(),
        "src_sin_cambios_sin_commitear": _arbol_limpio_en_src(),
        "conjunto": args.conjunto,
        "instancias": args.instancias,
        "cantidad_ordenes": args.cantidad_ordenes,
        "depositos": depositos,
        "pares": comparar_por_deposito(instancias),
        "ordenes_sobre_algun_deposito": ordenes_sobre_algun_deposito(instancias, list(depositos.values())),
        "nodos_fuera_de_toda_comuna": afuera,
        "grafo": args.grafo.name,
        "pico_del_proceso_mb": pico_del_proceso_mb(),
        "rotulo": "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales",
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_bytes((json.dumps(contenido, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode(CODIFICACION))
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    escribir_sidecar(
        registrar(
            artefacto="independencia_del_deposito_caba",
            ambito="Ciudad Autonoma de Buenos Aires",
            fuente="generador de hoppit.entorno_simulacion sobre el grafo de la via A",
            herramienta="tools/independencia_del_deposito_caba.py",
            parametros={
                "version_del_generador": contenido["version_del_generador"],
                "commit": contenido["commit"],
                "sha256": _sha256(args.salida),
            },
        ),
        args.salida,
    )
    _LOG.info("Escrito %s | %s", args.salida, {k: v["total"] for k, v in contenido["pares"].items()})
    _LOG.info("Pico del proceso: %s MB", contenido["pico_del_proceso_mb"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
