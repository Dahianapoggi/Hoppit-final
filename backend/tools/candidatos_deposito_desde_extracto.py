"""Candidatos a Deposito en CABA y partidos limitrofes desde el extracto de
Geofabrik, solo lectura, sin elegir ni desempatar (B-81) (sesion 2026-10-13F,
bloque F; versionado en la sesion 2026-10-15A, S2, desde el script
`f_candidatos.py` del directorio temporal de esa sesion, sin cambiar su
logica).

No produjo ninguna cifra de B-221: en 13F no se corrio, por memoria (B-221,
punto 10). Procesar el extracto de Argentina exige 4 GB libres, comprobados
en una orden aparte.

- Predios `industrial=logistics` (areas: vias cerradas y multipoligonos):
  identificador, nombre, operador, superficie, comuna o partido (el que
  contiene su punto representativo) y distancia en linea recta (del borde del
  predio, 0 si se tocan) a la Avenida General Paz y al Riachuelo.
- Conteo aparte de `landuse=industrial` por jurisdiccion, con su superficie.
- Jurisdicciones: los limites `boundary=administrative` del extracto: la
  Ciudad (relacion 1224652), sus comunas ("Comuna N" dentro de la Ciudad) y
  los partidos Vicente Lopez, General San Martin, Tres de Febrero, La
  Matanza, Lanus y Avellaneda ("Partido de ...").
- General Paz: vias con `highway` y nombre que contiene "General Paz".
  Riachuelo: vias `waterway` con nombre que contiene "Riachuelo".

Necesita pyosmium (entorno de la extraccion, no el del proyecto).

    python tools/candidatos_deposito_desde_extracto.py PBF DIRECTORIO
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import logging
import pathlib
import re
import sys
import time
import unicodedata
from typing import Any, Final

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.candidatos_deposito_desde_extracto")

PARTIDOS: Final[tuple[str, ...]] = (
    "Vicente López", "General San Martín", "Tres de Febrero", "La Matanza", "Lanús", "Avellaneda",
)
CIUDAD: Final[int] = 1224652
UTM_ZONA_21S: Final[str] = "EPSG:32721"
WGS84: Final[str] = "EPSG:4326"
COMUNA: Final[re.Pattern[str]] = re.compile(r"Comuna \d{1,2}")
CATEGORIA_DE_DIACRITICO: Final[str] = "Mn"
CODIFICACION: Final[str] = "utf-8"
ETIQUETAS_DEL_FILTRO: Final[tuple[tuple[str, str], ...]] = (
    ("industrial", "logistics"), ("landuse", "industrial"), ("boundary", "administrative"),
    ("waterway", "river"), ("waterway", "canal"), ("waterway", "riverbank"),
    ("highway", "motorway"), ("highway", "trunk"), ("highway", "primary"), ("highway", "motorway_link"),
    ("highway", "trunk_link"),
)


def sin_tildes(t: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != CATEGORIA_DE_DIACRITICO
    ).lower()


def es_comuna(nombre: str) -> bool:
    """Si el nombre de un limite tiene la forma "Comuna N" (la condicion de
    estar dentro de la Ciudad se verifica aparte, con la geometria)."""
    return COMUNA.fullmatch(nombre) is not None


def partido_del_limite(nombre: str) -> str | None:
    """El partido limitrofe cuyo nombre, sin tildes ni mayusculas, es
    "Partido de <partido>", o None."""
    for partido in PARTIDOS:
        if sin_tildes(nombre) == sin_tildes(f"Partido de {partido}"):
            return partido
    return None


def main(argumentos: list[str]) -> int:
    import osmium
    import shapely
    from pyproj import Transformer
    from shapely.ops import transform, unary_union

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("pbf", type=pathlib.Path)
    lector.add_argument("directorio", type=pathlib.Path)
    args = lector.parse_args(argumentos)
    pbf, directorio = args.pbf, args.directorio
    directorio.mkdir(parents=True, exist_ok=True)
    utm = Transformer.from_crs(WGS84, UTM_ZONA_21S, always_xy=True).transform

    fabrica = osmium.geom.WKBFactory()
    inicio = time.time()
    logistica: list[dict[str, Any]] = []
    industrial: list[dict[str, Any]] = []
    limites: dict[int, dict[str, Any]] = {}
    lineas: dict[str, list[Any]] = {"General Paz": [], "Riachuelo": []}
    filtro = osmium.filter.TagFilter(*ETIQUETAS_DEL_FILTRO)
    for obj in osmium.FileProcessor(str(pbf)).with_areas(filtro).with_filter(filtro):
        if obj.is_node():
            continue
        tags = {t.k: t.v for t in obj.tags}
        nombre = tags.get("name", "")
        if obj.is_way():
            if "highway" in tags and "general paz" in sin_tildes(nombre):
                lineas["General Paz"].append(shapely.from_wkb(fabrica.create_linestring(obj)))
            elif "waterway" in tags and "riachuelo" in sin_tildes(nombre):
                lineas["Riachuelo"].append(shapely.from_wkb(fabrica.create_linestring(obj)))
            continue
        if not obj.is_area():
            continue
        try:
            geom = shapely.from_wkb(fabrica.create_multipolygon(obj))
        except RuntimeError:
            continue
        origen = ("way" if obj.from_way() else "relation", obj.orig_id())
        if tags.get("boundary") == "administrative" and origen[0] == "relation":
            limites[origen[1]] = {"nombre": nombre, "admin_level": tags.get("admin_level"), "geom": geom}
        if tags.get("industrial") == "logistics":
            logistica.append({"osm": f"{origen[0]}/{origen[1]}", "nombre": nombre, "operador": tags.get("operator", ""),
                              "landuse": tags.get("landuse", ""), "geom": geom})
        if tags.get("landuse") == "industrial":
            industrial.append({"osm": f"{origen[0]}/{origen[1]}", "geom": geom})
    _LOG.info(
        "pasada: %.0f s | logistica %d | industrial %d | limites %d | general paz %d | riachuelo %d",
        time.time() - inicio, len(logistica), len(industrial), len(limites),
        len(lineas["General Paz"]), len(lineas["Riachuelo"]),
    )

    ciudad = limites[CIUDAD]["geom"]
    jurisdicciones: dict[str, tuple[int, Any]] = {}
    for rid, limite in limites.items():
        n = limite["nombre"]
        if es_comuna(n) and ciudad.contains(limite["geom"].representative_point()):
            jurisdicciones[f"CABA, {n}"] = (rid, limite["geom"])
        partido = partido_del_limite(n)
        if partido is not None:
            jurisdicciones[f"Partido de {partido}"] = (rid, limite["geom"])
    faltan = [p for p in PARTIDOS if f"Partido de {p}" not in jurisdicciones]
    comunas = sorted(k for k in jurisdicciones if k.startswith("CABA"))

    def jurisdiccion(geom: Any) -> str | None:
        punto = geom.representative_point()
        for nombre_j, (_, g) in jurisdicciones.items():
            if g.contains(punto):
                return nombre_j
        return "CABA, sin comuna" if ciudad.contains(punto) else None

    paz = transform(utm, unary_union(lineas["General Paz"])) if lineas["General Paz"] else None
    riachuelo = transform(utm, unary_union(lineas["Riachuelo"])) if lineas["Riachuelo"] else None
    filas = []
    for c in logistica:
        j = jurisdiccion(c["geom"])
        if j is None:
            continue
        g = transform(utm, c["geom"])
        filas.append({
            "osm": c["osm"], "nombre": c["nombre"], "operador": c["operador"], "landuse": c["landuse"],
            "superficie_m2": round(g.area), "jurisdiccion": j,
            "dist_general_paz_m": round(g.distance(paz)) if paz is not None else None,
            "dist_riachuelo_m": round(g.distance(riachuelo)) if riachuelo is not None else None,
        })
    filas.sort(key=lambda f: (f["jurisdiccion"], f["osm"]))
    conteo: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"predios": 0, "superficie_m2": 0})
    for i in industrial:
        j = jurisdiccion(i["geom"])
        if j is None:
            continue
        clave = "CABA" if j.startswith("CABA") else j
        conteo[clave]["predios"] += 1
        conteo[clave]["superficie_m2"] += round(transform(utm, i["geom"]).area)

    with (directorio / "industrial_logistics.csv").open("w", encoding=CODIFICACION, newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]) if filas else ["osm"], lineterminator="\n")
        w.writeheader()
        w.writerows(filas)
    resumen = {
        "extracto": pbf.name,
        "jurisdicciones_encontradas": sorted(jurisdicciones),
        "partidos_no_encontrados": faltan,
        "comunas_encontradas": len(comunas),
        "lineas": {k: len(v) for k, v in lineas.items()},
        "industrial_logistics_por_jurisdiccion": dict(collections.Counter(
            f["jurisdiccion"].split(",")[0] if f["jurisdiccion"].startswith("CABA") else f["jurisdiccion"] for f in filas
        )),
        "landuse_industrial_por_jurisdiccion": dict(sorted(conteo.items())),
        "segundos_orientativos": round(time.time() - inicio),
    }
    (directorio / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1), encoding=CODIFICACION)
    _LOG.info("%s", json.dumps(resumen, ensure_ascii=False, indent=1))
    for fila in filas:
        _LOG.info("%s", fila)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
