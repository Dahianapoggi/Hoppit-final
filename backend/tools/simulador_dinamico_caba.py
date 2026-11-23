"""Simulador dinamico sobre CABA (encargo 2026-11-18A, bloques 2, 4 y 7).

Envuelve `hoppit.entorno_simulacion.puerto_dinamico` con los insumos de CABA:

- `insumos`: carga el grafo versionado y los insumos del generador de
  instancias (via `diagnostico_dqn_etapa1.cargar_contexto`, solo lectura),
  arma el sorteador de Ordenes (comunas, nodos por comuna, demanda de P-09),
  la forma de llegada de LaDe-P de Hangzhou y la capacidad por Vehiculo
  (formula de B-70 con el tamano de la etapa 1: ceil(10 x E[d] / 0,85)), y
  los escribe en un JSON con las coordenadas de los nodos elegibles.
- `matrices`: para cada jornada, las distancias por la red (Dijkstra de
  SciPy de `instancias_dqn_etapa1.DistanciasPorLaRed`) entre el Deposito y
  los nodos de las primeras `--ordenes-max` Ordenes, en un `.npz` por
  jornada. Las corridas con menos Ordenes usan la submatriz.
- `correr`: un escenario (cantidad de Ordenes, proporcion dinamica, carga)
  sobre `--jornadas` jornadas y las politicas pedidas; un CSV con una fila
  por jornada y politica, y un sidecar JSON.
- Encargo 2026-11-19A: `--regla-18B` en `correr` y `capacidad` vuelve al
  fin de jornada y al orden del plan con R2 del 18B (para reproducirlo).
- `capacidad` (encargo 2026-11-18B, bloque 4): para cada regla de regreso,
  corre una politica en una grilla de cantidades de Ordenes y da la
  capacidad simulada: la mayor cantidad N de la grilla con mediana entre
  jornadas de los diferidos <= `--fraccion` x N. Un CSV por jornada y un
  resumen JSON por regla y N (Ordenes por carga, fraccion radial).

No importa nada de la teoria de colas: la cantidad de Ordenes de cada
escenario la decide quien llama (bloque 3, `tools/intensidad_de_trafico.py`).

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import pathlib
import sys
import time
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion import puerto_dinamico as pd_  # noqa: E402
from hoppit.shared.intervalo_por_celda import leer_tabla_de_intervalos  # noqa: E402

ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"
RAIZ: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]
FORMA_DE_LLEGADA: Final[pathlib.Path] = RAIZ / "datos/transformado/hangzhou/forma_de_llegada_por_hora_hangzhou_2026-09-30.csv"
ORDENES_POR_VEHICULO_DE_LA_ETAPA_1: Final[int] = 10
FACTOR_DE_OCUPACION_B70: Final[float] = 0.85
SEMILLA_DE_LA_POLITICA_ALEATORIA: Final[int] = 9
"""Primera clave del flujo de la politica aleatoria: SeedSequence(20261118,
spawn_key=(9, jornada)), distinta de las de las Ordenes (tres claves)."""

_LOG = logging.getLogger("hoppit.tools.simulador_dinamico_caba")


def sha256(ruta: pathlib.Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def capacidad_de_la_etapa_1(demanda_media: float) -> int:
    return math.ceil(ORDENES_POR_VEHICULO_DE_LA_ETAPA_1 * demanda_media / FACTOR_DE_OCUPACION_B70)


def leer_forma(ruta: pathlib.Path = FORMA_DE_LLEGADA) -> tuple[pd_.FormaDeLlegada, dict[int, float]]:
    with ruta.open(encoding="utf-8") as archivo:
        mediana = {int(f["hora"]): float(f["mediana"]) for f in csv.DictReader(archivo)}
    return pd_.FormaDeLlegada.desde_mediana_por_hora(mediana), mediana


def leer_insumos(ruta: pathlib.Path) -> tuple[pd_.SorteadorDeOrdenes, pd_.FormaDeLlegada, dict[str, Any]]:
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    s = datos["sorteador"]
    sorteador = pd_.SorteadorDeOrdenes(
        int(s["deposito_nodo"]), tuple(s["comunas"]), tuple(s["probabilidades_de_comuna"]),
        {int(c): tuple(n) for c, n in s["nodos_por_comuna"].items()}, tuple(s["valores_de_demanda"]),
        tuple(s["probabilidades_de_demanda"]))
    forma = pd_.FormaDeLlegada(tuple(datos["forma"]["horas"]), tuple(datos["forma"]["fracciones"]))
    return sorteador, forma, datos


def _comando_insumos(args: argparse.Namespace) -> int:
    from tools.diagnostico_dqn_etapa1 import cargar_contexto

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    contexto = cargar_contexto(args.datos_tmp)
    nodos_por_comuna = {c: [n.nodo for n in nodos] for c, nodos in contexto.nodos_por_comuna.items()}
    sorteador = pd_.SorteadorDeOrdenes.armar(contexto.deposito.nodo, contexto.pesos, nodos_por_comuna, contexto.demanda)
    forma, mediana = leer_forma()
    coordenadas = {str(n.nodo): [n.latitud, n.longitud] for nodos in contexto.nodos_por_comuna.values() for n in nodos}
    datos = {
        "rotulo": ROTULO,
        "deposito": {"nodo": contexto.deposito.nodo, "latitud": contexto.deposito.latitud,
                     "longitud": contexto.deposito.longitud, "sha256": contexto.deposito.sha256},
        "huellas": {"tabla_de_pesos": contexto.huellas.tabla_de_pesos, "grafo": contexto.huellas.grafo,
                    "distribucion_de_demanda": contexto.huellas.distribucion_de_demanda,
                    "forma_de_llegada": sha256(FORMA_DE_LLEGADA)},
        "demanda_media": sorteador.demanda_media(),
        "capacidad": capacidad_de_la_etapa_1(sorteador.demanda_media()),
        "regla_de_la_capacidad": "ceil(10 x E[d] / 0,85): formula de B-70 con el tamano de la etapa 1",
        "forma": {"horas": list(forma.horas), "fracciones": list(forma.fracciones),
                  "mediana_por_hora_de_lade_p": {str(h): v for h, v in sorted(mediana.items())}},
        "sorteador": {"deposito_nodo": sorteador.deposito_nodo, "comunas": list(sorteador.comunas),
                      "probabilidades_de_comuna": list(sorteador.probabilidades_de_comuna),
                      "nodos_por_comuna": {str(c): list(n) for c, n in sorteador.nodos_por_comuna.items()},
                      "valores_de_demanda": list(sorteador.valores_de_demanda),
                      "probabilidades_de_demanda": list(sorteador.probabilidades_de_demanda)},
        "coordenadas": coordenadas,
        "nodos_fuera_de_toda_comuna": contexto.nodos_fuera_de_toda_comuna,
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(datos, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"capacidad": datos["capacidad"], "demanda_media": datos["demanda_media"],
                      "nodos_elegibles": len(coordenadas), "sha256": sha256(args.salida)}))
    return 0


def ruta_de_la_matriz(directorio: pathlib.Path, jornada: int) -> pathlib.Path:
    return directorio / f"jornada_{jornada:03d}.npz"


def leer_matriz(directorio: pathlib.Path, jornada: int) -> pd_.MatrizDeDistancias:
    with np.load(ruta_de_la_matriz(directorio, jornada)) as datos:
        return pd_.MatrizDeDistancias(datos["nodos"].tolist(), datos["metros"])


def _comando_matrices(args: argparse.Namespace) -> int:
    from tools.diagnostico_dqn_etapa1 import GRAFO_VERSIONADO, SHA256_DEL_GRAFO
    from tools.instancias_dqn_etapa1 import DistanciasPorLaRed
    import osmnx as ox

    sorteador, forma, _ = leer_insumos(args.insumos)
    if sha256(GRAFO_VERSIONADO) != SHA256_DEL_GRAFO:
        raise SystemExit("el grafo versionado no tiene el SHA-256 esperado")
    args.salida_dir.mkdir(parents=True, exist_ok=True)
    pendientes = [j for j in range(args.jornadas) if not ruta_de_la_matriz(args.salida_dir, j).exists()]
    if not pendientes:
        return 0
    distancias = DistanciasPorLaRed(ox.load_graphml(GRAFO_VERSIONADO))
    resumen = []
    for j in pendientes:
        antes = time.perf_counter()
        ordenes = pd_.generar_jornada(sorteador, forma, j, args.ordenes_max, 0.0, conjunto=args.conjunto)
        nodos = sorted({sorteador.deposito_nodo, *(o.nodo for o in ordenes)})
        pares = distancias.entre(nodos)
        matriz = pd_.MatrizDeDistancias.desde_pares(pares)
        ruta = ruta_de_la_matriz(args.salida_dir, j)
        np.savez_compressed(ruta, nodos=np.array(matriz.nodos, dtype=np.int64), metros=matriz.metros,
                            ordenes_max=np.array(args.ordenes_max))
        resumen.append({"jornada": j, "nodos": len(nodos), "segundos": round(time.perf_counter() - antes, 2),
                        "sha256": sha256(ruta)})
        _LOG.info("jornada %d: %d nodos, %.1f s", j, len(nodos), time.perf_counter() - antes)
    destino = args.salida_dir / f"matrices_{pendientes[0]:03d}_{pendientes[-1]:03d}.json"
    destino.write_text(json.dumps({"rotulo": ROTULO, "ordenes_max": args.ordenes_max, "conjunto": args.conjunto,
                                   "insumos_sha256": sha256(args.insumos),
                                   "jornadas": resumen}, indent=1) + "\n", encoding="utf-8")
    return 0


def politica(nombre: str, jornada: int) -> pd_.Politica:
    if nombre == "a":
        return pd_.VehiculoMasCercano()
    if nombre == "b":
        return pd_.InsercionMasBarata()
    if nombre == "d":
        return pd_.MasCercanoConInsercion()
    if nombre == "e":
        return pd_.VecinoMasCercano()
    if nombre == "c":
        return pd_.Aleatoria(np.random.SeedSequence(pd_.SEMILLA_BASE_JORNADAS_DINAMICAS,
                                                    spawn_key=(SEMILLA_DE_LA_POLITICA_ALEATORIA, jornada)))
    raise SystemExit(f"politica desconocida: {nombre}")


def correr_escenario(sorteador: pd_.SorteadorDeOrdenes, forma: pd_.FormaDeLlegada, capacidad: int,
                     matrices: pathlib.Path, tabla: Any, nombre: str, ordenes: int, proporcion: float, carga_min: float,
                     jornadas: Sequence[int], politicas: Sequence[str], vehiculos: int = 3,
                     extra: dict[str, Any] | None = None, conjunto: int = pd_.CONJUNTO_DINAMICO_COMPARACION
                     ) -> list[dict[str, Any]]:
    """`extra`: otros campos de `ParametrosDelSimulador` (encargo 2026-11-18B:
    `fin_por_llegada`, la regla de regreso y su umbral, el factor de Parada)."""
    filas = []
    extra = dict(extra or {})
    parametros = pd_.ParametrosDelSimulador(capacidad=capacidad, carga_min=carga_min, cantidad_de_vehiculos=vehiculos,
                                            **extra)
    for j in jornadas:
        lista = pd_.generar_jornada(sorteador, forma, j, ordenes, proporcion, conjunto=conjunto)
        matriz = leer_matriz(matrices, j)
        matriz = matriz.submatriz([sorteador.deposito_nodo, *(o.nodo for o in lista)])
        grado = pd_.grado_de_dinamismo(lista)
        for p in politicas:
            antes = time.perf_counter()
            resultado = pd_.correr_jornada(lista, sorteador.deposito_nodo, matriz, tabla, parametros, politica(p, j))
            filas.append({"escenario": nombre, "ordenes_pedidas": ordenes, "proporcion_dinamica": proporcion,
                          "vehiculos": vehiculos,
                          "carga_por_regreso_min": carga_min, **{k: v for k, v in extra.items()},
                          "jornada": j, **grado, **resultado.fila(),
                          "segundos_de_la_jornada": time.perf_counter() - antes})
    return filas


def parametros_extra(args: argparse.Namespace) -> dict[str, Any]:
    """Opciones del encargo 2026-11-18B que pasan a `ParametrosDelSimulador`."""
    extra: dict[str, Any] = {}
    if args.fin_por_salida:
        extra["fin_por_llegada"] = False
    extra["regla_de_regreso"] = args.regla_de_regreso
    extra["umbral_de_regreso"] = args.umbral_de_regreso
    if args.factor_de_parada != 1.0:
        extra["factor_de_parada"] = args.factor_de_parada
    if getattr(args, "regla_18B", False):
        extra["fin_por_la_mas_cercana"] = False
        extra["reordenar_al_cargar_con_umbral"] = False
    return extra


def _comando_correr(args: argparse.Namespace) -> int:
    from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    sorteador, forma, datos = leer_insumos(args.insumos)
    tabla = leer_tabla_de_intervalos(RAIZ / TABLA_VIGENTE)
    antes = time.perf_counter()
    filas = correr_escenario(sorteador, forma, datos["capacidad"], args.matrices, tabla, args.escenario, args.ordenes,
                             args.proporcion, args.carga, range(args.primera_jornada, args.primera_jornada + args.jornadas),
                             args.politicas.split(","), args.vehiculos, parametros_extra(args), args.conjunto)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    columnas = list(dict.fromkeys(c for f in filas for c in f))
    with args.salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(filas)
    sidecar = {"rotulo": ROTULO, "argumentos": {k: str(v) for k, v in vars(args).items() if k != "funcion"},
               "insumos_sha256": sha256(args.insumos), "tabla": str(TABLA_VIGENTE), "tabla_sha256": sha256(RAIZ / TABLA_VIGENTE),
               "capacidad": datos["capacidad"], "filas": len(filas), "segundos": round(time.perf_counter() - antes, 1),
               "csv_sha256": sha256(args.salida)}
    args.salida.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"filas": len(filas), "segundos": sidecar["segundos"], "csv_sha256": sidecar["csv_sha256"]}))
    return 0


FRACCION_DE_DIFERIDOS_DE_LA_CAPACIDAD: Final[float] = 0.05


def capacidad_simulada(resumen_por_n: Sequence[dict[str, Any]], fraccion: float = FRACCION_DE_DIFERIDOS_DE_LA_CAPACIDAD
                       ) -> dict[str, Any]:
    """La mayor N de la grilla con mediana de diferidos <= fraccion x N, y si la
    condicion es monotona en N (cumple hasta un punto y despues no)."""
    cumple = [(int(r["ordenes"]), r["diferidos_mediana"] <= fraccion * r["ordenes"]) for r in resumen_por_n]
    cumple.sort()
    validas = [n for n, c in cumple if c]
    primera_falla = next((n for n, c in cumple if not c), None)
    monotona = primera_falla is None or all(not c for n, c in cumple if n >= primera_falla)
    return {"capacidad": max(validas) if validas else None, "primera_falla": primera_falla, "monotona": monotona,
            "cumple_en_el_ultimo_punto": cumple[-1][1] if cumple else None,
            "puntos_que_cumplen_despues_de_la_primera_falla": [n for n, c in cumple if c and primera_falla is not None
                                                               and n > primera_falla]}


def _resumen_de_n(filas: Sequence[dict[str, Any]]) -> dict[str, Any]:
    med = lambda c: float(np.median([float(f[c]) for f in filas]))  # noqa: E731
    return {"ordenes": int(filas[0]["ordenes_pedidas"]), "jornadas": len(filas), "diferidos_mediana": med("diferidos"),
            "diferidos_media": float(np.mean([float(f["diferidos"]) for f in filas])),
            "ordenes_por_carga_mediana": med("ordenes_por_carga_media"), "regresos_mediana": med("regresos"),
            "fraccion_radial_del_viaje_mediana": float(np.median([f["radial_min"] / f["viaje_min"] for f in filas
                                                                   if f["viaje_min"] > 0])),
            "fraccion_radial_del_tiempo_de_flota_mediana": float(np.median([f["radial_min"] / f["duracion_total_min"]
                                                                            for f in filas if f["duracion_total_min"] > 0])),
            "medida_principal_mediana_min": med("medida_principal_min"), "duracion_total_mediana_min": med("duracion_total_min"),
            "entregadas_mediana": med("entregadas")}


def _comando_capacidad(args: argparse.Namespace) -> int:
    from tools.entrenar_dqn_etapa1 import TABLA_VIGENTE

    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    sorteador, forma, datos = leer_insumos(args.insumos)
    tabla = leer_tabla_de_intervalos(RAIZ / TABLA_VIGENTE)
    antes = time.perf_counter()
    todas: list[dict[str, Any]] = []
    resumen: dict[str, Any] = {}
    for regla in args.reglas.split(","):
        por_n = []
        for n in range(args.desde, args.hasta + 1, args.paso):
            filas = correr_escenario(sorteador, forma, datos["capacidad"], args.matrices, tabla, f"capacidad_{regla}_n{n}", n,
                                     args.proporcion, args.carga, range(args.jornadas), [args.politica], 3,
                                     {"regla_de_regreso": regla, "umbral_de_regreso": args.umbral_de_regreso,
                                      **({"fin_por_la_mas_cercana": False, "reordenar_al_cargar_con_umbral": False}
                                         if args.regla_18B else {})})
            todas.extend(filas)
            por_n.append(_resumen_de_n(filas))
            _LOG.info("%s N=%d: diferidos (mediana) %.1f", regla, n, por_n[-1]["diferidos_mediana"])
        resumen[regla] = {"por_n": por_n, **capacidad_simulada(por_n, args.fraccion)}
    columnas = list(dict.fromkeys(c for f in todas for c in f))
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    with args.salida.open("w", encoding="utf-8", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=columnas, lineterminator="\n")
        escritor.writeheader()
        escritor.writerows(todas)
    sidecar = {"rotulo": ROTULO, "argumentos": {k: str(v) for k, v in vars(args).items() if k != "funcion"},
               "definicion": "capacidad simulada = mayor N de la grilla con mediana entre jornadas de los diferidos "
                             "<= fraccion x N (encargo 2026-11-18B, bloque 4)",
               "insumos_sha256": sha256(args.insumos), "tabla_sha256": sha256(RAIZ / TABLA_VIGENTE),
               "csv_sha256": sha256(args.salida), "segundos": round(time.perf_counter() - antes, 1), "reglas": resumen}
    args.salida.with_suffix(".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({r:{"capacidad": v["capacidad"], "monotona": v["monotona"]} for r, v in resumen.items()}))
    return 0


def lector_de_argumentos() -> argparse.ArgumentParser:
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = lector.add_subparsers(required=True)
    i = sub.add_parser("insumos")
    i.add_argument("--datos-tmp", type=pathlib.Path, default=pathlib.Path("datos/tmp"))
    i.add_argument("--salida", type=pathlib.Path, required=True)
    i.set_defaults(funcion=_comando_insumos)
    m = sub.add_parser("matrices")
    m.add_argument("--insumos", type=pathlib.Path, required=True)
    m.add_argument("--jornadas", type=int, required=True)
    m.add_argument("--ordenes-max", type=int, required=True)
    m.add_argument("--salida-dir", type=pathlib.Path, required=True)
    m.add_argument("--conjunto", type=int, default=pd_.CONJUNTO_DINAMICO_COMPARACION,
                   help="conjunto de jornadas (encargo 2026-11-19A: 1 comparacion, 2 piloto, 3 entrenamiento, 4 validacion)")
    m.set_defaults(funcion=_comando_matrices)
    c = sub.add_parser("correr")
    c.add_argument("--insumos", type=pathlib.Path, required=True)
    c.add_argument("--matrices", type=pathlib.Path, required=True)
    c.add_argument("--escenario", required=True)
    c.add_argument("--ordenes", type=int, required=True)
    c.add_argument("--proporcion", type=float, required=True)
    c.add_argument("--carga", type=float, default=10.0)
    c.add_argument("--jornadas", type=int, default=50)
    c.add_argument("--primera-jornada", type=int, default=0)
    c.add_argument("--politicas", default="a,b,c")
    c.add_argument("--vehiculos", type=int, default=3)
    c.add_argument("--fin-por-salida", action="store_true",
                   help="regla de fin de jornada del 18A (hora de salida); solo para reproducir aquellas corridas")
    c.add_argument("--regla-de-regreso", default=pd_.REGRESO_CON_ALGO_POR_CARGAR, choices=pd_.REGLAS_DE_REGRESO)
    c.add_argument("--umbral-de-regreso", type=int, default=6)
    c.add_argument("--factor-de-parada", type=float, default=1.0)
    c.add_argument("--conjunto", type=int, default=pd_.CONJUNTO_DINAMICO_COMPARACION)
    c.add_argument("--regla-18B", action="store_true",
                   help="fin de jornada y orden del plan con R2 como en el 18B; solo para reproducir aquellas corridas")
    c.add_argument("--salida", type=pathlib.Path, required=True)
    c.set_defaults(funcion=_comando_correr)
    k = sub.add_parser("capacidad")
    k.add_argument("--insumos", type=pathlib.Path, required=True)
    k.add_argument("--matrices", type=pathlib.Path, required=True)
    k.add_argument("--reglas", default="R1,R2,R3")
    k.add_argument("--umbral-de-regreso", type=int, default=6)
    k.add_argument("--politica", default="b")
    k.add_argument("--proporcion", type=float, default=0.5)
    k.add_argument("--carga", type=float, default=10.0)
    k.add_argument("--jornadas", type=int, default=50)
    k.add_argument("--desde", type=int, default=20)
    k.add_argument("--hasta", type=int, default=250)
    k.add_argument("--paso", type=int, default=5)
    k.add_argument("--fraccion", type=float, default=FRACCION_DE_DIFERIDOS_DE_LA_CAPACIDAD)
    k.add_argument("--regla-18B", action="store_true", help="como en `correr`")
    k.add_argument("--salida", type=pathlib.Path, required=True)
    k.set_defaults(funcion=_comando_capacidad)
    return lector


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
    args = lector_de_argumentos().parse_args(argumentos)
    return int(args.funcion(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
