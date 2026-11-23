"""Planificacion completa contra NFR-09, en proceso y sin red (sesion
2026-10-13D, bloque N).

Clase de las cifras: requerimiento no funcional. Medicion informativa: que
mide NFR-09 sigue pendiente de decision del equipo, y esta herramienta no
cambia el requerimiento ni el codigo.

N1. `POST /v1/planificaciones` sin cuerpo, completo, con la aplicacion armada
como en tools/huella_instancia_canonica.py: `construir_contexto` por omision
(grafo y Deposito de San Isidro, Ordenes de la instancia canonica con
`SEMILLA_DE_INSTANCIA`, Prediccion de ETA con la tabla vigente), la jornada
fijada en 2026-09-27 y `TestClient`, en proceso. Cada repeticion arma su
propio contexto sobre una base SQLite nueva en un directorio temporal. Quedan
fuera del reloj el armado del contexto (que da de alta las Ordenes) y la
apertura del cliente (`with TestClient(...)`, que arranca el hilo del
cliente). La Cartografia se construye una vez y se reutiliza: no guarda
caminos entre corridas, y `matriz_de_costos` corre sus Dijkstra en cada
planificacion.

N2. Desglose con el codigo existente, cada pieza cronometrada por separado
sobre un contexto propio de cada repeticion, tambien con base nueva:
`matriz_de_costos` de Cartografia sobre los nodos de la instancia;
`ensamblar_instancia` del servicio de planificacion (normalizacion de la
solicitud, matriz y armado de la instancia), y `resolver` de
`SolverVRPDeterminista()`, el motor que arma `construir_contexto`, sobre esa
instancia.

N3. Arranque, orientativo y sin protocolo: tiempo y pico del conjunto de
trabajo del proceso al construir la Cartografia de San Isidro y al armar el
primer contexto, una vez, antes de las series.

Protocolo de B-209, punto 7 (B-211, punto 3): 1 precalentamiento descartado
por serie, N1 y despues N2, y 10 repeticiones de cada una en un orden
intercalado que fija `random.Random(SEMILLA_DEL_ORDEN).shuffle` sobre los
pares (serie, repeticion) ordenados, como en `tools/metricas_cvrplib.py`;
solo `time.perf_counter` alrededor de lo medido. La serie de 2026-10-13F
(V2, commit 3642a02) corrio sin intercalar: primero las 10 de N1 y despues
las 10 de N2.

Configuracion desde la sesion 2026-10-15A (bloque V): la de la referencia
nueva (B-227, punto 4), con el margen de capacidad de la operacion,
`MARGEN_DE_LA_OPERACION` del puerto de Planificacion de Rutas, y la huella
del POST de esa referencia. La serie de 2026-10-13F se midio con margen 0. Si al empezar hay menos de 4 GB de memoria libre,
no mide y termina con codigo 3 (regla 9 de la sesion). Registra los procesos
`python`, `bash`, `tail` y `grep` que corrian al empezar, sin contar el
propio.

Percentiles con interpolacion lineal (`numpy.percentile`, metodo "linear").

    python tools/metricas_planificacion_completa.py
"""
from __future__ import annotations

import csv
import datetime as dt
import gc
import hashlib
import io
import json
import logging
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_SOLUCIONES,
    MARGEN_DE_LA_OPERACION,
    SolverVRPDeterminista,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.amazon_2021 import pico_del_proceso_mb
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.metricas_cvrplib import sha256_de_la_solucion
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    construir_contexto,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.metricas_planificacion_completa")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
RESULTADOS = RAIZ / "datos" / "tmp" / "metricas" / "planificacion_completa_2026-10-15A.json"
MARGEN = MARGEN_DE_LA_OPERACION
SEMILLA_DEL_ORDEN = 20261015
SERIE_N1 = "N1"
SERIE_N2 = "N2"
JORNADA = dt.date(2026, 9, 27)
REPETICIONES = 10
UMBRAL_NFR_09_S = 3.0
MEMORIA_LIBRE_MINIMA_GB = 4.0
CODIGO_SIN_CONDICIONES = 3
CLASE = "requerimiento no funcional"
CODIFICACION = "utf-8"
NOMBRE_DE_LA_BASE = "planificacion.sqlite3"
PROCESOS_VIGILADOS = ("python.exe", "bash.exe", "tail.exe", "grep.exe")
# La de la huella vigente desde 2026-10-15A, con el margen de la operacion
# (tools/huella_instancia_canonica.py).
HUELLA_POST_DE_REFERENCIA = "eb229eed8617cbeee6d389c522187f1254f84efa6acc04f2163f736f5effc814"
RUIDOSOS = ("hoppit", "httpx")


def _git(*argumentos: str) -> str:
    return subprocess.run(["git", *argumentos], cwd=RAIZ, capture_output=True, text=True, check=True).stdout.strip()


def _p(valores: list[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def otros_procesos() -> list[dict[str, str]]:
    """Procesos vigilados que corren ademas de este, segun `tasklist`. Se
    excluyen este proceso y su padre, que en la venv de Windows es el
    lanzador `python.exe`. Los `bash.exe` de la orden que lo lanzo si
    aparecen: hay que distinguirlos en el informe."""
    if sys.platform != "win32":
        return []
    salida = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, encoding="oem", errors="replace", check=True
    ).stdout
    propios = {str(os.getpid()), str(os.getppid())}
    return [
        {"imagen": fila[0], "pid": fila[1]}
        for fila in csv.reader(io.StringIO(salida))
        if fila and fila[0].lower() in PROCESOS_VIGILADOS and fila[1] not in propios
    ]


def _contexto(cartografia: GrafoVialAMBA, base: pathlib.Path) -> ContextoDeLaApi:
    return construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=base,
        cartografia=cartografia,
        reloj=lambda: JORNADA,
        margen_de_capacidad=MARGEN,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )


def _en_base_nueva(cartografia: GrafoVialAMBA, medir: Callable[[ContextoDeLaApi], dict[str, Any]]) -> dict[str, Any]:
    """Arma un contexto sobre una base nueva, mide y suelta las conexiones
    antes de borrar el temporal (B-150)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        registro = medir(_contexto(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE))
        gc.collect()
    return registro


def medir_post(contexto: ContextoDeLaApi) -> dict[str, Any]:
    """N1: solo el POST, con el cliente ya abierto."""
    with cliente_autenticado(contexto) as cliente:
        inicio = time.perf_counter()
        respuesta = cliente.post("/v1/planificaciones", json={})
        segundos = time.perf_counter() - inicio
    respuesta.raise_for_status()
    cuerpo = respuesta.json()
    return {
        "segundos": segundos,
        "codigo_http": respuesta.status_code,
        "plan": str(cuerpo["plan"]["id"]),
        "sha256_del_post": hashlib.sha256(respuesta.content).hexdigest(),
        "rutas": len(cuerpo["rutas"]),
    }


def medir_desglose(contexto: ContextoDeLaApi) -> dict[str, Any]:
    """N2: matriz, ensamblado de la instancia y resolucion, por separado."""
    servicio = contexto.planificacion
    inicio = time.perf_counter()
    instancia = servicio.ensamblar_instancia(contexto.deposito, contexto.flota, None, JORNADA)
    ensamblado_s = time.perf_counter() - inicio

    inicio = time.perf_counter()
    matriz = contexto.cartografia.matriz_de_costos(instancia.matriz.nodos)
    matriz_s = time.perf_counter() - inicio

    motor = SolverVRPDeterminista()
    inicio = time.perf_counter()
    solucion = motor.resolver(instancia)
    resolver_s = time.perf_counter() - inicio
    return {
        "ensamblar_instancia_s": ensamblado_s,
        "matriz_de_costos_s": matriz_s,
        "resolver_s": resolver_s,
        "nodos_de_la_matriz": len(instancia.matriz.nodos),
        "matriz_igual_a_la_de_la_instancia": matriz.como_matriz() == instancia.matriz.como_matriz(),
        "sha256_de_la_matriz": hashlib.sha256(json.dumps(instancia.matriz.como_matriz()).encode(CODIFICACION)).hexdigest(),
        "sha256_de_la_solucion": sha256_de_la_solucion(solucion),
        "distancia_m": solucion.distancia_total_m,
    }


def _resumen(valores: list[float]) -> dict[str, float]:
    return {
        "mediana_s": _p(valores, 50),
        "p25_s": _p(valores, 25),
        "p75_s": _p(valores, 75),
        "riq_s": _p(valores, 75) - _p(valores, 25),
        "minimo_s": min(valores),
        "maximo_s": max(valores),
    }


def main(argumentos: list[str]) -> int:
    del argumentos
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in RUIDOSOS:
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    _LOG.setLevel(logging.INFO)
    total, libre = memoria_fisica_gb()
    procesos = otros_procesos()
    _LOG.info("Memoria fisica libre al empezar: %.2f de %.2f GB", libre or float("nan"), total or float("nan"))
    _LOG.info("Otros procesos vigilados al empezar: %s", procesos or "ninguno")
    if libre is None or libre < MEMORIA_LIBRE_MINIMA_GB:
        _LOG.info("Menos de %.0f GB libres: no se mide (regla 9).", MEMORIA_LIBRE_MINIMA_GB)
        return CODIGO_SIN_CONDICIONES
    with sin_suspension() as pedido:
        _LOG.info("Pedido de no suspender el equipo: %s", "hecho" if pedido else "no disponible")
        return serie(libre, procesos)


def serie(libre_al_empezar: float, procesos: list[dict[str, str]]) -> int:
    RESULTADOS.parent.mkdir(parents=True, exist_ok=True)
    if RESULTADOS.exists():
        raise SystemExit(f"{RESULTADOS} ya existe: es de otra serie; renombrarlo antes de empezar")
    escribir_condiciones(RESULTADOS.with_name(RESULTADOS.stem + "_inicio.json"))

    # N3: orientativo, una vez y fuera de las series.
    pico_antes = pico_del_proceso_mb()
    inicio = time.perf_counter()
    cartografia = crear_cartografia(GRAFO_PREDETERMINADO)
    cartografia.construir()
    cartografia_s = time.perf_counter() - inicio
    pico_cartografia = pico_del_proceso_mb()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        inicio = time.perf_counter()
        _contexto(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE)
        contexto_s = time.perf_counter() - inicio
        gc.collect()
    arranque = {
        "orientativo_sin_protocolo": True,
        "grafo": GRAFO_PREDETERMINADO.name,
        "construir_cartografia_s": cartografia_s,
        "armar_contexto_s": contexto_s,
        "pico_del_proceso_mb_antes": pico_antes,
        "pico_del_proceso_mb_despues_de_la_cartografia": pico_cartografia,
        "pico_del_proceso_mb_despues_del_contexto": pico_del_proceso_mb(),
    }
    _LOG.info("N3 | %s", arranque)

    _en_base_nueva(cartografia, medir_post)
    _en_base_nueva(cartografia, medir_desglose)
    orden = sorted((serie, repeticion) for serie in (SERIE_N1, SERIE_N2) for repeticion in range(1, REPETICIONES + 1))
    random.Random(SEMILLA_DEL_ORDEN).shuffle(orden)
    post: list[dict[str, Any]] = []
    desglose: list[dict[str, Any]] = []
    for posicion, (serie_medida, repeticion) in enumerate(orden, start=1):
        if serie_medida == SERIE_N1:
            post.append({"repeticion": repeticion, "posicion": posicion, **_en_base_nueva(cartografia, medir_post)})
            _LOG.info("  N1 r%d (%d) | %.3f s | %s", repeticion, posicion, post[-1]["segundos"], post[-1]["sha256_del_post"][:12])
        else:
            desglose.append({"repeticion": repeticion, "posicion": posicion, **_en_base_nueva(cartografia, medir_desglose)})
            _LOG.info(
                "  N2 r%d (%d) | ensamblar %.3f s | matriz %.3f s | resolver %.3f s", repeticion, posicion,
                desglose[-1]["ensamblar_instancia_s"], desglose[-1]["matriz_de_costos_s"], desglose[-1]["resolver_s"],
            )
    post.sort(key=lambda c: c["repeticion"])
    desglose.sort(key=lambda c: c["repeticion"])

    resumen = {
        "N1_post": _resumen([c["segundos"] for c in post]),
        "N2_ensamblar_instancia": _resumen([c["ensamblar_instancia_s"] for c in desglose]),
        "N2_matriz_de_costos": _resumen([c["matriz_de_costos_s"] for c in desglose]),
        "N2_resolver": _resumen([c["resolver_s"] for c in desglose]),
    }
    contenido = {
        "clase": CLASE,
        "jornada": JORNADA.isoformat(),
        "margen_de_capacidad": MARGEN,
        "semilla_del_orden": SEMILLA_DEL_ORDEN,
        "orden": [f"{serie_medida} r{repeticion}" for serie_medida, repeticion in orden],
        "memoria_libre_al_empezar_gb": libre_al_empezar,
        "otros_procesos_al_empezar": procesos,
        "arranque_N3": arranque,
        "post_N1": post,
        "desglose_N2": desglose,
        "resumen": resumen,
        "sha256_del_post_distintos": sorted({c["sha256_del_post"] for c in post}),
        "planes_distintos": sorted({c["plan"] for c in post}),
        "sha256_de_la_solucion_distintos": sorted({c["sha256_de_la_solucion"] for c in desglose}),
        "sha256_de_la_matriz_distintos": sorted({c["sha256_de_la_matriz"] for c in desglose}),
    }
    RESULTADOS.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    condiciones = escribir_condiciones(RESULTADOS)
    escribir_sidecar(
        registrar(
            artefacto="metricas_planificacion_completa",
            ambito="POST /v1/planificaciones sobre la instancia canonica, San Isidro",
            fuente="tools/metricas_planificacion_completa.py",
            herramienta="tools/metricas_planificacion_completa.py",
            parametros={
                "clase": CLASE,
                "commit": _git("rev-parse", "HEAD"),
                "arbol_limpio": _git("status", "--porcelain") == "",
                "motor": f"SolverVRPDeterminista(), limite {LIMITE_DE_SOLUCIONES}",
                "repeticiones": REPETICIONES,
                "margen_de_capacidad": MARGEN,
                "semilla_del_orden": SEMILLA_DEL_ORDEN,
                "condiciones": condiciones.name,
                "sha256": hashlib.sha256(RESULTADOS.read_bytes()).hexdigest(),
            },
        ),
        RESULTADOS,
    )
    _LOG.info("Clase: %s", CLASE)
    for nombre, r in resumen.items():
        _LOG.info(
            "%s | mediana %.3f s | RIQ %.3f s | minimo %.3f s | maximo %.3f s | menos de %.0f s: %s",
            nombre, r["mediana_s"], r["riq_s"], r["minimo_s"], r["maximo_s"], UMBRAL_NFR_09_S,
            "si en las 10" if r["maximo_s"] < UMBRAL_NFR_09_S else "NO en todas",
        )
    _LOG.info(
        "SHA-256 del POST distintos: %d (%s); igual a la huella: %s | Planes: %s",
        len(contenido["sha256_del_post_distintos"]), ", ".join(s[:12] for s in contenido["sha256_del_post_distintos"]),
        contenido["sha256_del_post_distintos"] == [HUELLA_POST_DE_REFERENCIA], ", ".join(contenido["planes_distintos"]),
    )
    _LOG.info(
        "SHA-256 de la solucion de N2 distintos: %d (%s)", len(contenido["sha256_de_la_solucion_distintos"]),
        ", ".join(s[:12] for s in contenido["sha256_de_la_solucion_distintos"]),
    )
    _LOG.info("Resultados en %s", RESULTADOS)
    unico = len(contenido["sha256_del_post_distintos"]) == 1 and len(contenido["sha256_de_la_solucion_distintos"]) == 1
    return 0 if unico else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
