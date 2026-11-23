"""Grafo vial "drive" desde un extracto .osm.pbf, recortado al limite de una
relacion de OSM: la via B de B-221 (sesion 2026-10-13F, bloques E2 y E3;
versionado en la sesion 2026-10-15A, S2, desde el script `e2_via_b.py` del
directorio temporal de esa sesion, sin cambiar su logica).

Cifras de B-221 que produce: el grafo de CABA por la via B (17.926 nodos y
37.253 arcos, desde el extracto de Geofabrik `argentina-261013`), comparado
con la via A por `tools/comparar_grafos_viales.py` (B-221, punto 10). E3, el
control con San Isidro, no se completo.

Repite los pasos de `ox.graph_from_polygon` de OSMnx 2.0.7 salvo la descarga.
Filtro de vias: el de la red "drive" de OSMnx 2.0.7, tomado de
`osmnx._overpass._get_network_filter("drive")` y aplicado con la semantica de
Overpass (`tools/filtro_de_vias_overpass.py`). Solo vias (`way`), como la
consulta de OSMnx.

Pasos, en el orden de `graph_from_polygon`:
 1. Poligono de la relacion (Nominatim, por identificador) y el mismo
    poligono con 500 m de buffer, en UTM.
 2. Primera pasada por el extracto: nodos dentro del rectangulo del buffer y
    vias que pasan el filtro con algun nodo en ese rectangulo; despues, solo
    las vias con algun nodo dentro del poligono con buffer (Overpass toma las
    vias que tienen nodos dentro del poligono de la consulta).
 3. Segunda pasada: escribe en XML los nodos de esas vias (con sus etiquetas)
    y las vias.
 4. `ox.graph_from_xml(bidirectional=False, simplify=False, retain_all=True)`,
    truncado al poligono con buffer, mayor componente debil, simplificado,
    truncado al poligono, mayor componente debil y `street_count` contado
    sobre el grafo con buffer.

Necesita OSMnx 2.0.7 y pyosmium (4.3.1 en 13F), en el entorno aparte de la
extraccion, no en el del proyecto. Procesar el extracto de Argentina exige
memoria: solo con 4 GB libres, comprobados en una orden aparte (B-222, punto
7). Una pasada por los nodos tardo unos 11 minutos en el equipo Windows.

    python tools/grafo_vial_desde_extracto.py PBF RELACION NOMBRE DIRECTORIO_SALIDA CACHE_OSMNX
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import pathlib
import sys
import time
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import networkx as nx
import osmium
import osmnx as ox
import shapely
from osmnx import _overpass, projection, simplification, stats, truncate

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.filtro_de_vias_overpass import Regla, pasa, reglas_del_filtro

FORMATO_LOG: Final[str] = "%(asctime)s %(message)s"
_LOG = logging.getLogger("hoppit.tools.grafo_vial_desde_extracto")

BUFFER_M: Final[int] = 500
TIPO_DE_RED: Final[str] = "drive"
BLOQUE_HASH: Final[int] = 1 << 20
METROS_CUADRADOS_POR_KM2: Final[float] = 1e6
BYTES_POR_MB: Final[int] = 2**20
DECIMALES_AREA: Final[int] = 2
DECIMALES_SEGUNDOS: Final[int] = 1
# Distribucion de pyosmium en el indice de paquetes, para leer su version del
# entorno (sesion 2026-10-15B, bloque G; antes se anotaba fija la de 13F).
DISTRIBUCION_DE_PYOSMIUM: Final[str] = "osmium"
SIN_DATO_DE_VERSION: Final[str] = "sin dato"


def version_de_pyosmium() -> str:
    """La version instalada: de la metadata del paquete y, si no esta, del
    modulo; si ninguno la expone, "sin dato" (no una version supuesta)."""
    from importlib import metadata

    try:
        return metadata.version(DISTRIBUCION_DE_PYOSMIUM)
    except metadata.PackageNotFoundError:
        return str(getattr(osmium, "__version__", SIN_DATO_DE_VERSION))


class PrimeraPasada(osmium.SimpleHandler):  # type: ignore[misc]
    """Nodos dentro del rectangulo y vias que pasan el filtro con algun nodo
    en el."""

    def __init__(self, caja: tuple[float, float, float, float], reglas: list[Regla]) -> None:
        super().__init__()
        self.caja = caja
        self.reglas = reglas
        self.nodos: dict[int, tuple[float, float]] = {}
        self.vias: dict[int, list[int]] = {}

    def node(self, n: Any) -> None:
        if not n.location.valid():
            return
        lon, lat = n.location.lon, n.location.lat
        x0, y0, x1, y1 = self.caja
        if x0 <= lon <= x1 and y0 <= lat <= y1:
            self.nodos[n.id] = (lon, lat)

    def way(self, w: Any) -> None:
        if "highway" not in w.tags:
            return
        refs = [nd.ref for nd in w.nodes]
        if not any(r in self.nodos for r in refs):
            return
        if pasa({t.k: t.v for t in w.tags}, self.reglas):
            self.vias[w.id] = refs


class SegundaPasada(osmium.SimpleHandler):  # type: ignore[misc]
    """Escribe los nodos y las vias elegidos en la primera pasada."""

    def __init__(self, nodos: set[int], vias: set[int], escritor: Any) -> None:
        super().__init__()
        self.nodos, self.vias, self.escritor = nodos, vias, escritor

    def node(self, n: Any) -> None:
        if n.id in self.nodos:
            self.escritor.add_node(n)

    def way(self, w: Any) -> None:
        if w.id in self.vias:
            self.escritor.add_way(w)


def sha256(ruta: pathlib.Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(BLOQUE_HASH), b""):
            h.update(bloque)
    return h.hexdigest()


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("pbf", type=pathlib.Path)
    lector.add_argument("relacion", help="identificador de la relacion de OSM")
    lector.add_argument("nombre", help="nombre del ambito para el artefacto")
    lector.add_argument("salida", type=pathlib.Path, help="directorio de salida")
    lector.add_argument("cache", help="directorio de cache de OSMnx")
    args = lector.parse_args(argumentos)
    pbf_p, salida_p, relacion, nombre = args.pbf, args.salida, args.relacion, args.nombre
    salida_p.mkdir(parents=True, exist_ok=True)
    ox.settings.cache_folder = args.cache
    inicio = time.time()

    filtro = _overpass._get_network_filter(TIPO_DE_RED)
    reglas = reglas_del_filtro(filtro)
    _LOG.info("Filtro drive de OSMnx %s: %s", ox.__version__, filtro)

    area = ox.geocode_to_gdf(f"R{relacion}", by_osmid=True)
    poligono = area.iloc[0]["geometry"]
    poly_proj, crs_utm = projection.project_geometry(poligono)
    poly_buff, _ = projection.project_geometry(poly_proj.buffer(BUFFER_M), crs=crs_utm, to_latlong=True)
    _LOG.info("Relacion %s: %s | rectangulo con buffer %s", relacion, area.iloc[0]["display_name"], poly_buff.bounds)

    p1 = PrimeraPasada(poly_buff.bounds, reglas)
    p1.apply_file(str(pbf_p))
    ids = list(p1.nodos)
    xs = [p1.nodos[i][0] for i in ids]
    ys = [p1.nodos[i][1] for i in ids]
    dentro = {i for i, ok in zip(ids, shapely.contains_xy(poly_buff, xs, ys)) if ok}
    vias = {v for v, refs in p1.vias.items() if any(r in dentro for r in refs)}
    nodos_necesarios = {r for v in vias for r in p1.vias[v]}
    _LOG.info(
        "Primera pasada: %d nodos en el rectangulo, %d dentro del buffer, %d vias candidatas, %d vias, %d nodos de esas vias",
        len(p1.nodos), len(dentro), len(p1.vias), len(vias), len(nodos_necesarios),
    )

    xml = salida_p / f"recorte_{relacion}.osm"
    if xml.exists():
        xml.unlink()
    escritor = osmium.SimpleWriter(str(xml))
    SegundaPasada(nodos_necesarios, vias, escritor).apply_file(str(pbf_p))
    escritor.close()

    g_buff = ox.graph_from_xml(xml, bidirectional=False, simplify=False, retain_all=True)
    g_buff = truncate.truncate_graph_polygon(g_buff, poly_buff, truncate_by_edge=False)
    g_buff = truncate.largest_component(g_buff, strongly=False)
    g_buff = simplification.simplify_graph(g_buff)
    g = truncate.truncate_graph_polygon(g_buff, poligono, truncate_by_edge=False)
    g = truncate.largest_component(g, strongly=False)
    nx.set_node_attributes(g, values=stats.count_streets_per_node(g_buff, nodes=g.nodes), name="street_count")
    _LOG.info(
        "Grafo via B de %s: %d nodos, %d arcos, %d componentes fuertes",
        nombre, g.number_of_nodes(), g.number_of_edges(), nx.number_strongly_connected_components(g),
    )

    destino = salida_p / f"grafo_{nombre}_via_b.graphml"
    ox.save_graphml(g, destino)
    area_km2 = round(float(area.to_crs(area.estimate_utm_crs()).area.iloc[0]) / METROS_CUADRADOS_POR_KM2, DECIMALES_AREA)
    version_osmium = version_de_pyosmium()
    escribir_sidecar(
        registrar(
            artefacto="grafo",
            ambito=nombre,
            fuente=f"extracto de Geofabrik {pbf_p.name} (SHA-256 {sha256(pbf_p)})",
            herramienta=(
                f"osmnx {ox.__version__} (graph_from_xml) y pyosmium {version_osmium}, "
                "tools/grafo_vial_desde_extracto.py (antes e2_via_b.py de la sesion 2026-10-13F)"
            ),
            parametros={
                "via": "B, extracto descargado",
                "consulta": f"R{relacion}",
                "tipo_red": TIPO_DE_RED,
                "filtro_de_vias": filtro,
                "semantica_del_filtro": "la de Overpass: clave presente; !~ = clave ausente o valor que no contiene la expresion (sin anclar); solo vias",
                "pasos": "buffer de 500 m, vias con algun nodo en el buffer, graph_from_xml sin simplificar, truncado al buffer, mayor componente debil, simplificado, truncado al poligono, mayor componente debil, street_count sobre el grafo con buffer",
                "simplificado": True,
                "area_nombre_completo": str(area.iloc[0]["display_name"]),
                "area_id_osm": int(area.iloc[0]["osm_id"]),
                "area_km2": area_km2,
                "nodos": g.number_of_nodes(),
                "arcos": g.number_of_edges(),
                "sha256": sha256(destino),
                "segundos_orientativos": round(time.time() - inicio, DECIMALES_SEGUNDOS),
                "momento_local": dt.datetime.now().isoformat(timespec="seconds"),
            },
        ),
        destino,
    )
    _LOG.info("Escrito %s (%.1f MB) en %.0f s", destino, destino.stat().st_size / BYTES_POR_MB, time.time() - inicio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
