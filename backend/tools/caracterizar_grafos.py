"""Caracterizacion del grafo vial de cinco partidos (sesion 2026-10-13C,
bloque G; B-207, punto 3c; B-208, punto 7).

Clase de las cifras: datos reales de OpenStreetMap. Solo lee los artefactos
versionados, sin volver a extraer: San Isidro en datos/grafos/ y Lanus, La
Matanza, Almirante Brown y Tigre en datos/grafos_por_partido/. Antes de cada
uno informa la memoria fisica libre.

Como se detecta cada medida, sobre el GraphML cargado con `ox.load_graphml`
(un `MultiDiGraph`, un arco por sentido de circulacion):

- area resuelta: `parametros.area_km2` del sidecar de procedencia;
- nodos y arcos: `number_of_nodes` y `number_of_edges`, contando arcos
  paralelos;
- km de calle por km2: suma de `length` sobre el grafo no dirigido de OSMnx
  (`ox.convert.to_undirected`, que une los dos sentidos de una calle de doble
  mano en un arco), en km, sobre el area resuelta. Se informa tambien la suma
  sobre los arcos dirigidos;
- longitud media de arco: media de `length` sobre los arcos dirigidos;
- grado medio de entrada y de salida: media de `in_degree` y de `out_degree`
  sobre los nodos (en un grafo dirigido las dos son arcos / nodos);
- mayor componente fuertemente conexa: `nx.strongly_connected_components`.
  Para los nodos fuera de ella, "no la alcanzan" son los que no tienen camino
  hacia ella, y "no son alcanzables desde ella" los que no tienen camino desde
  ella. Un nodo puede estar en los dos grupos, y se cuenta aparte;
- sentido unico: arcos con atributo `oneway` verdadero, sobre los arcos que
  lo traen. Los que no lo traen se cuentan y no se infieren;
- sin `maxspeed`: arcos sin la clave `maxspeed` en el artefacto, antes de que
  OSMnx impute velocidades (B-53). Es la fraccion que el producto imputa.

Sin argumentos caracteriza los cinco partidos de G. Con `--directorio`,
todos los `grafo_*.graphml` de ese directorio (bloque E), y con
`--salida`, otro archivo de resultados. Para cada grafo se informan tambien
la consulta, el nombre completo, el tipo y el identificador de la entidad de
OSM que resolvio la geocodificacion, del sidecar, para verificar que sea el
partido y no una localidad (B-43).

Uso:

    python tools/caracterizar_grafos.py
    python tools/caracterizar_grafos.py --directorio datos/tmp/grafos_universo --salida datos/tmp/metricas/grafos_universo_2026-10-13C.json
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import Any

import networkx as nx
import osmnx as ox

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from tools.condiciones_de_medicion import memoria_fisica_gb

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.caracterizar_grafos")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
POR_PARTIDO = RAIZ / "datos" / "grafos_por_partido"
GRAFOS = {
    "San Isidro": GRAFO_PREDETERMINADO,
    "Lanus": POR_PARTIDO / "grafo_lanus_2026-10-12.graphml",
    "La Matanza": POR_PARTIDO / "grafo_la_matanza_2026-10-12.graphml",
    "Almirante Brown": POR_PARTIDO / "grafo_almirante_brown_2026-10-12.graphml",
    "Tigre": POR_PARTIDO / "grafo_tigre_2026-10-12.graphml",
}
SUFIJO_SIDECAR = ".procedencia.json"
CLASE = "datos reales de OpenStreetMap"
SALIDA = RAIZ / "datos" / "tmp" / "metricas" / "grafos_2026-10-13C.json"
CODIFICACION = "utf-8"
METROS_POR_KM = 1000.0


def _verdadero(valor: Any) -> bool:
    """`oneway` llega como bool de OSMnx; si llegara como texto, se lee igual."""
    if isinstance(valor, str):
        return valor.strip().lower() in {"true", "yes", "1"}
    return bool(valor)


def caracterizar(nombre: str, ruta: pathlib.Path) -> dict[str, Any]:
    sidecar = json.loads(ruta.with_name(ruta.name + SUFIJO_SIDECAR).read_text(encoding=CODIFICACION))
    area = float(sidecar["parametros"]["area_km2"])
    grafo = ox.load_graphml(ruta)
    nodos = grafo.number_of_nodes()
    arcos = grafo.number_of_edges()
    longitudes = [float(d["length"]) for _, _, d in grafo.edges(data=True)]
    sin_longitud = sum(1 for _, _, d in grafo.edges(data=True) if "length" not in d)
    no_dirigido = ox.convert.to_undirected(grafo)
    km_no_dirigido = sum(float(d["length"]) for _, _, d in no_dirigido.edges(data=True)) / METROS_POR_KM

    mayor = max(nx.strongly_connected_components(grafo), key=len)
    representante = next(iter(mayor))
    alcanzan = nx.ancestors(grafo, representante) | {representante}
    alcanzables = nx.descendants(grafo, representante) | {representante}
    fuera = set(grafo.nodes()) - mayor
    no_alcanzan = {n for n in fuera if n not in alcanzan}
    no_alcanzables = {n for n in fuera if n not in alcanzables}

    con_oneway = [d["oneway"] for _, _, d in grafo.edges(data=True) if "oneway" in d]
    sin_maxspeed = sum(1 for _, _, d in grafo.edges(data=True) if "maxspeed" not in d)

    return {
        "partido": nombre,
        "artefacto": ruta.name,
        "herramienta_del_artefacto": sidecar["herramienta"],
        "consulta": sidecar["parametros"].get("consulta"),
        "area_nombre_completo": sidecar["parametros"].get("area_nombre_completo"),
        "area_tipo_osm": sidecar["parametros"].get("area_tipo_osm"),
        "area_id_osm": sidecar["parametros"].get("area_id_osm"),
        "area_resuelta_km2": area,
        "nodos": nodos,
        "arcos": arcos,
        "nodos_por_km2": nodos / area,
        "km_de_calle_no_dirigido": km_no_dirigido,
        "km_de_calle_por_km2": km_no_dirigido / area,
        "km_de_arcos_dirigidos": sum(longitudes) / METROS_POR_KM,
        "arcos_sin_length": sin_longitud,
        "longitud_media_de_arco_m": sum(longitudes) / len(longitudes),
        "grado_medio_de_entrada": sum(d for _, d in grafo.in_degree()) / nodos,
        "grado_medio_de_salida": sum(d for _, d in grafo.out_degree()) / nodos,
        "nodos_en_la_mayor_componente_fuerte": len(mayor),
        "fraccion_en_la_mayor_componente_fuerte": len(mayor) / nodos,
        "componentes_fuertes": nx.number_strongly_connected_components(grafo),
        "nodos_fuera": len(fuera),
        "fuera_que_no_la_alcanzan": len(no_alcanzan),
        "fuera_no_alcanzables_desde_ella": len(no_alcanzables),
        "fuera_en_los_dos_grupos": len(no_alcanzan & no_alcanzables),
        "arcos_con_oneway": len(con_oneway),
        "arcos_sin_oneway": arcos - len(con_oneway),
        "fraccion_de_sentido_unico": sum(1 for v in con_oneway if _verdadero(v)) / len(con_oneway) if con_oneway else None,
        "arcos_sin_maxspeed": sin_maxspeed,
        "fraccion_sin_maxspeed": sin_maxspeed / arcos,
    }


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--directorio", type=pathlib.Path, default=None)
    parser.add_argument("--salida", type=pathlib.Path, default=SALIDA)
    args = parser.parse_args(argumentos)
    grafos = (
        GRAFOS if args.directorio is None
        else {ruta.stem.removeprefix("grafo_"): ruta for ruta in sorted(args.directorio.glob("grafo_*.graphml"))}
    )
    resultados = []
    for nombre, ruta in grafos.items():
        _, libre = memoria_fisica_gb()
        _LOG.info("Memoria fisica libre antes de %s: %s GB", nombre, f"{libre:.2f}" if libre is not None else "sin dato")
        resultado = caracterizar(nombre, ruta)
        resultados.append(resultado)
        _LOG.info("  %s", json.dumps(resultado, ensure_ascii=False))
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_bytes(
        (json.dumps({"clase": CLASE, "partidos": resultados}, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION)
    )
    _LOG.info("Resultados en %s", args.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
