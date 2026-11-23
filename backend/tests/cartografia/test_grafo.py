"""Verifica los tres metodos de Cartografia contra el grafo vial real
(FR-01, NFR-08).

No se usa un grafo sintetico a proposito: lo que hay que verificar es que
la topologia real del artefacto versionado se consulta bien, y un grafo de
juguete no puede mostrar eso. Cada prueba deja en el log el resultado
concreto del caso que ejercita.
"""
from __future__ import annotations

import logging
import math

import networkx as nx
import osmnx as ox
import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia._grafo import seleccionar_artefacto
from hoppit.cartografia.puerto import CaminoInexistente, GrafoVialAMBA, crear_servicio
from hoppit.shared.errores import EntidadNoEncontrada

_LOG = logging.getLogger(__name__)

# Centro de San Isidro, sobre la plaza Mitre. Coordenada elegida a mano,
# no derivada del grafo, para que la prueba de anclaje resuelva un punto
# de la via publica y no un nodo que ya se sabe que existe.
LAT_CONSULTA = -34.4718
LON_CONSULTA = -58.5074

# Un desplazamiento chico respecto de un nodo conocido debe seguir
# resolviendo a ese mismo nodo.
DESPLAZAMIENTO_GRADOS = 0.00002

RADIO_TERRESTRE_M = 6_371_009.0
TOLERANCIA_METROS = 0.5

# Medidas del artefacto, tal como quedaron loggeadas en su extraccion.
NODOS_ESPERADOS = 5163
ARISTAS_ESPERADAS = 12036
COMPONENTES_DEBILES_ESPERADAS = 1
COMPONENTES_FUERTES_ESPERADAS = 19

# Muestra para los casos de matriz. Chica a proposito: la corrida grande,
# sobre los 21 nodos de la instancia de demostracion, es una medicion y va
# en tools/medir_matriz_costos.py, no en la suite.
CANTIDAD_NODOS_DE_PRUEBA = 6


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia de gran circulo, escrita aparte a proposito.

    La implementacion consulta la formula de OSMnx; verificarla contra esa
    misma funcion no probaria nada. Esta es una segunda implementacion
    independiente de la misma formula.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RADIO_TERRESTRE_M * math.asin(math.sqrt(a))


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_servicio(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture(scope="module")
def grafo_crudo() -> nx.MultiDiGraph:
    """El artefacto leido por fuera del servicio.

    Sirve unicamente para elegir los casos de prueba (que nodos son
    adyacentes, que coordenadas tiene cada uno): esa topologia no la expone
    el puerto de Cartografia, y no corresponde exponerla solo para poder
    testear. Lo que se verifica despues es lo que responde el servicio.
    """
    return ox.load_graphml(seleccionar_artefacto(GRAFO_PREDETERMINADO))


@pytest.fixture(scope="module")
def arco_real(grafo_crudo: nx.MultiDiGraph) -> tuple[int, int]:
    """Un arco existente del artefacto, elegido de forma determinista."""
    origen, destino = sorted(grafo_crudo.edges())[0][:2]
    return int(origen), int(destino)


def test_el_resumen_reproduce_lo_medido_en_la_extraccion(grafo: GrafoVialAMBA) -> None:
    """Lo que Cartografia carga tiene que ser lo que la extraccion midio.

    Los valores esperados son los que quedaron loggeados al construir el
    artefacto en el bloque 1; si el grafo cargado difiere, cambio la
    version del artefacto sin que nadie lo declarara.
    """
    resumen = grafo.resumen()
    _LOG.info(
        "resumen() -> nodos=%d aristas=%d componentes_debiles=%d componentes_fuertes=%d "
        "mayor_componente_fuerte=%d (%.2f%% de los nodos)",
        resumen.nodos,
        resumen.aristas,
        resumen.componentes_debiles,
        resumen.componentes_fuertes,
        resumen.nodos_en_mayor_componente_fuerte,
        resumen.fraccion_en_mayor_componente_fuerte * 100,
    )
    assert resumen.nodos == NODOS_ESPERADOS
    assert resumen.aristas == ARISTAS_ESPERADAS
    assert resumen.componentes_debiles == COMPONENTES_DEBILES_ESPERADAS
    assert resumen.componentes_fuertes == COMPONENTES_FUERTES_ESPERADAS


def test_costo_de_un_arco_real(grafo: GrafoVialAMBA, arco_real: tuple[int, int]) -> None:
    origen, destino = arco_real
    costo = grafo.costo(origen, destino)

    _LOG.info(
        "costo(%d, %d) -> distancia_m=%.2f tiempo_base_s=%.2f (%.1f km/h implicitos)",
        origen,
        destino,
        costo.distancia_m,
        costo.tiempo_base_s,
        (costo.distancia_m / costo.tiempo_base_s) * 3.6,
    )

    assert costo.origen == origen and costo.destino == destino
    assert costo.distancia_m > 0
    assert costo.tiempo_base_s > 0


def test_costo_es_deterministico(grafo: GrafoVialAMBA, arco_real: tuple[int, int]) -> None:
    """NFR-07 empieza aca: dos consultas iguales, misma respuesta."""
    primero = grafo.costo(*arco_real)
    segundo = grafo.costo(*arco_real)
    _LOG.info("costo repetido -> %s == %s", primero, segundo)
    assert primero == segundo


def test_costo_entre_nodos_no_adyacentes_no_inventa_un_arco(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """El costo entre nodos sin segmento vial en comun es un camino, no un
    arco: pedirlo aca tiene que fallar y no devolver un numero (NFR-08)."""
    origen = min(grafo_crudo.nodes())
    no_adyacentes = [
        n for n in grafo_crudo.nodes() if n != origen and not grafo_crudo.has_edge(origen, n)
    ]
    destino = min(no_adyacentes)

    _LOG.info("costo(%d, %d) sobre nodos no adyacentes -> se espera error", origen, destino)
    with pytest.raises(EntidadNoEncontrada):
        grafo.costo(int(origen), int(destino))


def test_costo_de_un_nodo_inexistente_falla(grafo: GrafoVialAMBA) -> None:
    with pytest.raises(EntidadNoEncontrada):
        grafo.costo(-1, -2)


def test_nodo_mas_cercano_a_una_coordenada_real(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """Anclaje de una Parada al grafo, verificado por fuerza bruta: ningun
    otro nodo del artefacto puede estar mas cerca que el devuelto."""
    nodo = grafo.nodo_mas_cercano(LAT_CONSULTA, LON_CONSULTA)

    distancia_devuelta = _haversine_m(LAT_CONSULTA, LON_CONSULTA, nodo.lat, nodo.lon)
    minima = min(
        _haversine_m(LAT_CONSULTA, LON_CONSULTA, float(datos["y"]), float(datos["x"]))
        for _, datos in grafo_crudo.nodes(data=True)
    )

    _LOG.info(
        "nodo_mas_cercano(%.4f, %.4f) -> id_osm=%d lat=%.6f lon=%.6f a %.2f m "
        "(minimo sobre los %d nodos: %.2f m)",
        LAT_CONSULTA,
        LON_CONSULTA,
        nodo.id_osm,
        nodo.lat,
        nodo.lon,
        distancia_devuelta,
        grafo_crudo.number_of_nodes(),
        minima,
    )

    assert nodo.id_osm in grafo_crudo
    assert distancia_devuelta == pytest.approx(minima, abs=TOLERANCIA_METROS)


def test_nodo_mas_cercano_resuelve_al_nodo_de_su_propia_coordenada(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph
) -> None:
    """Consultado casi encima de un nodo conocido, devuelve ese nodo."""
    id_esperado = min(grafo_crudo.nodes())
    datos = grafo_crudo.nodes[id_esperado]

    nodo = grafo.nodo_mas_cercano(
        float(datos["y"]) + DESPLAZAMIENTO_GRADOS,
        float(datos["x"]) + DESPLAZAMIENTO_GRADOS,
    )
    _LOG.info("nodo_mas_cercano cerca de %d -> %d", id_esperado, nodo.id_osm)
    assert nodo.id_osm == int(id_esperado)


def test_consultar_sin_construir_es_error_de_uso() -> None:
    with pytest.raises(RuntimeError):
        crear_servicio(GRAFO_PREDETERMINADO).nodo_mas_cercano(LAT_CONSULTA, LON_CONSULTA)
    with pytest.raises(RuntimeError):
        crear_servicio(GRAFO_PREDETERMINADO).resumen()


# --- Componente fuertemente conexa mayor ----------------------------------


@pytest.fixture(scope="module")
def componente_mayor(grafo: GrafoVialAMBA) -> tuple[int, ...]:
    return grafo.nodos_de_mayor_componente_fuerte()


def test_la_componente_mayor_coincide_con_la_que_reporta_el_resumen(
    grafo: GrafoVialAMBA, componente_mayor: tuple[int, ...]
) -> None:
    """Las dos medidas salen del mismo grafo: si difieren, una miente."""
    resumen = grafo.resumen()
    _LOG.info(
        "nodos_de_mayor_componente_fuerte() -> %d nodos de %d (%.2f%%), "
        "min=%d max=%d",
        len(componente_mayor),
        resumen.nodos,
        len(componente_mayor) / resumen.nodos * 100,
        componente_mayor[0],
        componente_mayor[-1],
    )
    assert len(componente_mayor) == resumen.nodos_en_mayor_componente_fuerte


def test_la_componente_mayor_es_fuertemente_conexa_de_verdad(
    grafo_crudo: nx.MultiDiGraph, componente_mayor: tuple[int, ...]
) -> None:
    """Verificacion independiente: el subgrafo inducido tiene que ser una
    unica componente fuerte, no un conjunto de nodos cualquiera."""
    inducido = grafo_crudo.subgraph(componente_mayor)
    componentes = list(nx.strongly_connected_components(inducido))
    _LOG.info(
        "subgrafo inducido por la componente mayor -> nodos=%d componentes_fuertes=%d",
        inducido.number_of_nodes(),
        len(componentes),
    )
    assert len(componentes) == 1
    assert len(componentes[0]) == len(componente_mayor)


def test_la_componente_mayor_es_deterministica(grafo: GrafoVialAMBA) -> None:
    """NFR-07: el desempate no puede depender del orden de NetworkX."""
    assert grafo.nodos_de_mayor_componente_fuerte() == grafo.nodos_de_mayor_componente_fuerte()


def test_hay_nodos_fuera_de_la_componente_mayor(
    grafo: GrafoVialAMBA, componente_mayor: tuple[int, ...]
) -> None:
    """Si el grafo estuviera entero en una sola componente, restringir los
    candidatos no haria falta y el caso de par sin camino no existiria."""
    resumen = grafo.resumen()
    afuera = resumen.nodos - len(componente_mayor)
    _LOG.info("nodos fuera de la componente fuerte mayor: %d", afuera)
    assert afuera > 0


# --- Matriz de costos ------------------------------------------------------


@pytest.fixture(scope="module")
def nodos_de_prueba(componente_mayor: tuple[int, ...]) -> list[int]:
    """Muestra chica y determinista de la componente mayor.

    Se toman nodos espaciados a lo largo del orden del artefacto, no los
    primeros: los primeros identificadores OSM tienden a estar agrupados
    geograficamente y darian caminos artificialmente cortos.
    """
    paso = len(componente_mayor) // CANTIDAD_NODOS_DE_PRUEBA
    return [componente_mayor[i * paso] for i in range(CANTIDAD_NODOS_DE_PRUEBA)]


def test_la_matriz_cubre_todos_los_pares(
    grafo: GrafoVialAMBA, nodos_de_prueba: list[int]
) -> None:
    matriz = grafo.matriz_de_costos(nodos_de_prueba)

    assert matriz.nodos == tuple(nodos_de_prueba)
    assert len(matriz.caminos) == len(nodos_de_prueba) ** 2
    for origen in nodos_de_prueba:
        for destino in nodos_de_prueba:
            assert (origen, destino) in matriz.caminos


def test_la_diagonal_es_cero_y_sin_arcos(
    grafo: GrafoVialAMBA, nodos_de_prueba: list[int]
) -> None:
    matriz = grafo.matriz_de_costos(nodos_de_prueba)
    for nodo in nodos_de_prueba:
        camino = matriz.camino(nodo, nodo)
        assert camino.distancia_m == 0.0
        assert camino.arcos == ()


def test_un_par_con_camino_conocido(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph, nodos_de_prueba: list[int]
) -> None:
    """La distancia devuelta tiene que ser la del camino minimo, medida por
    una segunda via: `nx.shortest_path_length` sobre el artefacto crudo."""
    origen, destino = nodos_de_prueba[0], nodos_de_prueba[1]
    camino = grafo.matriz_de_costos([origen, destino]).camino(origen, destino)

    esperada = nx.shortest_path_length(grafo_crudo, origen, destino, weight="length")
    _LOG.info(
        "camino %d -> %d: distancia_m=%.2f arcos=%d (referencia independiente=%.2f)",
        origen,
        destino,
        camino.distancia_m,
        camino.cantidad_de_arcos,
        esperada,
    )
    assert camino.distancia_m == pytest.approx(float(esperada))
    assert camino.cantidad_de_arcos > 0


def test_la_secuencia_de_arcos_existe_toda_en_el_grafo(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph, nodos_de_prueba: list[int]
) -> None:
    """NFR-08. Cada arco del camino tiene que ser un segmento vial real, los
    arcos tienen que encadenarse extremo con extremo entre el origen y el
    destino declarados, y sus distancias tienen que sumar el costo del
    camino. Sin las tres cosas, el escalar no es auditable."""
    matriz = grafo.matriz_de_costos(nodos_de_prueba)
    arcos_verificados = 0

    for (origen, destino), camino in matriz.caminos.items():
        if origen == destino:
            continue

        assert camino.arcos[0].origen == origen
        assert camino.arcos[-1].destino == destino

        for anterior, siguiente in zip(camino.arcos, camino.arcos[1:]):
            assert anterior.destino == siguiente.origen

        for arco in camino.arcos:
            assert grafo_crudo.has_edge(arco.origen, arco.destino)
            arcos_verificados += 1

        assert sum(arco.distancia_m for arco in camino.arcos) == pytest.approx(
            camino.distancia_m
        )

    _LOG.info(
        "NFR-08 | %d arcos verificados en el grafo sobre %d caminos",
        arcos_verificados,
        len(matriz.caminos),
    )
    assert arcos_verificados > 0


def test_como_matriz_es_cuadrada_y_respeta_el_orden_pedido(
    grafo: GrafoVialAMBA, nodos_de_prueba: list[int]
) -> None:
    """El solver indexa por posicion: esa correspondencia es el contrato."""
    matriz = grafo.matriz_de_costos(nodos_de_prueba)
    plana = matriz.como_matriz()

    assert len(plana) == len(nodos_de_prueba)
    assert all(len(fila) == len(nodos_de_prueba) for fila in plana)
    for i, origen in enumerate(nodos_de_prueba):
        for j, destino in enumerate(nodos_de_prueba):
            assert plana[i][j] == matriz.distancia_m(origen, destino)


def test_un_nodo_repetido_no_se_recalcula_ni_rompe_el_orden(
    grafo: GrafoVialAMBA, nodos_de_prueba: list[int]
) -> None:
    """Dos Paradas distintas pueden anclar al mismo nodo (ARQ-05)."""
    origen, destino = nodos_de_prueba[0], nodos_de_prueba[1]
    matriz = grafo.matriz_de_costos([origen, destino, origen])

    assert matriz.nodos == (origen, destino, origen)
    plana = matriz.como_matriz()
    assert plana[0] == plana[2]
    assert len(matriz.caminos) == 4  # dos nodos distintos, no tres


def test_la_matriz_es_deterministica(grafo: GrafoVialAMBA, nodos_de_prueba: list[int]) -> None:
    """NFR-07 sobre lo que consume el solver."""
    primera = grafo.matriz_de_costos(nodos_de_prueba).como_matriz()
    segunda = grafo.matriz_de_costos(nodos_de_prueba).como_matriz()
    assert primera == segunda


def test_un_par_sin_camino_falla_explicito(
    grafo: GrafoVialAMBA, grafo_crudo: nx.MultiDiGraph, componente_mayor: tuple[int, ...]
) -> None:
    """Un nodo fuera de la componente mayor no puede producir una celda con
    infinito ni desaparecer de la matriz: tiene que cortar la operacion."""
    afuera = sorted(set(int(n) for n in grafo_crudo.nodes()) - set(componente_mayor))
    aislado = afuera[0]
    de_adentro = componente_mayor[0]

    with pytest.raises(CaminoInexistente) as error:
        grafo.matriz_de_costos([de_adentro, aislado])

    _LOG.info(
        "matriz_de_costos con el nodo %d (fuera de la componente mayor) -> %s",
        aislado,
        error.value,
    )
    assert str(aislado) in str(error.value)
    assert "nodos_de_mayor_componente_fuerte" in str(error.value)


def test_un_nodo_inexistente_en_la_matriz_falla_distinto(grafo: GrafoVialAMBA) -> None:
    """Nodo que no esta en el grafo es EntidadNoEncontrada; nodo que esta
    pero es inalcanzable es CaminoInexistente. Son causas distintas."""
    with pytest.raises(EntidadNoEncontrada):
        grafo.matriz_de_costos([-1])


def test_pedir_un_par_ausente_de_la_matriz_falla(
    grafo: GrafoVialAMBA, nodos_de_prueba: list[int]
) -> None:
    matriz = grafo.matriz_de_costos(nodos_de_prueba[:2])
    with pytest.raises(EntidadNoEncontrada):
        matriz.camino(nodos_de_prueba[0], nodos_de_prueba[2])
