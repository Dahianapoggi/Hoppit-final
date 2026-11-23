"""Los dos predios con `industrial=logistics` de la Ciudad (sesion
2026-10-15B, bloque DP; insumo de la decision de Depositos, B-230, punto 4).
Solo lectura: no elige ni recomienda en el codigo y no escribe artefactos de
Deposito. Clase: datos reales de OpenStreetMap.

- DP1. De la respuesta de Overpass guardada en 2026-10-15A
  (`tools/candidatos_deposito_caba.py`, fecha fija 2026-10-13T00:00Z): las
  etiquetas de cada predio sin datos de contacto ni nombres (solo si tiene
  nombre), su superficie (poligono proyectado a UTM 21S), la comuna en la
  que caen su centroide y su punto representativo (poligonos de comunas de BA
  Data), y el nodo mas cercano de la mayor componente fuertemente conexa del
  grafo de CABA de la via A, con la operacion de Cartografia que usa
  `tools/ubicar_deposito.py`, desde el punto representativo (el que usa el
  procedimiento del Deposito, B-59), y su distancia.
- DP2. El procedimiento vigente del artefacto del Deposito (B-78, B-81):
  `poligonos_de_actividad_logistica` sobre la Ciudad y
  `elegir_poligono_de_deposito` con cada predio, las funciones del puerto de
  Preparacion de Datos que llama `tools/ubicar_deposito.py`, y el anclaje al
  nodo con Cartografia sobre el grafo de CABA. No se llama a
  `serializar_deposito`: el artefacto y su sidecar llevan el nombre del
  predio (`predio_nombre`), y la regla 10 de la sesion prohibe escribir
  nombres de empresas en cualquier archivo. La consulta va a Nominatim y a
  Overpass por OSMnx, con el User-Agent del proyecto, sin cache (las
  respuestas crudas traen contactos) y sin la consulta de estado de
  Overpass; como OSMnx reintenta por su cuenta ante 429 y 504 sin limite,
  `LimiteDePedidos` corta en dos pedidos por servicio (un intento y un
  reintento).
- DP2, complemento (`--api-osm DIR`): el estado actual de cada predio en la
  API de OpenStreetMap (`/api/0.6/way/<id>/full.json`), un pedido por objeto
  con un intento y un reintento, guardado sin contactos, sin campos de
  identidad de quien edito y sin nombres: version, fecha, etiquetas, si
  lleva la etiqueta de la regla y si la geometria es la misma que la de la
  respuesta de Overpass con fecha fija. No es el procedimiento del Deposito:
  sirve para saber si la etiqueta se mantiene hoy cuando Overpass no
  responde. `--dp2-desde` toma DP2 de una corrida anterior, para no volver a
  pedirle a Overpass.
- DP3. Distancias en linea recta (haversine) entre los dos predios y entre
  cada uno y D4, D5 y D6: los nodos provisionales de las instancias de
  comparacion de 2026-10-15A y las ubicaciones de esos candidatos en el
  resultado del bloque F de 2026-10-15A.

Cargar el grafo de CABA exige 1,5 GB libres, comprobados en una orden aparte.

    python tools/predios_logisticos_caba.py --grafo G --comunas C --overpass O --candidatos F.json --instancias I1 I2 I3 --salida S
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.amazon_2021 import pico_del_proceso_mb
from tools.candidatos_deposito_caba import (
    API_OSM_VIA,
    ETIQUETA_DE_LA_REGLA,
    INTENTOS,
    USER_AGENT,
    UTM_ZONA_21S,
    WGS84,
    es_de_contacto,
    lleva,
    pedir_guardando,
    sin_contactos,
    via_de_la_respuesta,
)
from tools.comparacion_de_geocodificadores import haversine

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.predios_logisticos_caba")

PREDIOS: Final[tuple[str, ...]] = ("way/1347897760", "way/679161482")
DEPOSITOS_PROVISIONALES: Final[tuple[str, ...]] = ("D4", "D5", "D6")
CIUDAD: Final[str] = "Ciudad Autónoma de Buenos Aires"
PREFIJO_DE_LA_CIUDAD: Final[str] = ""
CONTEXTO_DE_LA_CIUDAD: Final[str] = "Argentina"
CAMPO_COMUNA: Final[str] = "comuna"
DECIMALES_M: Final[int] = 1
CODIFICACION: Final[str] = "utf-8"
# Claves que nombran a una empresa o a una persona: no se escriben (regla 10).
CLAVES_DE_NOMBRE: Final[tuple[str, ...]] = (
    "name", "official_name", "alt_name", "short_name", "old_name", "loc_name", "operator", "brand", "owner",
)


class PedidosAgotados(RuntimeError):
    """Un servicio recibio mas pedidos que los permitidos."""


def es_de_nombre(clave: str) -> bool:
    return any(clave == c or clave.startswith(c + ":") for c in CLAVES_DE_NOMBRE)


def etiquetas_publicables(etiquetas: Mapping[str, str]) -> tuple[dict[str, str], bool]:
    """Las etiquetas sin contactos ni nombres, y si el objeto tiene nombre."""
    publicables = {k: v for k, v in etiquetas.items() if not es_de_contacto(k) and not es_de_nombre(k)}
    return dict(sorted(publicables.items())), any(es_de_nombre(k) for k in etiquetas)


def sin_nombres(respuesta: Any) -> Any:
    """La respuesta sin etiquetas de nombre en ninguno de sus elementos."""
    if isinstance(respuesta, dict) and isinstance(respuesta.get("elements"), list):
        for elemento in respuesta["elements"]:
            etiquetas = elemento.get("tags")
            if isinstance(etiquetas, dict):
                elemento["tags"] = {k: v for k, v in etiquetas.items() if not es_de_nombre(k)}
    return respuesta


def sin_contactos_identidades_ni_nombres(respuesta: Any) -> Any:
    return sin_nombres(sin_contactos(respuesta))


def mismo_poligono(a: Sequence[tuple[float, float]], b: Sequence[tuple[float, float]]) -> bool:
    return [(round(x, 7), round(y, 7)) for x, y in a] == [(round(x, 7), round(y, 7)) for x, y in b]


class LimiteDePedidos:
    """Cuenta los pedidos HTTP por servicio (host) y corta al pasar el
    maximo. Se instala sobre `requests.sessions.Session.request`, por donde
    pasan los pedidos de OSMnx."""

    def __init__(self, maximo_por_servicio: int = INTENTOS) -> None:
        self.maximo = maximo_por_servicio
        self.pedidos: dict[str, int] = {}

    def registrar(self, url: str) -> None:
        servicio = urllib.parse.urlparse(url).netloc
        self.pedidos[servicio] = self.pedidos.get(servicio, 0) + 1
        if self.pedidos[servicio] > self.maximo:
            raise PedidosAgotados(f"{servicio}: mas de {self.maximo} pedidos")

    @contextmanager
    def instalado(self) -> Iterator[LimiteDePedidos]:
        import requests

        original: Callable[..., Any] = requests.sessions.Session.request

        def request(sesion: Any, *args: Any, **kwargs: Any) -> Any:
            # requests.api pasa `method` y `url` por nombre; una llamada
            # directa puede pasarlos por posicion.
            self.registrar(str(kwargs["url"] if "url" in kwargs else args[1]))
            return original(sesion, *args, **kwargs)

        requests.sessions.Session.request = request  # type: ignore[method-assign]
        try:
            yield self
        finally:
            requests.sessions.Session.request = original  # type: ignore[method-assign]


def predios_de_overpass(respuesta: Mapping[str, Any], referencias: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Etiquetas y poligono (lon, lat) de cada predio pedido, de los
    elementos que traen geometria."""
    encontrados: dict[str, dict[str, Any]] = {}
    for elemento in respuesta["elements"]:
        referencia = f"{elemento['type']}/{elemento['id']}"
        if referencia in referencias and elemento.get("geometry"):
            encontrados[referencia] = {
                "etiquetas": dict(elemento.get("tags", {})),
                "poligono": [(float(p["lon"]), float(p["lat"])) for p in elemento["geometry"]],
            }
    return encontrados


def geometria(poligono: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """Superficie en UTM 21S, centroide y punto representativo (lat, lon)."""
    from pyproj import Transformer
    from shapely.geometry import Polygon
    from shapely.ops import transform

    figura = Polygon(poligono)
    a_utm = Transformer.from_crs(WGS84, UTM_ZONA_21S, always_xy=True).transform
    centroide, representativo = figura.centroid, figura.representative_point()
    return {
        "cerrado": poligono[0] == poligono[-1],
        "valido": figura.is_valid,
        "vertices": len(poligono) - 1,
        "superficie_m2": round(transform(a_utm, figura).area, DECIMALES_M),
        "centroide": (round(centroide.y, 7), round(centroide.x, 7)),
        "punto_representativo": (round(representativo.y, 7), round(representativo.x, 7)),
    }


def comuna_de(lat: float, lon: float, comunas: Mapping[str, Any]) -> int | None:
    from shapely.geometry import Point, shape

    punto = Point(lon, lat)
    for f in comunas["features"]:
        if shape(f["geometry"]).contains(punto):
            return int(f["properties"][CAMPO_COMUNA])
    return None


def distancias_entre(puntos: Mapping[str, tuple[float, float]]) -> dict[str, float]:
    """Distancia en linea recta entre cada par, con clave 'A-B'."""
    nombres = sorted(puntos)
    return {
        f"{a}-{b}": round(haversine(*puntos[a], *puntos[b]), DECIMALES_M)
        for i, a in enumerate(nombres) for b in nombres[i + 1:]
    }


def verificar_con_el_procedimiento(referencias: Sequence[str]) -> dict[str, Any]:
    """DP2: las funciones del procedimiento vigente, sin serializar."""
    import osmnx as ox

    from hoppit.preparacion_datos.puerto import elegir_poligono_de_deposito, poligonos_de_actividad_logistica
    from hoppit.shared.errores import EntidadNoEncontrada

    ox.settings.http_user_agent = USER_AGENT
    ox.settings.use_cache = False
    ox.settings.overpass_rate_limit = False
    # El registro del procedimiento escribe el nombre del predio: se calla.
    logging.getLogger("hoppit.preparacion_datos").setLevel(logging.WARNING)
    limite = LimiteDePedidos()
    predios = None
    errores = []
    with limite.instalado():
        for intento in range(1, INTENTOS + 1):
            try:
                predios = poligonos_de_actividad_logistica(
                    CIUDAD, prefijo_administrativo=PREFIJO_DE_LA_CIUDAD, contexto_geografico=CONTEXTO_DE_LA_CIUDAD,
                )
                break
            except PedidosAgotados as error:
                errores.append(f"intento {intento}: {error}")
                break
            except Exception as error:  # noqa: BLE001 - se registra la causa externa y se reintenta una vez
                errores.append(f"intento {intento}: {type(error).__name__}: {error}")
    resultado: dict[str, Any] = {"pedidos_por_servicio": limite.pedidos, "errores": errores}
    if predios is None:
        resultado["consultado"] = False
        return resultado
    resultado["consultado"] = True
    resultado["predios_devueltos"] = sorted(p.referencia_osm for p in predios)
    resultado["por_predio"] = {}
    for referencia in referencias:
        try:
            elegido = elegir_poligono_de_deposito(predios, referencia)
        except EntidadNoEncontrada as error:
            resultado["por_predio"][referencia] = {"verificacion_de_la_etiqueta": False,
                                                   "motivo": type(error).__name__}
            continue
        resultado["por_predio"][referencia] = {
            "verificacion_de_la_etiqueta": lleva({elegido.clave: elegido.valor}, ETIQUETA_DE_LA_REGLA),
            "clave": elegido.clave,
            "valor": elegido.valor,
            "superficie_m2_del_procedimiento": round(elegido.area_m2, DECIMALES_M),
            "punto_del_procedimiento": (round(elegido.lat, 7), round(elegido.lon, 7)),
            "tiene_nombre": elegido.nombre is not None,
        }
    return resultado


def main(argumentos: list[str]) -> int:
    from hoppit.cartografia.puerto import crear_servicio
    from hoppit.preparacion_datos.puerto import escribir_sidecar, registrar

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    lector = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for nombre in ("--grafo", "--comunas", "--overpass", "--candidatos", "--salida"):
        lector.add_argument(nombre, type=pathlib.Path, required=True)
    lector.add_argument("--instancias", type=pathlib.Path, nargs=len(DEPOSITOS_PROVISIONALES), required=True)
    lector.add_argument("--sin-red", action="store_true", help="no correr DP2")
    lector.add_argument("--dp2-desde", type=pathlib.Path, help="tomar DP2 de una corrida anterior")
    lector.add_argument("--api-osm", type=pathlib.Path, help="directorio para el complemento de DP2")
    args = lector.parse_args(argumentos)
    if args.salida.exists():
        raise SystemExit(f"{args.salida} ya existe: no se pisa")

    overpass = json.loads(args.overpass.read_text(encoding=CODIFICACION))
    comunas = json.loads(args.comunas.read_text(encoding=CODIFICACION))
    encontrados = predios_de_overpass(overpass, PREDIOS)
    faltan = sorted(set(PREDIOS) - set(encontrados))
    if faltan:
        raise SystemExit(f"la respuesta guardada no trae la geometria de {faltan}")

    _LOG.info("Construyendo Cartografia con %s", args.grafo.name)
    cartografia = crear_servicio(args.grafo)
    cartografia.construir()
    componente = cartografia.nodos_de_mayor_componente_fuerte()

    def anclar(lat: float, lon: float) -> dict[str, Any]:
        nodo = cartografia.nodo_mas_cercano(lat, lon, restringido_a=componente)
        return {"nodo": nodo.id_osm, "distancia_m": round(haversine(lat, lon, nodo.lat, nodo.lon), DECIMALES_M),
                "nodo_lat_lon": (nodo.lat, nodo.lon)}

    # DP1
    dp1: dict[str, Any] = {}
    for referencia in PREDIOS:
        etiquetas, tiene_nombre = etiquetas_publicables(encontrados[referencia]["etiquetas"])
        forma = geometria(encontrados[referencia]["poligono"])
        dp1[referencia] = {
            "etiquetas_sin_contactos_ni_nombres": etiquetas,
            "tiene_nombre": tiene_nombre,
            "lleva_la_etiqueta_de_la_regla": lleva(encontrados[referencia]["etiquetas"], ETIQUETA_DE_LA_REGLA),
            **forma,
            "comuna_del_centroide": comuna_de(*forma["centroide"], comunas),
            "comuna_del_punto_representativo": comuna_de(*forma["punto_representativo"], comunas),
            "nodo_mas_cercano_de_la_componente_mayor": anclar(*forma["punto_representativo"]),
        }

    # DP3
    resultado_f = json.loads(args.candidatos.read_text(encoding=CODIFICACION))
    ubicaciones_f = {c["id"]: tuple(c["ubicacion"]) for c in resultado_f["candidatos"] if c["id"] in DEPOSITOS_PROVISIONALES}
    nodos_provisionales: dict[str, tuple[float, float]] = {}
    for etiqueta, ruta in zip(DEPOSITOS_PROVISIONALES, args.instancias):
        instancia = json.loads(ruta.read_text(encoding=CODIFICACION))
        if instancia["deposito"]["descriptor"]["etiqueta"] != etiqueta:
            raise SystemExit(f"{ruta.name} no es la instancia del Deposito {etiqueta}")
        coordenadas = cartografia.coordenadas(instancia["deposito"]["nodo"])
        nodos_provisionales[etiqueta] = (coordenadas.lat, coordenadas.lon)
    puntos_predios = {r: tuple(dp1[r]["punto_representativo"]) for r in PREDIOS}
    dp3 = {
        "entre_los_predios_m": distancias_entre(puntos_predios),
        "a_los_nodos_provisionales_m": {
            r: {d: round(haversine(*puntos_predios[r], *nodos_provisionales[d]), DECIMALES_M) for d in DEPOSITOS_PROVISIONALES}
            for r in PREDIOS
        },
        "a_las_ubicaciones_del_bloque_F_m": {
            r: {d: round(haversine(*puntos_predios[r], *ubicaciones_f[d]), DECIMALES_M) for d in DEPOSITOS_PROVISIONALES}
            for r in PREDIOS
        },
        "punto_de_cada_predio": "punto representativo",
    }

    # DP2
    if args.dp2_desde is not None:
        dp2 = {**json.loads(args.dp2_desde.read_text(encoding=CODIFICACION))["DP2"], "tomado_de": args.dp2_desde.name}
    elif args.sin_red:
        dp2 = {"corrido": False, "motivo": "--sin-red"}
    else:
        dp2 = {"corrido": True, **verificar_con_el_procedimiento(PREDIOS)}
    for referencia, verificacion in (dp2.get("por_predio") or {}).items():
        if "punto_del_procedimiento" in verificacion:
            verificacion["nodo_con_el_punto_del_procedimiento"] = anclar(*verificacion["punto_del_procedimiento"])
    dp2["serializacion"] = (
        "no se llamo a serializar_deposito: el artefacto y su sidecar escriben el nombre del predio (regla 10)"
    )
    if args.api_osm is not None:
        complemento: dict[str, Any] = {}
        for referencia in PREDIOS:
            identificador = int(referencia.split("/")[1])
            try:
                respuesta = pedir_guardando(API_OSM_VIA.format(identificador=identificador),
                                            args.api_osm / f"osm_way_{identificador}_full.json",
                                            limpiar=sin_contactos_identidades_ni_nombres)
            except RuntimeError as error:
                complemento[referencia] = {"consultado": False, "error": str(error)}
                continue
            via = via_de_la_respuesta(respuesta, identificador)
            etiquetas, _ = etiquetas_publicables(via["etiquetas"])
            complemento[referencia] = {
                "consultado": True,
                "version": via["version"],
                "fecha_de_la_version": via["timestamp"],
                "etiquetas_sin_contactos_ni_nombres": etiquetas,
                "lleva_la_etiqueta_de_la_regla": lleva(via["etiquetas"], ETIQUETA_DE_LA_REGLA),
                "misma_geometria_que_overpass_2026_10_13": None if via["poligono"] is None
                else mismo_poligono(via["poligono"], encontrados[referencia]["poligono"]),
                "superficie_m2": None if via["poligono"] is None else geometria(via["poligono"])["superficie_m2"],
            }
        dp2["complemento_api_osm"] = complemento

    resultado = {
        "clase": "datos reales de OpenStreetMap",
        "overpass_guardada": {"archivo": args.overpass.name, "sha256": hashlib.sha256(args.overpass.read_bytes()).hexdigest(),
                              "fecha_de_los_datos": "2026-10-13T00:00:00Z"},
        "grafo": args.grafo.name,
        "componente_mayor": len(componente),
        "comunas": args.comunas.name,
        "DP1": dp1,
        "DP2": dp2,
        "DP3": dp3,
        "pico_del_proceso_mb": pico_del_proceso_mb(),
    }
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    crudo = (json.dumps(resultado, ensure_ascii=False, indent=1, default=str) + "\n").encode(CODIFICACION)
    args.salida.write_bytes(crudo)
    escribir_sidecar(
        registrar(
            artefacto="predios_logisticos_caba",
            ambito="Ciudad Autonoma de Buenos Aires",
            fuente="Overpass (fecha fija, guardada en 2026-10-15A) y, en DP2, Nominatim y Overpass por OSMnx",
            herramienta="tools/predios_logisticos_caba.py",
            parametros={"clase": resultado["clase"], "predios": list(PREDIOS), "user_agent": USER_AGENT,
                        "licencias": "OpenStreetMap: ODbL 1.0; comunas de BA Data: CC-BY-2.5-AR",
                        "sha256": hashlib.sha256(crudo).hexdigest()},
        ),
        args.salida,
    )
    _LOG.info("Escrito %s | pico %s MB", args.salida, resultado["pico_del_proceso_mb"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
