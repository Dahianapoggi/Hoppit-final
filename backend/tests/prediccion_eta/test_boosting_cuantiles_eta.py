"""Logica de `tools/boosting_cuantiles_eta.py` (sesion 2026-10-19A, bloque
BX): indice de la tabla, tabulacion con piso, reordenamiento y respaldo,
regla de DAT-35, bootstrap por conglomerados y sorteo en grillas. Sin LaDe ni
scikit-learn: matrices escritas a mano."""
from __future__ import annotations

import numpy as np
import pytest

from tools.boosting_cuantiles_eta import (
    ANCHO_MAXIMO_DE_LA_BANDA_MIN,
    GRILLA_DE_CUANTILES,
    N_MINIMO_POR_COMBINACION,
    FilaDeRespaldo,
    combinaciones,
    condiciones_del_producto,
    condiciones_por_intervalo,
    conteo_por_combinacion,
    indice_de_combinacion,
    llevar_al_piso,
    mediana_ponderada,
    metricas_por_intervalo,
    pesos_de_replica,
    proporcion_en_el_piso_por_combinacion,
    reordenar_cuantiles,
    sortear_en_grillas,
    tabla_como_csv,
    tabular,
)

Q = len(GRILLA_DE_CUANTILES)
RESPALDO = {f: FilaDeRespaldo(tuple(float(5 + i) for i in range(Q)), 0.1) for f in range(8, 23)}


def test_el_indice_recorre_las_combinaciones_en_orden_y_marca_las_de_afuera() -> None:
    tabla = combinaciones()
    assert len(tabla) == 15 * 20 * 20
    indices = indice_de_combinacion(tabla[:, 0], tabla[:, 1], tabla[:, 2])
    assert (indices == np.arange(len(tabla))).all()
    afuera = indice_de_combinacion(np.array([7, 23, 8, 8, 8]), np.array([1, 1, 0, 21, 1]), np.array([1, 1, 1, 1, 0]))
    assert (afuera == -1).all()


def test_conteo_y_proporcion_en_el_piso_por_combinacion() -> None:
    f, p, r = np.array([8, 8, 8, 9, 30]), np.array([2, 2, 2, 3, 2]), np.array([4, 4, 4, 1, 1])
    etiqueta = np.array([5.0, 7.0, 5.0, 12.0, 5.0])
    n = conteo_por_combinacion(f, p, r)
    fila = int(indice_de_combinacion(np.array([8]), np.array([2]), np.array([4]))[0])
    assert n[fila] == 3 and n.sum() == 4
    proporcion = proporcion_en_el_piso_por_combinacion(f, p, r, etiqueta)
    assert proporcion[fila] == pytest.approx(2 / 3)
    assert np.isnan(proporcion[0])


def test_piso_y_reordenamiento_se_cuentan() -> None:
    matriz = np.array([[4.0, 6.0, 5.5], [5.0, 6.0, 7.0]])
    en_el_piso, llevados = llevar_al_piso(matriz)
    assert llevados == 1 and en_el_piso[0, 0] == 5.0
    ordenada, cruzadas = reordenar_cuantiles(en_el_piso)
    assert cruzadas.tolist() == [True, False]
    assert ordenada[0].tolist() == [5.0, 5.5, 6.0]


def test_tabular_usa_el_respaldo_con_menos_de_200_filas_y_no_lo_cuenta_como_cruzado() -> None:
    tabla = combinaciones()
    predicciones = np.tile(np.linspace(6.0, 40.0, Q), (len(tabla), 1))
    predicciones[0, [3, 4]] = predicciones[0, [4, 3]]  # cruzada, con n suficiente
    predicciones[1, [3, 4]] = predicciones[1, [4, 3]]  # cruzada, pero va a respaldo
    n = np.full(len(tabla), N_MINIMO_POR_COMBINACION)
    n[1] = N_MINIMO_POR_COMBINACION - 1
    proporcion = np.full(len(tabla), 0.07)
    d = tabular(predicciones, n, proporcion, RESPALDO)
    assert d.respaldo.tolist().count(True) == 1 and d.respaldo[1]
    assert d.cruzadas[0] and not d.cruzadas[1]
    assert tuple(d.cuantiles[1]) == RESPALDO[8].cuantiles
    assert d.proporcion_en_el_piso[1] == 0.1 and d.proporcion_en_el_piso[0] == 0.07
    assert (np.diff(d.cuantiles, axis=1) >= 0).all()
    assert d.fraccion_en_respaldo() == pytest.approx(1 / len(tabla))
    assert d.factibles().sum() == 15 * 210
    celda = d.celda(0)
    assert celda.probabilidades == GRILLA_DE_CUANTILES and celda.franja == 8
    texto = tabla_como_csv(d).decode("utf-8")
    assert texto.endswith("\n") and "\r" not in texto
    assert len(texto.splitlines()) == len(tabla) + 1


def test_condiciones_por_intervalo_con_sus_margenes() -> None:
    referencia = {"mae_min": 7.0, "mediana_error_absoluto_min": 2.0,
                  "porcentaje_dentro_de_5_min": 70.0, "porcentaje_dentro_de_10_min": 89.0}
    justo = {"mae_min": 7.0, "mediana_error_absoluto_min": 2.5,
             "porcentaje_dentro_de_5_min": 69.0, "porcentaje_dentro_de_10_min": 88.0}
    assert all(condiciones_por_intervalo(referencia, justo).values())
    peor = {**justo, "mae_min": 7.0001, "mediana_error_absoluto_min": 2.51}
    condiciones = condiciones_por_intervalo(referencia, peor)
    assert condiciones["mae"] is False and condiciones["mediana_error_absoluto"] is False
    assert condiciones["dentro_de_5_min"] and condiciones["dentro_de_10_min"]


def _grupo(mae: float, mediana: float, cobertura: float) -> dict[str, float]:
    return {"mae_min": mae, "mediana_del_error_min": mediana, "cobertura": cobertura}


def test_condiciones_del_producto() -> None:
    vigente = {"1": _grupo(8.0, -0.2, 0.64), "2 a 5": _grupo(20.0, -0.5, 0.66), "6 a 10": _grupo(30.0, 2.0, 0.62)}
    d = {"1": _grupo(8.0, 0.9, 0.95), "2 a 5": _grupo(19.0, -1.0, 0.70), "6 a 10": _grupo(29.0, -2.0, 0.623)}
    condiciones = condiciones_del_producto(vigente, d, [100.0, ANCHO_MAXIMO_DE_LA_BANDA_MIN])
    # p10 a p90 de 0,95 esta a 0,15 de 0,80, mas cerca que 0,641 (0,159).
    assert all(condiciones.values()), condiciones
    d["1"] = _grupo(8.0, 1.01, 0.961)
    condiciones = condiciones_del_producto(vigente, d, [121.0])
    assert not condiciones["mediana_con_signo_1"]
    assert not condiciones["cobertura_1"]
    assert not condiciones["ancho_de_la_banda"]


def test_mediana_ponderada_y_pesos_de_replica() -> None:
    valores = np.array([1.0, 2.0, 3.0, 10.0])
    assert mediana_ponderada(valores, np.ones(4)) == 2.0
    assert mediana_ponderada(valores, np.array([0.0, 0.0, 1.0, 5.0])) == 10.0
    conglomerado = np.array([0, 0, 1, 2])
    pesos = pesos_de_replica(conglomerado, 3, np.random.default_rng(1))
    assert pesos[0] == pesos[1] and pesos.sum() >= 0
    multiplicidades = [pesos_de_replica(np.arange(3), 3, np.random.default_rng(s)).sum() for s in range(5)]
    assert multiplicidades == [3.0] * 5


def test_metricas_y_sorteo_en_grillas() -> None:
    metricas = metricas_por_intervalo(np.array([10.0, 20.0, 30.0]), np.array([10.0, 26.0, 41.0]))
    assert metricas["mae_min"] == pytest.approx(17 / 3)
    assert metricas["porcentaje_dentro_de_5_min"] == pytest.approx(100 / 3)
    assert metricas["porcentaje_dentro_de_10_min"] == pytest.approx(200 / 3)
    grillas = [(np.array([0.0, 1.0]), np.array([5.0, 15.0])), (np.array([0.0, 1.0]), np.array([0.0, 100.0]))]
    valores = sortear_en_grillas(np.array([[0, 1], [1, 0]]), np.array([[0.5, 0.5], [0.1, 1.0]]), grillas)
    assert valores.tolist() == [[10.0, 50.0], [10.0, 15.0]]
