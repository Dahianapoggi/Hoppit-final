"""Escritura de las series de la transformacion como artefacto versionado.

Una tabla chica en CSV con su sidecar de procedencia al lado (B-44), con el
nombre de la convencion del proyecto. Vive separado de `_procedencia`, que a
proposito no importa librerias de datos.

El CSV se escribe con fin de linea `\\n` en cualquier sistema: el mismo
artefacto generado en Windows y en otro equipo tiene que ser el mismo
archivo, byte a byte, para que git no vea un cambio donde no lo hay.

El sidecar registra el SHA-256 de esos bytes en `parametros.sha256`, con la
clave que verifica la lectura de shared/intervalo_por_celda.py (B-180): una
tabla regenerada se puede leer sin completar nada a mano.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from pathlib import Path

import pandas as pd

from hoppit.shared.intervalo_por_celda import CLAVE_SHA256

from ._procedencia import Procedencia, escribir_sidecar, nombre_de_artefacto, normalizar_ambito

_LOG = logging.getLogger(__name__)

EXTENSION_TABLA = "csv"
FIN_DE_LINEA = "\n"
CODIFICACION = "utf-8"


def directorio_de_la_ciudad(raiz: Path, ciudad: str) -> Path:
    """`<raiz>/<ciudad normalizada>`: donde viven las series de una ciudad."""
    return raiz / normalizar_ambito(ciudad)


def escribir_tabla_versionada(
    tabla: pd.DataFrame, directorio: Path, procedencia: Procedencia
) -> Path:
    """Escribe la tabla y su sidecar, con el SHA-256 de la tabla; devuelve la
    ruta de la tabla."""
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / nombre_de_artefacto(procedencia, EXTENSION_TABLA)
    contenido = tabla.to_csv(index=False, lineterminator=FIN_DE_LINEA).encode(CODIFICACION)
    ruta.write_bytes(contenido)
    sha256 = hashlib.sha256(contenido).hexdigest()
    escribir_sidecar(
        dataclasses.replace(
            procedencia, parametros={**procedencia.parametros, CLAVE_SHA256: sha256}
        ),
        ruta,
    )
    _LOG.info("Tabla escrita en %s | filas=%d | sha256=%s", ruta, len(tabla), sha256)
    return ruta
