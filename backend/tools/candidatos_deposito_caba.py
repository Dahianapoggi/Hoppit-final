"""Verificacion de los candidatos a Deposito de CABA y de partidos limitrofes
propuestos por un relevamiento web (sesion 2026-10-15A, bloque F; insumo de
la decision de Depositos, B-220, punto 7). Solo lectura: no elige ni
desempata (B-81) y no escribe artefactos de Deposito del producto. Clase:
datos reales de OpenStreetMap y de Georef.

- F1. Para cada objeto de OSM declarado, sus etiquetas y su geometria, de la
  API de OpenStreetMap (`/api/0.6/way/<id>/full.json`), y la comparacion con
  la etiqueta declarada. Ubicacion del objeto: el centroide del poligono de
  la via cerrada (en grados; para un predio la diferencia con el centroide
  proyectado es despreciable).
- F2. Cada direccion, geocodificada con Georef
  (`https://apis.datos.gob.ar/georef/api/direcciones`) en un solo pedido por
  lote, con el parametro de provincia que corresponde (02, la Ciudad; 06,
  Buenos Aires) y un resultado por direccion; coordenadas devueltas y
  distancia a las declaradas. Una direccion que Georef no resuelve se
  registra como tal.
- F3. Con las coordenadas mejor respaldadas (el objeto de OSM; si no hay,
  Georef, solo si la direccion que devuelve esta en la jurisdiccion
  declarada (un partido distinto no respalda al candidato: con solo el
  parametro de provincia, Georef puede elegir la misma calle en otro
  partido); si no, las declaradas, con la fuente indicada): si caen dentro del
  poligono de la Ciudad con el que se extrajo el grafo de la via A (la
  respuesta de Nominatim para la relacion 1224652, guardada en la extraccion);
  el nodo mas cercano de la mayor componente fuertemente conexa del grafo de
  la via A y su distancia (haversine); y, para las que caen afuera, la
  distancia al limite de la Ciudad (en UTM 21S).
- F4. Una consulta a Overpass con fecha fija (2026-10-13T00:00Z), acotada al
  rectangulo de la Ciudad: objetos con `industrial=logistics` (con geometria)
  y con `landuse=industrial` (con su centro). Se cuentan los que tienen el
  centro dentro del poligono de la Ciudad, con sus identificadores. Un
  intento y un reintento.
- F5. Contraste con la regla vigente del Deposito (B-78, B-81): el predio
  tiene que llevar `industrial=logistics`. Un candidato la cumpliria si su
  objeto declarado la lleva o si su ubicacion cae dentro de un predio con esa
  etiqueta de F4; fuera de la Ciudad, F4 no cubre, y sin objeto declarado
  queda sin verificar.

Red: User-Agent del proyecto sin contacto; las respuestas se guardan en el
directorio indicado y no se vuelven a pedir. Las etiquetas de contacto de los
objetos de OSM (`contact:*`, `email`, `phone`, `fax`, `website` y similares)
se quitan antes de guardar nada, en las respuestas y en el resultado: no se
escriben datos de contacto en ningun archivo (regla 10 de la sesion). Desde
la sesion 2026-10-15B tambien se quitan los campos de identidad de quien
edito cada elemento (`user`, `uid`), que la API de OpenStreetMap incluye. Cargar el grafo de CABA exige 6
GB libres, comprobados en una orden aparte.

    python tools/candidatos_deposito_caba.py --grafo GRAFO --poligono NOMINATIM.json --cache DIR --salida SALIDA.json
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.amazon_2021 import pico_del_proceso_mb
from tools.comparacion_de_geocodificadores import haversine

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.candidatos_deposito_caba")

USER_AGENT: Final[str] = "HOPPIT-PFI-UADE/0.11"
API_OSM_VIA: Final[str] = "https://api.openstreetmap.org/api/0.6/way/{identificador}/full.json"
GEOREF: Final[str] = "https://apis.datos.gob.ar/georef/api/direcciones"
OVERPASS: Final[str] = "https://overpass-api.de/api/interpreter"
FECHA_DE_OVERPASS: Final[str] = "2026-10-13T00:00:00Z"
TIEMPO_DE_OVERPASS_S: Final[int] = 90
INTENTOS: Final[int] = 2
PAUSA_ENTRE_INTENTOS_S: Final[float] = 5.0
PAUSA_ENTRE_PEDIDOS_S: Final[float] = 1.1
TIEMPO_DE_ESPERA_S: Final[int] = 120
PROVINCIA_CIUDAD: Final[str] = "02"
PROVINCIA_BUENOS_AIRES: Final[str] = "06"
UTM_ZONA_21S: Final[str] = "EPSG:32721"
WGS84: Final[str] = "EPSG:4326"
ETIQUETA_DE_LA_REGLA: Final[tuple[str, str]] = ("industrial", "logistics")
ETIQUETA_INDUSTRIAL: Final[tuple[str, str]] = ("landuse", "industrial")
CODIFICACION: Final[str] = "utf-8"
DECIMALES_M: Final[int] = 1
PREFIJOS_DE_CONTACTO: Final[tuple[str, ...]] = ("contact:", "email", "phone", "fax", "mobile", "website", "url",
                                               "facebook", "instagram", "twitter")
SUFIJOS_DE_CONTACTO: Final[tuple[str, ...]] = (":email", ":phone", ":fax", ":mobile", ":website", ":url")
CAMPOS_DE_IDENTIDAD: Final[tuple[str, ...]] = ("user", "uid")


@dataclasses.dataclass(frozen=True)
class Candidato:
    """Un candidato tal como lo declara el relevamiento web."""

    identificador: str
    nombre: str
    direccion: str
    provincia_georef: str
    jurisdiccion_declarada: str
    coordenadas_declaradas: tuple[float, float] | None
    via_osm: int | None
    etiqueta_declarada: tuple[str, str] | None
    # Departamento de Georef que corresponde a la jurisdiccion declarada; en
    # la Ciudad, cualquier comuna (None).
    departamento_esperado: str | None = None


# Transcripcion de la tabla del encargo de la sesion 2026-10-15A, bloque F.
CANDIDATOS: Final[tuple[Candidato, ...]] = (
    Candidato("D1", "Base operativa de paquetería, Munro", "Av. Bartolomé Mitre 3446", PROVINCIA_BUENOS_AIRES,
              "Munro, Vicente López", (-34.528, -58.523), None, None, "Vicente López"),
    Candidato("D2", "Planta logística, San Martín", "Dr. Ramón Carrillo 2176", PROVINCIA_BUENOS_AIRES,
              "San Martín", (-34.576, -58.532), 464827126, ("amenity", "post_office"), "General San Martín"),
    Candidato("D2b", "Parque Industrial Suárez", "Av. Juan Manuel de Rosas 2969", PROVINCIA_BUENOS_AIRES,
              "José León Suárez", None, 243173767, ("landuse", "industrial"), "General San Martín"),
    Candidato("D3", "Centro de distribución, Mercado Central", "Ramón Carrillo entre Av. General Paz y Boulogne Sur Mer",
              PROVINCIA_BUENOS_AIRES, "La Matanza", (-34.7107, -58.4719), 978264476, ("landuse", "commercial"),
              "La Matanza"),
    Candidato("D4", "Centro de Transferencia de Cargas, Villa Soldati", "Av. Coronel Roca y Pergamino", PROVINCIA_CIUDAD,
              "CABA", (-34.671, -58.445), 436335196, ("landuse", "commercial")),
    Candidato("D5", "Planta operativa urbana, Flores", "Av. Directorio 2717", PROVINCIA_CIUDAD,
              "CABA", (-34.634, -58.463), None, None),
    Candidato("D6", "Centro de depósitos, Barracas", "California 2082", PROVINCIA_CIUDAD,
              "CABA", (-34.647, -58.377), None, None),
)


def es_de_contacto(clave: str) -> bool:
    return clave.startswith(PREFIJOS_DE_CONTACTO) or clave.endswith(SUFIJOS_DE_CONTACTO)


def sin_contactos(respuesta: Any) -> Any:
    """La respuesta sin etiquetas de contacto ni campos de identidad de quien
    edito en ninguno de sus elementos."""
    if isinstance(respuesta, dict) and isinstance(respuesta.get("elements"), list):
        for elemento in respuesta["elements"]:
            for campo in CAMPOS_DE_IDENTIDAD:
                elemento.pop(campo, None)
            etiquetas = elemento.get("tags")
            if isinstance(etiquetas, dict):
                elemento["tags"] = {k: v for k, v in etiquetas.items() if not es_de_contacto(k)}
    return respuesta


def _guardar(archivo: pathlib.Path, contenido: Any) -> None:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(contenido, ensure_ascii=False), encoding=CODIFICACION)


def pedir_guardando(
    url: str, archivo: pathlib.Path, cuerpo: bytes | None = None, tipo: str | None = None,
    limpiar: Callable[[Any], Any] = sin_contactos,
) -> Any:
    """GET (o POST con `cuerpo`) con un intento y un reintento; guarda la
    respuesta pasada por `limpiar` (por omision, sin etiquetas de contacto ni
    campos de identidad) y no la vuelve a pedir si ya esta guardada (una
    guardada antes sin limpiar se reescribe limpia)."""
    if archivo.exists():
        guardada = json.loads(archivo.read_text(encoding=CODIFICACION))
        limpia = limpiar(json.loads(json.dumps(guardada)))
        if limpia != guardada:
            _guardar(archivo, limpia)
            _LOG.info("%s: reescrita limpia", archivo.name)
        return limpia
    cabeceras = {"User-Agent": USER_AGENT}
    if tipo:
        cabeceras["Content-Type"] = tipo
    ultimo: Exception | None = None
    for intento in range(1, INTENTOS + 1):
        try:
            pedido = urllib.request.Request(url, data=cuerpo, headers=cabeceras)
            with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_S) as respuesta:
                crudo = respuesta.read()
            contenido = limpiar(json.loads(crudo.decode(CODIFICACION)))
            _guardar(archivo, contenido)
            _LOG.info("%s: intento %d, %d bytes", url[:90], intento, len(crudo))
            time.sleep(PAUSA_ENTRE_PEDIDOS_S)
            return contenido
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            ultimo = error
            _LOG.info("%s: intento %d fallo: %s", url[:90], intento, error)
            if intento < INTENTOS:
                time.sleep(PAUSA_ENTRE_INTENTOS_S)
    raise RuntimeError(f"{url}: sin respuesta en {INTENTOS} intentos: {ultimo}")


def via_de_la_respuesta(respuesta: Mapping[str, Any], identificador: int) -> dict[str, Any]:
    """Etiquetas, nodos y centroide de una via de la respuesta `full`."""
    from shapely.geometry import MultiPoint, Polygon

    nodos = {int(e["id"]): (float(e["lon"]), float(e["lat"])) for e in respuesta["elements"] if e["type"] == "node"}
    via = next(e for e in respuesta["elements"] if e["type"] == "way" and int(e["id"]) == identificador)
    puntos = [nodos[int(n)] for n in via["nodes"]]
    cerrada = len(puntos) > 3 and puntos[0] == puntos[-1]
    figura = Polygon(puntos) if cerrada else MultiPoint(puntos)
    centro = figura.centroid if not cerrada or figura.is_valid else MultiPoint(puntos).centroid
    return {
        "etiquetas": dict(via.get("tags", {})),
        "version": via.get("version"),
        "timestamp": via.get("timestamp"),
        "nodos": len(puntos),
        "cerrada": cerrada,
        "centroide": (round(centro.y, 7), round(centro.x, 7)),
        "poligono": puntos if cerrada else None,
    }


def lote_georef(candidatos: Sequence[Candidato]) -> dict[str, Any]:
    return {
        "direcciones": [
            {"direccion": c.direccion, "provincia": c.provincia_georef, "max": 1} for c in candidatos
        ]
    }


def resultado_georef(resultado: Mapping[str, Any]) -> dict[str, Any] | None:
    """La primera direccion devuelta, si tiene coordenadas; si no, None."""
    direcciones = resultado.get("direcciones") or []
    if not direcciones or direcciones[0]["ubicacion"]["lat"] is None:
        return None
    d = direcciones[0]
    return {
        "nomenclatura": d.get("nomenclatura"),
        "departamento": (d.get("departamento") or {}).get("nombre"),
        "localidad_censal": (d.get("localidad_censal") or {}).get("nombre"),
        "altura": (d.get("altura") or {}).get("valor"),
        "lat": d["ubicacion"]["lat"],
        "lon": d["ubicacion"]["lon"],
    }


def _sin_tildes(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def georef_en_la_jurisdiccion(candidato: Candidato, georef: Mapping[str, Any] | None) -> bool:
    """Si la direccion que devolvio Georef esta en la jurisdiccion declarada:
    en la Ciudad, cualquier comuna; en la provincia, el departamento
    esperado."""
    if georef is None or georef.get("departamento") is None:
        return False
    if candidato.provincia_georef == PROVINCIA_CIUDAD:
        return _sin_tildes(str(georef["departamento"])).startswith("comuna")
    return candidato.departamento_esperado is not None and _sin_tildes(str(georef["departamento"])) == _sin_tildes(
        candidato.departamento_esperado
    )


def mejor_ubicacion(
    candidato: Candidato, osm: Mapping[str, Any] | None, georef: Mapping[str, Any] | None
) -> tuple[tuple[float, float] | None, str]:
    """Coordenadas mejor respaldadas y su fuente: el objeto de OSM, Georef o
    las declaradas."""
    if osm is not None:
        return (float(osm["centroide"][0]), float(osm["centroide"][1])), "objeto de OSM (centroide)"
    if georef is not None and georef_en_la_jurisdiccion(candidato, georef):
        return (float(georef["lat"]), float(georef["lon"])), "Georef"
    if candidato.coordenadas_declaradas is not None:
        if georef is not None:
            return candidato.coordenadas_declaradas, "declaradas (Georef la resolvio en otra jurisdiccion)"
        return candidato.coordenadas_declaradas, "declaradas"
    return None, "sin ubicacion"


def consulta_overpass(sur: float, oeste: float, norte: float, este: float) -> str:
    return (
        f'[out:json][timeout:{TIEMPO_DE_OVERPASS_S}][date:"{FECHA_DE_OVERPASS}"][bbox:{sur},{oeste},{norte},{este}];\n'
        f'nwr["{ETIQUETA_DE_LA_REGLA[0]}"="{ETIQUETA_DE_LA_REGLA[1]}"]->.log;\n'
        f'nwr["{ETIQUETA_INDUSTRIAL[0]}"="{ETIQUETA_INDUSTRIAL[1]}"]->.ind;\n'
        ".log out tags geom;\n"
        ".ind out tags center;\n"
    )


def centro_del_elemento(elemento: Mapping[str, Any]) -> tuple[float, float] | None:
    """(lat, lon) del nodo, del `center` o del promedio de la geometria."""
    if "lat" in elemento:
        return float(elemento["lat"]), float(elemento["lon"])
    if "center" in elemento:
        return float(elemento["center"]["lat"]), float(elemento["center"]["lon"])
    puntos = elemento.get("geometry") or []
    if puntos:
        return sum(p["lat"] for p in puntos) / len(puntos), sum(p["lon"] for p in puntos) / len(puntos)
    return None


def lleva(etiquetas: Mapping[str, str], etiqueta: tuple[str, str]) -> bool:
    return etiquetas.get(etiqueta[0]) == etiqueta[1]


def main(argumentos: list[str]) -> int:
    import networkx as nx
    import osmnx as ox
    from pyproj import Transformer
    from shapely.geometry import Point, Polygon, shape
    from shapely.ops import transform

    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    lector.add_argument("--grafo", type=pathlib.Path, required=True, help="GraphML de la via A")
    lector.add_argument("--poligono", type=pathlib.Path, required=True, help="respuesta de Nominatim de la relacion")
    lector.add_argument("--cache", type=pathlib.Path, required=True, help="directorio de respuestas guardadas")
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")

    nominatim = json.loads(args.poligono.read_text(encoding=CODIFICACION))[0]
    ciudad = shape(nominatim["geojson"])
    a_utm = Transformer.from_crs(WGS84, UTM_ZONA_21S, always_xy=True).transform
    ciudad_utm = transform(a_utm, ciudad)

    # F1
    osm: dict[str, dict[str, Any] | None] = {}
    for c in CANDIDATOS:
        if c.via_osm is None:
            osm[c.identificador] = None
            continue
        respuesta = pedir_guardando(API_OSM_VIA.format(identificador=c.via_osm), args.cache / f"osm_way_{c.via_osm}_full.json")
        osm[c.identificador] = via_de_la_respuesta(respuesta, c.via_osm)

    # F2
    lote = lote_georef(CANDIDATOS)
    georef_crudo = pedir_guardando(GEOREF, args.cache / "georef_lote.json", json.dumps(lote).encode(CODIFICACION), "application/json")
    georef = {c.identificador: resultado_georef(r) for c, r in zip(CANDIDATOS, georef_crudo["resultados"])}

    # F4
    oeste, sur, este, norte = ciudad.bounds
    consulta = consulta_overpass(sur, oeste, norte, este)
    overpass = pedir_guardando(OVERPASS, args.cache / "overpass_logistica_e_industrial.json",
                      urllib.parse.urlencode({"data": consulta}).encode(CODIFICACION),
                      "application/x-www-form-urlencoded")
    logistica: dict[str, dict[str, Any]] = {}
    industrial: dict[str, tuple[float, float]] = {}
    for elemento in overpass["elements"]:
        clave = f"{elemento['type']}/{elemento['id']}"
        etiquetas = elemento.get("tags", {})
        centro = centro_del_elemento(elemento)
        if centro is None or not ciudad.contains(Point(centro[1], centro[0])):
            continue
        if lleva(etiquetas, ETIQUETA_DE_LA_REGLA):
            puntos = [(p["lon"], p["lat"]) for p in elemento.get("geometry") or []]
            logistica[clave] = {
                "nombre": etiquetas.get("name"),
                "operador": etiquetas.get("operator"),
                "landuse": etiquetas.get("landuse"),
                "centro": centro,
                "poligono": Polygon(puntos) if len(puntos) > 3 and puntos[0] == puntos[-1] else None,
            }
        if lleva(etiquetas, ETIQUETA_INDUSTRIAL):
            industrial[clave] = centro

    # F3
    _LOG.info("Cargando %s", args.grafo.name)
    grafo = ox.load_graphml(args.grafo)
    mayor = max(nx.strongly_connected_components(grafo), key=len)
    nodos = sorted(mayor)
    coordenadas = [(float(grafo.nodes[n]["y"]), float(grafo.nodes[n]["x"])) for n in nodos]
    filas = []
    for c in CANDIDATOS:
        ubicacion, fuente = mejor_ubicacion(c, osm[c.identificador], georef[c.identificador])
        fila: dict[str, Any] = {
            "id": c.identificador,
            "candidato": c.nombre,
            "direccion": c.direccion,
            "jurisdiccion_declarada": c.jurisdiccion_declarada,
            "coordenadas_declaradas": c.coordenadas_declaradas,
            "objeto_declarado": None if c.via_osm is None else f"way/{c.via_osm}",
            "etiqueta_declarada": None if c.etiqueta_declarada is None else "=".join(c.etiqueta_declarada),
            "osm": None if osm[c.identificador] is None else {k: v for k, v in osm[c.identificador].items() if k != "poligono"},
            "georef": georef[c.identificador],
            "georef_en_la_jurisdiccion_declarada": georef_en_la_jurisdiccion(c, georef[c.identificador]),
            "ubicacion": ubicacion,
            "fuente_de_la_ubicacion": fuente,
        }
        objeto = osm[c.identificador]
        if objeto is not None and c.etiqueta_declarada is not None:
            fila["etiqueta_declarada_presente"] = lleva(objeto["etiquetas"], c.etiqueta_declarada)
        if georef[c.identificador] is not None and c.coordenadas_declaradas is not None:
            g = georef[c.identificador]
            fila["georef_a_las_declaradas_m"] = round(
                haversine(g["lat"], g["lon"], c.coordenadas_declaradas[0], c.coordenadas_declaradas[1]), DECIMALES_M
            )
        if objeto is not None and georef[c.identificador] is not None:
            g = georef[c.identificador]
            fila["georef_al_objeto_m"] = round(
                haversine(g["lat"], g["lon"], objeto["centroide"][0], objeto["centroide"][1]), DECIMALES_M
            )
        if ubicacion is not None:
            punto = Point(ubicacion[1], ubicacion[0])
            dentro = ciudad.contains(punto)
            distancias = [haversine(ubicacion[0], ubicacion[1], lat, lon) for lat, lon in coordenadas]
            i = min(range(len(distancias)), key=distancias.__getitem__)
            fila.update(
                {
                    "dentro_de_la_ciudad": dentro,
                    "nodo_mas_cercano_de_la_componente_mayor": nodos[i],
                    "distancia_al_nodo_m": round(distancias[i], DECIMALES_M),
                    "distancia_al_limite_de_la_ciudad_m": None
                    if dentro
                    else round(transform(a_utm, punto).distance(ciudad_utm), DECIMALES_M),
                }
            )
            predios = [k for k, v in logistica.items() if v["poligono"] is not None and v["poligono"].contains(punto)]
            fila["predios_logisticos_que_la_contienen"] = predios
        # F5
        if objeto is not None and lleva(objeto["etiquetas"], ETIQUETA_DE_LA_REGLA):
            regla = "la cumpliria: el objeto declarado lleva industrial=logistics"
        elif fila.get("predios_logisticos_que_la_contienen"):
            regla = "la cumpliria: la ubicacion cae dentro de un predio con industrial=logistics"
        elif fila.get("dentro_de_la_ciudad"):
            regla = "no la cumple: ni el objeto declarado ni un predio de la Ciudad con industrial=logistics la contiene"
        elif objeto is not None:
            regla = "no la cumple con el objeto declarado (no lleva industrial=logistics); fuera de la Ciudad, F4 no cubre otros predios"
        else:
            regla = "sin verificar: fuera de la Ciudad y sin objeto declarado"
        fila["regla_del_deposito"] = regla
        filas.append(fila)
        _LOG.info("%s", json.dumps(fila, ensure_ascii=False, default=str))

    resultado = {
        "clase": "datos reales de OpenStreetMap y de Georef",
        "poligono_de_la_ciudad": {"archivo": args.poligono.name, "relacion": nominatim.get("osm_id"),
                                  "nombre": nominatim.get("display_name")},
        "grafo": args.grafo.name,
        "componente_mayor": len(nodos),
        "pico_del_proceso_mb": pico_del_proceso_mb(),
        "candidatos": filas,
        "overpass": {
            "fecha": FECHA_DE_OVERPASS,
            "consulta": consulta,
            "industrial_logistics_en_la_ciudad": [
                {"id": k, **{c: v[c] for c in ("nombre", "operador", "landuse", "centro")}} for k, v in sorted(logistica.items())
            ],
            "landuse_industrial_en_la_ciudad": len(industrial),
            "landuse_industrial_ids": sorted(industrial),
        },
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1, default=str), encoding=CODIFICACION)
    escribir_sidecar(
        registrar(
            artefacto="candidatos_deposito_caba",
            ambito="Ciudad Autonoma de Buenos Aires y partidos limitrofes",
            fuente="API de OpenStreetMap, Georef y Overpass (fecha fija), con el grafo de la via A",
            herramienta="tools/candidatos_deposito_caba.py",
            parametros={
                "clase": resultado["clase"],
                "grafo": args.grafo.name,
                "poligono": args.poligono.name,
                "poligono_sha256": hashlib.sha256(args.poligono.read_bytes()).hexdigest(),
                "overpass_fecha": FECHA_DE_OVERPASS,
                "user_agent": USER_AGENT,
                "licencias": "OpenStreetMap: ODbL 1.0; Georef: CC BY 4.0",
                "sha256": hashlib.sha256(args.salida.read_bytes()).hexdigest(),
            },
        ),
        args.salida,
    )
    _LOG.info(
        "Overpass: %d con industrial=logistics y %d con landuse=industrial dentro de la Ciudad",
        len(logistica), len(industrial),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
