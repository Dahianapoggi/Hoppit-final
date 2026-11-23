"""Comparacion de la etapa 1 del agente DQN contra OR-Tools (sesion
2026-10-19A, Q6; ARQ-46, protocolo fijado antes de entrenar).

- 50 instancias de comparacion (`tools/instancias_dqn_etapa1.py`, conjunto
  de comparacion, distancias de Cartografia) y 200 episodios por instancia;
  los tres motores sobre las mismas semillas de episodio, con sorteos
  apareados por Orden (B-191), en el `EntornoSecuencial`.
- Agente: en lazo cerrado con `EvaluadorPoliticaDQN.elegir_accion` (el camino
  de inferencia del producto, numpy sobre los pesos versionados; no la red de
  PyTorch).
- Linea de base primaria: OR-Tools (`SolverVRPDeterminista`, un Vehiculo,
  capacidad igual a la demanda total, sin capacidad efectiva) con la matriz de
  costo en segundos: viaje a la velocidad de trabajo mas, al llegar a una
  Parada, la media de la componente de parada en la franja de las 8 por cada
  Orden. Bajo B-196 ese costo ordena las secuencias igual que la distancia,
  porque la suma de las componentes de parada es la misma en cualquier
  recorrido; las diferencias vienen del solver (B-200, punto 4).
- Linea de base secundaria: OR-Tools con costo de distancia.
- Las lineas de base son planes fijos evaluados en el simulador.
- Metrica: mediana de la duracion total por instancia; secundarias: p90 y
  distancia. Diferencia pareada agente menos linea de base primaria por
  instancia; Wilcoxon de rangos con signo; intervalo del 95 % por bootstrap
  con 10.000 remuestreos sobre la mediana de las diferencias; diferencia
  minima detectable 2,80 x desvio / raiz de 50 (aproximacion normal, potencia
  0,8, alfa 0,05).
- Lectura fijada de antemano (ARQ-46): intervalo que contiene el cero,
  paridad dentro del intervalo, con la diferencia minima detectable;
  enteramente por encima de cero, el agente es peor; enteramente por debajo,
  el agente es mejor, con la magnitud en minutos y la advertencia de que la
  unica fuente posible de ventaja es la franja.

Rotulo (DAT-30): [simulacion sobre instancias sorteadas en CABA; no es
validacion contra datos reales], instancias de 10 Ordenes y un Vehiculo, sin
llegadas durante la jornada.

    python tools/comparar_dqn_etapa1.py --salida datos/tmp/dqn_etapa1/comparacion_2026-10-19A.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import math
import pathlib
import platform
import sys
import time
from collections.abc import Sequence
from typing import Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import (
    EntornoSecuencial,
    Instancia,
    ParadaDeLaInstancia,
    SemillaDeEpisodio,
    paradas_de_la_instancia,
    recorrer_secuencia,
)
from hoppit.planificacion_rutas.motor._politica_dqn import EvaluadorPoliticaDQN
from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    METROS_POR_KM,
    MINUTOS_POR_HORA,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    FranjaFueraDeLaJornada,
    TablaDeIntervalos,
    leer_tabla_de_intervalos,
    media_de_celda,
)
from tools.motor_instancia_externa import InstanciaExterna, resolver

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.comparar_dqn_etapa1")

EPISODIOS_POR_INSTANCIA: Final[int] = 200
SEMILLA_BASE_COMPARACION_DQN: Final[int] = 20261023
SEMILLA_BOOTSTRAP_COMPARACION_DQN: Final[int] = 20261024
REMUESTREOS_BOOTSTRAP: Final[int] = 10_000
NIVEL_DEL_INTERVALO: Final[float] = 0.95
# z de 0,975 mas z de 0,8: 1,96 + 0,84 (aproximacion normal, B-236 punto 2).
FACTOR_DE_LA_DIFERENCIA_MINIMA: Final[float] = 2.80
PERCENTIL_SECUNDARIO: Final[float] = 90.0
SEGUNDOS_POR_MINUTO: Final[float] = 60.0
PORCENTAJE: Final[float] = 100.0
IDENTIFICADOR_DEL_VEHICULO: Final[str] = "v1"
PESOS: Final[pathlib.Path] = pathlib.Path("datos/modelos/dqn_etapa1/pesos_dqn_etapa1_2026-10-19A.npz")
TABLA_VIGENTE: Final[pathlib.Path] = pathlib.Path(
    "datos/transformado/hangzhou/dispersion_relativa_de_intervalos_hangzhou_2026-10-01.csv"
)
ROTULO: Final[str] = "simulacion sobre instancias sorteadas en CABA; no es validacion contra datos reales"
NOTA_AL_PIE: Final[str] = (
    "Elaboracion propia en el entorno de simulacion de HOPPIT. Destinos sorteados en CABA en proporcion a la "
    "poblacion por comuna (INDEC, Censo 2022). Demanda por Orden de la distribucion de paquetes por parada de Amazon "
    "2021 (EE. UU.). Tiempo de cada tramo: viaje por la red de OpenStreetMap a una velocidad de trabajo de 19 km/h, "
    "que es un parametro supuesto, mas una componente de parada tomada de LaDe (Hangzhou). Sin llegada de Ordenes "
    "durante la jornada (etapa 1). 50 instancias de comparacion de 10 Ordenes y un Vehiculo, disjuntas de las de "
    "entrenamiento; semilla base SEMILLA_BASE_INSTANCIAS_CABA. Las cifras describen el comportamiento de los motores "
    "dentro del simulador y no constituyen una medicion de la operacion real."
)


def costo_en_segundos(
    distancia_m: float, ordenes_en_el_destino: int, media_de_parada_min: float, velocidad_km_h: float
) -> float:
    """Viaje a la velocidad de trabajo mas la media de la componente de parada
    por cada Orden del destino (cero si el destino es el Deposito)."""
    viaje_min = distancia_m / METROS_POR_KM / velocidad_km_h * MINUTOS_POR_HORA
    return (viaje_min + ordenes_en_el_destino * media_de_parada_min) * SEGUNDOS_POR_MINUTO


def matrices(
    instancia: Instancia, paradas: Sequence[ParadaDeLaInstancia], media_de_parada_min: float, velocidad_km_h: float
) -> tuple[list[int], list[list[float]], list[list[float]]]:
    """Nodos (Deposito primero, despues las Paradas en el orden de los slots),
    matriz de distancia en metros y matriz de costo en segundos."""
    nodos = [instancia.deposito_nodo] + [p.nodo for p in paradas]
    ordenes = [0] + [len(p.ordenes) for p in paradas]

    def distancia(o: int, d: int) -> float:
        return 0.0 if o == d else instancia.distancia_por_la_red_m(o, d)

    metros = [[distancia(o, d) for d in nodos] for o in nodos]
    segundos = [
        [0.0 if i == j else costo_en_segundos(metros[i][j], ordenes[j], media_de_parada_min, velocidad_km_h)
         for j in range(len(nodos))]
        for i in range(len(nodos))
    ]
    return nodos, metros, segundos


def secuencia_de_or_tools(nodos: Sequence[int], matriz: Sequence[Sequence[float]], paradas: Sequence[ParadaDeLaInstancia]) -> list[int]:
    """Los slots en el orden de la Ruta que devuelve el motor del producto."""
    demanda_total = sum(p.demanda for p in paradas)
    externa = InstanciaExterna(
        nombre="etapa1", matriz=matriz, demandas=[0] + [p.demanda for p in paradas], indice_deposito=0,
        capacidad_por_vehiculo={IDENTIFICADOR_DEL_VEHICULO: demanda_total}, nodos=list(nodos),
        ordenes={i + 1: p.ordenes for i, p in enumerate(paradas)},
    )
    (ruta,) = resolver(externa).rutas
    slot_de = {p.nodo: i for i, p in enumerate(paradas)}
    return [slot_de[p.nodo] for p in ruta.paradas]


def distancia_de_la_secuencia(instancia: Instancia, paradas: Sequence[ParadaDeLaInstancia], slots: Sequence[int]) -> float:
    recorrido = [instancia.deposito_nodo] + [paradas[s].nodo for s in slots] + [instancia.deposito_nodo]
    return sum(0.0 if o == d else instancia.distancia_por_la_red_m(o, d) for o, d in zip(recorrido, recorrido[1:]))


def episodio_del_agente(
    entorno: EntornoSecuencial, evaluador: EvaluadorPoliticaDQN, instancia: Instancia, semilla: SemillaDeEpisodio
) -> tuple[float, list[int]]:
    """Lazo cerrado: la duracion y la secuencia de slots elegida."""
    observacion = entorno.reiniciar(instancia, semilla)
    slots: list[int] = []
    terminado = False
    while not terminado:
        accion = evaluador.elegir_accion(observacion.vector, observacion.mascara)
        observacion, _, terminado, _ = entorno.paso(accion)
        slots.append(accion)
    return entorno.duracion_min, slots


@dataclasses.dataclass(frozen=True)
class Estadistica:
    diferencias: list[float]
    mediana_de_las_diferencias: float
    intervalo: tuple[float, float]
    wilcoxon_estadistico: float | None
    wilcoxon_p: float | None
    diferencia_minima_detectable: float
    desvio: float
    lectura: str


def estadistica_pareada(diferencias: Sequence[float], generador: np.random.Generator,
                        remuestreos: int = REMUESTREOS_BOOTSTRAP) -> Estadistica:
    """Wilcoxon de rangos con signo, IC del 95 % por bootstrap de la mediana
    de las diferencias, diferencia minima detectable y lectura de ARQ-46."""
    from scipy.stats import wilcoxon

    d = np.asarray(diferencias, dtype=float)
    n = len(d)
    remuestras = generador.integers(0, n, size=(remuestreos, n))
    medianas = np.median(d[remuestras], axis=1)
    cola = (1 - NIVEL_DEL_INTERVALO) / 2 * PORCENTAJE
    bajo, alto = (float(v) for v in np.percentile(medianas, (cola, PORCENTAJE - cola)))
    if np.all(d == 0):
        estadistico = p = None
    else:
        prueba = wilcoxon(d)
        estadistico, p = float(prueba.statistic), float(prueba.pvalue)
    desvio = float(np.std(d, ddof=1)) if n > 1 else float("nan")
    minima = FACTOR_DE_LA_DIFERENCIA_MINIMA * desvio / math.sqrt(n)
    if bajo <= 0.0 <= alto:
        lectura = "paridad dentro del intervalo"
    elif bajo > 0.0:
        lectura = "el agente es peor"
    else:
        lectura = "el agente es mejor (la unica fuente posible de ventaja en la etapa 1 es la franja)"
    return Estadistica(list(d), float(np.median(d)), (bajo, alto), estadistico, p, minima, desvio, lectura)


def main(argumentos: Sequence[str]) -> int:
    import scipy

    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
    from tools.amazon_2021 import pico_del_proceso_mb
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    lector.add_argument("--pesos", type=pathlib.Path, default=PESOS)
    lector.add_argument("--instancias", type=int, default=None, help="solo para una prueba rapida")
    lector.add_argument("--episodios", type=int, default=EPISODIOS_POR_INSTANCIA, help="solo para una prueba rapida")
    args = lector.parse_args(list(argumentos))
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    inicio = time.perf_counter()

    tabla: TablaDeIntervalos = leer_tabla_de_intervalos(TABLA_VIGENTE)
    media_8 = media_de_celda(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, PRIMERA_FRANJA_DE_LA_JORNADA))
    evaluador = EvaluadorPoliticaDQN(args.pesos)
    evaluador.cargar_pesos()
    contenido, instancias = leer_instancias_de_comparacion()
    if args.instancias is not None:
        instancias = instancias[: args.instancias]
    entorno = EntornoSecuencial(tabla)

    por_instancia = []
    fuera_de_jornada = {"agente": 0, "primaria": 0, "secundaria": 0}
    for numero, instancia in enumerate(instancias):
        paradas = paradas_de_la_instancia(instancia)
        nodos, metros, segundos = matrices(instancia, paradas, media_8, VELOCIDAD_DE_TRABAJO_KM_H)
        primaria = secuencia_de_or_tools(nodos, segundos, paradas)
        secundaria = secuencia_de_or_tools(nodos, metros, paradas)
        duraciones: dict[str, list[float]] = {"agente": [], "primaria": [], "secundaria": []}
        distancias_del_agente = []
        secuencias_del_agente: dict[tuple[int, ...], int] = {}
        for episodio in range(args.episodios):
            semilla = SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN, numero * EPISODIOS_POR_INSTANCIA + episodio)
            try:
                duracion, slots = episodio_del_agente(entorno, evaluador, instancia, semilla)
                duraciones["agente"].append(duracion)
                distancias_del_agente.append(distancia_de_la_secuencia(instancia, paradas, slots))
                secuencias_del_agente[tuple(slots)] = secuencias_del_agente.get(tuple(slots), 0) + 1
            except FranjaFueraDeLaJornada:
                fuera_de_jornada["agente"] += 1
            for nombre, slots in (("primaria", primaria), ("secundaria", secundaria)):
                try:
                    duraciones[nombre].append(recorrer_secuencia(entorno, instancia, semilla, slots)[0])
                except FranjaFueraDeLaJornada:
                    fuera_de_jornada[nombre] += 1
        frecuente = max(secuencias_del_agente.items(), key=lambda kv: kv[1])[0]
        registro = {
            "instancia": numero,
            "paradas": len(paradas),
            "secuencia_primaria": primaria,
            "secuencia_secundaria": secundaria,
            "primaria_igual_a_secundaria": primaria == secundaria,
            "secuencias_distintas_del_agente": len(secuencias_del_agente),
            "secuencia_mas_frecuente_del_agente": list(frecuente),
            "agente_igual_a_primaria_en_la_mas_frecuente": list(frecuente) == primaria,
        }
        for nombre, valores in duraciones.items():
            registro[f"mediana_{nombre}_min"] = float(np.median(valores))
            registro[f"p90_{nombre}_min"] = float(np.percentile(valores, PERCENTIL_SECUNDARIO))
        registro["distancia_primaria_m"] = distancia_de_la_secuencia(instancia, paradas, primaria)
        registro["distancia_secundaria_m"] = distancia_de_la_secuencia(instancia, paradas, secundaria)
        registro["distancia_mediana_agente_m"] = float(np.median(distancias_del_agente))
        por_instancia.append(registro)
        _LOG.info("Instancia %d | %s", numero, {k: v for k, v in registro.items() if k.startswith(("mediana", "distancia"))})

    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_COMPARACION_DQN)
    contra_primaria = estadistica_pareada(
        [r["mediana_agente_min"] - r["mediana_primaria_min"] for r in por_instancia], generador)
    contra_secundaria = estadistica_pareada(
        [r["mediana_agente_min"] - r["mediana_secundaria_min"] for r in por_instancia], generador)
    primaria_contra_secundaria = estadistica_pareada(
        [r["mediana_primaria_min"] - r["mediana_secundaria_min"] for r in por_instancia], generador)
    resultado = {
        "rotulo": ROTULO,
        "nota_al_pie": NOTA_AL_PIE,
        "tamano_de_instancia": "10 Ordenes, 1 Vehiculo; sin llegadas durante la jornada",
        "instancias": len(por_instancia),
        "episodios_por_instancia": args.episodios,
        "semillas": {"SEMILLA_BASE_COMPARACION_DQN": SEMILLA_BASE_COMPARACION_DQN,
                     "SEMILLA_BOOTSTRAP_COMPARACION_DQN": SEMILLA_BOOTSTRAP_COMPARACION_DQN},
        "media_de_la_componente_de_parada_franja_8_min": media_8,
        "velocidad_de_trabajo_km_h": VELOCIDAD_DE_TRABAJO_KM_H,
        "pesos": args.pesos.name,
        "pesos_sha256": evaluador.version,
        "deposito_nodo": contenido["deposito"]["nodo"],
        "tabla_sha256": tabla.sha256,
        "episodios_fuera_de_la_jornada": fuera_de_jornada,
        "primaria_igual_a_secundaria": sum(r["primaria_igual_a_secundaria"] for r in por_instancia),
        "agente_igual_a_primaria_en_la_secuencia_mas_frecuente": sum(
            r["agente_igual_a_primaria_en_la_mas_frecuente"] for r in por_instancia),
        "agente_menos_primaria": dataclasses.asdict(contra_primaria),
        "agente_menos_secundaria": dataclasses.asdict(contra_secundaria),
        "primaria_menos_secundaria": dataclasses.asdict(primaria_contra_secundaria),
        "secundarias": {
            "p90_agente_menos_primaria_mediana_min": float(np.median(
                [r["p90_agente_min"] - r["p90_primaria_min"] for r in por_instancia])),
            "distancia_agente_menos_primaria_mediana_m": float(np.median(
                [r["distancia_mediana_agente_m"] - r["distancia_primaria_m"] for r in por_instancia])),
            "distancia_primaria_menos_secundaria_mediana_m": float(np.median(
                [r["distancia_primaria_m"] - r["distancia_secundaria_m"] for r in por_instancia])),
        },
        "por_instancia": por_instancia,
        "versiones": {"numpy": np.__version__, "scipy": scipy.__version__, "python": platform.python_version()},
        "segundos": round(time.perf_counter() - inicio, 1),
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(resultado, ensure_ascii=False, indent=1, default=str) + "\n"
    args.salida.write_bytes(texto.encode("utf-8"))
    escribir_sidecar(
        registrar(artefacto="comparacion_dqn_etapa1", ambito="Ciudad Autonoma de Buenos Aires",
                  fuente="EntornoSecuencial sobre las 50 instancias de comparacion", herramienta="tools/comparar_dqn_etapa1.py",
                  parametros={"sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(), "rotulo": ROTULO,
                              "pesos_sha256": evaluador.version, "versiones": resultado["versiones"]}),
        args.salida,
    )
    _LOG.info("Agente menos primaria | mediana %.3f min | IC %s | Wilcoxon p %s | DMD %.3f | %s",
              contra_primaria.mediana_de_las_diferencias, contra_primaria.intervalo, contra_primaria.wilcoxon_p,
              contra_primaria.diferencia_minima_detectable, contra_primaria.lectura)
    _LOG.info("Pico %s MB | %s s", resultado["pico_del_proceso_mb"], resultado["segundos"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
