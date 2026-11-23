"""Comparacion de geocodificadores por direccion (RB-10) sobre direcciones de
referencia de la Ciudad (sesion 2026-10-13F, bloque G; versionado en la
sesion 2026-10-15A, S2, desde los scripts `g1_muestra.py`, `g2_consultas.py`
y `g3_medidas.py` del directorio temporal de esa sesion, sin cambiar su
logica). Clase: diagnostico, datos abiertos del GCBA y de OpenStreetMap.

Cifras de B-221 que produce (punto 11): Georef resolvio 90 de 100 a nivel de
altura, mediana 17,2 m a la referencia, 4,4 % a mas de 100 m y 0,8 m al arco
de la red; Nominatim publico 66, mediana 9,0 m, 4,5 % y 10,1 m.

Subcomandos, en orden, sobre un mismo directorio:

- `muestra DIRECTORIO` (G1): 100 direcciones de "Establecimientos
  Educativos" del GCBA (data.buenosaires.gob.ar, CC-BY-2.5-AR), GeoJSON en
  EPSG:9498 (`establecimientos_educativos.geojson` en el directorio),
  reproyectado a WGS84. Son establecimientos, no personas. Un edificio
  (`cui`) aporta una sola direccion: la primera de `dir` (antes de " - "), si
  tiene la forma "calle altura". Estratificada por comuna, con asignacion
  proporcional a los edificios de cada comuna (resto mayor) y la semilla
  20261013. Se agrega "Av. Cabildo 2400" sin coordenadas de referencia.
  Escribe `referencia_g1.json`.
- `consultas DIRECTORIO` (G2): Georef por lote y Nominatim publico, en serie,
  un solo proceso, como maximo 1 consulta por segundo por servicio, con el
  User-Agent del proyecto y cache local de cada respuesta. USIG no se
  consulta. Escribe `respuestas_g2.json` y `cache/`.
- `medidas DIRECTORIO GRAFO` (G3): por servicio, resueltas a nivel de altura
  (Georef: con altura y ubicacion; Nominatim: con `house_number`); distancia
  al punto de referencia (haversine): mediana, p90, maximo y proporcion a
  mas de 100 m, sobre las resueltas a nivel de altura; distancia al arco mas
  cercano del grafo y si ese arco lleva el nombre de la calle consultada; y
  tiempo por consulta, orientativo. Escribe `medidas_g3.json`.

En 13F, G2 envio el User-Agent "HOPPIT-PFI-UADE/0.11 (proyecto academico de
grado; comparacion de geocodificadores, una sola corrida)", sin contacto. La
herramienta usa el exacto de la regla 10 de 2026-10-15A.

    python tools/comparacion_de_geocodificadores.py muestra DIRECTORIO
    python tools/comparacion_de_geocodificadores.py consultas DIRECTORIO
    python tools/comparacion_de_geocodificadores.py medidas DIRECTORIO GRAFO
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import math
import pathlib
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping
from typing import Any, Final

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.comparacion_de_geocodificadores")

CODIFICACION: Final[str] = "utf-8"
SEMILLA: Final[int] = 20261013
TOTAL: Final[int] = 100
FORMA: Final[re.Pattern[str]] = re.compile(r"^(?P<calle>[^\d].*?)\s+(?P<altura>\d{1,5})$")
SEPARADOR_DE_DIRECCIONES: Final[str] = " - "
CRS_DE_LA_FUENTE: Final[str] = "EPSG:9498"
WGS84: Final[str] = "EPSG:4326"

USER_AGENT: Final[str] = "HOPPIT-PFI-UADE/0.11"
GEOREF: Final[str] = "https://apis.datos.gob.ar/georef/api/direcciones"
NOMINATIM: Final[str] = "https://nominatim.openstreetmap.org/search"
PAUSA_S: Final[float] = 1.1
TIEMPO_DE_ESPERA_S: Final[int] = 60
CIUDAD: Final[str] = "Ciudad Autónoma de Buenos Aires"
PROVINCIA_GEOREF_CIUDAD: Final[str] = "02"
DIGITOS_DEL_NOMBRE_EN_CACHE: Final[int] = 16

RADIO_TERRESTRE_M: Final[float] = 6371008.8
UMBRAL_LEJANO_M: Final[float] = 100.0
PERCENTIL_MEDIANA: Final[int] = 50
PERCENTIL_90: Final[int] = 90
VACIAS: Final[frozenset[str]] = frozenset(
    {"av", "avenida", "calle", "pasaje", "pje", "de", "del", "la", "las", "los", "el"}
)
CATEGORIA_DE_DIACRITICO: Final[str] = "Mn"


# --- G1 ---------------------------------------------------------------------


def direccion_de_la_fuente(campo_dir: str | None) -> tuple[str, int] | None:
    """La primera direccion del campo `dir` (antes de " - "), como (calle,
    altura), si tiene la forma "calle altura"; si no, None."""
    primera = str(campo_dir or "").split(SEPARADOR_DE_DIRECCIONES)[0].strip()
    m = FORMA.match(primera)
    if not m:
        return None
    return m["calle"].strip(), int(m["altura"])


def asignacion_proporcional(tamanos: Mapping[int, int], total: int) -> dict[int, int]:
    """Cuotas por estrato proporcionales a su tamano, con el metodo del resto
    mayor; los empates de resto se resuelven por el menor estrato."""
    suma = sum(tamanos.values())
    cuotas = {c: total * n / suma for c, n in tamanos.items()}
    asignado = {c: math.floor(q) for c, q in cuotas.items()}
    for c in sorted(cuotas, key=lambda c: (-(cuotas[c] - asignado[c]), c))[: total - sum(asignado.values())]:
        asignado[c] += 1
    return asignado


def armar_muestra(
    por_comuna: Mapping[int, list[dict[str, Any]]], asignado: Mapping[int, int], semilla: int = SEMILLA
) -> list[dict[str, Any]]:
    """Muestra por comuna, en orden de comuna, de los edificios ordenados por
    `cui`, con un solo generador; claves r001, r002, ..."""
    azar = random.Random(semilla)
    muestra: list[dict[str, Any]] = []
    for c in sorted(por_comuna):
        candidatos = sorted(por_comuna[c], key=lambda e: e["cui"])
        muestra += azar.sample(candidatos, asignado[c])
    for i, e in enumerate(muestra, start=1):
        e["clave"] = f"r{i:03d}"
    return muestra


def muestra(directorio: pathlib.Path) -> None:
    from pyproj import Transformer

    datos = json.loads((directorio / "establecimientos_educativos.geojson").read_text(encoding=CODIFICACION))
    if not datos["crs"]["properties"]["name"].endswith(CRS_DE_LA_FUENTE.split(":")[1]):
        raise ValueError(f"la fuente no esta en {CRS_DE_LA_FUENTE}")
    a_wgs84 = Transformer.from_crs(CRS_DE_LA_FUENTE, WGS84, always_xy=True)

    por_edificio: dict[int, dict[str, Any]] = {}
    descartadas: collections.Counter[str] = collections.Counter()
    for f in sorted(datos["features"], key=lambda f: f["properties"]["id"]):
        p = f["properties"]
        cui = int(p["cui"]) if p.get("cui") is not None else None
        if cui is None or cui in por_edificio:
            descartadas["edificio repetido o sin cui"] += 1
            continue
        direccion = direccion_de_la_fuente(p.get("dir"))
        if direccion is None or f.get("geometry") is None or p.get("com") is None:
            descartadas["direccion sin forma 'calle altura', sin punto o sin comuna"] += 1
            continue
        calle, altura = direccion
        x, y = f["geometry"]["coordinates"]
        lon, lat = a_wgs84.transform(x, y)
        por_edificio[cui] = {
            "cui": cui,
            "id_en_la_fuente": p["id"],
            "direccion": f"{calle} {altura}",
            "calle": calle,
            "altura": altura,
            "comuna": int(p["com"]),
            "barrio": p.get("bar"),
            "lat": lat,
            "lon": lon,
        }

    por_comuna: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for e in por_edificio.values():
        por_comuna[e["comuna"]].append(e)
    asignado = asignacion_proporcional({c: len(v) for c, v in por_comuna.items()}, TOTAL)
    seleccion = armar_muestra(por_comuna, asignado)
    seleccion.append({"clave": "cabildo_2400", "direccion": "Av. Cabildo 2400", "calle": "Av. Cabildo", "altura": 2400,
                      "comuna": None, "lat": None, "lon": None, "nota": "caso de ejemplo, sin coordenadas de referencia"})
    total = sum(len(v) for v in por_comuna.values())
    salida = {
        "fuente": "GCBA, Establecimientos Educativos (data.buenosaires.gob.ar/dataset/establecimientos-educativos), GeoJSON EPSG:9498",
        "licencia": "CC-BY-2.5-AR (segun la API CKAN del portal)",
        "origen_de_las_coordenadas": "campo `sag` de la fuente: 'Mapa Escolar - Ministerio de Educacion'; la fuente no dice si se geocodificaron con USIG",
        "semilla": SEMILLA,
        "edificios_utilizables": total,
        "descartadas": dict(descartadas),
        "asignacion_por_comuna": {str(c): asignado[c] for c in sorted(asignado)},
        "direcciones": seleccion,
    }
    (directorio / "referencia_g1.json").write_text(json.dumps(salida, ensure_ascii=False, indent=1), encoding=CODIFICACION)
    _LOG.info("%d %s %s", total, dict(descartadas), salida["asignacion_por_comuna"])


# --- G2 ---------------------------------------------------------------------


def lote_georef(direcciones: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Un solo pedido por lote, maximo 1 resultado por direccion, provincia
    02 (la Ciudad)."""
    return {
        "direcciones": [
            {"direccion": d["direccion"], "provincia": PROVINCIA_GEOREF_CIUDAD, "max": 1} for d in direcciones
        ]
    }


def consulta_nominatim(direccion: str) -> tuple[str, str]:
    """URL de la consulta en texto libre, con la Ciudad y el pais, y el
    nombre de su archivo en la cache."""
    q = f"{direccion}, {CIUDAD}, Argentina"
    url = NOMINATIM + "?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 1, "countrycodes": "ar"}
    )
    nombre = "nominatim_" + hashlib.sha1(q.encode(CODIFICACION)).hexdigest()[:DIGITOS_DEL_NOMBRE_EN_CACHE]
    return url, nombre


def _pedir(url: str, cuerpo: dict[str, Any] | None = None) -> tuple[Any, float]:
    datos = json.dumps(cuerpo).encode(CODIFICACION) if cuerpo is not None else None
    pedido = urllib.request.Request(url, data=datos, headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    inicio = time.perf_counter()
    with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_S) as r:
        contenido = json.loads(r.read().decode(CODIFICACION))
    return contenido, time.perf_counter() - inicio


def _en_cache(cache: pathlib.Path, nombre: str, url: str, cuerpo: dict[str, Any] | None = None) -> dict[str, Any]:
    archivo = cache / f"{nombre}.json"
    if archivo.exists():
        return dict(json.loads(archivo.read_text(encoding=CODIFICACION)))
    contenido, segundos = _pedir(url, cuerpo)
    registro = {"url": url, "cuerpo": cuerpo, "segundos": segundos, "momento": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "respuesta": contenido}
    archivo.write_text(json.dumps(registro, ensure_ascii=False), encoding=CODIFICACION)
    time.sleep(PAUSA_S)
    return registro


def consultas(directorio: pathlib.Path) -> None:
    cache = directorio / "cache"
    cache.mkdir(exist_ok=True)
    referencia = json.loads((directorio / "referencia_g1.json").read_text(encoding=CODIFICACION))["direcciones"]
    lote = lote_georef(referencia)
    georef = _en_cache(cache, "georef_lote", GEOREF, lote)
    _LOG.info("georef %.2f s para %d", georef["segundos"], len(lote["direcciones"]))
    nominatim = {}
    for d in referencia:
        url, nombre = consulta_nominatim(d["direccion"])
        nominatim[d["clave"]] = _en_cache(cache, nombre, url)
    _LOG.info("nominatim %d", len(nominatim))
    (directorio / "respuestas_g2.json").write_text(
        json.dumps({"georef": georef, "nominatim": nominatim}, ensure_ascii=False), encoding=CODIFICACION
    )


# --- G3 ---------------------------------------------------------------------


def palabras(texto: str) -> set[str]:
    """Palabras sin tildes, en minusculas, sin signos ni palabras vacias."""
    t = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != CATEGORIA_DE_DIACRITICO
    )
    return {p for p in re.split(r"[^a-z0-9]+", t) if p and p not in VACIAS}


def coincide(consulta: str, nombre: Any) -> bool:
    """Si las palabras de la calle consultada contienen a las del nombre del
    arco, o al reves. El nombre del arco puede ser texto o lista."""
    nombres = nombre if isinstance(nombre, list) else [nombre] if isinstance(nombre, str) else []
    q = palabras(consulta)
    return any(q and (q <= palabras(n) or (palabras(n) and palabras(n) <= q)) for n in nombres)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * RADIO_TERRESTRE_M * math.asin(math.sqrt(a))


def medidas(directorio: pathlib.Path, grafo: pathlib.Path) -> None:
    import geopandas as gpd
    import numpy as np
    import osmnx as ox
    from shapely.geometry import Point

    referencia = json.loads((directorio / "referencia_g1.json").read_text(encoding=CODIFICACION))["direcciones"]
    respuestas = json.loads((directorio / "respuestas_g2.json").read_text(encoding=CODIFICACION))
    georef = respuestas["georef"]["respuesta"]["resultados"]
    segundos_georef = respuestas["georef"]["segundos"] / len(georef)

    puntos: dict[str, dict[str, dict[str, Any]]] = {"georef": {}, "nominatim": {}}
    for d, r in zip(referencia, georef):
        dirs = r.get("direcciones") or []
        u = dirs[0]["ubicacion"] if dirs else None
        altura = bool(dirs) and dirs[0]["altura"]["valor"] is not None and u and u["lat"] is not None
        puntos["georef"][d["clave"]] = {
            "altura": bool(altura),
            "lat": u["lat"] if u else None,
            "lon": u["lon"] if u else None,
            "devuelto": dirs[0]["nomenclatura"] if dirs else None,
            "segundos": segundos_georef,
        }
    for d in referencia:
        registro = respuestas["nominatim"][d["clave"]]
        res = registro["respuesta"]
        primero = res[0] if res else None
        puntos["nominatim"][d["clave"]] = {
            "altura": bool(primero and "house_number" in primero.get("address", {})),
            "lat": float(primero["lat"]) if primero else None,
            "lon": float(primero["lon"]) if primero else None,
            "devuelto": primero["display_name"] if primero else None,
            "segundos": registro["segundos"],
        }

    g = ox.load_graphml(grafo)
    gp = ox.project_graph(g)
    crs = gp.graph["crs"]
    resultado = {}
    for servicio, por_clave in puntos.items():
        claves = [d["clave"] for d in referencia if d.get("lat") is not None]
        con_punto = [k for k in claves if por_clave[k]["lat"] is not None]
        if con_punto:
            serie = gpd.GeoSeries([Point(por_clave[k]["lon"], por_clave[k]["lat"]) for k in con_punto], crs=WGS84).to_crs(crs)
            aristas, distancias = ox.distance.nearest_edges(gp, serie.x.to_numpy(), serie.y.to_numpy(), return_dist=True)
            for k, arista, dist in zip(con_punto, aristas, distancias):
                nombre = gp.edges[arista].get("name")
                d = next(x for x in referencia if x["clave"] == k)
                por_clave[k]["dist_al_arco_m"] = float(dist)
                por_clave[k]["nombre_del_arco"] = nombre
                por_clave[k]["arco_con_el_nombre_consultado"] = coincide(d["calle"], nombre)
        referencias = {d["clave"]: d for d in referencia}
        altura = [k for k in claves if por_clave[k]["altura"]]
        dist = [haversine(referencias[k]["lat"], referencias[k]["lon"], por_clave[k]["lat"], por_clave[k]["lon"]) for k in altura]
        for k, x in zip(altura, dist):
            por_clave[k]["dist_a_la_referencia_m"] = x
        al_arco = [por_clave[k]["dist_al_arco_m"] for k in altura]
        resultado[servicio] = {
            "consultadas": len(claves),
            "resueltas_a_nivel_de_altura": len(altura),
            "proporcion_a_nivel_de_altura": len(altura) / len(claves),
            "con_algun_punto": len(con_punto),
            "dist_a_la_referencia_m": {
                "mediana": float(np.percentile(dist, PERCENTIL_MEDIANA)), "p90": float(np.percentile(dist, PERCENTIL_90)),
                "maximo": max(dist),
            },
            "proporcion_a_mas_de_100_m": sum(1 for x in dist if x > UMBRAL_LEJANO_M) / len(dist),
            "dist_al_arco_m": {
                "mediana": float(np.percentile(al_arco, PERCENTIL_MEDIANA)), "p90": float(np.percentile(al_arco, PERCENTIL_90)),
                "maximo": max(al_arco),
            },
            "arco_con_el_nombre_consultado": sum(1 for k in altura if por_clave[k]["arco_con_el_nombre_consultado"]) / len(altura),
            "segundos_por_consulta_orientativo": {
                "mediana": float(np.median([por_clave[k]["segundos"] for k in claves])),
                "nota": "Georef: el lote de 101 en un pedido, dividido por 101" if servicio == "georef" else "un pedido por direccion",
            },
            "av_cabildo_2400": puntos[servicio]["cabildo_2400"],
        }
    (directorio / "medidas_g3.json").write_text(
        json.dumps({"resumen": resultado, "por_direccion": puntos}, ensure_ascii=False, indent=1, default=str),
        encoding=CODIFICACION,
    )
    _LOG.info("%s", json.dumps(resultado, ensure_ascii=False, indent=1, default=str))


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = lector.add_subparsers(dest="paso", required=True)
    sub.add_parser("muestra").add_argument("directorio", type=pathlib.Path)
    sub.add_parser("consultas").add_argument("directorio", type=pathlib.Path)
    p_medidas = sub.add_parser("medidas")
    p_medidas.add_argument("directorio", type=pathlib.Path)
    p_medidas.add_argument("grafo", type=pathlib.Path)
    args = lector.parse_args(argumentos)
    if args.paso == "muestra":
        muestra(args.directorio)
    elif args.paso == "consultas":
        consultas(args.directorio)
    else:
        medidas(args.directorio, args.grafo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
