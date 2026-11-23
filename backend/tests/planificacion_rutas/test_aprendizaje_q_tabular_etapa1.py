"""Pruebas de `tools/aprendizaje_q_tabular_etapa1.py` (encargo 2026-11-18A,
bloque 1): la cuenta de estados, el goloso con empates y que en una instancia
chica el aprendizaje Q tabular llega al optimo exacto de Held-Karp."""
from __future__ import annotations

import pathlib

import numpy as np
import pytest

pytest.importorskip("torch")

from hoppit.entorno_simulacion.puerto import Instancia, OrdenDeLaInstancia, VehiculoDeLaFlota  # noqa: E402
from hoppit.shared.intervalo_por_celda import Coordenada, leer_tabla_de_intervalos  # noqa: E402
from tools import aprendizaje_q_tabular_etapa1 as q_tabular  # noqa: E402
from tools import diagnostico_dqn_etapa1 as diagnostico  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402

TABLA = pathlib.Path(__file__).resolve().parents[2] / original.TABLA_VIGENTE


def instancia(semilla: int, paradas: int) -> Instancia:
    azar = np.random.default_rng(semilla)
    puntos = azar.uniform(0, 4_000, size=(paradas + 1, 2))
    nodos = list(range(paradas + 1))
    distancias = {(a, b): float(np.linalg.norm(puntos[a] - puntos[b]) * 1.3) for a in nodos for b in nodos if a != b}
    ordenes = tuple(OrdenDeLaInstancia(f"o-{i:02d}", i, Coordenada(-34.6, -58.4), 1) for i in range(1, paradas + 1))
    return Instancia(0, Coordenada(-34.6, -58.4), ordenes, (VehiculoDeLaFlota("v", 100),), distancias, f"{semilla:064d}")


def test_estados_alcanzables() -> None:
    assert q_tabular.estados_alcanzables(1) == 2
    assert q_tabular.estados_alcanzables(3) == 1 + 3 * 4
    assert q_tabular.estados_alcanzables(10) == 5_121


def test_goloso_respeta_la_mascara_y_desempata_al_primero() -> None:
    q = np.array([5.0, 1.0, 1.0, 9.0])
    assert q_tabular.accion_golosa(q, np.array([False, True, True, False])) == 1
    assert q_tabular.accion_golosa(np.zeros(4), np.array([False, False, True, True])) == 2


def test_control_exacto_coincide_con_held_karp() -> None:
    from hoppit.entorno_simulacion.puerto import SemillaDeEpisodio

    tabla = leer_tabla_de_intervalos(TABLA)
    chica = instancia(5, 5)
    entorno = diagnostico.EntornoConParadaFija(tabla)
    optimo, secuencia = diagnostico.recorrido_optimo(chica, diagnostico.medianas_por_franja(tabla), entorno.reloj_min)
    control = q_tabular.control_exacto(entorno, chica, SemillaDeEpisodio(1, 0), secuencia)
    assert control["duracion_de_la_secuencia_optima_en_el_entorno_min"] == pytest.approx(optimo)
    assert control["suma_de_recompensas_de_la_secuencia_optima"] == pytest.approx(-optimo)
    assert control["valor_optimo_por_induccion_min"] == pytest.approx(optimo)
    assert control["pares_evaluados"] == 5 * 4 * 2 ** 3 + 5
    assert control["mascara_igual_a_las_paradas_pendientes"]


def test_llega_al_optimo_en_una_instancia_chica() -> None:
    tabla = leer_tabla_de_intervalos(TABLA)
    chica = instancia(3, 5)
    entorno = diagnostico.EntornoConParadaFija(tabla)
    optimo, secuencia = diagnostico.recorrido_optimo(chica, diagnostico.medianas_por_franja(tabla), entorno.reloj_min)
    resultado = q_tabular.aprender(entorno, chica, presupuesto=5_000, alfa=1.0, semilla=1, cada=500)
    assert resultado.goloso_min == pytest.approx(optimo)
    assert resultado.secuencia_golosa == secuencia or resultado.goloso_min == pytest.approx(optimo)
    assert len(resultado.tabla) <= q_tabular.estados_alcanzables(5)
    assert len(resultado.medianas_vistas) == 1
    assert [f["paso"] for f in resultado.curva][:3] == [0, 500, 1_000]
