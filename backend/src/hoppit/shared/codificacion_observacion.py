"""Codificacion de la observacion de la etapa 1 del agente (ARQ-43, ARQ-45).

Fuente unica de la regla, compartida por el plano offline (el entorno
secuencial y el entrenamiento) y por la inferencia (`EvaluadorPoliticaDQN`):
con las reglas de import 2 y 5 no hay otra ubicacion desde la que los dos
lados lean la misma funcion (ARQ-45). La funcion es pura: recibe numeros ya
calculados (distancias en metros, reloj, banderas y demandas) y devuelve el
vector. No importa el grafo, ni Cartografia, ni el dominio; quien la llama
calcula las distancias.

Vector de tamano fijo, `TAMANO_DE_LA_OBSERVACION` = 56 componentes en
`float32`:

- por cada slot `i` de 0 a `N_MAX` - 1, cinco componentes: ocupado (1 si la
  instancia tiene una Parada en ese slot), pendiente (1 si esta pendiente; 0
  si ya se cumplio o el slot esta vacio), distancia por la red desde la
  posicion actual hasta la Parada, distancia por la red desde la Parada hasta
  el Deposito, y demanda de la Parada en bultos. Las distancias van sobre
  `ESCALA_DISTANCIA_M` y la demanda sobre `ESCALA_DEMANDA_BULTOS`. Un slot
  vacio tiene sus cinco componentes en 0;
- seis globales: fraccion de Paradas pendientes sobre las de la instancia,
  hora normalizada dentro de la jornada de 8 a 22, su seno y su coseno (sin
  corte artificial entre los extremos), distancia por la red desde la
  posicion actual hasta el Deposito sobre `ESCALA_DISTANCIA_M`, y duracion
  acumulada del episodio sobre `ESCALA_MINUTOS`.

Las escalas son constantes, nunca el maximo de la instancia: normalizar por
instancia haria que la misma situacion diera vectores distintos segun el
caso (ARQ-43). Sus valores son valores de diseno de la sesion 2026-10-19A,
no datos:

- `ESCALA_DISTANCIA_M` = 10.000 m: del orden de un tramo largo dentro de la
  Ciudad, de modo que las distancias por la red entre nodos de CABA quedan
  en unidades de 0 a unos 3, sin saturar y sin comprimir los tramos cortos;
- `ESCALA_MINUTOS` = 480 min: una jornada de ocho horas; un episodio de 10
  Paradas dura unas horas y su duracion acumulada queda entre 0 y 1;
- `ESCALA_DEMANDA_BULTOS` = 10 bultos: la demanda de una Parada del
  generador de CABA (paquetes por parada de Amazon, DAT-25) queda en el
  orden de la unidad. La demanda no restringe en la etapa 1; va en el
  vector para que su tamano no cambie en la etapa 2.

El orden de los slots es el de las Paradas de la instancia exportada; esta
funcion no reordena nada.

Cambiar el contenido, el orden o una escala exige subir
`VERSION_DE_LA_CODIFICACION`: el evaluador de pesos la compara con la de su
sidecar y no carga pesos entrenados con otra (ARQ-44).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

import numpy as np

NOMBRE_DE_LA_CODIFICACION: Final[str] = "observacion_etapa_1"
VERSION_DE_LA_CODIFICACION: Final[int] = 1

N_MAX: Final[int] = 10
COMPONENTES_POR_SLOT: Final[int] = 5
COMPONENTES_GLOBALES: Final[int] = 6
TAMANO_DE_LA_OBSERVACION: Final[int] = N_MAX * COMPONENTES_POR_SLOT + COMPONENTES_GLOBALES

ESCALA_DISTANCIA_M: Final[float] = 10_000.0
ESCALA_MINUTOS: Final[float] = 480.0
ESCALA_DEMANDA_BULTOS: Final[float] = 10.0

# La jornada simulada, de las 8 a las 22 (B-181, punto 2), en minutos desde
# las 0:00: la hora normalizada vale 0 a las 8:00 y 1 a las 22:00.
INICIO_DE_LA_JORNADA_MIN: Final[float] = 8 * 60.0
FIN_DE_LA_JORNADA_MIN: Final[float] = 22 * 60.0

_TIPO: Final = np.float32
_VUELTA: Final[float] = 2 * math.pi


class ObservacionInvalida(ValueError):
    """Los datos no describen una observacion de la etapa 1."""


def codificar_observacion(
    *,
    pendientes: Sequence[bool],
    distancia_desde_la_posicion_m: Sequence[float],
    distancia_al_deposito_m: Sequence[float],
    demanda_bultos: Sequence[float],
    reloj_min: float,
    distancia_de_la_posicion_al_deposito_m: float,
    duracion_acumulada_min: float,
) -> np.ndarray:
    """El vector de la observacion.

    Las cuatro secuencias van por Parada, en el orden de los slots, con una
    entrada por Parada de la instancia (de 1 a `N_MAX`); los slots que
    sobran quedan vacios.
    """
    cantidad = len(pendientes)
    if not 1 <= cantidad <= N_MAX:
        raise ObservacionInvalida(f"la instancia tiene {cantidad} Paradas; la etapa 1 admite de 1 a {N_MAX}")
    if not (len(distancia_desde_la_posicion_m) == len(distancia_al_deposito_m) == len(demanda_bultos) == cantidad):
        raise ObservacionInvalida("las secuencias por Parada no tienen la misma longitud")
    valores = (
        *distancia_desde_la_posicion_m, *distancia_al_deposito_m, *demanda_bultos,
        reloj_min, distancia_de_la_posicion_al_deposito_m, duracion_acumulada_min,
    )
    if not all(math.isfinite(v) and v >= 0 for v in valores):
        raise ObservacionInvalida("las distancias, demandas, reloj y duracion tienen que ser finitos y no negativos")

    vector = np.zeros(TAMANO_DE_LA_OBSERVACION, dtype=_TIPO)
    slots = vector[: N_MAX * COMPONENTES_POR_SLOT].reshape(N_MAX, COMPONENTES_POR_SLOT)
    slots[:cantidad, 0] = 1.0
    slots[:cantidad, 1] = np.asarray(pendientes, dtype=bool)
    slots[:cantidad, 2] = np.asarray(distancia_desde_la_posicion_m, dtype=float) / ESCALA_DISTANCIA_M
    slots[:cantidad, 3] = np.asarray(distancia_al_deposito_m, dtype=float) / ESCALA_DISTANCIA_M
    slots[:cantidad, 4] = np.asarray(demanda_bultos, dtype=float) / ESCALA_DEMANDA_BULTOS

    hora = (reloj_min - INICIO_DE_LA_JORNADA_MIN) / (FIN_DE_LA_JORNADA_MIN - INICIO_DE_LA_JORNADA_MIN)
    globales = vector[N_MAX * COMPONENTES_POR_SLOT :]
    globales[0] = sum(bool(p) for p in pendientes) / cantidad
    globales[1] = hora
    globales[2] = math.sin(_VUELTA * hora)
    globales[3] = math.cos(_VUELTA * hora)
    globales[4] = distancia_de_la_posicion_al_deposito_m / ESCALA_DISTANCIA_M
    globales[5] = duracion_acumulada_min / ESCALA_MINUTOS
    return vector


def mascara_de_acciones(pendientes: Sequence[bool]) -> np.ndarray:
    """Acciones validas: el slot esta ocupado y pendiente (ARQ-43). La
    calcula el entorno; la red nunca la calcula por su cuenta."""
    if not 1 <= len(pendientes) <= N_MAX:
        raise ObservacionInvalida(f"la instancia tiene {len(pendientes)} Paradas; la etapa 1 admite de 1 a {N_MAX}")
    mascara = np.zeros(N_MAX, dtype=bool)
    mascara[: len(pendientes)] = np.asarray(pendientes, dtype=bool)
    return mascara


__all__ = [
    "NOMBRE_DE_LA_CODIFICACION",
    "VERSION_DE_LA_CODIFICACION",
    "N_MAX",
    "COMPONENTES_POR_SLOT",
    "COMPONENTES_GLOBALES",
    "TAMANO_DE_LA_OBSERVACION",
    "ESCALA_DISTANCIA_M",
    "ESCALA_MINUTOS",
    "ESCALA_DEMANDA_BULTOS",
    "INICIO_DE_LA_JORNADA_MIN",
    "FIN_DE_LA_JORNADA_MIN",
    "ObservacionInvalida",
    "codificar_observacion",
    "mascara_de_acciones",
]
