"""Busqueda de direcciones con Georef (RB-10; B-226, punto 2; B-247; B-259,
punto 6; 2026-10-27C, G).

Georef es el proveedor principal decidido (B-226, punto 2), detras de un
puerto: `GeocodificadorDeDirecciones`. Vive en Cartografia porque lo que
devuelve, una coordenada, solo sirve anclada al grafo, y el anclaje es de
Cartografia. Ningun otro componente lo usa.

Lo que este adaptador hace del lado del servidor, para no pasar las
condiciones de uso verificadas en fuente primaria (B-247) para quien no
pertenece a la Administracion Publica Nacional:

- limites de 10 consultas por segundo, 40 por minuto, 2.000 por hora y
  10.000 por dia, contados en este proceso; una consulta que los pasaria no
  sale y levanta `LimiteDeConsultasAlcanzado`;
- una consulta por pedido, nunca por lotes;
- cache en memoria por texto: repetir una busqueda no consume cuota;
- tiempo de espera fijo, y `GeocodificadorNoResponde` si Georef no contesta.

El User-Agent es el del proyecto (`shared/red.py`). La atribucion que exige
la licencia (CC BY 4.0) la muestra la interfaz.
"""
from __future__ import annotations

import collections
import dataclasses
import json
import logging
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.red import USER_AGENT_DEL_PROYECTO

_LOG = logging.getLogger(__name__)

URL_DE_GEOREF = "https://apis.datos.gob.ar/georef/api/direcciones"
ATRIBUCION_DE_GEOREF = "Servicio Georef – argentina.gob.ar/georef"

# Limites de B-247 para quien no pertenece a la Administracion Publica
# Nacional: (ventana en segundos, consultas admitidas en esa ventana).
SEGUNDO_S = 1
MINUTO_S = 60
HORA_S = 60 * MINUTO_S
DIA_S = 24 * HORA_S
CONSULTAS_POR_SEGUNDO = 10
CONSULTAS_POR_MINUTO = 40
CONSULTAS_POR_HORA = 2_000
CONSULTAS_POR_DIA = 10_000
LIMITES_DE_GEOREF: tuple[tuple[int, int], ...] = (
    (SEGUNDO_S, CONSULTAS_POR_SEGUNDO),
    (MINUTO_S, CONSULTAS_POR_MINUTO),
    (HORA_S, CONSULTAS_POR_HORA),
    (DIA_S, CONSULTAS_POR_DIA),
)

TIEMPO_DE_ESPERA_S = 5.0
CANDIDATOS_POR_CONSULTA = 5
LARGO_MAXIMO_DEL_TEXTO = 200
# Textos distintos que se recuerdan; al llenarse se olvida el menos usado.
CAPACIDAD_DE_LA_CACHE = 512
ESTADO_DEMASIADOS_PEDIDOS = 429
PRIMER_ESTADO_DE_ERROR_DEL_SERVIDOR = 500


class GeocodificadorNoResponde(ErrorDeDominio):
    """Georef no contesto a tiempo, o contesto con un error suyo."""


class LimiteDeConsultasAlcanzado(ErrorDeDominio):
    """La consulta pasaria uno de los limites de uso; no se hizo."""

    def __init__(self, mensaje: str, segundos_de_espera: int) -> None:
        super().__init__(mensaje)
        self.segundos_de_espera = segundos_de_espera


@dataclasses.dataclass(frozen=True)
class CandidatoDeDireccion:
    """Una direccion que Georef reconocio, con su ubicacion."""

    direccion: str
    """La nomenclatura normalizada que devuelve Georef."""
    lat: float
    lon: float


class GeocodificadorDeDirecciones(Protocol):
    def buscar(self, texto: str) -> tuple[CandidatoDeDireccion, ...]: ...


Transporte = Callable[[urllib.request.Request, float], bytes]


def _transporte_urllib(pedido: urllib.request.Request, tiempo_de_espera_s: float) -> bytes:
    with urllib.request.urlopen(pedido, timeout=tiempo_de_espera_s) as respuesta:
        cuerpo: bytes = respuesta.read()
        return cuerpo


def normalizar_texto(texto: str) -> str:
    """La clave de la cache: sin espacios de mas y sin distinguir mayusculas."""
    return " ".join(texto.split()).casefold()


class GeocodificadorGeoref:
    """`GeocodificadorDeDirecciones` sobre la API publica de Georef.

    `provincia` acota la busqueda (el identificador de Georef: "02" la
    Ciudad, "06" la provincia de Buenos Aires), porque la misma calle y altura
    existen en muchas localidades. `transporte` y `reloj` son parametros para
    que las pruebas no salgan a la red ni dependan del tiempo real."""

    def __init__(
        self,
        provincia: str | None,
        transporte: Transporte = _transporte_urllib,
        reloj: Callable[[], float] = time.monotonic,
        url: str = URL_DE_GEOREF,
    ) -> None:
        self._provincia = provincia
        self._transporte = transporte
        self._reloj = reloj
        self._url = url
        self._consultas: collections.deque[float] = collections.deque()
        self._cache: collections.OrderedDict[str, tuple[CandidatoDeDireccion, ...]] = collections.OrderedDict()
        self._cerrojo = threading.Lock()

    def buscar(self, texto: str) -> tuple[CandidatoDeDireccion, ...]:
        clave = normalizar_texto(texto)
        with self._cerrojo:
            if clave in self._cache:
                self._cache.move_to_end(clave)
                return self._cache[clave]
            self._registrar_consulta()
        candidatos = self._consultar(clave)
        with self._cerrojo:
            self._cache[clave] = candidatos
            while len(self._cache) > CAPACIDAD_DE_LA_CACHE:
                self._cache.popitem(last=False)
        return candidatos

    def _registrar_consulta(self) -> None:
        """Cuenta la consulta, o levanta si pasaria alguno de los limites.
        Se llama con el cerrojo tomado."""
        ahora = self._reloj()
        while self._consultas and ahora - self._consultas[0] >= DIA_S:
            self._consultas.popleft()
        for ventana_s, admitidas in LIMITES_DE_GEOREF:
            en_la_ventana = [t for t in self._consultas if ahora - t < ventana_s]
            if len(en_la_ventana) >= admitidas:
                espera = int(ventana_s - (ahora - en_la_ventana[0])) + 1
                raise LimiteDeConsultasAlcanzado(
                    f"Se alcanzo el limite de {admitidas} consultas a Georef cada {ventana_s} s", espera
                )
        self._consultas.append(ahora)

    def _consultar(self, texto: str) -> tuple[CandidatoDeDireccion, ...]:
        parametros: dict[str, Any] = {"direccion": texto, "max": CANDIDATOS_POR_CONSULTA}
        if self._provincia is not None:
            parametros["provincia"] = self._provincia
        url = f"{self._url}?{urllib.parse.urlencode(parametros)}"
        pedido = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT_DEL_PROYECTO, "Accept": "application/json"}
        )
        try:
            crudo = self._transporte(pedido, TIEMPO_DE_ESPERA_S)
        except urllib.error.HTTPError as error:
            if error.code == ESTADO_DEMASIADOS_PEDIDOS:
                raise LimiteDeConsultasAlcanzado("Georef respondio que se pasaron sus limites", int(MINUTO_S)) from error
            if error.code >= PRIMER_ESTADO_DE_ERROR_DEL_SERVIDOR:
                raise GeocodificadorNoResponde(f"Georef respondio {error.code}") from error
            # Un 4xx de Georef es un texto que no sabe interpretar: no hay
            # candidatos, no es una falla del servicio.
            _LOG.info("Georef respondio %d para una busqueda: sin candidatos", error.code)
            return ()
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as error:
            raise GeocodificadorNoResponde(f"Georef no respondio: {error}") from error
        try:
            datos = json.loads(crudo)
        except json.JSONDecodeError as error:
            raise GeocodificadorNoResponde("Georef devolvio una respuesta que no es JSON") from error
        return _candidatos(datos)


def _candidatos(datos: Any) -> tuple[CandidatoDeDireccion, ...]:
    """Las direcciones con ubicacion; las que Georef no pudo ubicar, no."""
    candidatos = []
    for direccion in (datos or {}).get("direcciones") or []:
        ubicacion = direccion.get("ubicacion") or {}
        lat, lon = ubicacion.get("lat"), ubicacion.get("lon")
        if lat is None or lon is None:
            continue
        candidatos.append(
            CandidatoDeDireccion(direccion=str(direccion.get("nomenclatura") or ""), lat=float(lat), lon=float(lon))
        )
    return tuple(candidatos)
