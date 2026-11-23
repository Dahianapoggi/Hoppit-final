"""Verifica que la semilla de reproducibilidad tenga una sola fuente.

No prueba el valor -- cambiarlo es una decision de proyecto -- sino que los
tres consumidores tomen el mismo, que es lo que se rompio antes al declararla
en cada tool por separado.
"""
from __future__ import annotations

import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA  # noqa: E402
from tools import escenario_demostracion, medir_matriz_costos, medir_planificacion  # noqa: E402


def test_la_semilla_es_un_entero() -> None:
    assert isinstance(SEMILLA_DE_INSTANCIA, int)


def test_los_tres_tools_usan_la_semilla_compartida() -> None:
    """Si alguno la redeclarara, sus mediciones dejarian de ser comparables
    con las de los otros dos sin que nada lo detecte."""
    for modulo in (escenario_demostracion, medir_matriz_costos, medir_planificacion):
        assert modulo.SEMILLA_DE_INSTANCIA is SEMILLA_DE_INSTANCIA


def test_ningun_tool_declara_su_propia_semilla() -> None:
    """La constante vieja no puede sobrevivir en ningun tool."""
    for modulo in (escenario_demostracion, medir_matriz_costos, medir_planificacion):
        assert not hasattr(modulo, "SEMILLA_PREDETERMINADA")


def test_el_escenario_es_reproducible_con_la_semilla_compartida() -> None:
    nodos = list(range(1000, 1100))
    primero = escenario_demostracion.construir_ordenes(nodos)
    segundo = escenario_demostracion.construir_ordenes(nodos)
    assert [(o.id, o.demanda, o.destino) for o in primero] == [
        (o.id, o.demanda, o.destino) for o in segundo
    ]
