"""La version del producto y la del contrato son distintas y se publican
como tales.

Confundirlas lleva a conclusiones erroneas: quien vea subir la version del
producto podria suponer que el contrato cambio, y quien vea /v1 estable
podria suponer que el producto no avanzo.
"""
from __future__ import annotations

import http
import re

import pytest
from fastapi.testclient import TestClient

import hoppit
from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest._aplicacion import PREFIJO_DE_VERSION
from hoppit.frontera.api_rest.puerto import crear_app
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

# Versionado semantico sin relleno de ceros: 0.5.0, nunca 0.05.000.
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

VERSION_ESPERADA = "1.0.0"
CONTRATO_ESPERADO = "v1"


@pytest.fixture(scope="module")
def cliente() -> TestClient:
    return cliente_autenticado(
        construir_contexto(
            con_escenario=False,
            base_de_datos=None,
            grafo=GRAFO_PREDETERMINADO,
            deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        )
    )


def test_el_producto_declara_su_version() -> None:
    """La fuente de verdad es pyproject; el codigo la lee de la metadata."""
    assert hoppit.__version__ == VERSION_ESPERADA


def test_la_version_es_semantica_y_sin_relleno_de_ceros() -> None:
    assert SEMVER.match(hoppit.__version__)


def test_la_mayor_es_uno_desde_la_version_final() -> None:
    """La 1.0.0 es la version final del Proyecto Final (decision del equipo,
    2026-11-23): cambiar la mayor tiene que ser una decision deliberada."""
    assert hoppit.__version__.startswith("1.")


def test_el_estado_publica_las_dos_versiones_por_separado(
    cliente: TestClient,
) -> None:
    respuesta = cliente.get(f"{PREFIJO_DE_VERSION}/estado")

    assert respuesta.status_code == http.HTTPStatus.OK
    cuerpo = respuesta.json()
    assert cuerpo["version"] == VERSION_ESPERADA
    assert cuerpo["contrato"] == CONTRATO_ESPERADO
    assert cuerpo["producto"]


def test_la_version_publicada_es_la_del_paquete(cliente: TestClient) -> None:
    """Una sola fuente de verdad: si se editara el numero a mano en el
    codigo, esto fallaria."""
    publicada = cliente.get(f"{PREFIJO_DE_VERSION}/estado").json()["version"]

    assert publicada == hoppit.__version__
    assert cliente.get("/openapi.json").json()["info"]["version"] == hoppit.__version__


def test_la_version_del_contrato_coincide_con_el_prefijo_de_las_rutas(
    cliente: TestClient,
) -> None:
    """Lo que el estado dice del contrato tiene que ser lo que las rutas
    hacen.

    El endpoint de descubrimiento queda afuera a proposito: es el unico sin
    versionar, porque preguntar que version corre no puede exigir conocerla.
    """
    from hoppit.frontera.api_rest._aplicacion import RUTA_DE_ESTADO

    contrato = cliente.get(f"{PREFIJO_DE_VERSION}/estado").json()["contrato"]

    rutas = cliente.get("/openapi.json").json()["paths"]
    assert all(
        ruta.startswith(f"/{contrato}/")
        for ruta in rutas
        if ruta != RUTA_DE_ESTADO
    )
