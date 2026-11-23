"""Evaluador de la politica aprendida de la etapa 1 (DQN) -- D2 (ARQ-44).

Inferencia greedy sobre pesos congelados y versionados (ARQ-06): numpy puro,
sin PyTorch, que solo se usa fuera de linea para entrenar (B-233, punto 6).
Es determinista por construccion y sostiene NFR-07: dos multiplicaciones de
matrices con ReLU, una salida lineal, la mascara y el argmax.

Pesos: un `.npz` con arrays nombrados en `float32` (`capa1.peso`,
`capa1.sesgo`, ... `capa3.sesgo`; cada peso en la forma (salida, entrada)),
leido sin pickle, y su sidecar de procedencia. Antes de cargar se verifica el
SHA-256 del `.npz` contra el sidecar (la lectura unica de
`shared/intervalo_por_celda.py`), el formato y que la codificacion de la
observacion del sidecar sea la de `shared/codificacion_observacion.py`; si
algo no coincide, error explicito y no se carga (ARQ-44).

`elegir_accion(observacion, mascara)` es la operacion que usa la comparacion
de la etapa 1, en lazo cerrado dentro del simulador (ARQ-46).

`resolver(instancia)` existe solo para instancias de un Vehiculo y hasta
`N_MAX` Paradas, y levanta `InstanciaFueraDelDominioDQN` fuera de ese
dominio. Sin simulacion, el reloj avanza con el viaje determinista (la
distancia por la red sobre la velocidad de trabajo, un supuesto declarado)
mas `media_de_celda` de la componente de parada de 0 a 100 m en la franja del
reloj, una vez por Orden. Es una regla provisoria del chat central (B-237,
punto 5), a confirmar con Chat A. El evaluador no se conecta a
`construir_contexto` (B-236): ningun ensamblado lo elige.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Final

import numpy as np

from hoppit.shared.codificacion_observacion import (
    ESCALA_DEMANDA_BULTOS,
    ESCALA_DISTANCIA_M,
    ESCALA_MINUTOS,
    N_MAX,
    NOMBRE_DE_LA_CODIFICACION,
    TAMANO_DE_LA_OBSERVACION,
    VERSION_DE_LA_CODIFICACION,
    codificar_observacion,
    mascara_de_acciones,
)
from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.intervalo_por_celda import (
    CLAVE_PARAMETROS,
    CODIFICACION,
    DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M,
    MINUTOS_POR_HORA,
    VELOCIDAD_DE_TRABAJO_KM_H,
    TablaDeIntervalos,
    franja_del_reloj,
    leer_artefacto_verificado,
    media_de_celda,
    ruta_del_sidecar,
    viaje_por_la_red_min,
)

from ._solver_vrp import _identificador_de_ruta, _resumen_de_instancia
from .base import (
    InstanciaInvalida,
    InstanciaProblema,
    MotorDeRuteo,
    ParadaDeInstancia,
    ParadaPlaneada,
    Ruta,
    SolucionRuteo,
)

FORMATO_DE_LOS_PESOS: Final[str] = "dqn_etapa1_npz_float32_v1"
CAPAS: Final[tuple[str, ...]] = ("capa1", "capa2", "capa3")
DIMENSIONES: Final[tuple[int, ...]] = (TAMANO_DE_LA_OBSERVACION, 128, 128, N_MAX)
SUFIJO_PESO: Final[str] = ".peso"
SUFIJO_SESGO: Final[str] = ".sesgo"
CLAVE_FORMATO: Final[str] = "formato"
CLAVE_CODIFICACION: Final[str] = "codificacion"
CLAVE_ESCALAS: Final[str] = "escalas"
CLAVE_NOMBRE: Final[str] = "nombre"
CLAVE_VERSION: Final[str] = "version"

PENALIZACION_ACCION_INVALIDA: Final[float] = -1e9
"""Se suma a los valores de las acciones invalidas antes de todo argmax:
finita, para no propagar NaN (ARQ-43)."""

# Hora de salida del Deposito para `resolver`: las 8:00, el parametro de
# demostracion de la simulacion (B-178, punto 9).
HORA_DE_SALIDA: Final[int] = 8
MINUTO_DE_SALIDA: Final[float] = HORA_DE_SALIDA * MINUTOS_POR_HORA

_TIPO: Final = np.float32


class PesosNoVerificados(ErrorDeDominio):
    """Los pesos no se pueden cargar: formato, forma o codificacion distintos
    de los que espera el evaluador."""


class InstanciaFueraDelDominioDQN(InstanciaInvalida):
    """`resolver` solo admite un Vehiculo y hasta `N_MAX` Paradas."""


def nombres_de_los_arrays() -> tuple[str, ...]:
    return tuple(f"{capa}{sufijo}" for capa in CAPAS for sufijo in (SUFIJO_PESO, SUFIJO_SESGO))


def formas_esperadas() -> dict[str, tuple[int, ...]]:
    formas: dict[str, tuple[int, ...]] = {}
    for capa, entrada, salida in zip(CAPAS, DIMENSIONES[:-1], DIMENSIONES[1:]):
        formas[f"{capa}{SUFIJO_PESO}"] = (salida, entrada)
        formas[f"{capa}{SUFIJO_SESGO}"] = (salida,)
    return formas


def escalas_esperadas() -> dict[str, float]:
    """Las escalas con que el codigo normaliza la observacion. Los pesos se
    entrenaron con unas escalas y solo valen con esas: ARQ-44 pide que el
    evaluador falle si el sidecar no las declara iguales (ARQ-66; 2026-10-20A,
    X.2; 2026-10-20B, X2)."""
    return {
        "ESCALA_DISTANCIA_M": float(ESCALA_DISTANCIA_M),
        "ESCALA_MINUTOS": float(ESCALA_MINUTOS),
        "ESCALA_DEMANDA_BULTOS": float(ESCALA_DEMANDA_BULTOS),
    }


def codificacion_esperada() -> dict[str, Any]:
    return {CLAVE_NOMBRE: NOMBRE_DE_LA_CODIFICACION, CLAVE_VERSION: VERSION_DE_LA_CODIFICACION}


class EvaluadorPoliticaDQN(MotorDeRuteo):
    """Politica greedy sobre pesos versionados (ARQ-44)."""

    def __init__(
        self,
        ruta_pesos_versionados: Path,
        tabla: TablaDeIntervalos | None = None,
        velocidad_km_h: float = VELOCIDAD_DE_TRABAJO_KM_H,
        salida_min: float = MINUTO_DE_SALIDA,
    ) -> None:
        self._ruta_pesos = ruta_pesos_versionados
        self._tabla = tabla
        self._velocidad_km_h = velocidad_km_h
        self._salida_min = salida_min
        self._version: str | None = None  # se fija al cargar (ARQ-15)
        self._capas: list[tuple[np.ndarray, np.ndarray]] = []

    @property
    def version(self) -> str | None:
        """El SHA-256 de los pesos cargados, o None si no se cargaron."""
        return self._version

    def cargar_pesos(self) -> None:
        """Verifica y carga. Sin sidecar, con otro SHA-256, otro formato, otra
        forma o otra codificacion, levanta el error y no carga nada."""
        artefacto = leer_artefacto_verificado(self._ruta_pesos)
        registro = json.loads(ruta_del_sidecar(self._ruta_pesos).read_text(encoding=CODIFICACION))
        parametros = registro.get(CLAVE_PARAMETROS, {})
        if parametros.get(CLAVE_FORMATO) != FORMATO_DE_LOS_PESOS:
            raise PesosNoVerificados(
                f"{self._ruta_pesos.name}: formato {parametros.get(CLAVE_FORMATO)!r}, se espera {FORMATO_DE_LOS_PESOS!r}"
            )
        if parametros.get(CLAVE_CODIFICACION) != codificacion_esperada():
            raise PesosNoVerificados(
                f"{self._ruta_pesos.name}: entrenados con la codificacion {parametros.get(CLAVE_CODIFICACION)!r}, "
                f"y la vigente es {codificacion_esperada()!r}"
            )
        escalas = parametros.get(CLAVE_ESCALAS)
        if not isinstance(escalas, dict) or {k: float(v) for k, v in escalas.items()} != escalas_esperadas():
            raise PesosNoVerificados(
                f"{self._ruta_pesos.name}: escalas {escalas!r} en el sidecar, y las del codigo son "
                f"{escalas_esperadas()!r}"
            )
        with np.load(io.BytesIO(artefacto.contenido), allow_pickle=False) as archivo:
            arrays = {nombre: archivo[nombre] for nombre in archivo.files}
        formas = formas_esperadas()
        if set(arrays) != set(formas):
            raise PesosNoVerificados(f"{self._ruta_pesos.name}: arrays {sorted(arrays)}, se esperan {sorted(formas)}")
        for nombre, forma in formas.items():
            if arrays[nombre].shape != forma or arrays[nombre].dtype != _TIPO:
                raise PesosNoVerificados(
                    f"{self._ruta_pesos.name}: {nombre} es {arrays[nombre].dtype}{arrays[nombre].shape}, se espera "
                    f"float32{forma}"
                )
        self._capas = [(arrays[f"{c}{SUFIJO_PESO}"], arrays[f"{c}{SUFIJO_SESGO}"]) for c in CAPAS]
        self._version = artefacto.sha256

    def valores(self, observacion: np.ndarray) -> np.ndarray:
        """Los valores Q de las `N_MAX` acciones, sin mascara."""
        if not self._capas:
            self.cargar_pesos()
        activacion = np.asarray(observacion, dtype=_TIPO)
        if activacion.shape != (TAMANO_DE_LA_OBSERVACION,):
            raise ValueError(f"la observacion tiene forma {activacion.shape}")
        for posicion, (peso, sesgo) in enumerate(self._capas):
            activacion = peso @ activacion + sesgo
            if posicion < len(self._capas) - 1:
                activacion = np.maximum(activacion, _TIPO(0))
        return activacion

    def elegir_accion(self, observacion: np.ndarray, mascara: np.ndarray) -> int:
        """La accion valida de mayor valor; ante empate, la de menor slot."""
        mascara = np.asarray(mascara, dtype=bool)
        if mascara.shape != (N_MAX,) or not mascara.any():
            raise ValueError("la mascara tiene que tener alguna accion valida")
        penalizados = self.valores(observacion).astype(np.float64) + PENALIZACION_ACCION_INVALIDA * (~mascara)
        return int(np.argmax(penalizados))

    def resolver(self, instancia: InstanciaProblema) -> SolucionRuteo:
        """Secuencia greedy de las Paradas de un Vehiculo (regla provisoria de
        B-237, punto 5): el reloj avanza el viaje determinista y la media de
        la componente de parada de cada Orden."""
        if (
            len(instancia.capacidad_por_vehiculo) != 1
            or len(instancia.depositos) != 1
            or not 1 <= len(instancia.paradas) <= N_MAX
        ):
            raise InstanciaFueraDelDominioDQN(
                f"el evaluador de la etapa 1 resuelve un Vehiculo, un Deposito y hasta {N_MAX} Paradas; la "
                f"instancia trae {len(instancia.capacidad_por_vehiculo)} Vehiculos, {len(instancia.depositos)} "
                f"Depositos y {len(instancia.paradas)} Paradas"
            )
        if self._tabla is None:
            raise InstanciaFueraDelDominioDQN("resolver necesita la tabla de la componente de parada")
        ((vehiculo, capacidad),) = instancia.capacidad_por_vehiculo.items()
        (deposito,) = instancia.depositos
        paradas = instancia.paradas
        matriz = instancia.matriz
        pendientes = [True] * len(paradas)
        posicion = deposito
        reloj = self._salida_min
        distancia_total = 0.0
        visitadas: list[ParadaDeInstancia] = []
        while any(pendientes):
            observacion = codificar_observacion(
                pendientes=pendientes,
                distancia_desde_la_posicion_m=[matriz.distancia_m(posicion, p.nodo) for p in paradas],
                distancia_al_deposito_m=[matriz.distancia_m(p.nodo, deposito) for p in paradas],
                demanda_bultos=[float(p.demanda) for p in paradas],
                reloj_min=reloj,
                distancia_de_la_posicion_al_deposito_m=matriz.distancia_m(posicion, deposito),
                duracion_acumulada_min=reloj - self._salida_min,
            )
            accion = self.elegir_accion(observacion, mascara_de_acciones(pendientes))
            parada = paradas[accion]
            tramo = matriz.distancia_m(posicion, parada.nodo)
            distancia_total += tramo
            reloj += viaje_por_la_red_min(tramo, self._velocidad_km_h)
            for _ in parada.ordenes:
                celda = self._tabla.celda(DISTANCIA_DE_LA_COMPONENTE_DE_PARADA_M, franja_del_reloj(reloj))
                reloj += media_de_celda(celda)
            pendientes[accion] = False
            posicion = parada.nodo
            visitadas.append(parada)
        distancia_total += matriz.distancia_m(posicion, deposito)
        ruta_id = _identificador_de_ruta(_resumen_de_instancia(instancia), vehiculo, tuple(visitadas))
        ruta = Ruta(
            id=ruta_id,
            vehiculo_id=vehiculo,
            paradas=tuple(
                ParadaPlaneada(ruta_id=ruta_id, secuencia=secuencia, nodo=p.nodo, ordenes=p.ordenes, eta_estimado_s=None)
                for secuencia, p in enumerate(visitadas, start=1)
            ),
            distancia_m=distancia_total,
            carga=sum(p.demanda for p in visitadas),
            capacidad_vehiculo=capacidad,
        )
        return SolucionRuteo(rutas=(ruta,))
