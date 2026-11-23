"""Referencias descriptivas de la etapa 1 del agente DQN (sesion 2026-10-19B,
bloque R; B-239, punto 2; B-240, E17).

Dos politicas de referencia evaluadas en el `EntornoSecuencial` sobre las 50
instancias de comparacion de 2026-10-19A, con las mismas semillas de
episodio que `tools/comparar_dqn_etapa1.py`:

- vecino mas cercano, con la misma informacion que la observacion del
  agente: la proxima Parada es la pendiente de menor distancia por la red
  desde la posicion actual, leida del mismo vector que recibe el agente; ante
  un empate, el slot menor;
- aleatoria: uniforme entre las acciones validas, con un generador por
  episodio derivado de `SEMILLA_POLITICA_ALEATORIA_DQN`.

El agente (pesos versionados, sin reentrenar) y la linea de base primaria de
OR-Tools se evaluan otra vez con las funciones de Q6, de modo que sus cifras
reproducen las de 2026-10-19A.

Lectura fijada antes de correr (encargo 2026-10-19B, bloque R, punto 4) sobre
el intervalo de agente menos vecino mas cercano: si contiene el cero, "el
agente no se distingue del vecino mas cercano en este simulador"; si queda
entero por encima, "el agente es peor que el vecino mas cercano"; si queda
entero por debajo, "el agente es mejor que el vecino mas cercano y peor que
OR-Tools". Criterio de compatibilidad con la hipotesis de B-239, punto 2,
fijado en el informe de la sesion antes de correr: compatible si el intervalo
de vecino mas cercano menos OR-Tools queda entero por encima de cero.

Rotulo (DAT-30): [simulacion sobre instancias sorteadas en CABA; no es
validacion contra datos reales], 10 Ordenes, 1 Vehiculo, sin llegadas.

    python tools/referencias_dqn_etapa1.py --salida <json> --instancias-de-comparacion <ruta>
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import platform
import sys
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.entorno_simulacion.puerto import (
    EntornoSecuencial,
    Instancia,
    SemillaDeEpisodio,
    paradas_de_la_instancia,
    recorrer_secuencia,
)
from hoppit.planificacion_rutas.motor._politica_dqn import EvaluadorPoliticaDQN
from hoppit.shared.codificacion_observacion import COMPONENTES_POR_SLOT, N_MAX
from hoppit.shared.intervalo_por_celda import (
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    PRIMERA_FRANJA_DE_LA_JORNADA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    FranjaFueraDeLaJornada,
    leer_tabla_de_intervalos,
    media_de_celda,
)
from tools.comparar_dqn_etapa1 import (
    EPISODIOS_POR_INSTANCIA,
    NOTA_AL_PIE,
    PESOS,
    ROTULO,
    SEMILLA_BASE_COMPARACION_DQN,
    TABLA_VIGENTE,
    distancia_de_la_secuencia,
    episodio_del_agente,
    estadistica_pareada,
    matrices,
    secuencia_de_or_tools,
)

FORMATO_LOG: Final[str] = "%(asctime)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.referencias_dqn_etapa1")

SEMILLA_POLITICA_ALEATORIA_DQN: Final[int] = 20261025
SEMILLA_BOOTSTRAP_REFERENCIAS_DQN: Final[int] = 20261026
# Posicion, dentro de los cinco componentes de un slot, de la distancia por
# la red desde la posicion actual (`shared/codificacion_observacion.py`).
COMPONENTE_DISTANCIA_DESDE_LA_POSICION: Final[int] = 2
TAMANO_DE_INSTANCIA: Final[str] = "10 Ordenes, 1 Vehiculo; sin llegadas durante la jornada"

LECTURA_SIN_DIFERENCIA: Final[str] = "el agente no se distingue del vecino mas cercano en este simulador"
LECTURA_PEOR: Final[str] = "el agente es peor que el vecino mas cercano"
LECTURA_MEJOR: Final[str] = "el agente es mejor que el vecino mas cercano y peor que OR-Tools"
COMPATIBLE: Final[str] = "compatible"
NO_COMPATIBLE: Final[str] = "no compatible"

Politica = Callable[[np.ndarray, np.ndarray], int]


def vecino_mas_cercano(vector: np.ndarray, mascara: np.ndarray) -> int:
    """La Parada pendiente de menor distancia desde la posicion actual, leida
    de la observacion; ante un empate, el slot menor (`argmin` devuelve el
    primero)."""
    distancias = vector[: N_MAX * COMPONENTES_POR_SLOT].reshape(N_MAX, COMPONENTES_POR_SLOT)[
        :, COMPONENTE_DISTANCIA_DESDE_LA_POSICION
    ]
    candidatas = np.where(mascara, distancias.astype(float), np.inf)
    if not np.isfinite(candidatas).any():
        raise ValueError("no hay acciones validas")
    return int(np.argmin(candidatas))


def politica_aleatoria(generador: np.random.Generator) -> Politica:
    """Uniforme entre las acciones validas."""

    def elegir(vector: np.ndarray, mascara: np.ndarray) -> int:
        validas = np.flatnonzero(mascara)
        if not len(validas):
            raise ValueError("no hay acciones validas")
        return int(generador.choice(validas))

    return elegir


def generador_aleatorio(instancia: int, episodio: int) -> np.random.Generator:
    """Un flujo por episodio, independiente del orden en que se evaluan."""
    return np.random.default_rng(
        np.random.SeedSequence(SEMILLA_POLITICA_ALEATORIA_DQN, spawn_key=(instancia, episodio))
    )


def episodio_de_la_politica(
    entorno: EntornoSecuencial, politica: Politica, instancia: Instancia, semilla: SemillaDeEpisodio
) -> tuple[float, list[int]]:
    """Lazo cerrado de una politica de referencia: duracion y slots."""
    observacion = entorno.reiniciar(instancia, semilla)
    slots: list[int] = []
    terminado = False
    while not terminado:
        accion = politica(observacion.vector, observacion.mascara)
        observacion, _, terminado, _ = entorno.paso(accion)
        slots.append(accion)
    return entorno.duracion_min, slots


def lectura_del_agente(intervalo: tuple[float, float]) -> str:
    bajo, alto = intervalo
    if bajo <= 0.0 <= alto:
        return LECTURA_SIN_DIFERENCIA
    return LECTURA_PEOR if bajo > 0.0 else LECTURA_MEJOR


def compatibilidad(intervalo_vecino_menos_or_tools: tuple[float, float]) -> str:
    """Criterio fijado antes de correr: una regla con la misma informacion
    que el agente tampoco alcanza a OR-Tools."""
    return COMPATIBLE if intervalo_vecino_menos_or_tools[0] > 0.0 else NO_COMPATIBLE


MOTORES: Final[tuple[str, ...]] = ("agente", "vecino_mas_cercano", "aleatoria", "or_tools")


def evaluar(
    instancias: Sequence[Instancia], entorno: EntornoSecuencial, evaluador: EvaluadorPoliticaDQN,
    media_de_parada_min: float, episodios: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    por_instancia = []
    fuera_de_jornada = dict.fromkeys(MOTORES, 0)
    for numero, instancia in enumerate(instancias):
        paradas = paradas_de_la_instancia(instancia)
        nodos, _, segundos = matrices(instancia, paradas, media_de_parada_min, VELOCIDAD_DE_TRABAJO_KM_H)
        primaria = secuencia_de_or_tools(nodos, segundos, paradas)
        duraciones: dict[str, list[float]] = {m: [] for m in MOTORES}
        distancias: dict[str, list[float]] = {m: [] for m in MOTORES}
        for episodio in range(episodios):
            semilla = SemillaDeEpisodio(SEMILLA_BASE_COMPARACION_DQN, numero * EPISODIOS_POR_INSTANCIA + episodio)
            corridas: dict[str, Callable[[], tuple[float, list[int]]]] = {
                "agente": lambda: episodio_del_agente(entorno, evaluador, instancia, semilla),
                "vecino_mas_cercano": lambda: episodio_de_la_politica(entorno, vecino_mas_cercano, instancia, semilla),
                "aleatoria": lambda: episodio_de_la_politica(
                    entorno, politica_aleatoria(generador_aleatorio(numero, episodio)), instancia, semilla
                ),
                "or_tools": lambda: (recorrer_secuencia(entorno, instancia, semilla, primaria)[0], primaria),
            }
            for motor, correr in corridas.items():
                try:
                    duracion, slots = correr()
                except FranjaFueraDeLaJornada:
                    fuera_de_jornada[motor] += 1
                    continue
                duraciones[motor].append(duracion)
                distancias[motor].append(distancia_de_la_secuencia(instancia, paradas, slots))
        registro: dict[str, Any] = {"instancia": numero, "paradas": len(paradas)}
        for motor in MOTORES:
            registro[f"mediana_{motor}_min"] = float(np.median(duraciones[motor]))
            registro[f"distancia_mediana_{motor}_m"] = float(np.median(distancias[motor]))
        por_instancia.append(registro)
        _LOG.info("Instancia %d | %s", numero, {k: round(v, 2) for k, v in registro.items() if k.startswith("mediana")})
    return por_instancia, fuera_de_jornada


def resumir(por_instancia: Sequence[dict[str, Any]]) -> dict[str, Any]:
    generador = np.random.default_rng(SEMILLA_BOOTSTRAP_REFERENCIAS_DQN)

    def diferencia(a: str, b: str) -> dict[str, Any]:
        estadistica = estadistica_pareada(
            [r[f"mediana_{a}_min"] - r[f"mediana_{b}_min"] for r in por_instancia], generador
        )
        resultado = dataclasses.asdict(estadistica)
        # La lectura de `estadistica_pareada` es la de ARQ-46 para el agente
        # contra OR-Tools; aca no aplica y se reemplaza abajo.
        resultado.pop("lectura")
        resultado["distancia_mediana_de_las_diferencias_m"] = float(np.median(
            [r[f"distancia_mediana_{a}_m"] - r[f"distancia_mediana_{b}_m"] for r in por_instancia]
        ))
        return resultado

    agente_menos_vecino = diferencia("agente", "vecino_mas_cercano")
    vecino_menos_or_tools = diferencia("vecino_mas_cercano", "or_tools")
    aleatoria_menos_vecino = diferencia("aleatoria", "vecino_mas_cercano")
    agente_menos_vecino["lectura"] = lectura_del_agente(tuple(agente_menos_vecino["intervalo"]))
    vecino_menos_or_tools["compatibilidad_con_b239_punto_2"] = compatibilidad(tuple(vecino_menos_or_tools["intervalo"]))
    return {
        "medianas_de_la_mediana_por_instancia_min": {
            m: float(np.median([r[f"mediana_{m}_min"] for r in por_instancia])) for m in MOTORES
        },
        "medianas_de_la_distancia_por_instancia_m": {
            m: float(np.median([r[f"distancia_mediana_{m}_m"] for r in por_instancia])) for m in MOTORES
        },
        "agente_menos_vecino_mas_cercano": agente_menos_vecino,
        "vecino_mas_cercano_menos_or_tools": vecino_menos_or_tools,
        "aleatoria_menos_vecino_mas_cercano": aleatoria_menos_vecino,
    }


def main(argumentos: Sequence[str]) -> int:
    import scipy

    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
    from tools.amazon_2021 import pico_del_proceso_mb
    from tools.instancias_dqn_etapa1 import SALIDA_DE_COMPARACION, leer_instancias_de_comparacion

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.planificacion_rutas").setLevel(logging.WARNING)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    lector.add_argument("--instancias-de-comparacion", type=pathlib.Path, default=SALIDA_DE_COMPARACION)
    lector.add_argument("--pesos", type=pathlib.Path, default=PESOS)
    lector.add_argument("--instancias", type=int, default=None, help="solo para una prueba rapida")
    lector.add_argument("--episodios", type=int, default=EPISODIOS_POR_INSTANCIA, help="solo para una prueba rapida")
    args = lector.parse_args(list(argumentos))
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")
    inicio = time.perf_counter()

    tabla = leer_tabla_de_intervalos(TABLA_VIGENTE)
    media_8 = media_de_celda(tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, PRIMERA_FRANJA_DE_LA_JORNADA))
    evaluador = EvaluadorPoliticaDQN(args.pesos)
    evaluador.cargar_pesos()
    contenido, instancias = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    if args.instancias is not None:
        instancias = instancias[: args.instancias]
    entorno = EntornoSecuencial(tabla)

    por_instancia, fuera_de_jornada = evaluar(instancias, entorno, evaluador, media_8, args.episodios)
    resultado = {
        "rotulo": ROTULO,
        "nota_al_pie": NOTA_AL_PIE,
        "tamano_de_instancia": TAMANO_DE_INSTANCIA,
        "instancias": len(por_instancia),
        "episodios_por_instancia": args.episodios,
        "semillas": {
            "SEMILLA_BASE_COMPARACION_DQN": SEMILLA_BASE_COMPARACION_DQN,
            "SEMILLA_POLITICA_ALEATORIA_DQN": SEMILLA_POLITICA_ALEATORIA_DQN,
            "SEMILLA_BOOTSTRAP_REFERENCIAS_DQN": SEMILLA_BOOTSTRAP_REFERENCIAS_DQN,
        },
        "pesos": args.pesos.name,
        "pesos_sha256": evaluador.version,
        "instancias_de_comparacion": args.instancias_de_comparacion.name,
        "deposito_nodo": contenido["deposito"]["nodo"],
        "tabla_sha256": tabla.sha256,
        "episodios_fuera_de_la_jornada": fuera_de_jornada,
        **resumir(por_instancia),
        "por_instancia": por_instancia,
        "versiones": {"numpy": np.__version__, "scipy": scipy.__version__, "python": platform.python_version()},
        "segundos": round(time.perf_counter() - inicio, 1),
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    texto = json.dumps(resultado, ensure_ascii=False, indent=1, default=str) + "\n"
    args.salida.write_bytes(texto.encode("utf-8"))
    escribir_sidecar(
        registrar(artefacto="referencias_dqn_etapa1", ambito="Ciudad Autonoma de Buenos Aires",
                  fuente="EntornoSecuencial sobre las 50 instancias de comparacion",
                  herramienta="tools/referencias_dqn_etapa1.py",
                  parametros={"sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(), "rotulo": ROTULO,
                              "pesos_sha256": evaluador.version, "versiones": resultado["versiones"]}),
        args.salida,
    )
    a = resultado["agente_menos_vecino_mas_cercano"]
    v = resultado["vecino_mas_cercano_menos_or_tools"]
    _LOG.info("Agente menos vecino | mediana %.3f | IC %s | %s", a["mediana_de_las_diferencias"], a["intervalo"], a["lectura"])
    _LOG.info("Vecino menos OR-Tools | mediana %.3f | IC %s | %s", v["mediana_de_las_diferencias"], v["intervalo"],
              v["compatibilidad_con_b239_punto_2"])
    _LOG.info("Pico %s MB | %s s", resultado["pico_del_proceso_mb"], resultado["segundos"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
