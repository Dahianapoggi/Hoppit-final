"""Ubica el Deposito en un predio de paqueteria relevado en OSM (B-59).

Compone las dos mitades que por diseno no se conocen entre si: Preparacion
de Datos consulta OSM y serializa el artefacto; Cartografia ancla el punto a
un nodo de la red vial. El plano offline no importa el nucleo (ARQ-14,
ARQ-21), asi que la composicion vive aca y no adentro de ninguno de los dos.

El nodo se restringe a la mayor componente fuertemente conexa: un Deposito
fuera de ella deja pares sin camino y vuelve infactible el CVRP.

Los predios salen de dos entradas del mismo procedimiento (B-78, B-81). Por
omision, de los predios con actividad logistica del partido, que se
geocodifica con el prefijo administrativo. Para un ambito que no es un
partido, como la Ciudad (2026-10-27B, K), de la respuesta `way/<id>/full`
de la API de OSM: `--consultar-api-osm` la pide una vez (con un reintento) y
la guarda, o `--respuesta-osm` lee una ya guardada, con su fecha. En los dos
casos se verifica que el predio lleve la etiqueta del criterio.

    python tools/ubicar_deposito.py
    python tools/ubicar_deposito.py --predio way/508737647
    python tools/ubicar_deposito.py --partido "San Isidro" --directorio-salida datos/instancia
    python tools/ubicar_deposito.py --ambito "Ciudad Autonoma de Buenos Aires" \\
        --grafo datos/grafos/grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml \\
        --predio way/1347897760 --consultar-api-osm datos/tmp/deposito_caba
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import pathlib
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import crear_servicio
from hoppit.preparacion_datos.puerto import (
    CRITERIO_DEL_DEPOSITO,
    FUENTE_API_OSM,
    PoligonoUsoSuelo,
    elegir_poligono_de_deposito,
    poligonos_de_actividad_logistica,
    poligonos_desde_respuesta_osm,
    serializar_deposito,
)
from hoppit.shared.red import USER_AGENT_DEL_PROYECTO

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.ubicar_deposito")

PARTIDO_PREDETERMINADO = "San Isidro"
DIRECTORIO_PREDETERMINADO = pathlib.Path("datos/instancia")

# DPD, sobre la Panamericana en San Isidro. Los dos predios `industrial=
# logistics` del partido son DPD y UPS Supply Chain Solutions; se elige DPD
# porque es paqueteria pura, mientras que "Supply Chain Solutions" es la rama
# de logistica contractual y de carga de UPS, no reparto de ultima milla. Esa
# distincion es dato externo a OSM: por eso la eleccion es parametro del tool y
# queda registrada en procedencia, en vez de derivarse dentro del codigo.
PREDIO_PREDETERMINADO = "way/1525656127"

# API de OSM: un pedido y, si falla, un solo reintento (encargo 2026-10-27B,
# regla 1). La espera entre los dos es corta: no es un sondeo.
URL_API_OSM = "https://api.openstreetmap.org/api/0.6/{referencia}/full.json"
INTENTOS_API_OSM = 2
ESPERA_ENTRE_INTENTOS_S = 5.0
TIEMPO_DE_ESPERA_API_OSM_S = 30.0
NOMBRE_RESPUESTA_OSM = "osm_{elemento}_{identificador}_full_{fecha}.json"


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--partido", default=PARTIDO_PREDETERMINADO)
    parser.add_argument(
        "--ambito",
        default=None,
        help="Ambito del artefacto si no es el partido (por ejemplo, la Ciudad).",
    )
    parser.add_argument(
        "--predio",
        default=PREDIO_PREDETERMINADO,
        help="Referencia OSM del predio, p. ej. way/1525656127.",
    )
    parser.add_argument(
        "--grafo",
        type=pathlib.Path,
        default=GRAFO_PREDETERMINADO,
        help="Artefacto del grafo vial al que se ancla el Deposito.",
    )
    entrada = parser.add_mutually_exclusive_group()
    entrada.add_argument(
        "--respuesta-osm",
        type=pathlib.Path,
        default=None,
        help="Respuesta way/<id>/full de la API de OSM ya guardada.",
    )
    entrada.add_argument(
        "--consultar-api-osm",
        type=pathlib.Path,
        default=None,
        metavar="DIRECTORIO",
        help="Pide la respuesta way/<id>/full a la API de OSM y la guarda en DIRECTORIO.",
    )
    parser.add_argument(
        "--directorio-salida", type=pathlib.Path, default=DIRECTORIO_PREDETERMINADO
    )
    return parser.parse_args(argumentos)


def pedido_a_la_api_osm(referencia: str) -> urllib.request.Request:
    """El pedido `way/<id>/full`, con el User-Agent del proyecto."""
    return urllib.request.Request(
        URL_API_OSM.format(referencia=referencia),
        headers={"User-Agent": USER_AGENT_DEL_PROYECTO, "Accept": "application/json"},
    )


def consultar_api_osm(referencia: str, directorio: pathlib.Path) -> pathlib.Path:
    """Pide la respuesta del predio y la guarda tal como llego, con la fecha en el nombre."""
    pedido = pedido_a_la_api_osm(referencia)
    ultimo_error: Exception | None = None
    for intento in range(1, INTENTOS_API_OSM + 1):
        try:
            _LOG.info("API de OSM, intento %d de %d: GET %s", intento, INTENTOS_API_OSM, pedido.full_url)
            with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_API_OSM_S) as respuesta:
                contenido = respuesta.read()
            break
        except (urllib.error.URLError, TimeoutError) as error:
            ultimo_error = error
            _LOG.warning("La API de OSM no respondio: %s", error)
            if intento < INTENTOS_API_OSM:
                time.sleep(ESPERA_ENTRE_INTENTOS_S)
    else:
        raise RuntimeError(f"La API de OSM no respondio en {INTENTOS_API_OSM} intentos: {ultimo_error}")
    elemento, identificador = referencia.split("/")
    directorio.mkdir(parents=True, exist_ok=True)
    destino = directorio / NOMBRE_RESPUESTA_OSM.format(
        elemento=elemento, identificador=identificador, fecha=dt.date.today().isoformat()
    )
    destino.write_bytes(contenido)
    _LOG.info("Respuesta guardada en %s (%d bytes)", destino, len(contenido))
    return destino


def _predios_y_procedencia(args: argparse.Namespace) -> tuple[tuple[PoligonoUsoSuelo, ...], str, dict[str, Any]]:
    """Los candidatos, la fuente y lo que la procedencia tiene que decir de la entrada."""
    respuesta = args.respuesta_osm
    if args.consultar_api_osm is not None:
        respuesta = consultar_api_osm(args.predio, args.consultar_api_osm)
    if respuesta is None:
        return poligonos_de_actividad_logistica(args.partido), "", {}
    registro = json.loads(respuesta.read_text(encoding="utf-8"))
    momento = dt.datetime.fromtimestamp(respuesta.stat().st_mtime, tz=dt.timezone.utc)
    return (
        poligonos_desde_respuesta_osm(registro),
        FUENTE_API_OSM,
        {
            "respuesta_osm_archivo": respuesta.name,
            "respuesta_osm_fecha_utc": momento.isoformat(timespec="seconds"),
            "grafo": args.grafo.name,
        },
    )


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    predios, fuente, procedencia_adicional = _predios_y_procedencia(args)
    ambito = args.ambito if args.ambito is not None else args.partido
    if not predios:
        _LOG.error(
            "OSM no devolvio ningun predio con actividad logistica en %s. "
            "No se inventa una ubicacion: el Deposito queda sin valor.",
            ambito,
        )
        return 1
    _LOG.info("Criterio de eleccion: %s", CRITERIO_DEL_DEPOSITO)

    # Se loggean todos los candidatos, no una muestra: la eleccion es una
    # decision de proyecto y tiene que poder contrastarse contra el conjunto
    # completo del que salio.
    for predio in sorted(predios, key=lambda p: -p.area_m2):
        _LOG.info(
            "  candidato %s | %s=%s nombre=%s area=%.0f m2",
            predio.referencia_osm,
            predio.clave,
            predio.valor,
            predio.nombre if not fuente else "(omitido)",
            predio.area_m2,
        )
    elegido = elegir_poligono_de_deposito(predios, args.predio)
    if fuente:
        # El artefacto no lleva el nombre de la organizacion que OSM asocia
        # al predio: el encargo 2026-10-27B (regla 8) no admite nombres de
        # organizaciones en ningun archivo. El predio queda identificado por
        # su referencia de OSM, que es lo que el criterio verifica.
        elegido = dataclasses.replace(elegido, nombre=None)
        procedencia_adicional["predio_nombre_omitido"] = True

    grafo = crear_servicio(args.grafo)
    grafo.construir()
    componente = grafo.nodos_de_mayor_componente_fuerte()
    nodo = grafo.nodo_mas_cercano(elegido.lat, elegido.lon, restringido_a=componente)

    distancia_m = _distancia_m(elegido.lat, elegido.lon, nodo.lat, nodo.lon)
    _LOG.info(
        "Nodo del Deposito | %d en (%.6f, %.6f), a %.1f m del predio %s "
        "(restringido a los %d nodos de la componente fuerte mayor)",
        nodo.id_osm,
        nodo.lat,
        nodo.lon,
        distancia_m,
        elegido.referencia_osm,
        len(componente),
    )

    ruta, _ = serializar_deposito(
        partido=ambito,
        poligono=elegido,
        nodo=nodo.id_osm,
        lat_nodo=nodo.lat,
        lon_nodo=nodo.lon,
        distancia_al_predio_m=distancia_m,
        directorio=args.directorio_salida,
        **({"fuente": fuente, "procedencia_adicional": procedencia_adicional} if fuente else {}),
    )
    _LOG.info("Artefacto de Deposito: %s", ruta)
    return 0


def _distancia_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    import osmnx as ox

    return float(ox.distance.great_circle(lat1, lon1, lat2, lon2))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
