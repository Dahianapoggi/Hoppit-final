"""Control de la implementacion de DQN de la etapa 1 con una codificacion
tabular del estado (encargo 2026-11-19A, bloque 3; juez del 18A, TR1 y E1).

Corre el mismo lazo de entrenamiento de `diagnostico_dqn_etapa1.
entrenar_con_diagnostico` (que es el de `entrenar_dqn_etapa1.entrenar`, con
el mismo Double DQN, la misma mascara, los mismos hiperparametros y el mismo
orden de consumo de los generadores) en la configuracion de E4 del encargo
2026-11-17C: la instancia de comparacion 0 repetida en todos los episodios,
tiempo de Parada fijo en la mediana por celda y franja
(`EntornoConParadaFija`), semilla 20261022 y 50.000 pasos, con el epsilon
original sobre ese presupuesto. Lo unico que cambia es la observacion: en
lugar de los 56 rasgos de la etapa 1, un vector de indicadores con un 1 en
el estado (Parada actual, conjunto de Paradas cumplidas), el mismo estado de
`aprendizaje_q_tabular_etapa1` (markoviano en esta instancia, 18A, bloque 1).
Por eso la red y el buffer son los de la etapa 1 con otra entrada
(`ESTADOS` = 5.121 indicadores); la salida sigue siendo de 10 acciones.

Criterio fijado antes de correr (encargo): si el goloso final en la
instancia llega a menos del 1 % del optimo de Held-Karp (238,408 min, es
decir, <= 240,79), el algoritmo DQN esta bien implementado y lo que fallaba
en E4 es la representacion o la busqueda; si no llega, posible defecto de
implementacion.

Rotulo: simulacion sobre instancias sorteadas en CABA; no es validacion
contra datos reales.

    python tools/control_dqn_tabular_etapa1.py --salida-dir <anexos>/b3 \
        --instancias-de-comparacion <...>/instancias_de_comparacion_2026-10-19A.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import sys
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.shared.codificacion_observacion import N_MAX  # noqa: E402
from tools import diagnostico_dqn_etapa1 as diagnostico  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402

ROTULO: Final[str] = diagnostico.ROTULO
OPTIMO_HELD_KARP_MIN: Final[float] = 238.408
TOPE_DEL_CRITERIO_MIN: Final[float] = OPTIMO_HELD_KARP_MIN * 1.01


def indices_de_estado(n: int = N_MAX) -> dict[tuple[int, int], int]:
    """(Parada actual, cumplidas como mascara de bits) -> indice; el inicial
    (-1, 0) es el 0; despues, por cumplidas y por Parada actual dentro de ellas."""
    indices = {(-1, 0): 0}
    for cumplidas in range(1, 2 ** n):
        for actual in range(n):
            if cumplidas >> actual & 1:
                indices[(actual, cumplidas)] = len(indices)
    return indices


INDICES: Final[dict[tuple[int, int], int]] = indices_de_estado()
ESTADOS: Final[int] = len(INDICES)


@dataclasses.dataclass(frozen=True)
class ObservacionTabular:
    vector: np.ndarray
    mascara: np.ndarray


def indicadores(estado: tuple[int, int]) -> np.ndarray:
    x = np.zeros(ESTADOS, dtype=np.float32)
    x[INDICES[estado]] = 1.0
    return x


class EntornoTabular:
    """Envuelve un entorno secuencial y cambia el vector de la observacion por
    los indicadores del estado; la mascara, la recompensa y la terminacion son
    las del entorno envuelto."""

    def __init__(self, entorno: Any) -> None:
        self._entorno = entorno
        self._estado = (-1, 0)

    @property
    def paradas(self) -> Any:
        return self._entorno.paradas

    @property
    def duracion_min(self) -> float:
        return float(self._entorno.duracion_min)

    def reiniciar(self, instancia: Any, semilla: Any) -> ObservacionTabular:
        observacion = self._entorno.reiniciar(instancia, semilla)
        self._estado = (-1, 0)
        return ObservacionTabular(indicadores(self._estado), observacion.mascara)

    def paso(self, accion: int) -> tuple[ObservacionTabular, float, bool, Any]:
        siguiente, recompensa, terminado, info = self._entorno.paso(accion)
        self._estado = (accion, self._estado[1] | (1 << accion))
        return ObservacionTabular(indicadores(self._estado), siguiente.mascara), recompensa, terminado, info


def red_tabular() -> Any:
    import torch

    return torch.nn.Sequential(
        torch.nn.Linear(ESTADOS, original.OCULTAS), torch.nn.ReLU(),
        torch.nn.Linear(original.OCULTAS, original.OCULTAS), torch.nn.ReLU(),
        torch.nn.Linear(original.OCULTAS, N_MAX),
    )


class BufferTabular:
    """El buffer de la etapa 1 (mismo orden de guardado y mismo muestreo:
    `generador.integers(0, tamano, size=lote)`), pero guarda el indice del
    estado en lugar del vector de indicadores, para no reservar 2 x 100.000 x
    5.121 numeros; arma los indicadores al muestrear."""

    def __init__(self, capacidad: int) -> None:
        self.capacidad = capacidad
        self.observacion = np.zeros(capacidad, dtype=np.int64)
        self.mascara = np.zeros((capacidad, N_MAX), dtype=bool)
        self.accion = np.zeros(capacidad, dtype=np.int64)
        self.recompensa = np.zeros(capacidad, dtype=np.float32)
        self.siguiente = np.zeros(capacidad, dtype=np.int64)
        self.mascara_siguiente = np.zeros((capacidad, N_MAX), dtype=bool)
        self.terminal = np.zeros(capacidad, dtype=bool)
        self.tamano = 0
        self._proxima = 0

    def guardar(self, observacion: np.ndarray, mascara: np.ndarray, accion: int, recompensa: float,
                siguiente: np.ndarray, mascara_siguiente: np.ndarray, terminal: bool) -> None:
        i = self._proxima
        self.observacion[i], self.mascara[i], self.accion[i], self.recompensa[i] = (
            int(np.argmax(observacion)), mascara, accion, recompensa)
        self.siguiente[i], self.mascara_siguiente[i], self.terminal[i] = int(np.argmax(siguiente)), mascara_siguiente, terminal
        self._proxima = (i + 1) % self.capacidad
        self.tamano = min(self.tamano + 1, self.capacidad)

    def muestra(self, generador: np.random.Generator, lote: int) -> dict[str, np.ndarray]:
        indices = generador.integers(0, self.tamano, size=lote)
        observacion = np.zeros((lote, ESTADOS), dtype=np.float32)
        observacion[np.arange(lote), self.observacion[indices]] = 1.0
        siguiente = np.zeros((lote, ESTADOS), dtype=np.float32)
        siguiente[np.arange(lote), self.siguiente[indices]] = 1.0
        return {"observacion": observacion, "mascara": self.mascara[indices], "accion": self.accion[indices],
                "recompensa": self.recompensa[indices], "siguiente": siguiente,
                "mascara_siguiente": self.mascara_siguiente[indices], "terminal": self.terminal[indices]}


def correr(instancia: Any, tabla: Any, semilla: int, presupuesto: int, curva: pathlib.Path | None) -> dict[str, Any]:
    """El lazo de E4 con la codificacion tabular: se reemplazan la red y el
    buffer del modulo de diagnostico solo durante la corrida."""
    previos = diagnostico.red, diagnostico.Buffer
    diagnostico.red, diagnostico.Buffer = red_tabular, BufferTabular  # type: ignore[assignment,misc]
    try:
        entorno = EntornoTabular(diagnostico.EntornoConParadaFija(tabla))
        validacion = diagnostico.Validacion("instancia_fija_parada_fija", EntornoTabular(diagnostico.EntornoConParadaFija(tabla)),
                                            [instancia], 1, diagnostico.SEMILLA_DE_VALIDACION)
        configuracion = diagnostico.Configuracion(semilla=semilla, presupuesto=presupuesto, parada_fija=True,
                                                  instancia_fija=0)
        resultado = diagnostico.entrenar_con_diagnostico(entorno, lambda e: instancia, configuracion,  # type: ignore[arg-type]
                                                         [validacion], None, curva)
    finally:
        diagnostico.red, diagnostico.Buffer = previos  # type: ignore[assignment,misc]
    goloso, slots, _ = diagnostico.episodio(EntornoTabular(diagnostico.EntornoConParadaFija(tabla)),  # type: ignore[arg-type]
                                            lambda v, m: diagnostico.accion_golosa(resultado.en_linea, v, m), instancia,
                                            original.SemillaDeEpisodio(diagnostico.SEMILLA_DE_VALIDACION, 0))
    ultima = resultado.curva[-1]
    return {"pasos": resultado.pasos, "episodios": resultado.episodios, "pasos_de_gradiente": resultado.pasos_de_gradiente,
            "segundos": round(resultado.segundos, 1), "goloso_final_min": goloso, "secuencia_golosa_final": slots,
            "validacion_final_min": ultima.get("validacion_instancia_fija_parada_fija_media_min"),
            "curva_validacion": [(f["paso"], f.get("validacion_instancia_fija_parada_fija_media_min")) for f in resultado.curva
                                 if f.get("validacion_instancia_fija_parada_fija_media_min") is not None]}


def main(argumentos: Sequence[str]) -> int:
    from tools.instancias_dqn_etapa1 import leer_instancias_de_comparacion

    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lector.add_argument("--semilla", type=int, default=original.SEMILLA_BASE_ENTRENAMIENTO_DQN)
    lector.add_argument("--presupuesto", type=int, default=50_000)
    lector.add_argument("--salida-dir", type=pathlib.Path, required=True)
    lector.add_argument("--instancias-de-comparacion", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    diagnostico._preparar_torch()
    args.salida_dir.mkdir(parents=True, exist_ok=True)
    resumen_ruta = args.salida_dir / "resumen_dqn_tabular_e4.json"
    curva_ruta = args.salida_dir / "curva_dqn_tabular_e4.csv"
    if resumen_ruta.exists() or curva_ruta.exists():
        raise SystemExit("ya existe: no se pisa")
    tabla = diagnostico._tabla()
    _, comparacion = leer_instancias_de_comparacion(args.instancias_de_comparacion)
    instancia = comparacion[0]
    salida = correr(instancia, tabla, args.semilla, args.presupuesto, curva_ruta)
    resumen = {"rotulo": ROTULO, "configuracion": "E4 del 17C (instancia 0, Parada fija, semilla, presupuesto y epsilon) "
                                                 "con indicadores de estado (Parada actual, cumplidas)",
               "semilla": args.semilla, "presupuesto": args.presupuesto, "estados": ESTADOS,
               "optimo_held_karp_min": OPTIMO_HELD_KARP_MIN, "tope_del_criterio_min": TOPE_DEL_CRITERIO_MIN,
               **salida, "llega": bool(salida["goloso_final_min"] <= TOPE_DEL_CRITERIO_MIN),
               "exceso": salida["goloso_final_min"] / OPTIMO_HELD_KARP_MIN - 1,
               "curva_sha256": hashlib.sha256(curva_ruta.read_bytes()).hexdigest(), "versiones": diagnostico._versiones()}
    resumen_ruta.write_text(json.dumps(resumen, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: resumen[k] for k in ("goloso_final_min", "validacion_final_min", "llega", "exceso", "segundos")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
