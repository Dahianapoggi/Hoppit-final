"""Logica pura de las herramientas de la sesion 2026-10-13F versionadas en
2026-10-15A (bloque S): filtro de vias con la semantica de Overpass,
comparacion de dos grafos viales y comparacion de geocodificadores. Sin
datos reales ni red: fixtures escritos a mano."""
from __future__ import annotations

import math
import urllib.parse

import networkx as nx
import pytest

from tools.comparacion_de_geocodificadores import (
    PROVINCIA_GEOREF_CIUDAD,
    armar_muestra,
    asignacion_proporcional,
    coincide,
    consulta_nominatim,
    direccion_de_la_fuente,
    haversine,
    lote_georef,
    palabras,
)
from tools.comparar_grafos_viales import comparar, medidas, sortear_pares
from tools.filtro_de_vias_overpass import pasa, reglas_del_filtro

FILTRO = '["highway"]["area"!~"yes"]["highway"!~"footway|service"]["motorcar"~"yes|designated"]'


def test_el_filtro_se_lee_en_orden_y_se_reconstruye() -> None:
    reglas = reglas_del_filtro(FILTRO)
    assert [(c, o) for c, o, _ in reglas] == [
        ("highway", None),
        ("area", "!~"),
        ("highway", "!~"),
        ("motorcar", "~"),
    ]


def test_un_filtro_con_texto_que_no_es_condicion_falla() -> None:
    with pytest.raises(ValueError):
        reglas_del_filtro('["highway"] y algo mas')


def test_semantica_de_overpass() -> None:
    reglas = reglas_del_filtro(FILTRO)
    base = {"highway": "residential", "motorcar": "yes"}
    assert pasa(base, reglas)
    # La clave exigida falta.
    assert not pasa({"motorcar": "yes"}, reglas)
    # "!~": la clave ausente pasa; el valor que contiene la expresion, no.
    assert pasa(base, reglas)
    assert not pasa({**base, "area": "yes"}, reglas)
    # Sin anclar: "service" esta contenido en "services".
    assert not pasa({**base, "highway": "services"}, reglas)
    # "~": la clave tiene que estar y contener la expresion.
    assert not pasa({"highway": "residential"}, reglas)
    assert not pasa({"highway": "residential", "motorcar": "no"}, reglas)


def _grafo(arcos: list[tuple[int, int, float]]) -> nx.MultiDiGraph:
    g = nx.MultiDiGraph()
    for u, v, largo in arcos:
        g.add_edge(u, v, length=largo)
    return g


def test_medidas_de_un_grafo() -> None:
    g = _grafo([(1, 2, 100.0), (2, 1, 100.0), (2, 3, 300.0)])
    assert medidas(g) == {
        "nodos": 3,
        "arcos": 3,
        "componentes_fuertes": 2,
        "mayor_componente_fuerte": 2,
        "km_de_arcos_dirigidos": 0.5,
    }


def test_los_pares_son_deterministas_y_sin_repetir() -> None:
    comunes = list(range(1, 11))
    pares = sortear_pares(comunes, cantidad=20, semilla=7)
    assert pares == sortear_pares(comunes, cantidad=20, semilla=7)
    assert len(set(pares)) == 20
    assert all(o != d for o, d in pares)


def test_comparar_cuenta_nodos_arcos_y_distancias() -> None:
    # Un anillo de 8 nodos: 56 pares ordenados, mas que los 50 que se sortean
    # (con menos de 8 nodos comunes el sorteo no termina; hallazgo de S).
    anillo = [(n, n % 8 + 1, 10.0) for n in range(1, 9)]
    uno = _grafo(anillo + [(1, 5, 100.0)])
    dos = _grafo(anillo)
    resultado = comparar(uno, dos)
    assert resultado["nodos_comunes"] == 8
    assert resultado["arcos_comunes"] == 8
    assert resultado["arcos_solo_en_1"] == 1
    assert resultado["arcos_solo_en_2"] == 0
    # El arco 1->5 de 100 m no acorta ningun camino: las distancias coinciden.
    assert resultado["pares_con_camino_en_los_dos"] == 50
    assert resultado["pares_con_distancia_identica"] == 50


def test_direccion_de_la_fuente() -> None:
    assert direccion_de_la_fuente("Av. Rivadavia 1234 - Av. Rivadavia 1240") == ("Av. Rivadavia", 1234)
    assert direccion_de_la_fuente("  Peron, Juan Domingo, Tte. General 664 ") == ("Peron, Juan Domingo, Tte. General", 664)
    assert direccion_de_la_fuente("Sin altura") is None
    assert direccion_de_la_fuente("25 de Mayo") is None
    assert direccion_de_la_fuente(None) is None


def test_asignacion_proporcional_por_resto_mayor() -> None:
    # Cuotas 3,33 / 3,33 / 3,33 sobre 10: el resto va al menor estrato.
    assert asignacion_proporcional({1: 1, 2: 1, 3: 1}, 10) == {1: 4, 2: 3, 3: 3}
    # Cuotas 5,5 / 2,75 / 1,75: los dos restos mayores son 0,75.
    asignado = asignacion_proporcional({1: 22, 2: 11, 3: 7}, 10)
    assert asignado == {1: 5, 2: 3, 3: 2}
    assert sum(asignado.values()) == 10


def test_la_muestra_es_determinista_y_ordenada_por_comuna() -> None:
    por_comuna = {
        2: [{"cui": c} for c in (30, 10, 20)],
        1: [{"cui": c} for c in (5, 4)],
    }
    uno = armar_muestra({c: [dict(e) for e in v] for c, v in por_comuna.items()}, {1: 1, 2: 2}, semilla=3)
    dos = armar_muestra({c: [dict(e) for e in v] for c, v in por_comuna.items()}, {1: 1, 2: 2}, semilla=3)
    assert uno == dos
    assert [e["clave"] for e in uno] == ["r001", "r002", "r003"]
    assert uno[0]["cui"] in (4, 5)
    assert {e["cui"] for e in uno[1:]} <= {10, 20, 30}


def test_pedidos_a_los_geocodificadores() -> None:
    lote = lote_georef([{"direccion": "Av. Cabildo 2400"}])
    assert lote == {"direcciones": [{"direccion": "Av. Cabildo 2400", "provincia": PROVINCIA_GEOREF_CIUDAD, "max": 1}]}
    url, nombre = consulta_nominatim("Av. Cabildo 2400")
    consulta = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert consulta["q"] == ["Av. Cabildo 2400, Ciudad Autónoma de Buenos Aires, Argentina"]
    assert consulta["limit"] == ["1"]
    assert nombre.startswith("nominatim_") and nombre == consulta_nominatim("Av. Cabildo 2400")[1]


def test_palabras_y_coincidencia_de_nombres() -> None:
    assert palabras("Av. Santa Fé") == {"santa", "fe"}
    assert coincide("Av. Santa Fe", "Avenida Santa Fe")
    assert coincide("Santa Fe", ["Calle Falsa", "Avenida Santa Fe"])
    assert not coincide("Santa Fe", "Avenida Cordoba")
    # Sin nombre en el arco (NaN de OSMnx) no coincide.
    assert not coincide("Santa Fe", math.nan)


def test_haversine() -> None:
    assert haversine(-34.6, -58.4, -34.6, -58.4) == 0.0
    # Un minuto de latitud son unos 1.853 m.
    assert haversine(-34.6, -58.4, -34.6 + 1 / 60, -58.4) == pytest.approx(1853.2, abs=1.0)


def test_sortear_pares_falla_si_no_hay_pares_suficientes() -> None:
    """Sesion 2026-10-15B, bloque G: con 3 nodos hay 6 pares ordenados
    distintos; pedir mas fallaba sin terminar."""
    assert len(sortear_pares([1, 2, 3], cantidad=6)) == 6
    with pytest.raises(ValueError, match="6 pares distintos"):
        sortear_pares([1, 2, 3], cantidad=7)
