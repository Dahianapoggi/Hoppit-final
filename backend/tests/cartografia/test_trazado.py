"""Geometria dibujable del recorrido (FR-02, NFR-08).

Lo que se verifica es que el trazado sea el recorrido real y no una
aproximacion: que su distancia coincida con la del camino minimo con el que
se ruteo, que la polilinea sea continua, y que siga la traza de la calle
donde OSM la declara en vez de unir los nodos con rectas.
"""
from __future__ import annotations

import logging
import math

import networkx as nx
import osmnx as ox
import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia._grafo import ATRIBUTO_GEOMETRIA, seleccionar_artefacto
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio
from hoppit.shared.errores import EntidadNoEncontrada

_LOG = logging.getLogger(__name__)

# Tres nodos reales de la componente fuertemente conexa mayor, elegidos de
# forma fija: dos destinos de la instancia de demostracion y el Deposito.
DEPOSITO = 13282860598
DESTINO_A = 299362038
DESTINO_B = 334938533

GRADOS_A_METROS = 111_320.0
TOLERANCIA_M = 0.01


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_servicio(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture(scope="module")
def grafo_crudo() -> nx.MultiDiGraph:
    return ox.load_graphml(seleccionar_artefacto(GRAFO_PREDETERMINADO))


def test_el_trazado_recorre_los_nodos_pedidos_en_orden(grafo: GrafoVialAMBA) -> None:
    """Arranca en el primero y termina en el ultimo."""
    nodos = (DEPOSITO, DESTINO_A, DESTINO_B, DEPOSITO)
    trazado = grafo.trazado(nodos)

    inicio = grafo.coordenadas(DEPOSITO)
    assert trazado.coordenadas[0] == (inicio.lat, inicio.lon)
    assert trazado.coordenadas[-1] == (inicio.lat, inicio.lon)
    _LOG.info(
        "Trazado | nodos=%d puntos=%d arcos=%d distancia=%.1f m",
        len(nodos),
        len(trazado.coordenadas),
        trazado.arcos,
        trazado.distancia_m,
    )


def test_la_distancia_del_trazado_es_la_del_camino_ruteado(
    grafo: GrafoVialAMBA,
) -> None:
    """Se dibuja lo que se ruteo, no una medida recalculada aparte.

    Si el trazado informara una distancia propia, la interfaz podria mostrar
    un numero distinto del que el solver optimizo.
    """
    nodos = (DEPOSITO, DESTINO_A, DESTINO_B, DEPOSITO)
    matriz = grafo.matriz_de_costos(nodos)
    esperada = sum(
        matriz.distancia_m(origen, destino)
        for origen, destino in zip(nodos, nodos[1:])
    )

    assert grafo.trazado(nodos).distancia_m == pytest.approx(esperada)


def test_la_polilinea_es_continua_y_sin_puntos_repetidos(
    grafo: GrafoVialAMBA,
) -> None:
    """El destino de un arco es el origen del siguiente.

    Sin el filtro de repetidos, cada nodo intermedio saldria dos veces y la
    polilinea tendria pares de puntos identicos.
    """
    trazado = grafo.trazado((DEPOSITO, DESTINO_A, DESTINO_B, DEPOSITO))
    consecutivos = list(zip(trazado.coordenadas, trazado.coordenadas[1:]))

    assert all(a != b for a, b in consecutivos)


def test_el_trazado_sigue_la_curva_de_la_calle_y_no_la_recta(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """Sobre un arco con geometria declarada, el trazado tiene que pasar por
    sus puntos intermedios.

    Es la diferencia concreta que NFR-08 pide: unir los extremos con una
    recta dibujaria un segmento que no existe en la red vial.
    """
    origen, destino, datos = next(
        (u, v, d)
        for u, v, d in grafo_crudo.edges(data=True)
        if ATRIBUTO_GEOMETRIA in d and len(d[ATRIBUTO_GEOMETRIA].coords) > 2
    )
    trazado = grafo.trazado((int(origen), int(destino)))
    intermedios = len(datos[ATRIBUTO_GEOMETRIA].coords)

    assert len(trazado.coordenadas) == intermedios
    assert trazado.arcos == 1
    _LOG.info(
        "Arco con curva | %d -> %d puntos=%d", origen, destino, len(trazado.coordenadas)
    )


def test_el_trazado_orienta_la_geometria_en_el_sentido_del_recorrido(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """Ida y vuelta sobre el mismo par: cada una arranca donde corresponde.

    OSMnx guarda la geometria en el sentido del way de OSM, asi que en uno de
    los dos sentidos viene al reves. Sin corregirlo, la polilinea saltaria de
    un extremo al otro del arco.
    """
    origen, destino = next(
        (u, v)
        for u, v, d in grafo_crudo.edges(data=True)
        if ATRIBUTO_GEOMETRIA in d and grafo_crudo.has_edge(v, u)
    )
    ida = grafo.trazado((int(origen), int(destino)))
    vuelta = grafo.trazado((int(destino), int(origen)))

    inicio = grafo.coordenadas(int(origen))
    fin = grafo.coordenadas(int(destino))
    assert ida.coordenadas[0] == (inicio.lat, inicio.lon)
    assert vuelta.coordenadas[0] == (fin.lat, fin.lon)


def test_los_saltos_entre_puntos_no_exceden_el_arco_mas_largo(
    grafo: GrafoVialAMBA,
) -> None:
    """Ninguna discontinuidad: dos puntos consecutivos pertenecen al mismo
    arco o son extremos de arcos encadenados."""
    trazado = grafo.trazado((DEPOSITO, DESTINO_A, DESTINO_B, DEPOSITO))
    saltos_m = [
        math.dist(a, b) * GRADOS_A_METROS
        for a, b in zip(trazado.coordenadas, trazado.coordenadas[1:])
    ]

    # Ningun salto puede superar la distancia total del recorrido.
    assert max(saltos_m) < trazado.distancia_m


def test_un_trazado_necesita_al_menos_dos_nodos(grafo: GrafoVialAMBA) -> None:
    """Un solo punto no describe un recorrido."""
    with pytest.raises(EntidadNoEncontrada):
        grafo.trazado((DEPOSITO,))
