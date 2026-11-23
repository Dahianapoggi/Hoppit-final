"""Reproduccion por muestreo, sin reloj, de los diagnosticos de la sesion
2026-10-14B (sesion 2026-10-15A, bloque AU3).

Rotulo: "diagnostico; no es una medicion de tiempo". Usa las herramientas de
esa sesion tal como estan, sin cambiar su logica, y compara contra los JSON
que escribieron en `datos/tmp/metricas/`:

- K2 (`tools/diagnostico_limite_de_ramas.py`): llamadas al limite, antes y
  despues de la primera solucion, ramas y SHA-256 en la instancia canonica
  (opcion de k apagada) y en A-n32-k5, P-n76-k5 y P-n101-k4 (encendida).
- E (`tools/estrategias_de_respaldo.py`): A-n61-k9 con `SAVINGS` y la opcion
  de k apagada, y P-n23-k8 con `PATH_MOST_CONSTRAINED_ARC` y la opcion
  encendida, una corrida cada una. Ademas relee el JSON de E y calcula, por
  estrategia, el mayor numero de ramas de un caso fallido, para contrastarlo
  con `peor_caso_ramas_cuando_falla`, que es una suma.
- G2 (`tools/diagnostico_recolecciones_matriz.py`): la misma secuencia del
  arnes (Cartografia, precalentamiento y 10 POST, precalentamiento del
  desglose) con 2 repeticiones del desglose en lugar de 10.

Escribe un JSON con sidecar. Los JSON de 2026-10-14B no se tocan.

    python tools/reproduccion_diagnosticos_2026_10_14B.py --salida RUTA.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ortools.constraint_solver import routing_enums_pb2

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import crear_servicio as crear_cartografia
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.cvrplib import como_externa, leer_sol, leer_vrp
from tools.diagnostico_limite_de_ramas import SolverVRPInstrumentado
from tools.diagnostico_recolecciones_matriz import (
    _ejecutar_post,
    _en_base_nueva,
    medir_desglose_con_gc,
)
from tools.estrategias_de_respaldo import resolver_con_estrategia
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.motor_instancia_externa import instancia_canonica, instancia_del_problema

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.reproduccion_diagnosticos_2026_10_14B")

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
METRICAS: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "metricas"
DIRECTORIO_CVRPLIB: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "cvrplib"
JSON_K: Final[pathlib.Path] = METRICAS / "diagnostico_limite_ramas_2026-10-14B.json"
JSON_E: Final[pathlib.Path] = METRICAS / "estrategias_de_respaldo_2026-10-14B.json"
JSON_G2: Final[pathlib.Path] = METRICAS / "recolecciones_matriz_2026-10-14B.json"

ROTULO: Final[str] = "diagnostico; no es una medicion de tiempo"
CODIFICACION: Final[str] = "utf-8"
INSTANCIAS_K2: Final[tuple[str, ...]] = ("A-n32-k5", "P-n76-k5", "P-n101-k4")
# (estrategia, instancia, opcion de k) de la muestra de E.
CASOS_E: Final[tuple[tuple[str, str, bool], ...]] = (
    ("SAVINGS", "A-n61-k9", False),
    ("PATH_MOST_CONSTRAINED_ARC", "P-n23-k8", True),
)
REPETICIONES_POST: Final[int] = 10
REPETICIONES_DESGLOSE: Final[int] = 2
CAMPOS_K2: Final[tuple[str, ...]] = (
    "llamadas_totales",
    "llamadas_antes_solucion",
    "llamadas_despues_solucion",
    "ramas_totales",
    "estrategia_primera_solucion",
    "sha256",
)


def _leer(ruta: pathlib.Path) -> Any:
    return json.loads(ruta.read_text(encoding=CODIFICACION))


def _problema(nombre: str) -> Any:
    ruta = next(DIRECTORIO_CVRPLIB.glob(f"*/{nombre}.vrp"))
    return instancia_del_problema(como_externa(leer_vrp(ruta)))


def _medidas_k2(solver: SolverVRPInstrumentado, problema: Any) -> dict[str, Any]:
    metricas = solver.resolver_con_metricas(problema)
    assert metricas.solucion is not None
    return {
        "llamadas_totales": metricas.llamadas_totales,
        "llamadas_antes_solucion": metricas.llamadas_antes_solucion,
        "llamadas_despues_solucion": metricas.llamadas_totales - metricas.llamadas_antes_solucion,
        "ramas_totales": metricas.ramas_totales,
        "estrategia_primera_solucion": metricas.estrategia,
        "sha256": sha256_de_la_solucion(metricas.solucion),
    }


def _comparar(medido: dict[str, Any], referencia: dict[str, Any], campos: tuple[str, ...]) -> dict[str, Any]:
    distintos = {c: {"medido": medido[c], "14B": referencia[c]} for c in campos if medido[c] != referencia[c]}
    return {"medido": medido, "coincide": not distintos, "distintos": distintos}


def reproducir_k2() -> dict[str, Any]:
    referencia = _leer(JSON_K)
    externa, _ = instancia_canonica()
    resultado = {
        "canonica": _comparar(
            _medidas_k2(SolverVRPInstrumentado(usar_todos_los_vehiculos=False), instancia_del_problema(externa)),
            referencia["instancia_canonica"],
            CAMPOS_K2,
        )
    }
    solver = SolverVRPInstrumentado(usar_todos_los_vehiculos=True)
    for nombre in INSTANCIAS_K2:
        resultado[nombre] = _comparar(
            _medidas_k2(solver, _problema(nombre)), referencia["cvrplib_71"][nombre], CAMPOS_K2
        )
        _LOG.info("K2 %s: coincide=%s", nombre, resultado[nombre]["coincide"])
    _LOG.info("K2 canonica: coincide=%s", resultado["canonica"]["coincide"])
    return resultado


def reproducir_e() -> dict[str, Any]:
    referencia = _leer(JSON_E)
    por_estrategia = {e["estrategia"]: e for e in referencia["estrategias_evaluadas"]}
    resultado: dict[str, Any] = {}
    for estrategia, nombre, usar_todos in CASOS_E:
        ruta = next(DIRECTORIO_CVRPLIB.glob(f"*/{nombre}.vrp"))
        _, optimo = leer_sol(ruta.with_suffix(".sol"))
        corrida = resolver_con_estrategia(
            _problema(nombre),
            getattr(routing_enums_pb2.FirstSolutionStrategy, estrategia),
            usar_todos_los_vehiculos=usar_todos,
            optimo_referencia=optimo,
        )
        medido = {
            "encontro_primera_solucion": corrida.encontro_primera_solucion,
            "ramas_primera_solucion": corrida.ramas_primera_solucion,
            "ramas_totales": corrida.ramas_totales,
            "solucion_completa": corrida.solucion_completa,
            "vehiculos_usados": corrida.vehiculos_usados,
            "costo": None if corrida.costo != corrida.costo else corrida.costo,
            "sha256": corrida.sha256,
        }
        caso = next(
            c
            for c in por_estrategia[estrategia]["casos"]
            if c["instancia"] == nombre and c["usar_todos_los_vehiculos"] is usar_todos
        )
        clave = f"{estrategia} | {nombre} | k {'encendida' if usar_todos else 'apagada'}"
        resultado[clave] = _comparar(medido, caso["repeticion_1"], tuple(medido))
        _LOG.info("E %s: coincide=%s", clave, resultado[clave]["coincide"])
    # Relectura: el campo "peor caso" es la suma, sobre los casos fallidos, de
    # la mayor de las dos repeticiones.
    relectura = {}
    for estrategia, datos in por_estrategia.items():
        fallidos = [
            max(c["repeticion_1"]["ramas_totales"], c["repeticion_2"]["ramas_totales"])
            for c in datos["casos"]
            if not c["completo_y_valido"]
        ]
        relectura[estrategia] = {
            "peor_caso_ramas_cuando_falla_en_14B": datos["peor_caso_ramas_cuando_falla"],
            "casos_fallidos": len(fallidos),
            "suma_de_ramas_de_los_casos_fallidos": sum(fallidos),
            "mayor_ramas_de_un_caso_fallido": max(fallidos) if fallidos else None,
        }
    resultado["relectura_del_campo_peor_caso"] = relectura
    return resultado


def reproducir_g2() -> dict[str, Any]:
    referencia = _leer(JSON_G2)["repeticiones"]
    cartografia = crear_cartografia(GRAFO_PREDETERMINADO)
    cartografia.construir()
    for _ in range(REPETICIONES_POST + 1):
        _en_base_nueva(cartografia, _ejecutar_post)
    _en_base_nueva(cartografia, medir_desglose_con_gc)
    repeticiones = []
    for numero in range(1, REPETICIONES_DESGLOSE + 1):
        medido = _en_base_nueva(cartografia, medir_desglose_con_gc)
        anterior = referencia[numero - 1]
        repeticiones.append(
            {
                "repeticion": numero,
                "recolecciones_gc": medido["recolecciones_gc"],
                "sha256_solucion": medido["sha256_solucion"],
                "coincide_con_14B": medido["recolecciones_gc"] == anterior["recolecciones_gc"]
                and medido["sha256_solucion"] == anterior["sha256_solucion"],
            }
        )
        _LOG.info("G2 r%d: %s", numero, repeticiones[-1])
    return {"repeticiones": repeticiones}


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit", "httpx"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    _LOG.setLevel(logging.INFO)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    salida = lector.parse_args(argumentos).salida
    if salida.exists():
        raise SystemExit(f"{salida} ya existe: no se pisa")
    contenido = {"rotulo": ROTULO, "K2": reproducir_k2(), "E": reproducir_e(), "G2": reproducir_g2()}
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    escribir_sidecar(
        registrar(
            artefacto="reproduccion_diagnosticos_2026-10-14B",
            ambito="muestra de K2, E y G2 de la sesion 2026-10-14B",
            fuente="tools/reproduccion_diagnosticos_2026_10_14B.py",
            herramienta="tools/reproduccion_diagnosticos_2026_10_14B.py",
            parametros={"rotulo": ROTULO, "comparado_con": [p.name for p in (JSON_K, JSON_E, JSON_G2)]},
        ),
        salida,
    )
    _LOG.info("Escrito %s", salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
