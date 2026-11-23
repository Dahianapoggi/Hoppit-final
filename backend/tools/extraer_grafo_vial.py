"""Extrae el grafo vial de uno o mas partidos del AMBA y loggea sus medidas.

El partido va por linea de comandos: cambiar de candidato no requiere tocar
codigo. Sin `--directorio-salida` solo mide y no escribe artefactos, que es
lo que hace falta para comparar candidatos antes de elegir.

Con `--directorio-salida`, cada grafo se escribe como GraphML con su sidecar
de procedencia: consulta del limite, area resuelta, tipo de red,
simplificacion, version de OSMnx (`herramienta`), momento de la descarga
(`momento_utc`) y, desde el 13 de octubre de 2026, el SHA-256 del GraphML,
que agrega esta herramienta reescribiendo el sidecar. Con
`--directorio-fuera-de-git`, un GraphML de mas de `--limite-para-git-mb` se
mueve ahi con su sidecar.

`--solo-nodos-y-arcos` informa solo esos dos conteos, para verificar el
artefacto sin calcular las demas medidas del grafo.

`--overpass-fecha` fija la consulta a Overpass a un instante (por ejemplo
2026-10-13T00:00:00Z), con el parametro `date` de Overpass en la
configuracion de OSMnx (`ox.settings.overpass_settings`), y lo anota en el
sidecar (sesion 2026-10-13F, E1). El limite del area lo sigue resolviendo
Nominatim, sin fecha.

`--relacion-osm` toma el limite de la relacion de OSM indicada por su
identificador, en lugar de buscar el nombre: la busqueda por nombre puede
resolver a otra relacion (el 13/10/2026, "Ciudad Autonoma de Buenos Aires,
Argentina" resolvio a la 3082668 y no a la 1224652). Con esta opcion se
extrae un solo partido, cuyo nombre queda como ambito del artefacto.

Antes de cada partido informa la memoria fisica libre.

    python tools/extraer_grafo_vial.py "Vicente Lopez" "San Isidro"
    python tools/extraer_grafo_vial.py "Lanus" --directorio-salida datos/grafos_por_partido \\
        --directorio-fuera-de-git datos/tmp/grafos --solo-nodos-y-arcos

Cartografia carga el grafo que indica el ensamblado (el de San Isidro); sin
indicacion, solo el unico `grafo_*.graphml` de datos/grafos/, y con mas de
uno falla (B-209, punto 6). Los demas partidos van a
datos/grafos_por_partido/.

Version de OSMnx (B-209, punto 5). Antes de extraer, la herramienta compara
la version instalada con la que declara el sidecar del grafo de referencia,
el de San Isidro (`herramienta`, hoy "osmnx 2.0.7"). Si difieren, no extrae
ni mide. `--aceptar-version-distinta` la deja seguir y anota en el sidecar
de cada grafo la version de referencia y la instalada
(`version_de_osmnx_distinta_de_la_referencia`). La dependencia del producto
no se fija: el entorno del proyecto puede tener otra version (B-149).

Para extraer con la version de referencia sin tocar el entorno del
proyecto, se arma un entorno aparte con OSMnx fijado y el paquete del
proyecto en modo editable (en Windows, desde backend/; la carpeta del
entorno puede ser cualquiera fuera del repositorio):

    py -3.13 -m venv RUTA/venv_osmnx207
    RUTA/venv_osmnx207/Scripts/python -m pip install -e . "osmnx==2.0.7"
    RUTA/venv_osmnx207/Scripts/python tools/extraer_grafo_vial.py "Lanus" ...
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import logging
import pathlib
import json
import shutil
import sys

import osmnx as ox

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.preparacion_datos.puerto import (
    PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
    GrafoExtraido,
    escribir_sidecar,
    registrar,
    extraer_grafo_vial,
    loggear_resumen,
    resumir_grafo,
    serializar_grafo,
)
from tools.condiciones_de_medicion import memoria_fisica_gb

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.extraer_grafo_vial")

CLAVE_SHA256 = "sha256"
SUFIJO_SIDECAR = ".procedencia.json"
BYTES_POR_MB = 1024 * 1024
LIMITE_PARA_GIT_MB = 50.0
BLOQUE = 1 << 20
CLAVE_HERRAMIENTA = "herramienta"
CLAVE_VERSION_DISTINTA = "version_de_osmnx_distinta_de_la_referencia"
SIDECAR_DE_REFERENCIA = GRAFO_PREDETERMINADO.with_name(GRAFO_PREDETERMINADO.name + SUFIJO_SIDECAR)
CODIGO_VERSION_DISTINTA = 3
# Los ajustes por omision de OSMnx 2.0.7 mas el parametro `date` de Overpass.
# Las llaves dobles quedan simples al formatear la fecha, y OSMnx completa
# {timeout} y {maxsize} al consultar.
AJUSTES_DE_OVERPASS_CON_FECHA = '[out:json][timeout:{{timeout}}]{{maxsize}}[date:"{fecha}"]'
# Los mismos valores que registra preparacion_datos al extraer por nombre.
ARTEFACTO_GRAFO_VIAL = "grafo"
FUENTE_OSM = "OpenStreetMap via Overpass API"
TIPO_RED = "drive"
METROS_CUADRADOS_POR_KM2 = 1_000_000


def extraer_por_relacion(partido: str, relacion: int) -> GrafoExtraido:
    """El grafo vial "drive" dentro del limite de una relacion de OSM dada
    por su identificador, con la misma procedencia que la extraccion por
    nombre."""
    area = ox.geocode_to_gdf(f"R{relacion}", by_osmid=True)
    fila = area.iloc[0]
    area_km2 = round(float(area.to_crs(area.estimate_utm_crs()).area.iloc[0]) / METROS_CUADRADOS_POR_KM2, 2)
    _LOG.info(
        "Area resuelta | relacion %d -> %s (%s/%s, %.2f km2)",
        relacion, fila["display_name"], fila["osm_type"], fila["osm_id"], area_km2,
    )
    grafo = ox.graph_from_polygon(fila["geometry"], network_type=TIPO_RED)
    procedencia = registrar(
        artefacto=ARTEFACTO_GRAFO_VIAL,
        ambito=partido,
        fuente=FUENTE_OSM,
        herramienta=f"osmnx {ox.__version__}",
        parametros={
            "consulta": f"R{relacion}",
            "tipo_red": TIPO_RED,
            "simplificado": True,
            "area_nombre_completo": str(fila["display_name"]),
            "area_tipo_osm": str(fila["osm_type"]),
            "area_id_osm": int(fila["osm_id"]),
            "area_km2": area_km2,
        },
    )
    return GrafoExtraido(grafo=grafo, procedencia=procedencia)


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("partidos", nargs="+", help="Partidos a extraer, uno o mas.")
    parser.add_argument(
        "--directorio-salida",
        type=pathlib.Path,
        default=None,
        help="Si se indica, serializa cada grafo como GraphML en ese directorio.",
    )
    parser.add_argument(
        "--directorio-cache",
        type=pathlib.Path,
        default=None,
        help="Cache de respuestas de Overpass (evita redescargar en cada corrida).",
    )
    parser.add_argument(
        "--prefijo-administrativo",
        default=PREFIJO_ADMINISTRATIVO_PREDETERMINADO,
        help=(
            "Nivel administrativo con el que se consulta a OSM. Vacio ('') para "
            "consultar el nombre tal cual, p. ej. una localidad."
        ),
    )
    parser.add_argument(
        "--contexto-geografico",
        default=None,
        help=(
            "Contexto que desambigua la consulta a OSM; por omision, el del producto "
            "(Provincia de Buenos Aires, Argentina). Para la Ciudad Autonoma, que no es "
            "un partido de la provincia: --prefijo-administrativo '' --contexto-geografico Argentina."
        ),
    )
    parser.add_argument(
        "--overpass-url",
        default=None,
        help=(
            "Servidor de Overpass; por omision, el de OSMnx (https://overpass-api.de/api). "
            "overpass-api.de reparte entre dos servidores (gall y lambert); si uno no responde, "
            "se puede apuntar al otro, que es la misma instancia. Se anota en el sidecar."
        ),
    )
    parser.add_argument(
        "--solo-nodos-y-arcos",
        action="store_true",
        help="Informa solo la cantidad de nodos y arcos.",
    )
    parser.add_argument(
        "--directorio-fuera-de-git",
        type=pathlib.Path,
        default=None,
        help="Destino de los GraphML que superan el limite para git.",
    )
    parser.add_argument("--limite-para-git-mb", type=float, default=LIMITE_PARA_GIT_MB)
    parser.add_argument(
        "--relacion-osm",
        type=int,
        default=None,
        help="Identificador de la relacion de OSM que da el limite, en lugar de buscar el nombre.",
    )
    parser.add_argument(
        "--overpass-fecha",
        default=None,
        help="Instante al que se fija la consulta a Overpass, en ISO 8601 UTC (2026-10-13T00:00:00Z).",
    )
    parser.add_argument(
        "--aceptar-version-distinta",
        action="store_true",
        help=(
            "Extrae aunque la version de OSMnx instalada difiera de la del grafo de "
            "referencia, y lo anota en el sidecar."
        ),
    )
    return parser.parse_args(argumentos)


def version_instalada() -> str:
    """Como la escribe el sidecar: "osmnx X.Y.Z"."""
    return f"osmnx {ox.__version__}"


def version_de_referencia(sidecar: pathlib.Path = SIDECAR_DE_REFERENCIA) -> str:
    return str(json.loads(sidecar.read_text(encoding="utf-8"))[CLAVE_HERRAMIENTA])


def diferencia_de_version(
    instalada: str, referencia: str, sidecar: pathlib.Path = SIDECAR_DE_REFERENCIA
) -> dict[str, str] | None:
    """None si coinciden; si no, lo que se anota en el sidecar."""
    if instalada == referencia:
        return None
    return {"referencia": referencia, "instalada": instalada, "grafo_de_referencia": sidecar.name}


def _sha256(ruta: pathlib.Path) -> str:
    digesto = hashlib.sha256()
    with ruta.open("rb") as archivo:
        for bloque in iter(lambda: archivo.read(BLOQUE), b""):
            digesto.update(bloque)
    return digesto.hexdigest()


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)

    diferencia = diferencia_de_version(version_instalada(), version_de_referencia())
    if diferencia is not None:
        if not args.aceptar_version_distinta:
            _LOG.error(
                "OSMnx instalado (%s) difiere del grafo de referencia %s (%s): no se extrae. "
                "Usar el entorno aparte con la version de referencia (ver la ayuda) o "
                "--aceptar-version-distinta, que lo anota en el sidecar.",
                diferencia["instalada"], diferencia["grafo_de_referencia"], diferencia["referencia"],
            )
            return CODIGO_VERSION_DISTINTA
        _LOG.warning("Version de OSMnx distinta de la referencia, aceptada: %s", diferencia)

    if args.directorio_cache is not None:
        ox.settings.cache_folder = str(args.directorio_cache)
    if args.overpass_url is not None:
        ox.settings.overpass_url = args.overpass_url
    if args.overpass_fecha is not None:
        ox.settings.overpass_settings = AJUSTES_DE_OVERPASS_CON_FECHA.format(fecha=args.overpass_fecha)

    for partido in args.partidos:
        _, libre = memoria_fisica_gb()
        _LOG.info("Memoria fisica libre antes de %s: %s GB", partido, f"{libre:.2f}" if libre else "sin dato")
        contexto = {} if args.contexto_geografico is None else {"contexto_geografico": args.contexto_geografico}
        if args.relacion_osm is not None:
            if len(args.partidos) != 1:
                _LOG.error("Con --relacion-osm se extrae un solo partido.")
                return 2
            extraido = extraer_por_relacion(partido, args.relacion_osm)
        else:
            extraido = extraer_grafo_vial(
                partido, prefijo_administrativo=args.prefijo_administrativo, **contexto
            )
        if args.solo_nodos_y_arcos:
            _LOG.info(
                "Grafo vial de %s | nodos=%d arcos=%d",
                partido,
                extraido.grafo.number_of_nodes(),
                extraido.grafo.number_of_edges(),
            )
        else:
            loggear_resumen(partido, resumir_grafo(extraido.grafo))
        if args.directorio_salida is None:
            continue
        ruta = serializar_grafo(extraido, args.directorio_salida)
        anotaciones: dict[str, object] = {CLAVE_SHA256: _sha256(ruta)}
        if args.overpass_url is not None:
            anotaciones["overpass_url"] = args.overpass_url
        if args.overpass_fecha is not None:
            anotaciones["overpass_fecha"] = args.overpass_fecha
            anotaciones["overpass_settings"] = ox.settings.overpass_settings
        if diferencia is not None:
            anotaciones[CLAVE_VERSION_DISTINTA] = diferencia
        procedencia = dataclasses.replace(
            extraido.procedencia,
            parametros={**extraido.procedencia.parametros, **anotaciones},
        )
        sidecar = escribir_sidecar(procedencia, ruta)
        megabytes = ruta.stat().st_size / BYTES_POR_MB
        if args.directorio_fuera_de_git is not None and megabytes > args.limite_para_git_mb:
            args.directorio_fuera_de_git.mkdir(parents=True, exist_ok=True)
            ruta = pathlib.Path(shutil.move(str(ruta), args.directorio_fuera_de_git / ruta.name))
            sidecar = pathlib.Path(shutil.move(str(sidecar), args.directorio_fuera_de_git / sidecar.name))
        _LOG.info("%s | %.1f MB | %s | sidecar %s", partido, megabytes, ruta, sidecar.name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
