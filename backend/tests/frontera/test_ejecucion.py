"""Despacho de una Ruta (FR-16) y resultado de una Parada (CU-06 sin
telemetria), por la API: cada transicion que el ciclo de vida admite y las
que prohibe (sesion 2026-10-13F, bloque D)."""
from __future__ import annotations

import http
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import crear_app
from hoppit.planificacion_rutas.puerto import SIN_MARGEN
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def cliente(grafo: GrafoVialAMBA) -> TestClient:
    """Un sistema nuevo por prueba, con la instancia canonica planificada."""
    contexto = construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        margen_de_capacidad=SIN_MARGEN,
    )
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    return cliente


def _rutas(cliente: TestClient) -> list[dict[str, Any]]:
    return [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]


def _estados(cliente: TestClient, ordenes: list[str]) -> set[str]:
    return {cliente.get(f"/v1/ordenes/{orden}").json()["estado"] for orden in ordenes}


def _ordenes_de(ruta: dict[str, Any]) -> list[str]:
    return [orden["id"] for parada in ruta["paradas"] for orden in parada["ordenes"]]


def _resultado(cliente: TestClient, ruta: str, secuencia: int, resultado: str) -> Any:
    return cliente.post(f"/v1/rutas/{ruta}/paradas/{secuencia}/resultado", json={"resultado": resultado})


# --- Despacho (FR-16) -------------------------------------------------------


def test_despachar_lleva_las_ordenes_de_la_ruta_a_en_transito(cliente: TestClient) -> None:
    ruta, otra = _rutas(cliente)[:2]

    respuesta = cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert respuesta.json() == {"ruta_id": ruta["id"], "ordenes": _ordenes_de(ruta), "estado": "en_transito"}
    assert _estados(cliente, _ordenes_de(ruta)) == {"en_transito"}
    # Con la transicion en el historial.
    ultima = cliente.get(f"/v1/ordenes/{_ordenes_de(ruta)[0]}").json()["historial"][-1]
    assert (ultima["estado_anterior"], ultima["estado_nuevo"]) == ("planificada", "en_transito")
    # Las otras Rutas no se tocan.
    assert _estados(cliente, _ordenes_de(otra)) == {"planificada"}


def test_una_ruta_no_se_despacha_dos_veces(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    respuesta = cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "ruta_ya_despachada"
    assert {o["estado"] for o in respuesta.json()["detalle"]["ordenes"]} == {"en_transito"}


def test_no_se_despacha_una_ruta_de_un_plan_descartado(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    plan = cliente.get("/v1/planes").json()[0]["id"]
    assert cliente.delete(f"/v1/planes/{plan}").status_code == http.HTTPStatus.OK

    respuesta = cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
    assert respuesta.json()["codigo"] == "ruta_no_encontrada"
    assert _estados(cliente, _ordenes_de(ruta)) == {"creada"}


def test_despachar_una_ruta_inexistente_es_404(cliente: TestClient) -> None:
    respuesta = cliente.post("/v1/rutas/ruta-inexistente/despacho")

    assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
    assert respuesta.json()["codigo"] == "ruta_no_encontrada"


# --- Resultado de Parada (CU-06) ----------------------------------------------


@pytest.mark.parametrize(("resultado", "estado"), [("cumplida", "completada"), ("fallida", "fallida")])
def test_el_resultado_lleva_las_ordenes_de_la_parada_desde_en_transito(
    cliente: TestClient, resultado: str, estado: str
) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")
    primera = [o["id"] for o in ruta["paradas"][0]["ordenes"]]
    resto = [o["id"] for p in ruta["paradas"][1:] for o in p["ordenes"]]

    respuesta = _resultado(cliente, ruta["id"], 1, resultado)

    assert respuesta.status_code == http.HTTPStatus.OK
    cuerpo = respuesta.json()
    declarante = cuerpo.pop("declarante")
    assert cuerpo == {
        "ruta_id": ruta["id"], "secuencia": 1, "resultado": resultado, "ordenes": primera, "estado": estado
    }
    assert (declarante["rol"], declarante["medio"]) == ("operador", "web")
    assert _estados(cliente, primera) == {estado}
    assert _estados(cliente, resto) == {"en_transito"}
    ultima = cliente.get(f"/v1/ordenes/{primera[0]}").json()["historial"][-1]
    assert (ultima["estado_anterior"], ultima["estado_nuevo"]) == ("en_transito", estado)


def test_una_parada_con_varias_ordenes_las_lleva_a_todas(cliente: TestClient) -> None:
    ruta, secuencia, ordenes = next(
        (r, p["secuencia"], [o["id"] for o in p["ordenes"]])
        for r in _rutas(cliente)
        for p in r["paradas"]
        if len(p["ordenes"]) > 1
    )
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    respuesta = _resultado(cliente, ruta["id"], secuencia, "cumplida")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert _estados(cliente, ordenes) == {"completada"}


@pytest.mark.parametrize("resultado", ["cumplida", "fallida"])
def test_sin_despachar_el_resultado_no_se_admite(cliente: TestClient, resultado: str) -> None:
    """Planificada no pasa a Completada ni a Fallida."""
    ruta = _rutas(cliente)[0]

    respuesta = _resultado(cliente, ruta["id"], 1, resultado)

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "resultado_no_admitido"
    assert _estados(cliente, _ordenes_de(ruta)) == {"planificada"}


@pytest.mark.parametrize(
    ("primero", "segundo", "estado"),
    [
        ("cumplida", "fallida", "completada"),
        ("cumplida", "cumplida", "completada"),
        ("fallida", "cumplida", "fallida"),
        ("fallida", "fallida", "fallida"),
    ],
)
def test_una_parada_con_resultado_no_admite_otro(
    cliente: TestClient, primero: str, segundo: str, estado: str
) -> None:
    """Completada es terminal, y Fallida solo admite Reprogramada o Devuelta,
    que no se implementan."""
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")
    _resultado(cliente, ruta["id"], 1, primero)

    respuesta = _resultado(cliente, ruta["id"], 1, segundo)

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "resultado_no_admitido"
    assert {o["estado"] for o in respuesta.json()["detalle"]["ordenes"]} == {estado}


def test_una_parada_inexistente_es_404(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    for secuencia in (0, len(ruta["paradas"]) + 1):
        respuesta = _resultado(cliente, ruta["id"], secuencia, "cumplida")
        assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
        assert respuesta.json()["codigo"] == "parada_no_encontrada"
    assert _resultado(cliente, "ruta-inexistente", 1, "cumplida").json()["codigo"] == "ruta_no_encontrada"


def test_un_resultado_fuera_de_los_admitidos_es_422(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    respuesta = _resultado(cliente, ruta["id"], 1, "devuelta")

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"
    assert _estados(cliente, _ordenes_de(ruta)) == {"en_transito"}


# --- Una Ruta despachada no vuelve a Creada -----------------------------------


def test_una_ruta_despachada_no_se_invalida_ni_se_descarta_ni_se_desasigna(cliente: TestClient) -> None:
    """El ciclo de vida solo admite volver a Creada desde Planificada."""
    ruta = _rutas(cliente)[0]
    plan = cliente.get("/v1/planes").json()[0]["id"]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    for respuesta in (
        cliente.delete(f"/v1/rutas/{ruta['id']}"),
        cliente.delete(f"/v1/planes/{plan}"),
        cliente.post(f"/v1/ordenes/{_ordenes_de(ruta)[0]}/desasignacion"),
    ):
        assert respuesta.status_code == http.HTTPStatus.CONFLICT
        assert respuesta.json()["codigo"] == "ruta_en_ejecucion"

    assert cliente.get(f"/v1/rutas/{ruta['id']}").status_code == http.HTTPStatus.OK
    assert len(cliente.get("/v1/rutas").json()) == 3
    assert _estados(cliente, _ordenes_de(ruta)) == {"en_transito"}


# --- Declarante y orden de declaracion (ARQ-65; 2026-10-20C, B) ------------------


def test_el_resultado_registra_quien_lo_declaro(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")
    orden = ruta["paradas"][0]["ordenes"][0]["id"]

    declarante = _resultado(cliente, ruta["id"], 1, "cumplida").json()["declarante"]

    historial = cliente.get(f"/v1/ordenes/{orden}").json()["historial"]
    assert declarante["usuario_id"].startswith("usuario-")
    assert declarante["nombre_visible"]
    assert historial[-1]["declarante"] == declarante
    # Solo el resultado lleva declarante: el alta, la planificacion y el
    # despacho no.
    assert [t["declarante"] for t in historial[:-1]] == [None] * (len(historial) - 1)


def test_se_admite_declarar_fuera_del_orden_planificado(cliente: TestClient) -> None:
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")
    primera = [o["id"] for o in ruta["paradas"][0]["ordenes"]]
    segunda = [o["id"] for o in ruta["paradas"][1]["ordenes"]]

    respuesta = _resultado(cliente, ruta["id"], 2, "fallida")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert _estados(cliente, segunda) == {"fallida"}
    assert _estados(cliente, primera) == {"en_transito"}
    # La Ruta no cambia: las mismas Paradas, en el mismo orden, con las
    # mismas Ordenes. Solo cambia el estado de las Ordenes declaradas.
    despues = cliente.get(f"/v1/rutas/{ruta['id']}").json()
    assert [(p["secuencia"], [o["id"] for o in p["ordenes"]]) for p in despues["paradas"]] == [
        (p["secuencia"], [o["id"] for o in p["ordenes"]]) for p in ruta["paradas"]
    ]
    assert despues["distancia_m"] == ruta["distancia_m"]


def test_el_transportista_no_declara_resultados(grafo: GrafoVialAMBA) -> None:
    from hoppit.frontera.identidad.puerto import Rol

    contexto = construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        margen_de_capacidad=SIN_MARGEN,
    )
    operador = cliente_autenticado(contexto)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    ruta = _rutas(operador)[0]
    operador.post(f"/v1/rutas/{ruta['id']}/despacho")

    respuesta = _resultado(cliente_autenticado(contexto, Rol.TRANSPORTISTA), ruta["id"], 1, "cumplida")

    assert respuesta.status_code == http.HTTPStatus.FORBIDDEN
    assert respuesta.json()["codigo"] == "rol_no_autorizado"
    assert _estados(operador, [o["id"] for o in ruta["paradas"][0]["ordenes"]]) == {"en_transito"}


def test_el_409_de_una_ruta_en_ejecucion_nombra_los_estados_del_glosario(cliente: TestClient) -> None:
    """2026-10-20A, P24; 2026-10-20C, C9: el mensaje dice "En tránsito" y no
    el valor crudo. El detalle conserva los valores del contrato."""
    ruta = _rutas(cliente)[0]
    cliente.post(f"/v1/rutas/{ruta['id']}/despacho")

    respuesta = cliente.delete(f"/v1/rutas/{ruta['id']}")

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "ruta_en_ejecucion"
    assert "en En tránsito" in respuesta.json()["mensaje"]
    assert "en_transito" not in respuesta.json()["mensaje"]
    assert {o["estado"] for o in respuesta.json()["detalle"]["ordenes"]} == {"en_transito"}
