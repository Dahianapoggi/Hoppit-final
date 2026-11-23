"""Candidatos a Deposito por jurisdiccion, solo lectura y sin elegir (sesion
2026-10-13C, bloque F; B-78, B-81, B-73).

- `relevar NOMBRE=GRAFO ...`: por jurisdiccion, consulta en OSM los predios
  `industrial=logistics` dentro del poligono administrativo (el mismo que
  resuelve la extraccion del grafo: "Partido de NOMBRE, Provincia de Buenos
  Aires, Argentina"; para la Ciudad, `--ciudad`). Por predio: elemento e
  identificador de OSM, `name`, `operator` si esta etiquetado, superficie
  (proyectada a UTM), punto representativo, nodo mas cercano dentro de la
  mayor componente fuertemente conexa del grafo de la jurisdiccion y
  distancia en linea recta a ese nodo (haversine). Cuenta aparte los predios
  `landuse=industrial`, solo como conteo (B-73). En la Ciudad, asigna cada
  candidato a su comuna (`admin_level=8`) y barrio (`admin_level=10`) si
  esos limites estan en OSM. No elige ni desempata (B-81) y no escribe
  artefactos de Deposito del producto.
- `dispersion`: propuesta, no se aplica. Para k = 3 a 6, el conjunto de
  candidatos que maximiza la distancia minima entre pares. Ver
  `conjunto_de_maxima_dispersion`.

Resultados en datos/tmp/depositos_candidatos/, con sidecar.

    python tools/candidatos_deposito.py relevar "San Isidro=datos/grafos/grafo_san_isidro_2026-08-16.graphml" ...
    python tools/candidatos_deposito.py relevar --ciudad "Ciudad Autonoma de Buenos Aires=datos/tmp/grafos_universo/grafo_....graphml"
    python tools/candidatos_deposito.py dispersion
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import logging
import math
import pathlib
import random
import sys
from typing import Any

import networkx as nx
import numpy as np
import osmnx as ox

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar
from tools.condiciones_de_medicion import memoria_fisica_gb

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.candidatos_deposito")

RAIZ = pathlib.Path(__file__).resolve().parents[1]
DIRECTORIO = RAIZ / "datos" / "tmp" / "depositos_candidatos"
CANDIDATOS = DIRECTORIO / "candidatos_2026-10-13C.json"
DISPERSION = DIRECTORIO / "dispersion_2026-10-13C.json"
CONTEXTO_PROVINCIA = "Provincia de Buenos Aires, Argentina"
PREFIJO_PARTIDO = "Partido de"
CONTEXTO_CIUDAD = "Argentina"
TAGS_LOGISTICA = {"industrial": ["logistics"]}
TAGS_INDUSTRIAL = {"landuse": ["industrial"]}
ELEMENTOS_CON_SUPERFICIE = {"way", "relation"}
RADIO_TERRESTRE_M = 6_371_009.0
SEMILLA_DE_LA_DISPERSION = 20261013
REINICIOS_DE_LA_HEURISTICA = 200
MAXIMO_DE_COMBINACIONES_EXACTAS = 3_000_000
CODIFICACION = "utf-8"


def _texto(fila: Any, columna: str) -> str | None:
    if columna not in fila:
        return None
    valor = fila[columna]
    return None if valor != valor or valor is None else str(valor)  # NaN no es igual a si mismo


def _haversine(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> Any:
    fi1, fi2 = np.radians(lat1), np.radians(lat2)
    dfi, dla = fi2 - fi1, np.radians(lon2) - np.radians(lon1)
    a = np.sin(dfi / 2) ** 2 + np.cos(fi1) * np.cos(fi2) * np.sin(dla / 2) ** 2
    return 2 * RADIO_TERRESTRE_M * np.arcsin(np.sqrt(a))


def _limites_de_la_ciudad(poligono: Any, nivel: str) -> list[tuple[str, Any]]:
    try:
        limites = ox.features_from_polygon(poligono, tags={"boundary": "administrative", "admin_level": nivel})
    except Exception:  # noqa: BLE001 - sin limites en OSM es un resultado
        return []
    limites = limites[limites.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    if "admin_level" in limites:
        limites = limites[limites["admin_level"] == nivel]
    return [(_texto(fila, "name") or f"{idx[0]}/{idx[1]}", fila.geometry) for idx, fila in limites.iterrows()]


def relevar_jurisdiccion(nombre: str, grafo: pathlib.Path, ciudad: bool) -> dict[str, Any]:
    consulta = f"{nombre}, {CONTEXTO_CIUDAD}" if ciudad else f"{PREFIJO_PARTIDO} {nombre}, {CONTEXTO_PROVINCIA}"
    area = ox.geocode_to_gdf(consulta)
    fila_area = area.iloc[0]
    poligono = fila_area.geometry
    resultado: dict[str, Any] = {
        "jurisdiccion": nombre,
        "consulta": consulta,
        "entidad_osm": f"{fila_area['osm_type']}/{int(fila_area['osm_id'])}",
        "nombre_completo": str(fila_area["display_name"]),
        "grafo": grafo.name,
        "candidatos": [],
    }

    vial = ox.load_graphml(grafo)
    mayor = max(nx.strongly_connected_components(vial), key=len)
    nodos = sorted(mayor)
    latitudes = np.array([float(vial.nodes[n]["y"]) for n in nodos])
    longitudes = np.array([float(vial.nodes[n]["x"]) for n in nodos])

    try:
        logistica = ox.features_from_polygon(poligono, tags=TAGS_LOGISTICA)
    except Exception:  # noqa: BLE001 - la ausencia de resultados la informa OSMnx como excepcion
        logistica = None
    if logistica is not None and not logistica.empty:
        proyectados = logistica.to_crs(logistica.estimate_utm_crs())
        for (elemento, identificador), fila in logistica.iterrows():
            if elemento not in ELEMENTOS_CON_SUPERFICIE:
                continue
            punto = fila.geometry.representative_point()
            distancias = _haversine(punto.y, punto.x, latitudes, longitudes)
            cercano = int(np.argmin(distancias))
            resultado["candidatos"].append(
                {
                    "osm": f"{elemento}/{int(identificador)}",
                    "nombre": _texto(fila, "name"),
                    "operador": _texto(fila, "operator"),
                    "superficie_m2": round(float(proyectados.loc[(elemento, identificador)].geometry.area), 1),
                    "lat": float(punto.y),
                    "lon": float(punto.x),
                    "nodo_en_la_mayor_componente_fuerte": int(nodos[cercano]),
                    "distancia_al_nodo_m": round(float(distancias[cercano]), 1),
                }
            )
    try:
        industriales = ox.features_from_polygon(poligono, tags=TAGS_INDUSTRIAL)
        resultado["predios_landuse_industrial"] = int(
            sum(1 for (elemento, _) in industriales.index if elemento in ELEMENTOS_CON_SUPERFICIE)
        )
    except Exception:  # noqa: BLE001
        resultado["predios_landuse_industrial"] = 0

    if ciudad:
        import shapely.geometry

        for nivel, clave in (("8", "comuna"), ("10", "barrio")):
            limites = _limites_de_la_ciudad(poligono, nivel)
            resultado[f"limites_{clave}_en_osm"] = len(limites)
            for candidato in resultado["candidatos"]:
                punto = shapely.geometry.Point(candidato["lon"], candidato["lat"])
                candidato[clave] = next((n for n, g in limites if g.contains(punto)), None)
    if not resultado["candidatos"]:
        resultado["nota"] = "sin zona verificable con industrial=logistics"
    return resultado


def relevar(asignaciones: list[str], ciudad: bool) -> None:
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    previo = json.loads(CANDIDATOS.read_bytes()) if CANDIDATOS.exists() else {"jurisdicciones": {}}
    for asignacion in asignaciones:
        nombre, ruta = asignacion.split("=", 1)
        _, libre = memoria_fisica_gb()
        _LOG.info("Memoria fisica libre antes de %s: %.2f GB", nombre, libre or float("nan"))
        try:
            relevado = relevar_jurisdiccion(nombre, RAIZ / ruta, ciudad)
        except Exception as error:  # noqa: BLE001 - una falla de una jurisdiccion es un resultado
            relevado = {"jurisdiccion": nombre, "error": f"{type(error).__name__}: {error}"}
        previo["jurisdicciones"][nombre] = relevado
        _LOG.info(
            "%s | %s | candidatos industrial=logistics: %s | landuse=industrial: %s",
            nombre, relevado.get("entidad_osm", relevado.get("error")), len(relevado.get("candidatos", [])),
            relevado.get("predios_landuse_industrial"),
        )
    CANDIDATOS.write_bytes((json.dumps(previo, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    escribir_sidecar(
        registrar(
            artefacto="candidatos_a_deposito",
            ambito="AMBA, universo de B-208 mas la Ciudad",
            fuente="OpenStreetMap via Overpass API",
            herramienta=f"tools/candidatos_deposito.py, osmnx {ox.__version__}",
            parametros={
                "tags": TAGS_LOGISTICA,
                "conteo_aparte": TAGS_INDUSTRIAL,
                "jurisdicciones": sorted(previo["jurisdicciones"]),
                "sha256": hashlib.sha256(CANDIDATOS.read_bytes()).hexdigest(),
            },
        ),
        CANDIDATOS,
    )


def conjunto_de_maxima_dispersion(distancias: np.ndarray, k: int) -> tuple[list[int], str]:
    """Indices del conjunto de k que maximiza la distancia minima entre pares.

    Exacto si las combinaciones no pasan de `MAXIMO_DE_COMBINACIONES_EXACTAS`.
    Si no, heuristica declarada: `REINICIOS_DE_LA_HEURISTICA` arranques con
    `random.Random(20261013)`, cada uno con un candidato inicial al azar,
    agregado voraz del que mas aleja (max-min) y despues intercambios uno por
    uno mientras mejoren la distancia minima; se queda con el mejor, y en
    empate con el de indices ordenados menores."""
    n = len(distancias)

    def minima(conjunto: tuple[int, ...]) -> float:
        return min(distancias[a][b] for a, b in itertools.combinations(conjunto, 2))

    if math.comb(n, k) <= MAXIMO_DE_COMBINACIONES_EXACTAS:
        mejor = max(itertools.combinations(range(n), k), key=lambda c: (minima(c), [-i for i in c]))
        return list(mejor), "exacta"
    generador = random.Random(SEMILLA_DE_LA_DISPERSION)
    mejor_conjunto: tuple[int, ...] = ()
    mejor_valor = -1.0
    for _ in range(REINICIOS_DE_LA_HEURISTICA):
        conjunto = [generador.randrange(n)]
        while len(conjunto) < k:
            conjunto.append(max((c for c in range(n) if c not in conjunto), key=lambda c: min(distancias[c][s] for s in conjunto)))
        mejoro = True
        while mejoro:
            mejoro = False
            valor = minima(tuple(conjunto))
            for posicion, saliente in enumerate(list(conjunto)):
                for entrante in range(n):
                    if entrante in conjunto:
                        continue
                    prueba = conjunto[:posicion] + [entrante] + conjunto[posicion + 1:]
                    if minima(tuple(prueba)) > valor:
                        conjunto, valor, mejoro = prueba, minima(tuple(prueba)), True
                        break
                if mejoro:
                    break
        candidato = tuple(sorted(conjunto))
        valor = minima(candidato)
        if valor > mejor_valor or (valor == mejor_valor and candidato < mejor_conjunto):
            mejor_conjunto, mejor_valor = candidato, valor
    return list(mejor_conjunto), f"heuristica (max-min voraz e intercambios, {REINICIOS_DE_LA_HEURISTICA} arranques, semilla {SEMILLA_DE_LA_DISPERSION})"


def dispersion(grafo_amba: pathlib.Path | None) -> None:
    relevados = json.loads(CANDIDATOS.read_bytes())["jurisdicciones"]
    candidatos = [
        {**c, "jurisdiccion": j}
        for j, datos in sorted(relevados.items())
        for c in datos.get("candidatos", [])
    ]
    if grafo_amba is not None:
        vial = ox.load_graphml(grafo_amba)
        mayor = max(nx.strongly_connected_components(vial), key=len)
        nodos = sorted(mayor)
        latitudes = np.array([float(vial.nodes[n]["y"]) for n in nodos])
        longitudes = np.array([float(vial.nodes[n]["x"]) for n in nodos])
        for c in candidatos:
            d = _haversine(c["lat"], c["lon"], latitudes, longitudes)
            c["nodo_amba"] = int(nodos[int(np.argmin(d))])
        n = len(candidatos)
        distancias = np.zeros((n, n))
        for i, c in enumerate(candidatos):
            largos = nx.single_source_dijkstra_path_length(vial, c["nodo_amba"], weight="length")
            for j, otro in enumerate(candidatos):
                distancias[i][j] = largos.get(otro["nodo_amba"], math.inf)
        # La red es dirigida: entre dos Depositos se toma el menor de los dos
        # sentidos, para que la separacion no dependa de cual se nombra primero.
        distancias = np.minimum(distancias, distancias.T)
        medida = "distancia minima por la red (m), el menor de los dos sentidos"
    else:
        lat = np.array([c["lat"] for c in candidatos])
        lon = np.array([c["lon"] for c in candidatos])
        distancias = _haversine(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
        medida = "distancia en linea recta (m), porque el grafo del AMBA no esta"
    salida: dict[str, Any] = {"medida": medida, "candidatos": len(candidatos), "por_k": {}}
    for k in (3, 4, 5, 6):
        if len(candidatos) < k:
            salida["por_k"][str(k)] = {"nota": f"hay {len(candidatos)} candidatos, menos que k"}
            continue
        indices, metodo = conjunto_de_maxima_dispersion(distancias, k)
        pares = [distancias[a][b] for a, b in itertools.combinations(indices, 2)]
        salida["por_k"][str(k)] = {
            "metodo": metodo,
            "depositos": [
                {key: candidatos[i][key] for key in ("osm", "nombre", "operador", "jurisdiccion")} for i in indices
            ],
            "distancia_minima_m": round(min(pares), 1),
            "distancia_maxima_m": round(max(pares), 1),
            "matriz_m": [[round(float(distancias[a][b]), 1) for b in indices] for a in indices],
        }
        _LOG.info("k=%d | %s | minima %.0f m | maxima %.0f m | %s", k, metodo, min(pares), max(pares),
                  [(candidatos[i]["osm"], candidatos[i]["jurisdiccion"]) for i in indices])
    DISPERSION.write_bytes((json.dumps(salida, ensure_ascii=False, indent=1) + "\n").encode(CODIFICACION))
    escribir_sidecar(
        registrar(
            artefacto="dispersion_de_depositos",
            ambito="AMBA, propuesta sin aplicar",
            fuente=CANDIDATOS.name,
            herramienta="tools/candidatos_deposito.py dispersion",
            parametros={"medida": medida, "semilla": SEMILLA_DE_LA_DISPERSION,
                        "sha256": hashlib.sha256(DISPERSION.read_bytes()).hexdigest()},
        ),
        DISPERSION,
    )


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
    relevar_p = subcomandos.add_parser("relevar")
    relevar_p.add_argument("asignaciones", nargs="+", help="NOMBRE=RUTA_DEL_GRAFO")
    relevar_p.add_argument("--ciudad", action="store_true")
    dispersion_p = subcomandos.add_parser("dispersion")
    dispersion_p.add_argument("--grafo-amba", type=pathlib.Path, default=None)
    args = parser.parse_args(argumentos)
    if args.overpass_url is not None:
        ox.settings.overpass_url = args.overpass_url
    if args.subcomando == "relevar":
        relevar(args.asignaciones, args.ciudad)
    else:
        dispersion(args.grafo_amba)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
