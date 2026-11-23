"""Extraccion del grafo vial desde OpenStreetMap. B-24.

El grafo se extrae con OSMnx y se deja en disco como artefacto versionado;
Cartografia lo lee desde ahi y no importa este paquete (ARQ-14, ARQ-21).
El partido es parametro, no constante del codigo: la eleccion entre
candidatos del AMBA se decide por dato medido (tamano y conectividad del
grafo real), no a priori.

La otra fuente de datos reales del proyecto, LaDe, vive en `_lade.py`: no
comparte ni origen, ni formato, ni libreria con esto. Lo unico en comun es
`_procedencia`, que es justamente donde tiene que estar.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from hoppit.shared.errores import EntidadNoEncontrada, SeleccionDeArtefactoAmbigua
from hoppit.shared.resumen_grafo import ResumenGrafo

from ._procedencia import (
    SUFIJO_SIDECAR,
    SANGRIA_JSON,
    Procedencia,
    escribir_sidecar,
    nombre_de_artefacto,
    registrar,
)

_LOG = logging.getLogger(__name__)

# Red vehicular: es la unica pertinente para ruteo de Vehiculos de reparto
# (excluye peatonal y ciclovias exclusivas).
TIPO_RED_PREDETERMINADO = "drive"

# Los partidos se consultan a OSM con su contexto administrativo; sin el,
# nombres como "San Isidro" son ambiguos a nivel mundial.
CONTEXTO_GEOGRAFICO_PREDETERMINADO = "Provincia de Buenos Aires, Argentina"

# Y ademas con el nivel administrativo explicito: en Nominatim, "Vicente
# Lopez" a secas resuelve a la localidad homonima (3,9 km2), no al partido
# que la contiene (34,4 km2). El prefijo vale para todos los partidos del
# AMBA, no para un candidato en particular; queda como parametro para
# poder consultar un area que no sea un partido.
PREFIJO_ADMINISTRATIVO_PREDETERMINADO = "Partido de"

ARTEFACTO_GRAFO_VIAL = "grafo"
FUENTE_OSM = "OpenStreetMap via Overpass API"
EXTENSION_GRAFO = "graphml"
CODIFICACION = "utf-8"

METROS_CUADRADOS_POR_KM2 = 1_000_000
DECIMALES_AREA = 2


@dataclasses.dataclass(frozen=True)
class GrafoExtraido:
    """Grafo vial recien traido de OSM, junto con su trazabilidad."""

    grafo: nx.MultiDiGraph
    procedencia: Procedencia


@dataclasses.dataclass(frozen=True)
class AreaGeocodificada:
    """El poligono que OSM devolvio para la consulta.

    Se registra explicitamente porque el nombre de un partido del AMBA es
    ambiguo en Nominatim: 'Vicente Lopez' resuelve a la localidad
    homonima, no al partido que la contiene. Dejar asentados el
    identificador de relacion de OSM, el nombre completo y la superficie
    es lo que permite darse cuenta de que se extrajo el poligono
    equivocado, en vez de descubrirlo por un conteo de nodos raro.
    """

    nombre_completo: str
    tipo_osm: str
    id_osm: int
    area_km2: float


def consulta_osm(
    partido: str,
    prefijo_administrativo: str = PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
    contexto_geografico: str = CONTEXTO_GEOGRAFICO_PREDETERMINADO,
) -> str:
    """Nombre de partido -> consulta de geocodificacion para OSM.

    El nombre limpio del partido se conserva como identidad del artefacto;
    el prefijo y el contexto existen solo para desambiguar la consulta.
    """
    nombre = f"{prefijo_administrativo} {partido}".strip()
    return f"{nombre}, {contexto_geografico}"


def geocodificar_partido(
    partido: str,
    prefijo_administrativo: str = PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
    contexto_geografico: str = CONTEXTO_GEOGRAFICO_PREDETERMINADO,
) -> tuple[AreaGeocodificada, BaseGeometry]:
    """Resuelve la consulta a un unico poligono administrativo de OSM.

    Devuelve la descripcion del area (para log y procedencia) junto con la
    geometria, de modo que el grafo se construya sobre exactamente el
    poligono que se dejo asentado.
    """
    consulta = consulta_osm(partido, prefijo_administrativo, contexto_geografico)
    area = ox.geocode_to_gdf(consulta)
    fila = area.iloc[0]
    area_proyectada_m2 = area.to_crs(area.estimate_utm_crs()).area.iloc[0]

    descripcion = AreaGeocodificada(
        nombre_completo=str(fila["display_name"]),
        tipo_osm=str(fila["osm_type"]),
        id_osm=int(fila["osm_id"]),
        area_km2=round(float(area_proyectada_m2) / METROS_CUADRADOS_POR_KM2, DECIMALES_AREA),
    )
    _LOG.info(
        "Area resuelta | consulta=%r -> %s (%s/%d, %.2f km2)",
        consulta,
        descripcion.nombre_completo,
        descripcion.tipo_osm,
        descripcion.id_osm,
        descripcion.area_km2,
    )
    return descripcion, fila["geometry"]


def extraer_grafo_vial(
    partido: str,
    tipo_red: str = TIPO_RED_PREDETERMINADO,
    prefijo_administrativo: str = PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
    contexto_geografico: str = CONTEXTO_GEOGRAFICO_PREDETERMINADO,
) -> GrafoExtraido:
    """Descarga de OSM el grafo vial del partido indicado.

    El partido llega por parametro para poder cambiar de candidato sin
    tocar el codigo (ver `tools/extraer_grafo_vial.py`).
    """
    consulta = consulta_osm(partido, prefijo_administrativo, contexto_geografico)
    _LOG.info("Extrayendo grafo vial de OSM | consulta=%r tipo_red=%s", consulta, tipo_red)

    descripcion, poligono = geocodificar_partido(
        partido, prefijo_administrativo, contexto_geografico
    )
    grafo = ox.graph_from_polygon(poligono, network_type=tipo_red)

    procedencia = registrar(
        artefacto=ARTEFACTO_GRAFO_VIAL,
        ambito=partido,
        fuente=FUENTE_OSM,
        herramienta=f"osmnx {ox.__version__}",
        parametros={
            "consulta": consulta,
            "tipo_red": tipo_red,
            "simplificado": True,
            "area_nombre_completo": descripcion.nombre_completo,
            "area_tipo_osm": descripcion.tipo_osm,
            "area_id_osm": descripcion.id_osm,
            "area_km2": descripcion.area_km2,
        },
    )
    return GrafoExtraido(grafo=grafo, procedencia=procedencia)


def resumir_grafo(grafo: nx.MultiDiGraph) -> ResumenGrafo:
    """Conteos de validacion del grafo extraido."""
    componentes_fuertes = list(nx.strongly_connected_components(grafo))
    mayor = max((len(c) for c in componentes_fuertes), default=0)
    return ResumenGrafo(
        nodos=grafo.number_of_nodes(),
        aristas=grafo.number_of_edges(),
        componentes_debiles=nx.number_weakly_connected_components(grafo),
        componentes_fuertes=len(componentes_fuertes),
        nodos_en_mayor_componente_fuerte=mayor,
    )


def loggear_resumen(partido: str, resumen: ResumenGrafo) -> None:
    """Deja en el log las medidas del grafo (nunca `print`)."""
    _LOG.info(
        "Grafo vial de %s | nodos=%d aristas=%d componentes_debiles=%d "
        "componentes_fuertes=%d mayor_componente_fuerte=%d (%.2f%% de los nodos)",
        partido,
        resumen.nodos,
        resumen.aristas,
        resumen.componentes_debiles,
        resumen.componentes_fuertes,
        resumen.nodos_en_mayor_componente_fuerte,
        resumen.fraccion_en_mayor_componente_fuerte * 100,
    )


def serializar_grafo(extraido: GrafoExtraido, directorio: Path) -> Path:
    """Escribe el grafo como GraphML mas su sidecar de procedencia.

    La procedencia queda en dos lugares a proposito y con una sola fuente
    de verdad (`_procedencia`): adentro del GraphML, para que el artefacto
    no dependa de archivos vecinos, y en el sidecar JSON, para poder
    auditarlo sin abrir el grafo.
    """
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_de_artefacto(extraido.procedencia, EXTENSION_GRAFO)

    grafo = extraido.grafo.copy()
    grafo.graph.update(extraido.procedencia.como_atributos_planos())
    ox.save_graphml(grafo, filepath=ruta, encoding=CODIFICACION)
    _LOG.info("Grafo serializado en %s", ruta)

    escribir_sidecar(extraido.procedencia, ruta)
    return ruta


# --------------------------------------------------------------------------
# Ubicacion del Deposito (B-59).
#
# El Deposito no es una constante del codigo: es un parametro de la instancia.
# Se ubica en un predio etiquetado `industrial=logistics` en OSM y operado por
# una empresa de paqueteria, con la referencia del predio registrada en
# procedencia para que se pueda auditar de donde salio.
#
# El criterio anterior -- mayor superficie entre los predios con
# `landuse=industrial` -- era reproducible pero no discriminaba actividad
# logistica de cualquier otro uso industrial: el predio de mayor superficie del
# partido resulto ser una planta de saneamiento. `industrial=logistics` es la
# etiqueta que si describe la actividad.
#
# Este modulo consulta OSM y serializa el artefacto; anclar el punto a un nodo
# de la red vial es operacion de Cartografia, dueña del grafo, y la
# composicion de las dos la hace `tools/ubicar_deposito.py` -- este paquete no
# importa el nucleo (ARQ-14, ARQ-21).
# --------------------------------------------------------------------------

ARTEFACTO_DEPOSITO = "deposito"
EXTENSION_DEPOSITO = "json"

# Actividad logistica declarada en OSM. `industrial=logistics` describe a que
# se dedica el predio, no como esta zonificado: es lo que permite distinguir un
# centro de distribucion de paqueteria de una planta industrial cualquiera.
TAGS_ACTIVIDAD_LOGISTICA: dict[str, list[str]] = {"industrial": ["logistics"]}

_COLUMNA_NOMBRE_OSM = "name"
_ELEMENTOS_CON_SUPERFICIE = ("way", "relation")
# Coordenadas de la API de OSM: latitud y longitud sobre WGS-84.
CRS_GEOGRAFICO = "EPSG:4326"


# Criterio de eleccion, incrustado en el artefacto y en su procedencia. Va como
# dato y no solo como comentario del codigo: el artefacto tiene que poder
# explicarse solo, y sin esto quien lo lea no sabria por que el Deposito esta
# donde esta.
CRITERIO_DEL_DEPOSITO = (
    "Predio etiquetado industrial=logistics en OpenStreetMap y operado por una "
    "empresa de paqueteria. La referencia del predio es parametro: elegir entre "
    "los operadores de paqueteria del partido depende de que rama del negocio "
    "opera cada uno, que es dato externo a OSM y decision de proyecto. El codigo "
    "no la infiere; verifica que el predio elegido lleve efectivamente la "
    "etiqueta y que el nodo resultante este en la componente fuertemente conexa "
    "mayor del grafo vial."
)


@dataclasses.dataclass(frozen=True)
class PoligonoUsoSuelo:
    """Un predio con actividad logistica declarada, segun OSM."""

    element_osm: str
    id_osm: int
    nombre: str | None
    clave: str
    valor: str
    area_m2: float
    lat: float
    lon: float

    @property
    def referencia_osm(self) -> str:
        """Referencia citable del predio, p. ej. 'way/439569704'."""
        return f"{self.element_osm}/{self.id_osm}"


def poligonos_de_actividad_logistica(
    partido: str,
    tags: dict[str, list[str]] | None = None,
    prefijo_administrativo: str = PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
    contexto_geografico: str = CONTEXTO_GEOGRAFICO_PREDETERMINADO,
) -> tuple[PoligonoUsoSuelo, ...]:
    """Predios con actividad logistica declarada dentro del partido.

    El punto que se devuelve por predio es su punto representativo, no el
    centroide: en un poligono en forma de L el centroide puede caer afuera
    del predio, y lo que hace falta es un punto que este adentro.
    """
    consultados = tags if tags is not None else TAGS_ACTIVIDAD_LOGISTICA
    _, poligono = geocodificar_partido(partido, prefijo_administrativo, contexto_geografico)

    encontrados: list[PoligonoUsoSuelo] = []
    for clave, valores in consultados.items():
        crudos = ox.features_from_polygon(poligono, tags={clave: valores})
        if crudos.empty:
            _LOG.info("Sin predios para %s=%s en %s", clave, valores, partido)
            continue
        proyectados = crudos.to_crs(crudos.estimate_utm_crs())
        for (elemento, identificador), fila in crudos.iterrows():
            if elemento not in _ELEMENTOS_CON_SUPERFICIE:
                continue
            geometria = fila["geometry"]
            punto = geometria.representative_point()
            encontrados.append(
                PoligonoUsoSuelo(
                    element_osm=str(elemento),
                    id_osm=int(identificador),
                    nombre=(
                        str(fila[_COLUMNA_NOMBRE_OSM])
                        if _COLUMNA_NOMBRE_OSM in fila and fila[_COLUMNA_NOMBRE_OSM] == fila[_COLUMNA_NOMBRE_OSM]
                        else None
                    ),
                    clave=clave,
                    valor=str(fila[clave]),
                    area_m2=float(proyectados.loc[(elemento, identificador), "geometry"].area),
                    lat=float(punto.y),
                    lon=float(punto.x),
                )
            )

    _LOG.info(
        "Predios con actividad logistica en %s | total=%d claves=%s",
        partido,
        len(encontrados),
        sorted(consultados),
    )
    return tuple(encontrados)


FUENTE_API_OSM = "OpenStreetMap via API 0.6 (way/full)"


def poligonos_desde_respuesta_osm(
    respuesta: dict[str, Any], tags: dict[str, list[str]] | None = None
) -> tuple[PoligonoUsoSuelo, ...]:
    """Predios con actividad logistica de una respuesta `way/<id>/full` de la API de OSM.

    Es la otra entrada del mismo procedimiento (B-78, B-81) cuando el ambito
    no es un partido que se pueda geocodificar con el prefijo administrativo,
    como la Ciudad (2026-10-27B, K): la respuesta trae el way y sus nodos, y
    de ahi salen el poligono, el punto representativo y el area, igual que
    en `poligonos_de_actividad_logistica`. Un way sin las etiquetas
    consultadas no es candidato.
    """
    consultados = tags if tags is not None else TAGS_ACTIVIDAD_LOGISTICA
    elementos = respuesta.get("elements", [])
    coordenadas = {
        int(e["id"]): (float(e["lon"]), float(e["lat"])) for e in elementos if e.get("type") == "node"
    }
    encontrados: list[PoligonoUsoSuelo] = []
    for elemento in elementos:
        if elemento.get("type") != "way":
            continue
        etiquetas = elemento.get("tags", {})
        for clave, valores in consultados.items():
            if etiquetas.get(clave) not in valores:
                continue
            poligono = Polygon([coordenadas[int(n)] for n in elemento["nodes"]])
            punto = poligono.representative_point()
            # El area, proyectada a la zona UTM del predio, como la calcula
            # `poligonos_de_actividad_logistica`.
            proyectado, _ = ox.projection.project_geometry(poligono, crs=CRS_GEOGRAFICO)
            encontrados.append(
                PoligonoUsoSuelo(
                    element_osm="way",
                    id_osm=int(elemento["id"]),
                    nombre=etiquetas.get(_COLUMNA_NOMBRE_OSM),
                    clave=clave,
                    valor=str(etiquetas[clave]),
                    area_m2=float(proyectado.area),
                    lat=float(punto.y),
                    lon=float(punto.x),
                )
            )
    return tuple(encontrados)


def elegir_poligono_de_deposito(
    poligonos: tuple[PoligonoUsoSuelo, ...], referencia_osm: str
) -> PoligonoUsoSuelo:
    """El predio cuya referencia de OSM coincide con la indicada.

    La eleccion entre los predios con actividad logistica del partido es una
    decision de proyecto, no una inferencia: distinguir un operador de
    paqueteria de una rama de logistica contractual o de carga exige dato que
    OSM no trae. Por eso la referencia llega por parametro.

    Lo que si hace esta funcion es verificar la decision contra lo que OSM
    devuelve hoy: si el predio dejo de llevar la etiqueta de actividad
    logistica -- o dejo de existir -- falla en vez de ubicar el Deposito en un
    predio que ya no sostiene el criterio.
    """
    if not poligonos:
        raise EntidadNoEncontrada(
            "OSM no devolvio ningun predio con actividad logistica para el partido"
        )
    for candidato in poligonos:
        if candidato.referencia_osm == referencia_osm:
            _LOG.info(
                "Predio elegido para el Deposito | %s nombre=%s %s=%s area=%.0f m2 "
                "punto=(%.6f, %.6f)",
                candidato.referencia_osm,
                candidato.nombre,
                candidato.clave,
                candidato.valor,
                candidato.area_m2,
                candidato.lat,
                candidato.lon,
            )
            return candidato
    raise EntidadNoEncontrada(
        f"El predio {referencia_osm} no esta entre los de actividad logistica que "
        f"OSM devuelve para el partido: {sorted(p.referencia_osm for p in poligonos)}"
    )


def serializar_deposito(
    partido: str,
    poligono: PoligonoUsoSuelo,
    nodo: int,
    lat_nodo: float,
    lon_nodo: float,
    distancia_al_predio_m: float,
    directorio: Path,
    fuente: str = FUENTE_OSM,
    procedencia_adicional: dict[str, Any] | None = None,
) -> tuple[Path, Procedencia]:
    """Escribe el artefacto del Deposito con su procedencia.

    Queda en disco por la misma razon que el grafo: el nucleo lo recibe como
    parametro de la instancia, y el artefacto tiene que poder explicar de que
    predio de OSM salio sin el codigo que lo genero al lado. Por eso el
    criterio de eleccion viaja adentro del archivo y no solo en la
    documentacion.
    """
    procedencia = registrar(
        artefacto=ARTEFACTO_DEPOSITO,
        ambito=partido,
        fuente=fuente,
        herramienta=f"osmnx {ox.__version__}",
        parametros={
            "criterio": CRITERIO_DEL_DEPOSITO,
            "nodo": nodo,
            "lat": lat_nodo,
            "lon": lon_nodo,
            "predio_referencia_osm": poligono.referencia_osm,
            "predio_nombre": poligono.nombre,
            "predio_clave": poligono.clave,
            "predio_valor": poligono.valor,
            "predio_area_m2": round(poligono.area_m2, DECIMALES_AREA),
            "predio_lat": poligono.lat,
            "predio_lon": poligono.lon,
            "distancia_nodo_al_predio_m": round(distancia_al_predio_m, DECIMALES_AREA),
            "tags_consultados": TAGS_ACTIVIDAD_LOGISTICA,
            **(procedencia_adicional or {}),
        },
    )
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_de_artefacto(procedencia, EXTENSION_DEPOSITO)
    ruta.write_text(
        json.dumps(
            {
                "criterio": CRITERIO_DEL_DEPOSITO,
                "nodo": nodo,
                "lat": lat_nodo,
                "lon": lon_nodo,
                "predio_referencia_osm": poligono.referencia_osm,
                "predio_nombre": poligono.nombre,
            },
            ensure_ascii=False,
            indent=SANGRIA_JSON,
        )
        + "\n",
        encoding=CODIFICACION,
        # Con `\n` en cualquier sistema, como el sidecar: el artefacto se
        # versiona con `-text` y tiene que ser el mismo archivo en todo equipo.
        newline="\n",
    )
    _LOG.info(
        "Deposito serializado en %s | nodo=%d predio=%s (%s)",
        ruta,
        nodo,
        poligono.referencia_osm,
        poligono.nombre,
    )
    escribir_sidecar(procedencia, ruta)
    return ruta, procedencia


def leer_deposito(ruta: Path) -> int:
    """Nodo del Deposito segun el artefacto.

    Lo consumen los tools y las pruebas de integracion, que son quienes
    pasan el Deposito como parametro de la instancia. Ningun modulo del
    nucleo lee este archivo: para el nucleo el Deposito es un parametro.
    """
    registro = json.loads(ruta.read_text(encoding=CODIFICACION))
    return int(registro["nodo"])


def seleccionar_deposito(directorio: Path, nombre: str | None = None) -> Path:
    """Artefacto de Deposito a leer: el que indica quien ensambla.

    Si nadie lo indica, el unico `deposito_*.json` del directorio. Con mas de
    uno, falla, igual que la seleccion del grafo en Cartografia: no hay
    respaldo por orden lexicografico (B-209, punto 6). Un artefacto indicado
    que no existe tambien falla.

    El sidecar termina tambien en `.json` y el glob lo alcanza; se descarta
    explicitamente, porque no es un candidato.
    """
    if nombre is not None:
        ruta = directorio / nombre
        if not ruta.is_file():
            raise EntidadNoEncontrada(f"No existe el artefacto de Deposito indicado: {ruta}.")
        return ruta
    candidatos = sorted(
        ruta
        for ruta in directorio.glob(f"{ARTEFACTO_DEPOSITO}_*.{EXTENSION_DEPOSITO}")
        if not ruta.name.endswith(SUFIJO_SIDECAR)
    )
    if not candidatos:
        raise EntidadNoEncontrada(
            f"No hay artefacto de Deposito en {directorio}. "
            f"Generarlo con tools/ubicar_deposito.py."
        )
    if len(candidatos) > 1:
        raise SeleccionDeArtefactoAmbigua(
            f"Hay {len(candidatos)} artefactos de Deposito en {directorio} "
            f"({', '.join(c.name for c in candidatos)}) y no se indico cual leer."
        )
    return candidatos[0]
