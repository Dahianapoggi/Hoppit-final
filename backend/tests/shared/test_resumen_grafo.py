"""Verifica que ResumenGrafo sea una sola definicion, compartida por los
dos extremos del artefacto de grafo."""
from __future__ import annotations

from hoppit.cartografia.puerto import ResumenGrafo as ResumenDeCartografia
from hoppit.preparacion_datos.puerto import ResumenGrafo as ResumenDeExtraccion
from hoppit.shared.resumen_grafo import ResumenGrafo

NODOS = 5163
NODOS_EN_MAYOR_COMPONENTE_FUERTE = 5145


def test_ambos_extremos_usan_la_misma_definicion() -> None:
    """Si esto falla, alguien volvio a declarar el tipo por su cuenta y las
    medidas de extraccion y de carga dejaron de ser comparables."""
    assert ResumenDeExtraccion is ResumenGrafo
    assert ResumenDeCartografia is ResumenGrafo


def test_fraccion_en_mayor_componente_fuerte() -> None:
    resumen = ResumenGrafo(
        nodos=NODOS,
        aristas=12036,
        componentes_debiles=1,
        componentes_fuertes=19,
        nodos_en_mayor_componente_fuerte=NODOS_EN_MAYOR_COMPONENTE_FUERTE,
    )
    assert resumen.fraccion_en_mayor_componente_fuerte == (
        NODOS_EN_MAYOR_COMPONENTE_FUERTE / NODOS
    )


def test_grafo_vacio_no_divide_por_cero() -> None:
    vacio = ResumenGrafo(
        nodos=0,
        aristas=0,
        componentes_debiles=0,
        componentes_fuertes=0,
        nodos_en_mayor_componente_fuerte=0,
    )
    assert vacio.fraccion_en_mayor_componente_fuerte == 0.0
