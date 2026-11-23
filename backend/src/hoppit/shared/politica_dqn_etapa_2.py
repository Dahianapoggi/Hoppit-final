"""Inferencia de la politica aprendida de la etapa 2 (encargo 2026-11-19A,
bloques 4 a 7). Numpy puro, como `planificacion_rutas/motor/_politica_dqn.py`:
dos capas ocultas con ReLU, salida lineal, mascara y argmax. Esta en
`shared/` para que el simulador (evaluacion) y el producto (modo sombra de la
capa dinamica) usen la misma funcion.

Pesos: un `.npz` con arrays nombrados en `float32` (`capa1.peso`,
`capa1.sesgo`, ..., `capa3.sesgo`; pesos en la forma (salida, entrada)),
leido sin pickle, y su sidecar `.procedencia.json`, que tiene que declarar
`formato` = `FORMATO_DE_LOS_PESOS` y la codificacion
`observacion_etapa_2` en la version vigente; se verifica el SHA-256 del
`.npz` contra el sidecar. Si algo no coincide, error y no se carga.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any, Final

import numpy as np

from hoppit.shared.codificacion_observacion_etapa_2 import (
    CANTIDAD_DE_ACCIONES,
    NOMBRE_DE_LA_CODIFICACION,
    TAMANO_DE_LA_OBSERVACION,
    VERSION_DE_LA_CODIFICACION,
)

FORMATO_DE_LOS_PESOS: Final[str] = "dqn_etapa_2_npz_v1"
CAPAS: Final[tuple[str, ...]] = ("capa1", "capa2", "capa3")
OCULTAS: Final[int] = 128
DIMENSIONES: Final[tuple[int, ...]] = (TAMANO_DE_LA_OBSERVACION, OCULTAS, OCULTAS, CANTIDAD_DE_ACCIONES)
PENALIZACION_ACCION_INVALIDA: Final[float] = -1e9


class PesosInvalidos(ValueError):
    """Los pesos o su sidecar no corresponden a esta politica."""


def codificacion_esperada() -> dict[str, Any]:
    return {"nombre": NOMBRE_DE_LA_CODIFICACION, "version": VERSION_DE_LA_CODIFICACION,
            "tamano": TAMANO_DE_LA_OBSERVACION, "acciones": CANTIDAD_DE_ACCIONES}


class PoliticaDQNEtapa2:
    def __init__(self, arrays: dict[str, np.ndarray]) -> None:
        self._capas: list[tuple[np.ndarray, np.ndarray]] = []
        for capa, (entrada, salida) in zip(CAPAS, zip(DIMENSIONES, DIMENSIONES[1:])):
            peso, sesgo = arrays.get(f"{capa}.peso"), arrays.get(f"{capa}.sesgo")
            if peso is None or sesgo is None or peso.shape != (salida, entrada) or sesgo.shape != (salida,):
                raise PesosInvalidos(f"{capa}: falta o no tiene la forma ({salida}, {entrada})")
            self._capas.append((peso.astype(np.float32), sesgo.astype(np.float32)))

    @classmethod
    def desde_bytes(cls, contenido: bytes) -> PoliticaDQNEtapa2:
        with np.load(io.BytesIO(contenido), allow_pickle=False) as datos:
            return cls({k: datos[k] for k in datos.files})

    @classmethod
    def cargar(cls, ruta: Path) -> PoliticaDQNEtapa2:
        sidecar = json.loads(Path(f"{ruta}.procedencia.json").read_text(encoding="utf-8"))
        contenido = ruta.read_bytes()
        if sidecar.get("formato") != FORMATO_DE_LOS_PESOS:
            raise PesosInvalidos("formato de pesos distinto")
        if sidecar.get("codificacion") != codificacion_esperada():
            raise PesosInvalidos("codificacion de la observacion distinta")
        if sidecar.get("sha256") != hashlib.sha256(contenido).hexdigest():
            raise PesosInvalidos("el SHA-256 de los pesos no coincide con el sidecar")
        return cls.desde_bytes(contenido)

    def valores(self, observacion: np.ndarray) -> np.ndarray:
        x = np.asarray(observacion, dtype=np.float32)
        for i, (peso, sesgo) in enumerate(self._capas):
            x = peso @ x + sesgo
            if i < len(self._capas) - 1:
                x = np.maximum(x, 0.0)
        return x

    def elegir_accion(self, observacion: np.ndarray, mascara: np.ndarray) -> int:
        if not bool(np.any(mascara)):
            raise ValueError("ninguna accion valida")
        valores = self.valores(observacion) + PENALIZACION_ACCION_INVALIDA * (~np.asarray(mascara, dtype=bool))
        return int(np.argmax(valores))
