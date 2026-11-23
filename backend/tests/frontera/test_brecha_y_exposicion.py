"""Brecha de optimalidad (D23) y exposicion al riesgo vial (D25) de una Ruta,
operaciones aparte (encargo 2026-11-23A)."""
from __future__ import annotations

import http
import itertools

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import crear_servicio as crear_cartografia
from hoppit.planificacion_rutas.puerto import MAXIMO_EXACTO, brecha_de_optimalidad
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

# Matriz asimetrica a mano: 0 es el Deposito; 1 a 4, las Paradas.
COSTOS = {
    (0, 1): 10, (0, 2): 25, (0, 3): 30, (0, 4): 20,
    (1, 0): 12, (1, 2): 8, (1, 3): 22, (1, 4): 18,
    (2, 0): 24, (2, 1): 9, (2, 3): 6, (2, 4): 15,
    (3, 0): 28, (3, 1): 21, (3, 2): 7, (3, 4): 5,
    (4, 0): 19, (4, 1): 17, (4, 2): 14, (4, 3): 4,
}


def costo(a: int, b: int) -> float:
    return 0.0 if a == b else float(COSTOS[(a, b)])


def test_una_secuencia_peor_que_la_optima_da_la_brecha_calculada_a_mano() -> None:
    peor = [4, 2, 3, 1]
    # A mano: 0->4 20, 4->2 14, 2->3 6, 3->1 21, 1->0 12 = 73.
    costo_peor = 20 + 14 + 6 + 21 + 12
    # Optimo por fuerza bruta entre las 24 permutaciones.
    optimo = min(sum(costo(a, b) for a, b in zip((0, *p, 0), (*p, 0))) for p in itertools.permutations([1, 2, 3, 4]))
    assert optimo == 10 + 8 + 6 + 5 + 19  # 0-1-2-3-4-0 = 48
    b = brecha_de_optimalidad(0, peor, 0, costo)
    assert b.calculada and b.costo_m == costo_peor and b.optimo_m == optimo
    assert b.brecha == pytest.approx((costo_peor - optimo) / optimo) and b.brecha > 0
    assert b.orden_optimo == (1, 2, 3, 4)
    # En el orden optimo la brecha es 0.
    assert brecha_de_optimalidad(0, [1, 2, 3, 4], 0, costo).brecha == 0.0


def test_con_mas_de_12_paradas_no_se_calcula() -> None:
    nodos = list(range(1, MAXIMO_EXACTO + 2))
    b = brecha_de_optimalidad(0, nodos, 0, lambda a, c: 0.0 if a == c else float(abs(a - c)))
    assert not b.calculada and b.brecha is None and b.optimo_m is None
    assert b.motivo is not None and "12" in b.motivo and b.paradas == MAXIMO_EXACTO + 1


def test_la_ruta_del_plan_canonico_informa_su_brecha_y_la_huella_no_cambia() -> None:
    grafo = crear_cartografia(GRAFO_PREDETERMINADO)
    grafo.construir()
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    operador = cliente_autenticado(contexto)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    for r in operador.get("/v1/rutas").json():
        antes = operador.get(f"/v1/rutas/{r['id']}").json()
        cuerpo = operador.get(f"/v1/rutas/{r['id']}/brecha").json()
        assert cuerpo["calculada"] and cuerpo["objetivo"] == "distancia por la red"
        assert cuerpo["brecha"] >= 0.0 and cuerpo["optimo_m"] <= cuerpo["costo_m"] + 1e-6
        # El costo del orden vigente es la distancia publicada de la Ruta.
        assert cuerpo["costo_m"] == pytest.approx(antes["distancia_m"], rel=1e-9)
        assert sorted(cuerpo["orden_optimo"]) == sorted({p["punto"]["nodo"] for p in antes["paradas"]})
        # La Ruta publicada no cambia por consultar la brecha.
        assert operador.get(f"/v1/rutas/{r['id']}").json() == antes
        exposicion = operador.get(f"/v1/rutas/{r['id']}/exposicion-al-riesgo-vial").json()
        assert exposicion["calculada"] is False and exposicion["motivo"]
    assert operador.get("/v1/rutas/ruta-inexistente/brecha").status_code == http.HTTPStatus.NOT_FOUND
