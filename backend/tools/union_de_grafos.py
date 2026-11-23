"""Perdida de conectividad al unir grafos por partido (sesion 2026-10-13C,
bloque E6). Fuera de las metricas: sirve para elegir el metodo del grafo del
AMBA (E7).

- `adyacencia A B`: geocodifica los dos poligonos administrativos y verifica
  que compartan limite (longitud de la interseccion de sus bordes, en m).
- `comparar NOMBRE_A=GRAFO_A NOMBRE_B=GRAFO_B`:
  (a) une los dos grafos ya extraidos (`nx.compose`; los nodos de OSM con el
      mismo identificador se funden);
  (b) extrae de una sola vez el poligono unido con los mismos parametros que
      la extraccion del producto (`ox.graph_from_polygon(poligono,
      network_type="drive")`, con los valores por omision de OSMnx), lo
      guarda en datos/tmp/grafos_union/ con sidecar, y compara.
  Para (a) y (b): nodos, arcos y fraccion en la mayor componente
  fuertemente conexa. Nodos de limite: los de cada partido a menos de
  `RADIO_DEL_LIMITE_M` del poligono del otro, en UTM. Para cada uno se
  informa si tiene camino hacia algun nodo del otro partido y desde alguno,
  dentro del grafo de que se trate.

La extraccion (b) se corre con OSMnx 2.0.7 (entorno aparte), igual que los
grafos que se unen en (a).

    python tools/union_de_grafos.py adyacencia "Partido de San Isidro, Provincia de Buenos Aires, Argentina" "..."
    python tools/union_de_grafos.py comparar "San Isidro=datos/grafos/grafo_san_isidro_2026-08-16.graphml" "Vicente Lopez=..." --consultas "..." "..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
import time
from typing import Any

import networkx as nx
import osmnx as ox
from shapely.ops import unary_union

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import memoria_fisica_gb

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.union_de_grafos")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / "datos" / "tmp" / "grafos_union"
RADIO_DEL_LIMITE_M = 250.0
TIPO_RED = "drive"
CODIFICACION = "utf-8"


def _poligono(consulta: str) -> Any:
    area = ox.geocode_to_gdf(consulta)
    return area, area.iloc[0].geometry


def adyacencia(consulta_a: str, consulta_b: str) -> float:
    area_a, _ = _poligono(consulta_a)
    area_b, _ = _poligono(consulta_b)
    crs = area_a.estimate_utm_crs()
    borde_a = area_a.to_crs(crs).iloc[0].geometry.boundary
    borde_b = area_b.to_crs(crs).iloc[0].geometry.boundary
    compartido = float(borde_a.intersection(borde_b.buffer(1.0)).length)
    _LOG.info(
        "%s (%s/%s) y %s (%s/%s) | limite compartido %.0f m",
        consulta_a, area_a.iloc[0]["osm_type"], area_a.iloc[0]["osm_id"],
        consulta_b, area_b.iloc[0]["osm_type"], area_b.iloc[0]["osm_id"], compartido,
    )
    return compartido


def _medidas(grafo: nx.MultiDiGraph, de_a: set[int], de_b: set[int], limite_a: set[int], limite_b: set[int]) -> dict[str, Any]:
    mayor = max(nx.strongly_connected_components(grafo), key=len)

    def sin_conexion(limite: set[int], otro: set[int]) -> dict[str, int]:
        presentes = [n for n in limite if n in grafo]
        otros = [n for n in otro if n in grafo]
        hacia = nx.multi_source_dijkstra_path_length(grafo.reverse(copy=False), otros) if otros else {}
        desde = nx.multi_source_dijkstra_path_length(grafo, otros) if otros else {}
        return {
            "nodos_de_limite": len(presentes),
            "sin_camino_hacia_el_otro": sum(1 for n in presentes if n not in hacia),
            "sin_camino_desde_el_otro": sum(1 for n in presentes if n not in desde),
        }

    return {
        "nodos": grafo.number_of_nodes(),
        "arcos": grafo.number_of_edges(),
        "fraccion_en_la_mayor_componente_fuerte": len(mayor) / grafo.number_of_nodes(),
        "limite_del_primero": sin_conexion(limite_a, de_b),
        "limite_del_segundo": sin_conexion(limite_b, de_a),
    }


def _nodos_de_limite(grafo: nx.MultiDiGraph, poligono_otro: Any, crs: Any) -> set[int]:
    import geopandas as gpd

    nodos = ox.graph_to_gdfs(grafo, edges=False).to_crs(crs)
    otro = gpd.GeoSeries([poligono_otro], crs="EPSG:4326").to_crs(crs).iloc[0]
    cerca = nodos.geometry.distance(otro) <= RADIO_DEL_LIMITE_M
    return set(int(n) for n in nodos.index[cerca])


def comparar(asignaciones: list[str], consultas: list[str], solo_union: bool = False) -> None:
    (nombre_a, ruta_a), (nombre_b, ruta_b) = (a.split("=", 1) for a in asignaciones)
    area_a, poligono_a = _poligono(consultas[0])
    area_b, poligono_b = _poligono(consultas[1])
    crs = area_a.estimate_utm_crs()
    grafo_a = ox.load_graphml(RAIZ / ruta_a)
    grafo_b = ox.load_graphml(RAIZ / ruta_b)
    de_a, de_b = set(int(n) for n in grafo_a.nodes), set(int(n) for n in grafo_b.nodes)
    limite_a = _nodos_de_limite(grafo_a, poligono_b, crs)
    limite_b = _nodos_de_limite(grafo_b, poligono_a, crs)
    union = nx.compose(grafo_a, grafo_b)
    resultado: dict[str, Any] = {
        "par": [nombre_a, nombre_b],
        "consultas": consultas,
        "nodos_compartidos_por_los_dos_grafos": len(de_a & de_b),
        "radio_del_limite_m": RADIO_DEL_LIMITE_M,
        "a_union_de_los_grafos": _medidas(union, de_a, de_b, limite_a, limite_b),
    }
    salida = DIRECTORIO / f"comparacion_{nombre_a}_{nombre_b}.json".replace(" ", "_").lower()
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    if solo_union:
        salida.write_bytes((json.dumps(resultado, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
        _LOG.info("%s", json.dumps(resultado, ensure_ascii=False))
        return

    _, libre = memoria_fisica_gb()
    _LOG.info("Memoria fisica libre antes de extraer el poligono unido: %.2f GB", libre or float("nan"))
    inicio = time.monotonic()
    unido = ox.graph_from_polygon(unary_union([poligono_a, poligono_b]), network_type=TIPO_RED)
    segundos = time.monotonic() - inicio
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    ruta = DIRECTORIO / f"grafo_union_{nombre_a}_{nombre_b}.graphml".replace(" ", "_").lower()
    ox.save_graphml(unido, ruta)
    escribir_sidecar(
        registrar(
            artefacto="grafo_union",
            ambito=f"{nombre_a} + {nombre_b}",
            fuente="OpenStreetMap via Overpass API",
            herramienta=f"osmnx {ox.__version__}",
            parametros={
                "consultas": consultas,
                "tipo_red": TIPO_RED,
                "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(),
                "segundos_de_extraccion": round(segundos, 1),
            },
        ),
        ruta,
    )
    resultado["b_poligono_unido"] = _medidas(unido, de_a, de_b, limite_a, limite_b)
    resultado["b_segundos_de_extraccion"] = round(segundos, 1)
    resultado["b_nodos_que_no_estan_en_a_ni_en_b"] = sum(1 for n in unido.nodes if int(n) not in de_a | de_b)
    resultado["b_arcos_que_no_estan_en_la_union"] = sum(1 for u, v, k in unido.edges(keys=True) if not union.has_edge(u, v, k))
    salida.write_bytes((json.dumps(resultado, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    _LOG.info("%s", json.dumps(resultado, ensure_ascii=False))


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--overpass-url",
        default=None,
        help="Servidor de Overpass (por omision, el de OSMnx); ver tools/extraer_grafo_vial.py.",
    )
    subcomandos = parser.add_subparsers(dest="subcomando", required=True)
    ady = subcomandos.add_parser("adyacencia")
    ady.add_argument("consultas", nargs=2)
    comp = subcomandos.add_parser("comparar")
    comp.add_argument("asignaciones", nargs=2)
    comp.add_argument("--consultas", nargs=2, required=True)
    comp.add_argument("--solo-union", action="store_true", help="Solo (a), sin extraer el poligono unido.")
    args = parser.parse_args(argumentos)
    if args.overpass_url is not None:
        ox.settings.overpass_url = args.overpass_url
    if args.subcomando == "adyacencia":
        adyacencia(*args.consultas)
    else:
        comparar(args.asignaciones, args.consultas, args.solo_union)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
