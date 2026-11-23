"""Codificacion de la observacion de la etapa 2 del agente (encargo
2026-11-19A, bloque 4; propuesta del informe 2026-11-18A, bloque 9, punto 2;
informe 2026-11-17B, C.3). Modulo nuevo: no toca el de la etapa 1
(`codificacion_observacion.py`), cuyos pesos versionados siguen cargando.

Decision que observa: llega una Orden (o se vuelve a ofrecer una que espera)
y el agente elige un Vehiculo factible o esperar (B-282, punto 1); el lugar
en el plan lo pone la insercion mas barata en ese Vehiculo. Acciones:
`0 .. K_VEHICULOS - 1` (el Vehiculo de ese indice) y `ACCION_ESPERAR` = K.

Es una funcion pura, compartida por el plano offline (el entorno del
simulador dinamico y el entrenamiento) y por la inferencia en el producto
(el modo sombra de la capa dinamica): quien la llama calcula los rasgos en
minutos, bultos y cantidades; aca solo se escalan y se ordenan.

Vector de `TAMANO_DE_LA_OBSERVACION` = 46 componentes en `float32`:

- por cada Vehiculo (K = 3 ranuras; una ranura sin Vehiculo va en ceros y
  enmascarada), 13 componentes: estado en el Deposito, cargando, a una
  Parada y al Deposito (4 indicadores); factible (1 si la Orden entra en su
  capacidad comprometida); costo de la insercion mas barata en ese Vehiculo
  (min / 60; 0 si no es factible); lugar de esa insercion sobre el largo del
  plan (0 a 1; 0 con el plan vacio); si obliga a una carga nueva (0/1);
  minutos desde su nodo de partida hasta la Orden (/ 60); capacidad libre
  comprometida (/ capacidad); Ordenes en el plan y Ordenes por cargar
  (/ `ESCALA_DE_ORDENES`); minutos de viaje que le quedan al recorrido
  previsto del plan (/ 60);
- de la Orden, 4: minutos al Deposito (ida y vuelta / 2, / 60); demanda
  (/ capacidad); si es estatica (0/1); hora de llegada ((min - 480) / 840);
- globales, 3: reloj ((min - 480) / 840); Ordenes en la lista de espera y
  Ordenes llegadas en la ultima hora (/ `ESCALA_DE_ORDENES`).

Sin la opcion de teoria de colas (rho estimado en linea): si entra al agente
lo decide el usuario (B-287, punto 2).

Escalas fijas [supuesto de diseno, encargo 2026-11-19A]: 60 min (una hora);
la capacidad del Vehiculo; `ESCALA_DE_ORDENES` = 12,4, las Ordenes que
llenan un Vehiculo con la demanda media (20 / 1,615, informe 18A, bloque 2);
jornada de 8 a 22 (840 min). Cambiar contenido, orden o escala exige subir
`VERSION_DE_LA_CODIFICACION`.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Final

import numpy as np

NOMBRE_DE_LA_CODIFICACION: Final[str] = "observacion_etapa_2"
VERSION_DE_LA_CODIFICACION: Final[int] = 1

K_VEHICULOS: Final[int] = 3
ACCION_ESPERAR: Final[int] = K_VEHICULOS
CANTIDAD_DE_ACCIONES: Final[int] = K_VEHICULOS + 1

ESTADOS_DEL_VEHICULO: Final[tuple[str, ...]] = ("en_deposito", "cargando", "a_parada", "al_deposito")
RASGOS_POR_VEHICULO: Final[int] = 13
RASGOS_DE_LA_ORDEN: Final[int] = 4
RASGOS_GLOBALES: Final[int] = 3
TAMANO_DE_LA_OBSERVACION: Final[int] = K_VEHICULOS * RASGOS_POR_VEHICULO + RASGOS_DE_LA_ORDEN + RASGOS_GLOBALES

ESCALA_MINUTOS: Final[float] = 60.0
ESCALA_DE_ORDENES: Final[float] = 12.4
INICIO_DE_LA_JORNADA_MIN: Final[float] = 480.0
LARGO_DE_LA_JORNADA_MIN: Final[float] = 840.0


@dataclasses.dataclass(frozen=True)
class RasgosDelVehiculo:
    estado: str
    factible: bool
    costo_de_insercion_min: float
    lugar_de_insercion: int
    largo_del_plan: int
    obliga_carga_nueva: bool
    minutos_hasta_la_orden: float
    carga_comprometida: int
    ordenes_por_cargar: int
    minutos_restantes_del_plan: float


@dataclasses.dataclass(frozen=True)
class RasgosDeLaOrden:
    minutos_al_deposito_ida_y_vuelta: float
    demanda: int
    estatica: bool
    llegada_min: float


@dataclasses.dataclass(frozen=True)
class RasgosGlobales:
    reloj_min: float
    en_espera: int
    llegadas_en_la_ultima_hora: int


def codificar_observacion(vehiculos: Sequence[RasgosDelVehiculo], orden: RasgosDeLaOrden, globales: RasgosGlobales,
                          capacidad: int) -> np.ndarray:
    if len(vehiculos) > K_VEHICULOS:
        raise ValueError(f"a lo sumo {K_VEHICULOS} Vehiculos")
    if capacidad <= 0:
        raise ValueError("capacidad no positiva")
    if any(v.estado not in ESTADOS_DEL_VEHICULO for v in vehiculos):
        raise ValueError("estado de Vehiculo desconocido")
    x = np.zeros(TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
    for k, v in enumerate(vehiculos):
        b = k * RASGOS_POR_VEHICULO
        x[b + ESTADOS_DEL_VEHICULO.index(v.estado)] = 1.0
        x[b + 4] = 1.0 if v.factible else 0.0
        if v.factible:
            x[b + 5] = v.costo_de_insercion_min / ESCALA_MINUTOS
            x[b + 6] = v.lugar_de_insercion / v.largo_del_plan if v.largo_del_plan > 0 else 0.0
            x[b + 7] = 1.0 if v.obliga_carga_nueva else 0.0
        x[b + 8] = v.minutos_hasta_la_orden / ESCALA_MINUTOS
        x[b + 9] = (capacidad - v.carga_comprometida) / capacidad
        x[b + 10] = v.largo_del_plan / ESCALA_DE_ORDENES
        x[b + 11] = v.ordenes_por_cargar / ESCALA_DE_ORDENES
        x[b + 12] = v.minutos_restantes_del_plan / ESCALA_MINUTOS
    o = K_VEHICULOS * RASGOS_POR_VEHICULO
    x[o] = orden.minutos_al_deposito_ida_y_vuelta / 2.0 / ESCALA_MINUTOS
    x[o + 1] = orden.demanda / capacidad
    x[o + 2] = 1.0 if orden.estatica else 0.0
    x[o + 3] = (orden.llegada_min - INICIO_DE_LA_JORNADA_MIN) / LARGO_DE_LA_JORNADA_MIN
    g = o + RASGOS_DE_LA_ORDEN
    x[g] = (globales.reloj_min - INICIO_DE_LA_JORNADA_MIN) / LARGO_DE_LA_JORNADA_MIN
    x[g + 1] = globales.en_espera / ESCALA_DE_ORDENES
    x[g + 2] = globales.llegadas_en_la_ultima_hora / ESCALA_DE_ORDENES
    return x


def mascara_de_acciones(vehiculos: Sequence[RasgosDelVehiculo]) -> np.ndarray:
    """Los Vehiculos factibles y, siempre, esperar."""
    mascara = np.zeros(CANTIDAD_DE_ACCIONES, dtype=bool)
    for k, v in enumerate(vehiculos[:K_VEHICULOS]):
        mascara[k] = v.factible
    mascara[ACCION_ESPERAR] = True
    return mascara
