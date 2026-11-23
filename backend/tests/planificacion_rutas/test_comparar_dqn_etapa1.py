"""Logica de `tools/comparar_dqn_etapa1.py` (sesion 2026-10-19A, Q6): costo en
segundos de la linea de base primaria, secuencias de OR-Tools sobre las dos
matrices, distancia con regreso y estadistica pareada con la lectura de
ARQ-46. Instancias escritas a mano."""
from __future__ import annotations

import math

import numpy as np
import pytest

from hoppit.entorno_simulacion.puerto import Instancia, OrdenDeLaInstancia, VehiculoDeLaFlota, paradas_de_la_instancia
from hoppit.shared.intervalo_por_celda import Coordenada
from tools.comparar_dqn_etapa1 import (
    FACTOR_DE_LA_DIFERENCIA_MINIMA,
    costo_en_segundos,
    distancia_de_la_secuencia,
    estadistica_pareada,
    matrices,
    secuencia_de_or_tools,
)

PUNTOS = {1: (0.0, 0.0), 10: (1000.0, 0.0), 11: (1000.0, 1000.0), 12: (0.0, 1000.0), 13: (2000.0, 500.0)}


def _instancia() -> Instancia:
    def distancia(o: int, d: int) -> float:
        (xo, yo), (xd, yd) = PUNTOS[o], PUNTOS[d]
        return abs(xo - xd) + abs(yo - yd)

    ordenes = tuple(
        OrdenDeLaInstancia(f"orden-{i:03d}", nodo, Coordenada(-34.6, -58.4), 1)
        for i, nodo in enumerate((10, 11, 11, 12, 13))
    )
    return Instancia(1, Coordenada(-34.6, -58.4), ordenes, (VehiculoDeLaFlota("v", 99),),
                     {(o, d): distancia(o, d) for o in PUNTOS for d in PUNTOS if o != d}, "s" * 64)


def test_costo_en_segundos_de_la_linea_de_base_primaria() -> None:
    # 1.900 m a 19 km/h son 6 minutos; dos Ordenes con media de 7 minutos.
    assert costo_en_segundos(1900.0, 2, 7.0, 19.0) == pytest.approx((6.0 + 14.0) * 60.0)
    assert costo_en_segundos(1900.0, 0, 7.0, 19.0) == pytest.approx(360.0)


def test_las_dos_lineas_de_base_y_la_distancia_con_regreso() -> None:
    instancia = _instancia()
    paradas = paradas_de_la_instancia(instancia)
    nodos, metros, segundos = matrices(instancia, paradas, 7.0, 19.0)
    assert nodos == [1, 10, 11, 12, 13]
    assert segundos[0][2] == pytest.approx(costo_en_segundos(2000.0, 2, 7.0, 19.0))
    assert segundos[2][0] == pytest.approx(costo_en_segundos(2000.0, 0, 7.0, 19.0))
    primaria = secuencia_de_or_tools(nodos, segundos, paradas)
    secundaria = secuencia_de_or_tools(nodos, metros, paradas)
    assert sorted(primaria) == sorted(secundaria) == [0, 1, 2, 3]
    # En este cuadrado el recorrido minimo con regreso mide 6.000 m.
    assert distancia_de_la_secuencia(instancia, paradas, secundaria) == pytest.approx(6000.0)
    assert distancia_de_la_secuencia(instancia, paradas, primaria) == pytest.approx(6000.0)


def test_estadistica_pareada_y_lectura_fijada() -> None:
    pytest.importorskip("scipy")
    generador = np.random.default_rng(0)
    simetricas = [(-1.0) ** i * (i % 7) for i in range(50)]
    paridad = estadistica_pareada(simetricas, generador, remuestreos=2000)
    assert paridad.intervalo[0] <= 0.0 <= paridad.intervalo[1]
    assert paridad.lectura == "paridad dentro del intervalo"
    assert paridad.diferencia_minima_detectable == pytest.approx(
        FACTOR_DE_LA_DIFERENCIA_MINIMA * np.std(simetricas, ddof=1) / math.sqrt(50))
    peor = estadistica_pareada([5.0 + 0.1 * i for i in range(50)], generador, remuestreos=2000)
    assert peor.lectura == "el agente es peor" and peor.wilcoxon_p is not None and peor.wilcoxon_p < 0.05
    mejor = estadistica_pareada([-5.0 - 0.1 * i for i in range(50)], generador, remuestreos=2000)
    assert mejor.lectura.startswith("el agente es mejor")
    ceros = estadistica_pareada([0.0] * 50, generador, remuestreos=100)
    assert ceros.wilcoxon_p is None and ceros.lectura == "paridad dentro del intervalo"
