"""Incidentes por la API (FR-18; CU-07, paso 1): alta, consulta, lista y los
rechazos del dominio, sin recalculo (sesion 2026-10-15B, bloque IA)."""
from __future__ import annotations

import datetime as dt
import http
import itertools

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import crear_app
from hoppit.planificacion_rutas.puerto import SIN_MARGEN
from hoppit.shared.ids import IncidenteId
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

INSTANTE = dt.datetime(2026, 8, 24, 15, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def cliente(grafo: GrafoVialAMBA) -> TestClient:
    """Un sistema nuevo por prueba, con la instancia canonica planificada y
    el reloj y los identificadores de los Incidentes fijados."""
    numeros = itertools.count(1)
    minutos = itertools.count()
    contexto = construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        margen_de_capacidad=SIN_MARGEN,
        reloj_de_incidentes=lambda: INSTANTE + dt.timedelta(minutes=next(minutos)),
        identificador_de_incidentes=lambda: IncidenteId(f"incidente-{next(numeros)}"),
    )
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    return cliente


def _rutas(cliente: TestClient) -> list[str]:
    return [ruta["id"] for ruta in cliente.get("/v1/rutas").json()]


def test_alta_y_consulta_sin_tocar_la_ruta(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    antes = cliente.get(f"/v1/rutas/{ruta}").json()
    respuesta = cliente.post(
        "/v1/incidentes",
        json={"tipo": "corte_de_calle", "alcance": "parcial", "descripcion": "corte", "rutas_afectadas": [ruta, ruta]},
    )
    assert respuesta.status_code == http.HTTPStatus.CREATED
    cuerpo = respuesta.json()
    assert cuerpo == {
        "id": "incidente-1",
        "tipo": "corte_de_calle",
        "alcance": "parcial",
        "descripcion": "corte",
        "rutas_afectadas": [ruta],
        "declarado_en": "2026-08-24T15:00:00Z",
    }
    assert cliente.get("/v1/incidentes/incidente-1").json() == cuerpo
    assert cliente.get(f"/v1/rutas/{ruta}").json() == antes


def test_la_lista_va_por_instante_de_declaracion(cliente: TestClient) -> None:
    for tipo in ("siniestro", "desvio"):
        assert cliente.post("/v1/incidentes", json={"tipo": tipo, "alcance": "total"}).status_code == http.HTTPStatus.CREATED
    lista = cliente.get("/v1/incidentes").json()
    assert [i["id"] for i in lista] == ["incidente-1", "incidente-2"]
    assert [i["tipo"] for i in lista] == ["siniestro", "desvio"]


def test_cero_rutas_afectadas_es_valido(cliente: TestClient) -> None:
    respuesta = cliente.post("/v1/incidentes", json={"tipo": "cancelacion", "alcance": "total"})
    assert respuesta.status_code == http.HTTPStatus.CREATED
    assert respuesta.json()["rutas_afectadas"] == []
    assert respuesta.json()["descripcion"] == ""


@pytest.mark.parametrize(
    ("cuerpo", "codigo"),
    [
        ({"tipo": "inundacion", "alcance": "total"}, "tipo_de_incidente_invalido"),
        ({"tipo": "desvio", "alcance": "medio"}, "alcance_invalido"),
    ],
)
def test_tipo_y_alcance_invalidos_no_registran_nada(cliente: TestClient, cuerpo: dict[str, str], codigo: str) -> None:
    respuesta = cliente.post("/v1/incidentes", json=cuerpo)
    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == codigo
    assert cliente.get("/v1/incidentes").json() == []


def test_una_ruta_inexistente_se_rechaza_con_todas_las_que_faltan(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    respuesta = cliente.post(
        "/v1/incidentes",
        json={"tipo": "siniestro", "alcance": "parcial", "rutas_afectadas": ["ruta-x", ruta, "ruta-y"]},
    )
    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "rutas_inexistentes"
    assert respuesta.json()["detalle"] == {"rutas": ["ruta-x", "ruta-y"]}
    assert cliente.get("/v1/incidentes").json() == []


def test_un_incidente_inexistente_es_404(cliente: TestClient) -> None:
    respuesta = cliente.get("/v1/incidentes/incidente-que-no-existe")
    assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
    assert respuesta.json()["codigo"] == "incidente_no_encontrado"


def test_un_campo_ajeno_es_peticion_mal_formada(cliente: TestClient) -> None:
    respuesta = cliente.post("/v1/incidentes", json={"tipo": "desvio", "alcance": "total", "arco": 1})
    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"
