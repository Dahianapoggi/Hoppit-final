"""Deja la raiz del repositorio en el path de importacion.

Los tools son actores que disparan el sistema, no parte del paquete
instalable, y por eso viven fuera de `src/`. Las pruebas de integracion los
reutilizan en vez de duplicar el escenario de demostracion: si el escenario
se define dos veces, la corrida medida y la corrida probada pueden divergir
sin que nada lo detecte.

Desde la version 0.13.0, `construir_contexto` lee la clave de firma de las
sesiones del directorio de `HOPPIT_CLAVES_DE_FIRMA` y falla si no esta. Las
pruebas usan un par efimero, generado una vez por corrida en un temporal:
nunca leen ni escriben `datos/claves/`.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from hoppit.frontera.identidad.puerto import (  # noqa: E402
    VARIABLE_DE_ENTORNO_DE_CLAVES,
    escribir_par_de_claves,
    generar_clave_privada,
)


@pytest.fixture(scope="session", autouse=True)
def claves_de_firma_efimeras(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    directorio = tmp_path_factory.mktemp("claves_de_firma")
    escribir_par_de_claves(generar_clave_privada(), directorio)
    previa = os.environ.get(VARIABLE_DE_ENTORNO_DE_CLAVES)
    os.environ[VARIABLE_DE_ENTORNO_DE_CLAVES] = str(directorio)
    yield directorio
    if previa is None:
        os.environ.pop(VARIABLE_DE_ENTORNO_DE_CLAVES, None)
    else:
        os.environ[VARIABLE_DE_ENTORNO_DE_CLAVES] = previa
