"""Rotulo de un punto del grafo vial por cruce de calles (RB-02).

Se verifica contra el artefacto real, no contra un grafo sintetico: lo que
esta en discusion es si los nombres que OSM efectivamente trae alcanzan para
nombrar una esquina, y eso solo se puede afirmar sobre el grafo de verdad.
Los tres casos del contrato --cruce, via unica y degradacion a coordenadas--
se ejercitan sobre nodos reales elegidos de forma determinista.
"""
from __future__ import annotations

import logging

import networkx as nx
import osmnx as ox
import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia._grafo import ATRIBUTO_NOMBRE_VIA, seleccionar_artefacto
from hoppit.cartografia.puerto import GrafoVialAMBA, OrigenRotulo, crear_servicio
from hoppit.shared.errores import EntidadNoEncontrada

_LOG = logging.getLogger(__name__)

# Nodos del artefacto de San Isidro, uno por cada caso del contrato. Fijos y
# no derivados en la prueba: si el artefacto cambia de version y alguno deja
# de comportarse asi, la prueba tiene que fallar y no adaptarse en silencio.
NODO_CON_CRUCE = 29375382
CRUCE_ESPERADO = "Avenida Santa Fe y Paraná"
CALLES_ESPERADAS = ("Avenida Santa Fe", "Paraná")

NODO_CON_VIA_UNICA = 346455476
VIA_UNICA_ESPERADA = "Avenida Fondo de la Legua"

# Menor identificador entre los nodos sin ningun arco incidente con nombre.
NODO_SIN_NOMBRE = 32727667
COORDENADA_ESPERADA = "-34.496368, -58.556333"

NODO_INEXISTENTE = -1


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_servicio(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture(scope="module")
def grafo_crudo() -> nx.MultiDiGraph:
    """El artefacto leido por fuera del servicio, para elegir casos."""
    return ox.load_graphml(seleccionar_artefacto(GRAFO_PREDETERMINADO))


def test_un_cruce_real_se_nombra_por_sus_dos_calles(grafo: GrafoVialAMBA) -> None:
    rotulo = grafo.rotulo(NODO_CON_CRUCE)
    _LOG.info("Cruce | nodo=%d rotulo=%s", NODO_CON_CRUCE, rotulo.texto)

    assert rotulo.origen is OrigenRotulo.CRUCE_DE_CALLES
    assert rotulo.calles == CALLES_ESPERADAS
    assert rotulo.texto == CRUCE_ESPERADO
    assert rotulo.derivado_del_grafo


def test_un_nodo_de_una_sola_calle_se_nombra_por_esa_calle(grafo: GrafoVialAMBA) -> None:
    """Una sola calle con nombre no es una degradacion.

    El punto sigue teniendo un nombre real derivado del grafo; lo que no
    tiene es una segunda calle que lo cruce. El contrato lo distingue de la
    degradacion para que la interfaz no los muestre igual.
    """
    rotulo = grafo.rotulo(NODO_CON_VIA_UNICA)
    _LOG.info("Via unica | nodo=%d rotulo=%s", NODO_CON_VIA_UNICA, rotulo.texto)

    assert rotulo.origen is OrigenRotulo.VIA_UNICA
    assert rotulo.texto == VIA_UNICA_ESPERADA
    assert len(rotulo.calles) == 1
    assert rotulo.derivado_del_grafo


def test_sin_nombre_en_osm_degrada_a_coordenadas_y_lo_declara(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """El caso que RB-02 declara como riesgo, sobre un nodo donde ocurre.

    Se comprueba primero, contra el artefacto crudo, que efectivamente
    ninguno de sus arcos incidentes tiene nombre: sin eso la prueba estaria
    afirmando la degradacion sin haber verificado su causa.
    """
    incidentes = list(grafo_crudo.in_edges(NODO_SIN_NOMBRE, data=True)) + list(
        grafo_crudo.out_edges(NODO_SIN_NOMBRE, data=True)
    )
    assert incidentes, "el nodo elegido tiene que tener arcos incidentes"
    assert all(ATRIBUTO_NOMBRE_VIA not in datos for _, _, datos in incidentes)

    rotulo = grafo.rotulo(NODO_SIN_NOMBRE)
    _LOG.info(
        "Degradacion | nodo=%d arcos_incidentes=%d rotulo=%s",
        NODO_SIN_NOMBRE,
        len(incidentes),
        rotulo.texto,
    )

    assert rotulo.origen is OrigenRotulo.COORDENADAS
    assert rotulo.calles == ()
    assert rotulo.texto == COORDENADA_ESPERADA
    assert not rotulo.derivado_del_grafo


def test_la_coordenada_del_rotulo_es_la_del_nodo(grafo: GrafoVialAMBA) -> None:
    """El rotulo siempre lleva la ubicacion, tenga nombre o no.

    Una interfaz que dibuje el punto no deberia tener que pedir la
    coordenada por separado segun el caso que le haya tocado.
    """
    for nodo in (NODO_CON_CRUCE, NODO_CON_VIA_UNICA, NODO_SIN_NOMBRE):
        rotulo = grafo.rotulo(nodo)
        ubicacion = grafo.coordenadas(nodo)
        assert (rotulo.lat, rotulo.lon) == (ubicacion.lat, ubicacion.lon)


def test_el_rotulo_es_deterministico(grafo: GrafoVialAMBA) -> None:
    """Mismo nodo, mismo rotulo: las calles se ordenan, no se enumeran.

    Sin el orden explicito, el texto dependeria del orden en que NetworkX
    recorre los arcos incidentes.
    """
    assert grafo.rotulo(NODO_CON_CRUCE) == grafo.rotulo(NODO_CON_CRUCE)


def test_el_rotulo_de_un_nodo_inexistente_falla(grafo: GrafoVialAMBA) -> None:
    """No hay rotulo por defecto: un nodo que no esta no se nombra."""
    with pytest.raises(EntidadNoEncontrada):
        grafo.rotulo(NODO_INEXISTENTE)
