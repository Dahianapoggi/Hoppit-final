"""Toda respuesta de error tiene la misma forma (NFR-06).

Que la validacion de esquema responda con un formato y las reglas de negocio
con otro obligaria a quien consume la API a escribir dos manejadores para el
mismo endpoint. Estas pruebas fijan la forma unica y, sobre todo, que
normalizarla no perdio lo que la validacion de esquema sabe: que campo fallo
y por que.
"""
from __future__ import annotations

import http
import logging

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, CodigoDeError, crear_app
from hoppit.shared.ids import VehiculoId
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.escenario_demostracion import MATRICULAS
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

_LOG = logging.getLogger(__name__)

LAT = -34.4718
LON = -58.5074

CAMPOS_DE_LA_FORMA = {"codigo", "mensaje", "detalle"}

# Un Vehiculo de la Flota real, identificado por su matricula. Las peticiones
# de estas pruebas nombran uno que el sistema tiene de verdad, no un nombre
# inventado que solo funcionaba porque el campo acepta cualquier clave.
MATRICULA = str(MATRICULAS[0])


@pytest.fixture(scope="module")
def cliente() -> TestClient:
    """Sistema sin Ordenes: alcanza para provocar todos los errores.

    Sin base de datos, para que "no hay Ordenes" sea cierto y no dependa de
    lo que haya quedado en la base de desarrollo.
    """
    return cliente_autenticado(
        construir_contexto(
            con_escenario=False,
            base_de_datos=None,
            grafo=GRAFO_PREDETERMINADO,
            deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        )
    )


def _peticiones_que_fallan() -> list[tuple[str, str, dict | None]]:
    """Un caso por cada via por la que puede salir un error."""
    return [
        # Validacion de esquema: valor fuera de rango.
        ("POST", "/v1/ordenes", {"lat": LAT, "lon": LON, "demanda": 0}),
        # Validacion de esquema: campo faltante.
        ("POST", "/v1/ordenes", {"demanda": 1}),
        # Validacion de esquema: campo desconocido.
        ("POST", "/v1/ordenes", {"lat": LAT, "lon": LON, "demanda": 1, "color": "azul"}),
        # Regla de negocio: no hay Ordenes que planificar.
        ("POST", "/v1/planificaciones", {}),
        # Entidad inexistente.
        ("GET", "/v1/ordenes/orden-inexistente", None),
        ("GET", "/v1/rutas/ruta-inexistente", None),
    ]


@pytest.mark.parametrize("metodo,ruta,cuerpo", _peticiones_que_fallan())
def test_todo_error_tiene_la_misma_forma(
    cliente: TestClient, metodo: str, ruta: str, cuerpo: dict | None
) -> None:
    """Venga de donde venga, el error se lee igual."""
    respuesta = cliente.request(metodo, ruta, json=cuerpo)

    assert respuesta.status_code >= http.HTTPStatus.BAD_REQUEST
    cuerpo_de_error = respuesta.json()
    assert set(cuerpo_de_error) == CAMPOS_DE_LA_FORMA
    # El codigo pertenece al catalogo cerrado: es contra el que se decide.
    assert cuerpo_de_error["codigo"] in {c.value for c in CodigoDeError}
    assert cuerpo_de_error["mensaje"]
    _LOG.info(
        "%s %s -> %d %s", metodo, ruta, respuesta.status_code, cuerpo_de_error["codigo"]
    )


def test_ningun_error_conserva_el_formato_de_fastapi(cliente: TestClient) -> None:
    """`detail` es la clave que usaria FastAPI por su cuenta.

    Si aparece, quedo un camino sin traducir y la interfaz recibiria dos
    formas distintas del mismo endpoint.
    """
    for metodo, ruta, cuerpo in _peticiones_que_fallan():
        assert "detail" not in cliente.request(metodo, ruta, json=cuerpo).json()


def test_el_rechazo_de_esquema_conserva_el_campo_y_el_motivo(
    cliente: TestClient,
) -> None:
    """Lo que se preserva al normalizar: cual es el campo, por que se
    rechazo, y de que clase es la violacion.

    Sin esto, una interfaz que quiera marcar el campo en el formulario
    tendria que deducirlo del texto del mensaje.
    """
    respuesta = cliente.post(
        "/v1/ordenes", json={"lat": LAT, "lon": LON, "demanda": 0}
    )

    detalle = respuesta.json()["detalle"]
    assert len(detalle["campos"]) == 1
    campo = detalle["campos"][0]
    assert campo["campo"] == "demanda"
    assert campo["motivo"]
    assert campo["tipo"] == "greater_than_equal"


def test_varios_campos_invalidos_se_informan_todos(cliente: TestClient) -> None:
    """No se corta en el primero: quien completa un formulario tiene que ver
    todo lo que esta mal de una vez."""
    respuesta = cliente.post("/v1/ordenes", json={"demanda": 1})

    campos = {c["campo"] for c in respuesta.json()["detalle"]["campos"]}
    assert campos == {"lat", "lon"}
    assert all(
        c["tipo"] == "missing" for c in respuesta.json()["detalle"]["campos"]
    )


def test_el_nombre_del_campo_no_arrastra_el_origen_del_dato(
    cliente: TestClient,
) -> None:
    """Pydantic informa la ubicacion empezando por `body`, que no es parte
    del nombre del campo."""
    respuesta = cliente.post("/v1/ordenes", json={"demanda": 1})

    for campo in respuesta.json()["detalle"]["campos"]:
        assert not campo["campo"].startswith("body")


def test_un_campo_anidado_se_nombra_con_su_ruta_completa(
    cliente: TestClient,
) -> None:
    """La capacidad de un Vehiculo concreto dentro del mapa de la Flota."""
    respuesta = cliente.post(
        "/v1/planificaciones", json={"vehiculos": {MATRICULA: 0}}
    )

    campos = {c["campo"] for c in respuesta.json()["detalle"]["campos"]}
    assert f"vehiculos.{MATRICULA}" in campos


def test_los_errores_de_negocio_conservan_su_detalle_propio(
    cliente: TestClient,
) -> None:
    """Unificar la forma no aplano el detalle de cada caso.

    El del flujo 4a lleva demanda, capacidad y faltante; el de validacion
    lleva campos. Son detalles distintos bajo la misma clave.
    """
    contexto = construir_contexto(
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )
    nominal = contexto.flota[VehiculoId(MATRICULA)]
    cliente_propio = cliente_autenticado(contexto)
    # Una Orden que no entra en un Vehiculo de la Flota, planificada sola con
    # ese Vehiculo y su capacidad nominal: no entra ninguna, que es cuando el
    # flujo 4a sigue siendo un rechazo (B-220, punto 2). La capacidad no se
    # inventa para provocarlo, porque una distinta de la nominal se rechaza
    # antes (B-141).
    alta = cliente_propio.post(
        "/v1/ordenes", json={"lat": LAT, "lon": LON, "demanda": nominal + 1}
    )
    respuesta = cliente_propio.post(
        "/v1/planificaciones",
        json={"ordenes": [alta.json()["id"]], "vehiculos": {MATRICULA: nominal}},
    )

    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == CodigoDeError.DEMANDA_EXCEDE_CAPACIDAD.value
    assert set(cuerpo["detalle"]) == {"demanda_total", "capacidad_total", "faltante"}
