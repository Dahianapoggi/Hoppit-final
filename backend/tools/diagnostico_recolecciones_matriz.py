"""Diagnóstico de recolecciones de garbage collection en la anomalía de la matriz, sin reloj (sesión 2026-10-14B, bloque G2).

Rótulo: "diagnóstico; no es una medición de tiempo".

Copia instrumentada de la lógica de medición de herramientas de planificación completa
(tools/metricas_planificacion_completa.py) que registra mediante gc.callbacks el
número exacto de recolecciones de basura de cada generación (0, 1 y 2) que ocurren
automáticamente dentro de:
  - tramo de ensamblar_instancia
  - tramo de matriz_de_costos medida sola
  - tramo de resolución con SolverVRPDeterminista

Ejecuta el orden idéntico del arnés oficial (construcción de cartografía sobre San Isidro,
warmup N1, 10 repeticiones de N1 con POST /v1/planificaciones, warmup N2 y 10 repeticiones
de N2 con desglose).

Lectura fijada de antemano:
- Si en ninguna repetición hay recolección de generación 2 dentro del tramo de la matriz,
  la hipótesis queda refutada.
- Si la hay en todas, queda compatible, no confirmada, porque el conteo no mide su duración.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import gc
import json
import logging
import pathlib
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    SIN_MARGEN,
    SolverVRPDeterminista,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.diagnostico_recolecciones_matriz")

RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO_METRICAS: Final[pathlib.Path] = RAIZ / "datos" / "tmp" / "metricas"
SALIDA_POR_DEFECTO: Final[pathlib.Path] = (
    DIRECTORIO_METRICAS / "recolecciones_matriz_2026-10-14B.json"
)

ROTULO: Final[str] = "diagnóstico; no es una medición de tiempo"
CODIFICACION: Final[str] = "utf-8"
JORNADA: Final[dt.date] = dt.date(2026, 9, 27)
REPETICIONES: Final[int] = 10
NOMBRE_DE_LA_BASE: Final[str] = "planificacion.sqlite3"


def _git(*argumentos: str) -> str:
    try:
        return subprocess.run(
            ["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True
        ).stdout.strip()
    except Exception:
        return "sin dato"


@dataclasses.dataclass
class ConteoGeneraciones:
    gen0: int = 0
    gen1: int = 0
    gen2: int = 0

    def sumar(self, generacion: int) -> None:
        if generacion == 0:
            self.gen0 += 1
        elif generacion == 1:
            self.gen1 += 1
        elif generacion == 2:
            self.gen2 += 1

    def a_dict(self) -> dict[str, int]:
        return {"gen0": self.gen0, "gen1": self.gen1, "gen2": self.gen2}


class MonitorGC:
    """Registra eventos de recolección de basura por tramo."""

    def __init__(self) -> None:
        self.activo: str | None = None
        self.conteos: dict[str, ConteoGeneraciones] = {
            "ensamblar_instancia": ConteoGeneraciones(),
            "matriz_de_costos": ConteoGeneraciones(),
            "resolver": ConteoGeneraciones(),
        }

    def callback(self, phase: str, info: dict[str, Any]) -> None:
        if phase == "start" and self.activo in self.conteos:
            gen = info.get("generation", 0)
            self.conteos[self.activo].sumar(gen)

    def activar(self, tramo: str) -> None:
        self.activo = tramo

    def desactivar(self) -> None:
        self.activo = None


def _contexto(cartografia: GrafoVialAMBA, base: pathlib.Path) -> ContextoDeLaApi:
    return construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=base,
        cartografia=cartografia,
        reloj=lambda: JORNADA,
        margen_de_capacidad=SIN_MARGEN,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )


def _en_base_nueva(
    cartografia: GrafoVialAMBA, medir: Callable[[ContextoDeLaApi], dict[str, Any]]
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        registro = medir(_contexto(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE))
        gc.collect()
    return registro


def _ejecutar_post(contexto: ContextoDeLaApi) -> dict[str, Any]:
    """Réplica de medir_post del arnés original sin cronometrar."""
    with cliente_autenticado(contexto) as cliente:
        respuesta = cliente.post("/v1/planificaciones", json={})
    respuesta.raise_for_status()
    cuerpo = respuesta.json()
    return {"plan": str(cuerpo["plan"]["id"]), "rutas": len(cuerpo["rutas"])}


def medir_desglose_con_gc(contexto: ContextoDeLaApi) -> dict[str, Any]:
    """Desglose instrumentado para contar recolecciones de GC por tramo."""
    servicio = contexto.planificacion
    monitor = MonitorGC()
    gc.callbacks.append(monitor.callback)

    try:
        # Tramo 1: ensamblar_instancia
        monitor.activar("ensamblar_instancia")
        instancia = servicio.ensamblar_instancia(contexto.deposito, contexto.flota, None, JORNADA)
        monitor.desactivar()

        # Tramo 2: matriz_de_costos sola
        monitor.activar("matriz_de_costos")
        matriz = contexto.cartografia.matriz_de_costos(instancia.matriz.nodos)
        monitor.desactivar()

        # Tramo 3: resolver
        monitor.activar("resolver")
        motor = SolverVRPDeterminista(limite_de_soluciones=LIMITE_DE_SOLUCIONES)
        solucion = motor.resolver(instancia)
        monitor.desactivar()
    finally:
        if monitor.callback in gc.callbacks:
            gc.callbacks.remove(monitor.callback)

    return {
        "nodos_de_la_matriz": len(instancia.matriz.nodos),
        "matriz_igual_a_la_de_la_instancia": matriz.como_matriz() == instancia.matriz.como_matriz(),
        "sha256_solucion": sha256_de_la_solucion(solucion),
        "distancia_m": solucion.distancia_total_m,
        "recolecciones_gc": {
            "ensamblar_instancia": monitor.conteos["ensamblar_instancia"].a_dict(),
            "matriz_de_costos": monitor.conteos["matriz_de_costos"].a_dict(),
            "resolver": monitor.conteos["resolver"].a_dict(),
        },
    }


def evaluar_criterio_predeterminado(
    repeticiones: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aplica el criterio de lectura fijado de antemano sobre recolecciones de gen 2 en la matriz."""
    total_reps = len(repeticiones)
    reps_con_gen2_en_matriz = sum(
        1
        for r in repeticiones
        if r["recolecciones_gc"]["matriz_de_costos"]["gen2"] > 0
    )

    reps_con_gen0_en_matriz = sum(
        1
        for r in repeticiones
        if r["recolecciones_gc"]["matriz_de_costos"]["gen0"] > 0
    )

    reps_con_gen1_en_matriz = sum(
        1
        for r in repeticiones
        if r["recolecciones_gc"]["matriz_de_costos"]["gen1"] > 0
    )

    if reps_con_gen2_en_matriz == 0:
        estado = "refutada"
        conclusion = (
            "La hipótesis de que una recolección automática de generación 2 ocurre dentro "
            "del tramo de matriz_de_costos medida sola queda REFUTADA (0 de 10 repeticiones)."
        )
    elif reps_con_gen2_en_matriz == total_reps:
        estado = "compatible"
        conclusion = (
            "La hipótesis queda COMPATIBLE (no confirmada, porque el conteo no mide su duración): "
            "ocurrió al menos una recolección de generación 2 en las 10 de 10 repeticiones."
        )
    else:
        estado = "parcial"
        conclusion = (
            f"La hipótesis se observó parcialmente: ocurrió recolección de generación 2 en "
            f"{reps_con_gen2_en_matriz} de {total_reps} repeticiones."
        )

    return {
        "criterio": "lectura fijada de antemano (B-224 / B-225)",
        "estado_hipotesis": estado,
        "conclusion": conclusion,
        "repeticiones_evaluadas": total_reps,
        "repeticiones_con_gen2_en_matriz": reps_con_gen2_en_matriz,
        "repeticiones_con_gen1_en_matriz": reps_con_gen1_en_matriz,
        "repeticiones_con_gen0_en_matriz": reps_con_gen0_en_matriz,
    }


def ejecutar_diagnostico_g2(salida: pathlib.Path = SALIDA_POR_DEFECTO) -> dict[str, Any]:
    """Ejecuta el protocolo de medición de recolecciones con el arnés original."""
    _LOG.info("=== INICIANDO BLOQUE G2: RECOLECCIONES DE GC EN LA ANOMALÍA DE LA MATRIZ ===")

    # 1. Cartografía sobre San Isidro
    _LOG.info("Construyendo servicio de cartografía sobre %s...", GRAFO_PREDETERMINADO.name)
    cartografia = crear_cartografia(GRAFO_PREDETERMINADO)
    cartografia.construir()

    # 2. Secuencia idéntica al arnés:
    # 2.1 Warmup N1
    _LOG.info("Ejecutando warmup N1...")
    _en_base_nueva(cartografia, _ejecutar_post)

    # 2.2 10 repeticiones N1
    _LOG.info("Ejecutando 10 repeticiones N1 (POST /v1/planificaciones)...")
    for rep in range(1, REPETICIONES + 1):
        _en_base_nueva(cartografia, _ejecutar_post)

    # 2.3 Warmup N2
    _LOG.info("Ejecutando warmup N2...")
    _en_base_nueva(cartografia, medir_desglose_con_gc)

    # 2.4 10 repeticiones N2 con conteo de GC
    _LOG.info("Ejecutando 10 repeticiones N2 con conteo de GC por tramo...")
    repeticiones: list[dict[str, Any]] = []
    for rep in range(1, REPETICIONES + 1):
        resultado = _en_base_nueva(cartografia, medir_desglose_con_gc)
        resultado["repeticion"] = rep
        repeticiones.append(resultado)
        rgc = resultado["recolecciones_gc"]
        _LOG.info(
            "  r%02d | ensamblar: g0=%d, g1=%d, g2=%d | matriz: g0=%d, g1=%d, g2=%d | resolver: g0=%d, g1=%d, g2=%d",
            rep,
            rgc["ensamblar_instancia"]["gen0"],
            rgc["ensamblar_instancia"]["gen1"],
            rgc["ensamblar_instancia"]["gen2"],
            rgc["matriz_de_costos"]["gen0"],
            rgc["matriz_de_costos"]["gen1"],
            rgc["matriz_de_costos"]["gen2"],
            rgc["resolver"]["gen0"],
            rgc["resolver"]["gen1"],
            rgc["resolver"]["gen2"],
        )

    evaluacion = evaluar_criterio_predeterminado(repeticiones)
    _LOG.info("Resultado evaluación hipótesis: %s", evaluacion["conclusion"])

    datos_salida: dict[str, Any] = {
        "rotulo": ROTULO,
        "grafo": GRAFO_PREDETERMINADO.name,
        "repeticiones": repeticiones,
        "evaluacion_hipotesis": evaluacion,
    }

    # Guardar resultados
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes((json.dumps(datos_salida, ensure_ascii=False, indent=2) + "\n").encode(CODIFICACION))
    _LOG.info("Resultados de Bloque G2 guardados en %s", salida)

    escribir_sidecar(
        registrar(
            artefacto="recolecciones_matriz_2026-10-14B",
            ambito="San Isidro (10 repeticiones con orden idéntico del arnés de métricas)",
            fuente="tools/diagnostico_recolecciones_matriz.py",
            herramienta="tools/diagnostico_recolecciones_matriz.py",
            parametros={
                "rotulo": ROTULO,
                "commit": _git("rev-parse", "HEAD"),
                "repeticiones": REPETICIONES,
                "estado_hipotesis": evaluacion["estado_hipotesis"],
            },
        ),
        salida,
    )
    _LOG.info("Sidecar registrado.")
    return datos_salida


def main() -> None:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=pathlib.Path, default=SALIDA_POR_DEFECTO)
    args = parser.parse_args()

    ejecutar_diagnostico_g2(salida=args.salida)


if __name__ == "__main__":
    main()
