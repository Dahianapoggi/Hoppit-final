"""Procedencia de los artefactos que produce Preparacion de Datos.

Fuente unica de verdad de la trazabilidad dentro de este container: de que
ambito salio cada artefacto, cuando se genero y con que parametros. Los
demas modulos de `preparacion_datos` (_extraccion, _transformacion,
_calibracion) registran procedencia llamando aca, no reimplementando el
formato -- la relacion entre Preparacion de Datos y Cartografia es de
artefacto versionado, no de import (ARQ-14, ARQ-21), asi que el artefacto
tiene que poder explicarse solo, sin el codigo que lo genero al lado.

Este modulo no importa OSMnx ni ninguna libreria de datos a proposito: la
version de la herramienta usada la informa quien extrae, de modo que el
mismo mecanismo sirva para el grafo vial, para LaDe y para las series
calibradas.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import unicodedata
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

# Sidecar: el JSON de procedencia acompana al artefacto con su mismo nombre
# base, de modo que copiar uno sin el otro sea visiblemente incompleto.
SUFIJO_SIDECAR = ".procedencia.json"

# Fecha ISO 8601 (solo dia) para el nombre del artefacto; el instante
# completo, con zona, queda adentro del registro de procedencia.
FORMATO_FECHA_ISO = "%Y-%m-%d"

SANGRIA_JSON = 2
CODIFICACION = "utf-8"
FIN_DE_LINEA = "\n"

_SEPARADOR_NOMBRE = "_"
_CATEGORIA_UNICODE_DIACRITICO = "Mn"


def _instante_actual_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def normalizar_ambito(ambito: str) -> str:
    """Nombre de ambito -> identificador apto para nombre de archivo.

    'Vicente Lopez' -> 'vicente_lopez'. Se aplica solo al nombre del
    archivo; el nombre tal como lo dio el origen se conserva intacto
    dentro del registro de procedencia, que es lo que hay que poder
    auditar.
    """
    sin_diacriticos = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", ambito)
        if unicodedata.category(caracter) != _CATEGORIA_UNICODE_DIACRITICO
    )
    partes = sin_diacriticos.lower().replace("-", " ").split()
    return _SEPARADOR_NOMBRE.join(partes)


@dataclasses.dataclass(frozen=True)
class Procedencia:
    """De donde salio un artefacto, cuando y con que parametros."""

    artefacto: str
    """Nombre logico del artefacto, p. ej. 'grafo_vial'."""

    ambito: str
    """Ambito al que pertenece el artefacto, tal como se lo nombro en el
    origen: un partido del AMBA para el grafo vial, una ciudad para LaDe.
    El nombre es generico a proposito -- la dataclass sirve al grafo, a
    LaDe y a las series calibradas por igual, y ninguna de las tres
    comparte division administrativa."""

    fuente: str
    """Origen del dato, p. ej. 'OpenStreetMap via Overpass API'."""

    herramienta: str
    """Libreria y version que produjo el artefacto, p. ej. 'osmnx 2.0.6'."""

    momento_utc: dt.datetime
    """Instante de generacion, con zona explicita."""

    parametros: dict[str, Any]
    """Parametros de extraccion; con estos el artefacto es reproducible."""

    @property
    def fecha_iso(self) -> str:
        return self.momento_utc.strftime(FORMATO_FECHA_ISO)

    def como_dict(self) -> dict[str, Any]:
        return {
            "artefacto": self.artefacto,
            "ambito": self.ambito,
            "fuente": self.fuente,
            "herramienta": self.herramienta,
            "momento_utc": self.momento_utc.isoformat(),
            "parametros": dict(self.parametros),
        }

    def como_atributos_planos(self) -> dict[str, str]:
        """Version aplanada para formatos que solo aceptan escalares.

        GraphML admite atributos de grafo de tipo simple, no anidados: el
        bloque de parametros viaja serializado como JSON en una sola clave.
        """
        return {
            "procedencia_artefacto": self.artefacto,
            "procedencia_ambito": self.ambito,
            "procedencia_fuente": self.fuente,
            "procedencia_herramienta": self.herramienta,
            "procedencia_momento_utc": self.momento_utc.isoformat(),
            "procedencia_parametros": json.dumps(
                self.parametros, ensure_ascii=False, sort_keys=True
            ),
        }


def registrar(
    artefacto: str,
    ambito: str,
    fuente: str,
    herramienta: str,
    parametros: dict[str, Any],
) -> Procedencia:
    """Arma el registro de procedencia y lo deja en el log."""
    procedencia = Procedencia(
        artefacto=artefacto,
        ambito=ambito,
        fuente=fuente,
        herramienta=herramienta,
        momento_utc=_instante_actual_utc(),
        parametros=dict(parametros),
    )
    _LOG.info(
        "Procedencia registrada | artefacto=%s ambito=%s fuente=%s "
        "herramienta=%s momento_utc=%s parametros=%s",
        procedencia.artefacto,
        procedencia.ambito,
        procedencia.fuente,
        procedencia.herramienta,
        procedencia.momento_utc.isoformat(),
        procedencia.parametros,
    )
    return procedencia


def nombre_de_artefacto(procedencia: Procedencia, extension: str) -> str:
    """`<artefacto>_<ambito>_<fecha_ISO>.<extension>`.

    Convencion de nombre del proyecto; vive aca y no en cada modulo que
    escribe un artefacto para que haya un solo lugar donde cambiarla.
    """
    partes = (
        procedencia.artefacto,
        normalizar_ambito(procedencia.ambito),
        procedencia.fecha_iso,
    )
    return f"{_SEPARADOR_NOMBRE.join(partes)}.{extension.lstrip('.')}"


def _ruta_sidecar(ruta_artefacto: Path) -> Path:
    return ruta_artefacto.with_suffix(ruta_artefacto.suffix + SUFIJO_SIDECAR)


def escribir_sidecar(procedencia: Procedencia, ruta_artefacto: Path) -> Path:
    """Escribe el JSON de procedencia junto al artefacto y devuelve su ruta.

    Con fin de linea `\\n` en cualquier sistema: el sidecar es un artefacto
    versionado mas, y el mismo registro tiene que ser el mismo archivo en
    Windows y en otro equipo (B-180).
    """
    ruta_sidecar = _ruta_sidecar(ruta_artefacto)
    ruta_sidecar.write_text(
        json.dumps(procedencia.como_dict(), ensure_ascii=False, indent=SANGRIA_JSON) + "\n",
        encoding=CODIFICACION,
        newline=FIN_DE_LINEA,
    )
    _LOG.info("Procedencia escrita en %s", ruta_sidecar)
    return ruta_sidecar


def leer_sidecar(ruta_artefacto: Path) -> Procedencia | None:
    """La procedencia registrada junto al artefacto, o None si no hay sidecar.

    Inversa de `escribir_sidecar`: el formato del sidecar se conoce en un
    solo lugar. Un sidecar que existe pero no se puede leer levanta, en vez
    de tratarse como ausente: quien llama decide que hacer con eso.
    """
    ruta_sidecar = _ruta_sidecar(ruta_artefacto)
    if not ruta_sidecar.exists():
        return None
    registro = json.loads(ruta_sidecar.read_text(encoding=CODIFICACION))
    return Procedencia(
        artefacto=registro["artefacto"],
        ambito=registro["ambito"],
        fuente=registro["fuente"],
        herramienta=registro["herramienta"],
        momento_utc=dt.datetime.fromisoformat(registro["momento_utc"]),
        parametros=dict(registro["parametros"]),
    )
