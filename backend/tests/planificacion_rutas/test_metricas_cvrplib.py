"""Logica de tools/metricas_cvrplib.py y tools/cvrplib.py que usan las
metricas contra CVRPLIB (sesion 2026-10-13C, bloque C): verificacion de
factibilidad fuera del motor, costo EUC_2D, brecha y SHA-256 de la solucion.

Las instancias y soluciones son fixtures minimos escritos a mano: cinco
puntos con distancias de triangulos 3-4-5, para que el costo se pueda
verificar a mano. No son datos del problema ni de las metricas.
"""
from __future__ import annotations

import pytest

from hoppit.planificacion_rutas.puerto import ParadaPlaneada, Ruta, SolucionRuteo
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId
from tools.cvrplib import InstanciaCVRP
from tools.metricas_cvrplib import brecha_pct, sha256_de_la_solucion, verificar_factibilidad

# Deposito en 1; clientes 2 a 5. Distancias EUC_2D enteras exactas:
# 1-2 = 5, 2-3 = 5, 3-1 = 10, 1-4 = 4, 4-5 = 5, 5-1 = 3.
COORDENADAS = {1: (0.0, 0.0), 2: (3.0, 4.0), 3: (6.0, 8.0), 4: (0.0, 4.0), 5: (3.0, 0.0)}
DEMANDAS = {1: 0, 2: 4, 3: 5, 4: 6, 5: 3}
CAPACIDAD = 13
# Con capacidad 13, repetir el cliente 2 en la primera Ruta carga 13 y no la
# excede: el caso del cliente repetido falla solo por eso.
VALIDA = [[2, 3], [4, 5]]
COSTO_DE_LA_VALIDA = (5 + 5 + 10) + (4 + 5 + 3)


def instancia(k: int = 2) -> InstanciaCVRP:
    return InstanciaCVRP(
        nombre=f"X-n5-k{k}", capacidad=CAPACIDAD, coordenadas=COORDENADAS, demandas=DEMANDAS, deposito=1, tipo="EUC_2D"
    )


def _condiciones(resultado: dict[str, object]) -> dict[str, object]:
    claves = ("cada_cliente_una_vez", "capacidad_respetada", "k_rutas", "ninguna_ruta_vacia")
    return {clave: resultado[clave] for clave in claves}


def test_acepta_una_solucion_valida_y_recalcula_su_costo() -> None:
    resultado = verificar_factibilidad(instancia(), VALIDA)
    assert _condiciones(resultado) == {
        "cada_cliente_una_vez": True,
        "capacidad_respetada": True,
        "k_rutas": True,
        "ninguna_ruta_vacia": True,
    }
    assert resultado["costo_recalculado"] == COSTO_DE_LA_VALIDA
    assert resultado["cargas"] == [9, 9]


@pytest.mark.parametrize(
    ("rutas", "k", "falla"),
    [
        pytest.param([[2, 3, 2], [4, 5]], 2, "cada_cliente_una_vez", id="cliente_repetido"),
        pytest.param([[2, 3], [4]], 2, "cada_cliente_una_vez", id="cliente_faltante"),
        pytest.param([[2, 3, 4], [5]], 2, "capacidad_respetada", id="carga_mayor_que_la_capacidad"),
        pytest.param([[2, 3], [4], [5]], 2, "k_rutas", id="cantidad_de_rutas_distinta_de_k"),
        pytest.param([[2, 3], [4, 5], []], 3, "ninguna_ruta_vacia", id="ruta_vacia"),
    ],
)
def test_detecta_cada_falla_por_separado(rutas: list[list[int]], k: int, falla: str) -> None:
    condiciones = _condiciones(verificar_factibilidad(instancia(k), rutas))
    assert condiciones[falla] is False
    assert all(valor for clave, valor in condiciones.items() if clave != falla)


@pytest.mark.parametrize(
    ("a", "b", "esperada"),
    [
        pytest.param((0.0, 0.0), (3.0, 4.0), 5, id="exacta"),
        pytest.param((0.0, 0.0), (1.5, 2.0), 3, id="media_redondea_hacia_arriba"),
        pytest.param((0.0, 0.0), (1.0, 1.0), 1, id="redondea_hacia_abajo"),
        pytest.param((0.0, 0.0), (0.0, 2.6), 3, id="redondea_hacia_arriba"),
    ],
)
def test_euc_2d_es_el_entero_mas_cercano_de_tsplib(
    a: tuple[float, float], b: tuple[float, float], esperada: int
) -> None:
    puntos = InstanciaCVRP(
        nombre="X-n2-k1", capacidad=1, coordenadas={1: a, 2: b}, demandas={1: 0, 2: 1}, deposito=1, tipo="EUC_2D"
    )
    assert puntos.distancia(1, 2) == esperada
    assert puntos.distancia(2, 1) == esperada


@pytest.mark.parametrize(
    ("costo", "publicada", "esperada"),
    [(110, 100, 10.0), (100, 100, 0.0), (95, 100, -5.0), (1519, 1153, 100 * 366 / 1153)],
)
def test_brecha_es_relativa_a_la_publicada_en_por_ciento(costo: float, publicada: float, esperada: float) -> None:
    assert brecha_pct(costo, publicada) == pytest.approx(esperada)


def _solucion(nodo_de_la_segunda_parada: int = 3) -> SolucionRuteo:
    rutas = []
    for numero, nodos in enumerate(([2, nodo_de_la_segunda_parada], [4, 5]), start=1):
        ruta_id = RutaId(f"ruta-prueba{numero}")
        rutas.append(
            Ruta(
                id=ruta_id,
                vehiculo_id=VehiculoId(f"V{numero}"),
                paradas=tuple(
                    ParadaPlaneada(
                        ruta_id=ruta_id, secuencia=s, nodo=n, ordenes=(OrdenId(f"o{n}"),), eta_estimado_s=None
                    )
                    for s, n in enumerate(nodos, start=1)
                ),
                distancia_m=float(numero) * 10.5,
                carga=9,
                capacidad_vehiculo=CAPACIDAD,
            )
        )
    return SolucionRuteo(rutas=tuple(rutas))


def test_sha256_de_la_solucion_es_estable() -> None:
    assert sha256_de_la_solucion(_solucion()) == sha256_de_la_solucion(_solucion())


def test_sha256_cambia_si_cambia_un_nodo() -> None:
    assert sha256_de_la_solucion(_solucion(3)) != sha256_de_la_solucion(_solucion(6))

