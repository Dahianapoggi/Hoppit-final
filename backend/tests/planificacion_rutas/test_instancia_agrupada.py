"""La instancia de demostracion ejercita el agrupamiento de Ordenes (RB-04).

El dominio siempre pudo agrupar varias Ordenes en una Parada --relacion 3 del
glosario, ya verificada por test_paradas_compartidas-- pero la instancia con
veinte destinos distintos no lo ejercitaba: el panel de Parada de la interfaz
habria mostrado siempre una sola Orden y la capacidad quedaria sin evidencia.

Estas pruebas fijan esa propiedad sobre la instancia real, para que no se
pierda si alguien vuelve a tocar el generador del escenario.
"""
from __future__ import annotations

import collections
import logging

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.gestion_ordenes.puerto import Orden
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.escenario_demostracion import (
    CANTIDAD_ORDENES,
    CANTIDAD_PARADAS,
    CANTIDAD_VEHICULOS,
    ORDENES_AGRUPADAS,
    capacidad_por_vehiculo,
    construir_ordenes,
)

_LOG = logging.getLogger(__name__)

# Lo que CLAUDE.md fija para la instancia de demostracion.
ORDENES_DOCUMENTADAS = 20
VEHICULOS_DOCUMENTADOS = 3


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture(scope="module")
def ordenes(grafo: GrafoVialAMBA) -> tuple[Orden, ...]:
    return construir_ordenes(grafo.nodos_de_mayor_componente_fuerte())


def test_se_mantienen_los_parametros_de_la_instancia(
    ordenes: tuple[Orden, ...],
) -> None:
    """N=20 Ordenes y K=3 Vehiculos siguen siendo los de CLAUDE.md.

    Lo que cambio es sobre cuantos destinos distintos caen esas 20 Ordenes,
    no cuantas son.
    """
    assert CANTIDAD_ORDENES == ORDENES_DOCUMENTADAS
    assert CANTIDAD_VEHICULOS == VEHICULOS_DOCUMENTADOS
    assert len(ordenes) == ORDENES_DOCUMENTADAS
    assert len(capacidad_por_vehiculo(ordenes)) == VEHICULOS_DOCUMENTADOS


def test_al_menos_dos_paradas_agrupan_mas_de_una_orden(
    ordenes: tuple[Orden, ...],
) -> None:
    """La condicion que RB-04 pide: mas de una Parada con mas de una Orden."""
    por_destino = collections.Counter(orden.destino for orden in ordenes)
    agrupadas = {nodo: n for nodo, n in por_destino.items() if n > 1}
    _LOG.info(
        "Agrupamiento | ordenes=%d destinos_distintos=%d paradas_agrupadas=%s",
        len(ordenes),
        len(por_destino),
        agrupadas,
    )

    assert len(agrupadas) >= 2
    assert len(por_destino) == CANTIDAD_PARADAS


def test_las_ordenes_agrupadas_se_reparten_en_paradas_distintas(
    ordenes: tuple[Orden, ...],
) -> None:
    """Dos Paradas con dos Ordenes, no una con tres.

    Concentrarlas en una sola Parada cumpliria el conteo de Ordenes pero
    dejaria el caso visible una sola vez en la pantalla.
    """
    por_destino = collections.Counter(orden.destino for orden in ordenes)
    agrupadas = {nodo: n for nodo, n in por_destino.items() if n > 1}
    assert len(agrupadas) == ORDENES_AGRUPADAS
    assert all(cantidad == 2 for cantidad in agrupadas.values())


def test_todos_los_destinos_son_nodos_reales_de_la_componente_mayor(
    grafo: GrafoVialAMBA, ordenes: tuple[Orden, ...]
) -> None:
    """Ningun destino inventado, y ninguno fuera de la componente fuerte."""
    utilizables = set(grafo.nodos_de_mayor_componente_fuerte())
    assert {orden.destino for orden in ordenes} <= utilizables


def test_la_instancia_es_reproducible(grafo: GrafoVialAMBA) -> None:
    """Misma semilla, misma instancia: destinos y demandas (NFR-07).

    Incluye el sorteo de que Paradas reciben la segunda Orden, que sale de la
    misma secuencia: si se sorteara aparte, dos corridas podrian agrupar en
    Paradas distintas con la misma semilla.
    """
    nodos = grafo.nodos_de_mayor_componente_fuerte()
    primera = construir_ordenes(nodos, semilla=SEMILLA_DE_INSTANCIA)
    segunda = construir_ordenes(nodos, semilla=SEMILLA_DE_INSTANCIA)

    assert [(o.id, o.destino, o.demanda) for o in primera] == [
        (o.id, o.destino, o.demanda) for o in segunda
    ]


def test_pedir_mas_agrupadas_que_destinos_es_un_error() -> None:
    """Cada destino puede recibir a lo sumo una segunda Orden."""
    with pytest.raises(ValueError):
        construir_ordenes(range(100), cantidad=10, agrupadas=6)
