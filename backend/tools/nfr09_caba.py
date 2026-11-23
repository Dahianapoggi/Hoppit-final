"""NFR-09 en el grafo de CABA (sesion 2026-10-15A, bloque C4; B-220, punto
3). Rotulo de las cifras: [requerimiento no funcional; instancias sorteadas
en CABA con Deposito provisional; no es validacion contra datos reales].

Por cada instancia de comparacion de `tools/generar_instancias_caba.py` (una
por Deposito provisional para medir, que no es decision): planificacion
completa, `POST /v1/planificaciones` en proceso (N1), y desglose (N2:
ensamblado, matriz sola y `resolver`), con el protocolo de
`tools/metricas_planificacion_completa.py`, cuyas funciones de medicion se
reutilizan: un precalentamiento por serie (el de N1 describe el Plan: Rutas,
distancia, Ordenes no planificadas y sus motivos), 10 repeticiones de cada
una en orden intercalado con `SEMILLA_DEL_ORDEN`, una base SQLite nueva por
repeticion, mediana, RIQ y maximo, y el SHA-256 de cada repeticion.

El sistema se arma aca y no con `construir_contexto`, que deriva la Flota y
las Ordenes del escenario de demostracion: con el grafo de CABA, la
Cartografia construida una sola vez, las Ordenes y la Flota de la instancia,
el Deposito de la instancia, el margen de la operacion y la jornada fija de
la huella. Sin Prediccion de ETA: el `POST` no lo publica.

Exige 4 GB libres al empezar (y 6 para cargar el grafo de CABA, comprobados
en una orden aparte) y registra el pico del proceso.

    python tools/nfr09_caba.py --grafo GRAFO --instancias I1.json I2.json --salida SALIDA.json
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import logging
import pathlib
import random
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.gestion_ordenes.puerto import Orden, RepositorioOrdenesSQLite, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas.puerto import (
    MARGEN_DE_LA_OPERACION,
    RepositorioRutasSQLite,
    crear_servicio as crear_planificacion,
)
from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from hoppit.shared.almacen import conectar
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, VehiculoId
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.amazon_2021 import pico_del_proceso_mb
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.metricas_planificacion_completa import (
    JORNADA,
    MEMORIA_LIBRE_MINIMA_GB,
    REPETICIONES,
    SEMILLA_DEL_ORDEN,
    SERIE_N1,
    SERIE_N2,
    _resumen,
    medir_desglose,
    medir_post,
    otros_procesos,
)

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.nfr09_caba")

ROTULO: Final[str] = (
    "requerimiento no funcional; instancias sorteadas en CABA con Deposito provisional; "
    "no es validacion contra datos reales"
)
NOMBRE_DE_LA_BASE: Final[str] = "caba.sqlite3"
CODIGO_SIN_CONDICIONES: Final[int] = 3
CODIFICACION: Final[str] = "utf-8"
RUIDOSOS: Final[tuple[str, ...]] = ("hoppit", "httpx")


def contexto_de_la_instancia(cartografia: GrafoVialAMBA, instancia: Mapping[str, Any], base: pathlib.Path) -> ContextoDeLaApi:
    """El sistema con la Cartografia dada, las Ordenes y la Flota de la
    instancia, su Deposito y el margen de la operacion, sobre una base."""
    bus = Bus()
    ordenes = crear_ordenes(bus, RepositorioOrdenesSQLite(conectar(base)))
    flota = {VehiculoId(v["identificador"]): int(v["capacidad"]) for v in instancia["flota"]}
    planificacion = crear_planificacion(
        bus,
        cartografia,
        ordenes,
        repositorio=RepositorioRutasSQLite(conectar(base)),
        flota=flota,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        reloj=lambda: JORNADA,
    )
    for orden in instancia["ordenes"]:
        ordenes.dar_de_alta(Orden(id=OrdenId(orden["identificador"]), demanda=int(orden["demanda"]), destino=int(orden["nodo"])))
    return ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=planificacion,
        cartografia=cartografia,
        deposito=int(instancia["deposito"]["nodo"]),
        flota=flota,
        identidad=identidad_efimera(),
    )


def describir_post(contexto: ContextoDeLaApi) -> dict[str, Any]:
    """El precalentamiento de N1: un POST sin cronometrar, con el Plan."""
    with cliente_autenticado(contexto) as cliente:
        respuesta = cliente.post("/v1/planificaciones", json={})
    cuerpo = respuesta.json()
    if respuesta.status_code != 201:
        return {"codigo_http": respuesta.status_code, "cuerpo": cuerpo}
    return {
        "codigo_http": respuesta.status_code,
        "plan": cuerpo["plan"]["id"],
        "distancia_total_m": cuerpo["distancia_total_m"],
        "rutas": [
            {"vehiculo": r["vehiculo_id"], "paradas": r["cantidad_paradas"], "ordenes": r["cantidad_ordenes"],
             "distancia_m": r["distancia_m"], "carga": r["carga"], "capacidad": r["capacidad_vehiculo"]}
            for r in cuerpo["rutas"]
        ],
        "ordenes_no_planificadas": cuerpo["ordenes_no_planificadas"],
        "sha256_del_post": hashlib.sha256(respuesta.content).hexdigest(),
    }


def _en_base_nueva(
    cartografia: GrafoVialAMBA, instancia: Mapping[str, Any], medir: Callable[[ContextoDeLaApi], dict[str, Any]]
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        registro = medir(contexto_de_la_instancia(cartografia, instancia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE))
        gc.collect()
    return registro


def serie_de_la_instancia(cartografia: GrafoVialAMBA, instancia: Mapping[str, Any]) -> dict[str, Any]:
    plan = _en_base_nueva(cartografia, instancia, describir_post)
    _LOG.info("Plan: %s", json.dumps(plan, ensure_ascii=False))
    _en_base_nueva(cartografia, instancia, medir_desglose)
    orden = sorted((s, r) for s in (SERIE_N1, SERIE_N2) for r in range(1, REPETICIONES + 1))
    random.Random(SEMILLA_DEL_ORDEN).shuffle(orden)
    post: list[dict[str, Any]] = []
    desglose: list[dict[str, Any]] = []
    for posicion, (serie, repeticion) in enumerate(orden, start=1):
        if serie == SERIE_N1:
            post.append({"repeticion": repeticion, "posicion": posicion, **_en_base_nueva(cartografia, instancia, medir_post)})
            _LOG.info("  N1 r%d (%d) | %.3f s | %s", repeticion, posicion, post[-1]["segundos"], post[-1]["sha256_del_post"][:12])
        else:
            desglose.append({"repeticion": repeticion, "posicion": posicion,
                             **_en_base_nueva(cartografia, instancia, medir_desglose)})
            _LOG.info("  N2 r%d (%d) | ensamblar %.3f s | matriz %.3f s | resolver %.3f s", repeticion, posicion,
                      desglose[-1]["ensamblar_instancia_s"], desglose[-1]["matriz_de_costos_s"], desglose[-1]["resolver_s"])
    post.sort(key=lambda c: c["repeticion"])
    desglose.sort(key=lambda c: c["repeticion"])
    return {
        "plan": plan,
        "orden": [f"{s} r{r}" for s, r in orden],
        "post_N1": post,
        "desglose_N2": desglose,
        "resumen": {
            "N1_post": _resumen([c["segundos"] for c in post]),
            "N2_ensamblar_instancia": _resumen([c["ensamblar_instancia_s"] for c in desglose]),
            "N2_matriz_de_costos": _resumen([c["matriz_de_costos_s"] for c in desglose]),
            "N2_resolver": _resumen([c["resolver_s"] for c in desglose]),
        },
        "sha256_del_post_distintos": sorted({c["sha256_del_post"] for c in post}),
        "sha256_de_la_solucion_distintos": sorted({c["sha256_de_la_solucion"] for c in desglose}),
        "pico_del_proceso_mb_al_terminar": pico_del_proceso_mb(),
    }


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in RUIDOSOS:
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    _LOG.setLevel(logging.INFO)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--grafo", type=pathlib.Path, required=True)
    lector.add_argument("--instancias", type=pathlib.Path, nargs="+", required=True)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    total, libre = memoria_fisica_gb()
    procesos = otros_procesos()
    _LOG.info("Memoria libre al empezar: %.2f de %.2f GB | otros procesos: %s", libre or 0.0, total or 0.0, procesos or "ninguno")
    if libre is None or libre < MEMORIA_LIBRE_MINIMA_GB:
        _LOG.info("Menos de %.0f GB libres: no se mide.", MEMORIA_LIBRE_MINIMA_GB)
        return CODIGO_SIN_CONDICIONES
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    escribir_condiciones(args.salida.with_name(args.salida.stem + "_inicio.json"))
    with sin_suspension():
        pico_antes = pico_del_proceso_mb()
        inicio = time.perf_counter()
        cartografia = crear_cartografia(args.grafo)
        cartografia.construir()
        construir_s = time.perf_counter() - inicio
        resultados = {}
        for ruta in args.instancias:
            instancia = json.loads(ruta.read_text(encoding=CODIFICACION))
            etiqueta = instancia["deposito"]["descriptor"]["etiqueta"]
            _LOG.info("Instancia %s (Deposito provisional %s, nodo %s)", ruta.name, etiqueta, instancia["deposito"]["nodo"])
            resultados[etiqueta] = {"instancia": ruta.name, "deposito_nodo": instancia["deposito"]["nodo"],
                                    **serie_de_la_instancia(cartografia, instancia)}
    contenido = {
        "rotulo": ROTULO,
        "jornada": JORNADA.isoformat(),
        "margen_de_capacidad": MARGEN_DE_LA_OPERACION,
        "semilla_del_orden": SEMILLA_DEL_ORDEN,
        "memoria_libre_al_empezar_gb": libre,
        "otros_procesos_al_empezar": procesos,
        "arranque": {"grafo": args.grafo.name, "construir_cartografia_s_orientativo": construir_s,
                     "pico_del_proceso_mb_antes": pico_antes},
        "por_deposito": resultados,
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    args.salida.write_bytes((json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    condiciones = escribir_condiciones(args.salida)
    escribir_sidecar(
        registrar(
            artefacto="nfr09_caba",
            ambito="Ciudad Autonoma de Buenos Aires, grafo de la via A",
            fuente="tools/nfr09_caba.py",
            herramienta="tools/nfr09_caba.py",
            parametros={"rotulo": ROTULO, "instancias": [r.name for r in args.instancias], "condiciones": condiciones.name,
                        "sha256": hashlib.sha256(args.salida.read_bytes()).hexdigest()},
        ),
        args.salida,
    )
    for etiqueta, r in resultados.items():
        for nombre, valores in r["resumen"].items():
            _LOG.info("%s | %s | mediana %.3f s | RIQ %.3f s | maximo %.3f s", etiqueta, nombre,
                      valores["mediana_s"], valores["riq_s"], valores["maximo_s"])
    _LOG.info("Pico del proceso: %.1f MB", contenido["pico_del_proceso_mb"] or 0.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
