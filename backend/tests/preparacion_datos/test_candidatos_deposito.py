"""Seleccion exacta de maxima dispersion de tools/candidatos_deposito.py
(sesion 2026-10-13C, bloque F): el conjunto de k candidatos que maximiza la
distancia minima entre pares, contra fuerza bruta en matrices chicas, y el
desempate por los indices menores.

Las matrices son fixtures minimos escritos a mano: puntos sobre una recta y
sobre un cuadrado, con la distancia entre ellos. No son candidatos reales
ni datos de las metricas.
"""
from __future__ import annotations

import itertools

import numpy as np
import pytest

from tools.candidatos_deposito import conjunto_de_maxima_dispersion


def _sobre_una_recta(posiciones: list[float]) -> np.ndarray:
    return np.array([[abs(a - b) for b in posiciones] for a in posiciones], dtype=float)


def _minima(distancias: np.ndarray, conjunto: tuple[int, ...]) -> float:
    return min(float(distancias[a][b]) for a, b in itertools.combinations(conjunto, 2))


def _fuerza_bruta(distancias: np.ndarray, k: int) -> tuple[float, list[int]]:
    """El mayor minimo entre pares y, entre los que lo alcanzan, el conjunto
    de indices menores en orden lexicografico."""
    conjuntos = list(itertools.combinations(range(len(distancias)), k))
    mejor = max(_minima(distancias, c) for c in conjuntos)
    return mejor, list(min(c for c in conjuntos if _minima(distancias, c) == mejor))


MATRICES = {
    "recta_sin_empates": _sobre_una_recta([0, 1, 3, 7, 8, 15]),
    "recta_equiespaciada": _sobre_una_recta([0, 1, 2, 3, 4, 5, 6]),
    "cuadrado_con_centro": np.array(
        [
            [0, 2, 2.828, 2, 1.414],
            [2, 0, 2, 2.828, 1.414],
            [2.828, 2, 0, 2, 1.414],
            [2, 2.828, 2, 0, 1.414],
            [1.414, 1.414, 1.414, 1.414, 0],
        ],
        dtype=float,
    ),
}


@pytest.mark.parametrize("nombre", sorted(MATRICES))
@pytest.mark.parametrize("k", [2, 3, 4])
def test_exacta_coincide_con_la_fuerza_bruta(nombre: str, k: int) -> None:
    distancias = MATRICES[nombre]
    elegidos, metodo = conjunto_de_maxima_dispersion(distancias, k)
    mejor, esperado = _fuerza_bruta(distancias, k)
    assert metodo == "exacta"
    assert _minima(distancias, tuple(elegidos)) == mejor
    assert elegidos == esperado


def test_en_la_recta_equiespaciada_elige_los_extremos_y_el_medio() -> None:
    elegidos, _ = conjunto_de_maxima_dispersion(MATRICES["recta_equiespaciada"], 3)
    assert elegidos == [0, 3, 6]


def test_en_el_cuadrado_no_elige_el_centro() -> None:
    elegidos, _ = conjunto_de_maxima_dispersion(MATRICES["cuadrado_con_centro"], 4)
    assert elegidos == [0, 1, 2, 3]
