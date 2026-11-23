"""Matriz de costos: donde va el tiempo (sesion 2026-10-15B, bloque MX;
B-220, punto 11; B-224, punto 3; B-230, punto 3). Diagnostico: no cambia
`src/` ni adopta nada.

- `perfil` (MX1): `cProfile` sobre una llamada a `matriz_de_costos` de
  Cartografia, despues de una llamada de precalentamiento, sobre los nodos de
  la instancia canonica de San Isidro (los 19 de `ensamblar_instancia`, con
  el margen de la operacion y la jornada de la huella) o sobre los de una
  instancia de CABA (el Deposito y los destinos de sus Ordenes). Reparte el
  tiempo entre Dijkstra (`_dijkstra_multisource` de networkx, que construye
  dentro de la misma funcion los caminos a todos los nodos del grafo), la
  reconstruccion de los arcos de cada par (`_arcos_del_camino`, que llama a
  `costo`) y la creacion de objetos (los `__init__` que generan las
  dataclasses). Como referencia, perfila tambien Dijkstra sin caminos
  (`single_source_dijkstra_path_length`) desde los mismos origenes. Los
  tiempos son los del perfilador, con su sobrecosto: sirven para repartir,
  no como medicion.
- `gc` (MX2): copia de `medir_desglose` de
  `tools/metricas_planificacion_completa.py`, con la matriz medida sola en
  tres variantes: tal como esta; con `gc.freeze()` al empezar la repeticion,
  cuando Cartografia ya esta construida, y `gc.unfreeze()` al terminarla
  (asi las variantes se pueden intercalar en un proceso); y con
  `gc.disable()` durante el tramo de la matriz y `gc.enable()` al salir. Un
  precalentamiento por variante, 10 repeticiones de cada una en el orden que
  fija `random.Random(SEMILLA_DEL_ORDEN).shuffle`, base SQLite nueva por
  repeticion, mediana y RIQ, y las recolecciones de cada generacion dentro
  de cada tramo. Exige las condiciones de medicion (regla 9).
- `scipy` (MX3): la matriz con `scipy.sparse.csgraph.dijkstra` sobre una
  matriz dispersa del grafo construida una vez (con el arco mas corto entre
  arcos paralelos, el criterio de `costo`), con los origenes indicados y
  predecesores, y los caminos de los pares reconstruidos como secuencias de
  nodos. Compara contra `matriz_de_costos`: diferencia maxima absoluta de
  distancias, pares con el mismo camino, y pares con camino distinto e igual
  distancia (otro desempate). Con `--cronometrar`, y si se cumplen las
  condiciones, tiempos de las dos con el protocolo: un precalentamiento,
  10 repeticiones intercaladas con semilla, mediana y RIQ. La variante de
  scipy no arma los `CostoArco` de cada camino: no da el mismo objeto que el
  producto, y eso se declara junto a sus tiempos. `scipy` esta instalada en
  el entorno pero no declarada como dependencia del proyecto.

    python tools/matriz_de_costos_diagnostico.py perfil --grafo canonica --salida S
    python tools/matriz_de_costos_diagnostico.py perfil --grafo G --instancia I.json --salida S
    python tools/matriz_de_costos_diagnostico.py gc --salida S
    python tools/matriz_de_costos_diagnostico.py scipy [--cronometrar] --grafo canonica --salida S
"""
from __future__ import annotations

import argparse
import cProfile
import gc
import hashlib
import json
import logging
import pathlib
import pstats
import random
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, MatrizDeCostos, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera
from tools.amazon_2021 import pico_del_proceso_mb
from tools.condiciones_de_medicion import escribir_condiciones, memoria_fisica_gb, sin_suspension
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, DIRECTORIO_INSTANCIA, construir_contexto

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.matriz_de_costos_diagnostico")

CANONICA: Final[str] = "canonica"
JORNADA_DE_LA_HUELLA: Final[str] = "2026-09-27"
SEMILLA_DEL_ORDEN: Final[int] = 20261015
REPETICIONES: Final[int] = 10
MEMORIA_LIBRE_MINIMA_GB: Final[float] = 4.0
CODIGO_SIN_CONDICIONES: Final[int] = 3
NOMBRE_DE_LA_BASE: Final[str] = "matriz.sqlite3"
CODIFICACION: Final[str] = "utf-8"
ATRIBUTO_LONGITUD_M: Final[str] = "length"
TOLERANCIA_DE_DISTANCIA_M: Final[float] = 1e-6
FUNCIONES_MOSTRADAS: Final[int] = 15
RUIDOSOS: Final[tuple[str, ...]] = ("hoppit", "httpx")

TAL_CUAL: Final[str] = "tal_cual"
CON_FREEZE: Final[str] = "gc_freeze"
CON_DISABLE: Final[str] = "gc_disable"
VARIANTES_GC: Final[tuple[str, ...]] = (TAL_CUAL, CON_FREEZE, CON_DISABLE)
PRODUCTO: Final[str] = "producto"
SCIPY: Final[str] = "scipy"

FUNCION_DIJKSTRA: Final[str] = "_dijkstra_multisource"
FUNCION_ARCOS: Final[str] = "_arcos_del_camino"
FUNCION_COSTO: Final[str] = "costo"
FUNCION_MATRIZ: Final[str] = "matriz_de_costos"
ARCHIVO_GENERADO: Final[str] = "<string>"
FUNCION_INIT: Final[str] = "__init__"
# La funcion de peso que networkx arma para un multigrafo: el minimo entre los
# arcos paralelos, evaluado en cada relajacion de Dijkstra.
ARCHIVO_DE_DIJKSTRA: Final[str] = "weighted.py"
FUNCION_DE_PESO: Final[str] = "<lambda>"


def _p(valores: Sequence[float], q: float) -> float:
    return float(np.percentile(valores, q, method="linear"))


def resumen_de_segundos(valores: Sequence[float]) -> dict[str, float]:
    return {"mediana_s": _p(valores, 50), "riq_s": _p(valores, 75) - _p(valores, 25),
            "minimo_s": min(valores), "maximo_s": max(valores)}


def orden_intercalado(variantes: Sequence[str], repeticiones: int = REPETICIONES,
                      semilla: int = SEMILLA_DEL_ORDEN) -> list[tuple[str, int]]:
    orden = sorted((v, r) for v in variantes for r in range(1, repeticiones + 1))
    random.Random(semilla).shuffle(orden)
    return orden


# --- Nodos ---------------------------------------------------------------------


def _jornada() -> Any:
    import datetime as dt

    return dt.date.fromisoformat(JORNADA_DE_LA_HUELLA)


def contexto_canonico(cartografia: GrafoVialAMBA, base: pathlib.Path) -> ContextoDeLaApi:
    """El de `medir_desglose`: instancia canonica, margen de la operacion y
    jornada de la huella, sobre una base nueva."""
    return construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=base,
        cartografia=cartografia,
        reloj=_jornada,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )


def nodos_canonicos(cartografia: GrafoVialAMBA) -> tuple[int, ...]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        contexto = contexto_canonico(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE)
        instancia = contexto.planificacion.ensamblar_instancia(contexto.deposito, contexto.flota, None, _jornada())
        nodos = tuple(instancia.matriz.nodos)
        del contexto, instancia
        gc.collect()
    return nodos


def nodos_de_la_instancia(instancia: Mapping[str, Any]) -> tuple[int, ...]:
    """El Deposito y los destinos de las Ordenes, sin repetir, en ese orden."""
    nodos = [int(instancia["deposito"]["nodo"])]
    for orden in instancia["ordenes"]:
        if int(orden["nodo"]) not in nodos:
            nodos.append(int(orden["nodo"]))
    return tuple(nodos)


def preparar(grafo: str, instancia: pathlib.Path | None) -> tuple[pathlib.Path, GrafoVialAMBA, tuple[int, ...]]:
    ruta = GRAFO_PREDETERMINADO if grafo == CANONICA else pathlib.Path(grafo)
    cartografia = crear_cartografia(ruta)
    cartografia.construir()
    if grafo == CANONICA:
        return ruta, cartografia, nodos_canonicos(cartografia)
    if instancia is None:
        raise SystemExit("con un grafo que no es el canonico hace falta --instancia")
    return ruta, cartografia, nodos_de_la_instancia(json.loads(instancia.read_text(encoding=CODIFICACION)))


# --- MX1 ----------------------------------------------------------------------


def _clave(funcion: tuple[str, int, str]) -> str:
    archivo, linea, nombre = funcion
    corto = archivo.replace("\\", "/")
    for marca in ("site-packages/", "src/"):
        if marca in corto:
            corto = corto.split(marca, 1)[1]
    return f"{corto}:{linea}({nombre})"


def reparto_del_perfil(
    estadisticas: Mapping[tuple[str, int, str], tuple[Any, ...]], creacion: tuple[float, int] | None = None,
) -> dict[str, Any]:
    """Tiempo total y por categoria, desde `pstats.Stats.stats`: para cada
    funcion, (llamadas primitivas, llamadas, tiempo propio, tiempo acumulado,
    llamadores). `pstats` junta bajo una misma clave todos los `__init__`
    que generan las dataclasses (mismo archivo, linea y nombre), y se queda
    con uno: la creacion de objetos se pasa aparte, sumada sobre las
    entradas crudas del perfilador (`creacion_de_objetos`)."""
    total = sum(v[2] for v in estadisticas.values())

    def acumulado(nombre: str, archivo: str | None = None) -> float:
        return sum(v[3] for k, v in estadisticas.items() if k[2] == nombre and (archivo is None or k[0].endswith(archivo)))

    if creacion is None:
        creacion = (
            sum(v[3] for k, v in estadisticas.items() if k[0] == ARCHIVO_GENERADO and k[2] == FUNCION_INIT),
            sum(v[1] for k, v in estadisticas.items() if k[0] == ARCHIVO_GENERADO and k[2] == FUNCION_INIT),
        )
    creacion_s, llamadas_init = creacion
    categorias = {
        "dijkstra_con_caminos_a_todos_los_nodos_s": acumulado(FUNCION_DIJKSTRA),
        "dentro_de_dijkstra_peso_entre_arcos_paralelos_s": acumulado(FUNCION_DE_PESO, ARCHIVO_DE_DIJKSTRA),
        "reconstruccion_de_los_arcos_s": acumulado(FUNCION_ARCOS),
        "costo_s": acumulado(FUNCION_COSTO),
        "creacion_de_objetos_dataclass_s": creacion_s,
    }
    return {
        "total_s": total,
        "matriz_de_costos_s": acumulado(FUNCION_MATRIZ),
        **categorias,
        "fracciones_del_total": {k.removesuffix("_s"): (v / total if total else None) for k, v in categorias.items()},
        "llamadas_a_init_de_dataclass": llamadas_init,
        "llamadas_a_costo": sum(v[1] for k, v in estadisticas.items() if k[2] == FUNCION_COSTO),
        "por_tiempo_propio": [
            {"funcion": _clave(k), "llamadas": v[1], "tiempo_propio_s": v[2], "tiempo_acumulado_s": v[3]}
            for k, v in sorted(estadisticas.items(), key=lambda e: -e[1][2])[:FUNCIONES_MOSTRADAS]
        ],
    }


def creacion_de_objetos(entradas: Sequence[Any]) -> tuple[float, int]:
    """Tiempo acumulado y llamadas de los `__init__` generados por las
    dataclasses, sobre las entradas crudas de `Profile.getstats()` (una por
    objeto de codigo, sin juntar)."""
    propias = [
        e for e in entradas
        if not isinstance(e.code, str) and e.code.co_filename == ARCHIVO_GENERADO and e.code.co_name == FUNCION_INIT
    ]
    return sum(e.totaltime for e in propias), sum(e.callcount for e in propias)


def _perfilar(llamada: Callable[[], Any]) -> tuple[Any, dict[tuple[str, int, str], tuple[Any, ...]], tuple[float, int]]:
    perfilador = cProfile.Profile()
    perfilador.enable()
    resultado = llamada()
    perfilador.disable()
    creacion = creacion_de_objetos(perfilador.getstats())
    return resultado, pstats.Stats(perfilador).stats, creacion  # type: ignore[attr-defined]


def perfil(args: argparse.Namespace) -> dict[str, Any]:
    import networkx as nx

    ruta, cartografia, nodos = preparar(args.grafo, args.instancia)
    cartografia.matriz_de_costos(nodos)
    matriz, estadisticas, creacion = _perfilar(lambda: cartografia.matriz_de_costos(nodos))
    grafo_nx = _grafo_de(cartografia)
    distintos = sorted(set(nodos))
    _, sin_caminos, _ = _perfilar(
        lambda: [nx.single_source_dijkstra_path_length(grafo_nx, o, weight=ATRIBUTO_LONGITUD_M) for o in distintos]
    )
    return {
        "clase": "diagnostico",
        "grafo": ruta.name,
        "nodos_del_grafo": grafo_nx.number_of_nodes(),
        "arcos_del_grafo": grafo_nx.number_of_edges(),
        "nodos_de_la_matriz": len(nodos),
        "origenes_distintos": len(distintos),
        "pares": len(matriz.caminos),
        "arcos_en_los_caminos": sum(c.cantidad_de_arcos for c in matriz.caminos.values()),
        "perfil_de_matriz_de_costos": reparto_del_perfil(estadisticas, creacion),
        "referencia_dijkstra_sin_caminos": {
            "total_s": sum(v[2] for v in sin_caminos.values()),
            "dijkstra_s": sum(v[3] for k, v in sin_caminos.items() if k[2] == FUNCION_DIJKSTRA),
        },
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }


def _grafo_de(cartografia: GrafoVialAMBA) -> Any:
    """El MultiDiGraph que Cartografia ya construyo. Lectura de diagnostico
    desde `tools/`: el puerto no lo expone."""
    return cartografia._grafo_construido()  # type: ignore[attr-defined]


# --- MX2 ----------------------------------------------------------------------


def _recolecciones() -> list[int]:
    return [g["collections"] for g in gc.get_stats()]


def medir_matriz_sola(contexto: ContextoDeLaApi, variante: str) -> dict[str, Any]:
    """Copia de `medir_desglose` (tools/metricas_planificacion_completa.py)
    con la variante del recolector y sin `resolver`, que no se mide aca."""
    servicio = contexto.planificacion
    if variante == CON_FREEZE:
        gc.freeze()
    try:
        antes = _recolecciones()
        inicio = time.perf_counter()
        instancia = servicio.ensamblar_instancia(contexto.deposito, contexto.flota, None, _jornada())
        ensamblado_s = time.perf_counter() - inicio
        medio = _recolecciones()
        if variante == CON_DISABLE:
            gc.disable()
        try:
            inicio = time.perf_counter()
            matriz = contexto.cartografia.matriz_de_costos(instancia.matriz.nodos)
            matriz_s = time.perf_counter() - inicio
        finally:
            if variante == CON_DISABLE:
                gc.enable()
        despues = _recolecciones()
    finally:
        if variante == CON_FREEZE:
            gc.unfreeze()
    return {
        "variante": variante,
        "ensamblar_instancia_s": ensamblado_s,
        "matriz_de_costos_s": matriz_s,
        "recolecciones_en_el_ensamblado": [b - a for a, b in zip(antes, medio)],
        "recolecciones_en_la_matriz": [b - a for a, b in zip(medio, despues)],
        "matriz_igual_a_la_de_la_instancia": matriz.como_matriz() == instancia.matriz.como_matriz(),
        "sha256_de_la_matriz": hashlib.sha256(json.dumps(matriz.como_matriz()).encode(CODIFICACION)).hexdigest(),
    }


def _en_base_nueva(cartografia: GrafoVialAMBA, variante: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporal:
        registro = medir_matriz_sola(contexto_canonico(cartografia, pathlib.Path(temporal) / NOMBRE_DE_LA_BASE), variante)
        gc.collect()
    return registro


def lectura_mx2(por_variante: Mapping[str, Mapping[str, Mapping[str, float]]]) -> dict[str, Any]:
    """La lectura fijada de antemano: en cada variante, si la mediana de la
    matriz sola deja de ser mayor que la del ensamblado de la misma
    variante. Respaldada si pasa con `gc.freeze()` o con `gc.disable()`."""
    deja = {v: r["matriz_de_costos"]["mediana_s"] <= r["ensamblar_instancia"]["mediana_s"] for v, r in por_variante.items()}
    respaldada = deja.get(CON_FREEZE, False) or deja.get(CON_DISABLE, False)
    return {
        "la_matriz_sola_deja_de_tardar_mas_que_el_ensamblado": deja,
        "hipotesis_de_B_224_punto_3": "respaldada por medicion" if respaldada else "refutada como explicacion principal",
    }


def serie_gc(args: argparse.Namespace) -> dict[str, Any]:
    cartografia = crear_cartografia(GRAFO_PREDETERMINADO)
    cartografia.construir()
    for variante in VARIANTES_GC:
        _en_base_nueva(cartografia, variante)
    registros = []
    for posicion, (variante, repeticion) in enumerate(orden_intercalado(VARIANTES_GC), start=1):
        registro = {"repeticion": repeticion, "posicion": posicion, **_en_base_nueva(cartografia, variante)}
        registros.append(registro)
        _LOG.info("  %s r%d (%d) | ensamblar %.3f s | matriz %.3f s | gen2 en la matriz %d", variante, repeticion,
                  posicion, registro["ensamblar_instancia_s"], registro["matriz_de_costos_s"],
                  registro["recolecciones_en_la_matriz"][2])
    por_variante = {
        v: {
            "ensamblar_instancia": resumen_de_segundos([r["ensamblar_instancia_s"] for r in registros if r["variante"] == v]),
            "matriz_de_costos": resumen_de_segundos([r["matriz_de_costos_s"] for r in registros if r["variante"] == v]),
            "recolecciones_gen2_en_la_matriz": sum(r["recolecciones_en_la_matriz"][2] for r in registros if r["variante"] == v),
            "recolecciones_gen2_en_el_ensamblado": sum(r["recolecciones_en_el_ensamblado"][2] for r in registros if r["variante"] == v),
        }
        for v in VARIANTES_GC
    }
    return {
        "clase": "diagnostico",
        "grafo": GRAFO_PREDETERMINADO.name,
        "semilla_del_orden": SEMILLA_DEL_ORDEN,
        "repeticiones": registros,
        "por_variante": por_variante,
        "lectura": lectura_mx2(por_variante),
        "sha256_de_la_matriz_distintos": sorted({r["sha256_de_la_matriz"] for r in registros}),
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }


# --- MX3 ----------------------------------------------------------------------


def matriz_dispersa(grafo: Any) -> tuple[Any, dict[int, int], list[int]]:
    """CSR del grafo: una celda por par dirigido, con el arco mas corto entre
    paralelos (el criterio de `costo`). Sin lazos."""
    from scipy.sparse import csr_matrix

    nodos = [int(n) for n in grafo.nodes]
    indice = {n: i for i, n in enumerate(nodos)}
    pesos: dict[tuple[int, int], float] = {}
    for origen, destino, datos in grafo.edges(data=True):
        if origen == destino:
            continue
        clave = (indice[int(origen)], indice[int(destino)])
        longitud = float(datos[ATRIBUTO_LONGITUD_M])
        if clave not in pesos or longitud < pesos[clave]:
            pesos[clave] = longitud
    filas, columnas = zip(*pesos) if pesos else ((), ())
    return csr_matrix((list(pesos.values()), (filas, columnas)), shape=(len(nodos), len(nodos))), indice, nodos


def camino_desde_predecesores(predecesores: Any, fila: int, destino: int, nodos: Sequence[int]) -> list[int] | None:
    """Secuencia de nodos OSM desde el origen de `fila` hasta `destino`
    (indices), o None si no hay camino."""
    camino = [destino]
    actual = destino
    while True:
        previo = int(predecesores[fila, actual])
        if previo < 0:
            break
        camino.append(previo)
        actual = previo
    camino.reverse()
    return [nodos[i] for i in camino]


def matriz_con_scipy(csr: Any, indice: Mapping[int, int], nodos_del_grafo: Sequence[int],
                     nodos: Sequence[int]) -> dict[tuple[int, int], tuple[float, list[int] | None]]:
    from scipy.sparse.csgraph import dijkstra

    distintos = sorted(set(int(n) for n in nodos))
    origenes = [indice[n] for n in distintos]
    distancias, predecesores = dijkstra(csr, directed=True, indices=origenes, return_predecessors=True)
    resultado = {}
    for fila, origen in enumerate(distintos):
        for destino in distintos:
            j = indice[destino]
            distancia = float(distancias[fila, j])
            camino = None if not np.isfinite(distancia) else (
                [origen] if origen == destino else camino_desde_predecesores(predecesores, fila, j, nodos_del_grafo))
            resultado[(origen, destino)] = (distancia, camino)
    return resultado


def nodos_del_camino_del_producto(matriz: MatrizDeCostos, origen: int, destino: int) -> list[int]:
    camino = matriz.caminos[(origen, destino)]
    if not camino.arcos:
        return [origen]
    return [camino.arcos[0].origen, *(a.destino for a in camino.arcos)]


def comparar(matriz: MatrizDeCostos, alternativa: Mapping[tuple[int, int], tuple[float, list[int] | None]]) -> dict[str, Any]:
    diferencias, mismos, distinto_igual, distinto_distinto, sin_camino = [], 0, 0, 0, 0
    for par, camino in matriz.caminos.items():
        distancia, nodos_alternativa = alternativa[par]
        if nodos_alternativa is None:
            sin_camino += 1
            continue
        diferencia = abs(camino.distancia_m - distancia)
        diferencias.append(diferencia)
        if nodos_del_camino_del_producto(matriz, *par) == nodos_alternativa:
            mismos += 1
        elif diferencia <= TOLERANCIA_DE_DISTANCIA_M:
            distinto_igual += 1
        else:
            distinto_distinto += 1
    return {
        "pares": len(matriz.caminos),
        "diferencia_maxima_absoluta_m": max(diferencias) if diferencias else None,
        "pares_con_el_mismo_camino": mismos,
        "pares_con_camino_distinto_e_igual_distancia": distinto_igual,
        "pares_con_camino_y_distancia_distintos": distinto_distinto,
        "pares_sin_camino_en_scipy": sin_camino,
        "tolerancia_de_distancia_m": TOLERANCIA_DE_DISTANCIA_M,
    }


def comparacion_scipy(args: argparse.Namespace) -> dict[str, Any]:
    import scipy

    ruta, cartografia, nodos = preparar(args.grafo, args.instancia)
    grafo = _grafo_de(cartografia)
    inicio = time.perf_counter()
    csr, indice, nodos_del_grafo = matriz_dispersa(grafo)
    construir_s = time.perf_counter() - inicio
    matriz = cartografia.matriz_de_costos(nodos)
    alternativa = matriz_con_scipy(csr, indice, nodos_del_grafo, nodos)
    contenido: dict[str, Any] = {
        "clase": "diagnostico; no se adopta",
        "grafo": ruta.name,
        "scipy": scipy.__version__,
        "scipy_declarada_en_el_proyecto": False,
        "nodos_de_la_matriz": len(nodos),
        "celdas_de_la_matriz_dispersa": int(csr.nnz),
        "arcos_del_grafo": grafo.number_of_edges(),
        "construir_la_matriz_dispersa_s_orientativo": construir_s,
        "comparacion": comparar(matriz, alternativa),
    }
    if args.cronometrar:
        contenido["tiempos"] = cronometrar_las_dos(cartografia, csr, indice, nodos_del_grafo, nodos)
    contenido["pico_del_proceso_mb"] = pico_del_proceso_mb()
    return contenido


def cronometrar_las_dos(cartografia: GrafoVialAMBA, csr: Any, indice: Mapping[int, int],
                        nodos_del_grafo: Sequence[int], nodos: Sequence[int]) -> dict[str, Any]:
    llamadas: dict[str, Callable[[], Any]] = {
        PRODUCTO: lambda: cartografia.matriz_de_costos(nodos),
        SCIPY: lambda: matriz_con_scipy(csr, indice, nodos_del_grafo, nodos),
    }
    for llamada in llamadas.values():
        llamada()
    registros = []
    for posicion, (variante, repeticion) in enumerate(orden_intercalado(tuple(llamadas)), start=1):
        inicio = time.perf_counter()
        llamadas[variante]()
        segundos = time.perf_counter() - inicio
        registros.append({"variante": variante, "repeticion": repeticion, "posicion": posicion, "segundos": segundos})
        _LOG.info("  %s r%d (%d) | %.4f s", variante, repeticion, posicion, segundos)
    return {
        "protocolo": "un precalentamiento por variante, 10 repeticiones intercaladas con random.Random(20261015)",
        "que_mide_scipy": "dijkstra desde los origenes distintos y caminos de los pares como secuencias de nodos; "
                          "sin los CostoArco de cada camino",
        "repeticiones": registros,
        "por_variante": {v: resumen_de_segundos([r["segundos"] for r in registros if r["variante"] == v]) for v in llamadas},
    }


# --- Comun ---------------------------------------------------------------------


def condiciones_para_cronometrar() -> tuple[bool, float | None]:
    _, libre = memoria_fisica_gb()
    return libre is not None and libre >= MEMORIA_LIBRE_MINIMA_GB, libre


def _git(*argumentos: str) -> str:
    import subprocess

    return subprocess.run(["git", *argumentos], capture_output=True, text=True, check=True).stdout.strip()


def _escribir(salida: pathlib.Path, contenido: Mapping[str, Any], subcomando: str, cronometra: bool) -> None:
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    crudo = (json.dumps(contenido, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_bytes(crudo)
    parametros: dict[str, Any] = {"subcomando": subcomando, "commit": _git("rev-parse", "HEAD"),
                                  "arbol_limpio": _git("status", "--porcelain") == "", "sha256": hashlib.sha256(crudo).hexdigest()}
    if cronometra:
        parametros["condiciones"] = escribir_condiciones(salida).name
    escribir_sidecar(registrar(artefacto=f"matriz_de_costos_{subcomando}", ambito=str(contenido.get("grafo")),
                               fuente="tools/matriz_de_costos_diagnostico.py",
                               herramienta="tools/matriz_de_costos_diagnostico.py", parametros=parametros), salida)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in RUIDOSOS:
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    _LOG.setLevel(logging.INFO)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="paso", required=True)
    for nombre in ("perfil", "scipy"):
        p = sub.add_parser(nombre)
        p.add_argument("--grafo", required=True, help=f"'{CANONICA}' o la ruta de un GraphML")
        p.add_argument("--instancia", type=pathlib.Path)
        p.add_argument("--salida", type=pathlib.Path, required=True)
        if nombre == "scipy":
            p.add_argument("--cronometrar", action="store_true")
    p_gc = sub.add_parser("gc")
    p_gc.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    cronometra = args.paso == "gc" or (args.paso == "scipy" and args.cronometrar)
    if cronometra:
        cumple, libre = condiciones_para_cronometrar()
        _LOG.info("Memoria libre al empezar: %s GB", libre)
        if not cumple:
            _LOG.info("Menos de %.0f GB libres: no se mide (regla 9).", MEMORIA_LIBRE_MINIMA_GB)
            return CODIGO_SIN_CONDICIONES
        escribir_condiciones(args.salida.with_name(args.salida.stem + "_inicio.json"))
        with sin_suspension():
            contenido = serie_gc(args) if args.paso == "gc" else comparacion_scipy(args)
        contenido = {**contenido, "memoria_libre_al_empezar_gb": libre}
    else:
        contenido = perfil(args) if args.paso == "perfil" else comparacion_scipy(args)
    _escribir(args.salida, contenido, args.paso, cronometra)
    _LOG.info("Escrito %s", args.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
