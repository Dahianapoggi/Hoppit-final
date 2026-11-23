"""El Plan de Distribucion por API (glosario, entidad 13).

Es la unidad de lectura del plan para el operador, por encima de la Ruta
individual. Antes de existir, una corrida no se podia volver a consultar:
sus totales vivian solo en la respuesta del disparo.
"""
from __future__ import annotations

import datetime as dt
import http
import logging

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

_LOG = logging.getLogger(__name__)

ORDENES_DE_LA_INSTANCIA = 20


@pytest.fixture(scope="module")
def base() -> ContextoDeLaApi:
    return construir_contexto(
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


@pytest.fixture()
def contexto(base: ContextoDeLaApi) -> ContextoDeLaApi:
    """Sistema propio por prueba, reutilizando el grafo ya cargado."""
    from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
    from hoppit.planificacion_rutas.puerto import crear_servicio as crear_planificacion
    from hoppit.shared.bus import Bus
    from tools.escenario_demostracion import construir_ordenes

    bus = Bus()
    ordenes = crear_ordenes(bus)
    for orden in construir_ordenes(
        base.cartografia.nodos_de_mayor_componente_fuerte()
    ):
        ordenes.dar_de_alta(orden)
    return ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=crear_planificacion(bus, base.cartografia, ordenes),
        cartografia=base.cartografia,
        deposito=base.deposito,
        flota=base.flota,
        identidad=identidad_efimera(),
    )


@pytest.fixture()
def cliente(contexto: ContextoDeLaApi) -> TestClient:
    return cliente_autenticado(contexto)


def _planificar(cliente: TestClient, **cuerpo) -> dict:
    respuesta = cliente.post("/v1/planificaciones", json=cuerpo)
    assert respuesta.status_code == http.HTTPStatus.CREATED, respuesta.json()
    return respuesta.json()


# --- El disparo devuelve el Plan ------------------------------------------


def test_la_planificacion_devuelve_el_plan_con_su_identidad(
    cliente: TestClient,
) -> None:
    """Es lo que permite volver a consultar la corrida."""
    cuerpo = _planificar(cliente)

    plan = cuerpo["plan"]
    assert plan["id"].startswith("plan-")
    assert cliente.get(f"/v1/planes/{plan['id']}").status_code == http.HTTPStatus.OK
    _LOG.info("Plan | %s", plan)


def test_los_totales_del_plan_vienen_calculados(cliente: TestClient) -> None:
    """No hay que sumar el listado de Rutas: esa suma deja de ser correcta en
    cuanto conviven varios Planes en una jornada."""
    cuerpo = _planificar(cliente)

    plan = cuerpo["plan"]
    rutas = cuerpo["rutas"]
    assert plan["cantidad_rutas"] == len(rutas)
    assert plan["cantidad_paradas"] == sum(r["cantidad_paradas"] for r in rutas)
    assert plan["demanda_total"] == sum(r["carga"] for r in rutas)
    assert plan["distancia_total_m"] == pytest.approx(
        sum(r["distancia_m"] for r in rutas)
    )


def test_cada_ruta_publica_su_plan(cliente: TestClient) -> None:
    cuerpo = _planificar(cliente)
    identificador = cuerpo["plan"]["id"]

    for ruta in cliente.get("/v1/rutas").json():
        assert ruta["plan_id"] == identificador
        assert cliente.get(f"/v1/rutas/{ruta['id']}").json()["plan_id"] == identificador


# --- Listado y detalle ----------------------------------------------------


def test_los_planes_se_listan_con_sus_totales(cliente: TestClient) -> None:
    _planificar(cliente)

    planes = cliente.get("/v1/planes").json()

    assert len(planes) == 1
    assert planes[0]["vehiculos"]
    assert planes[0]["cantidad_paradas"] > 0


def test_el_listado_de_planes_se_filtra_por_fecha(cliente: TestClient) -> None:
    cuerpo = _planificar(cliente)
    fecha = cuerpo["plan"]["fecha_planificacion"]
    otro_dia = str(dt.date.fromisoformat(fecha) - dt.timedelta(days=1))

    assert len(cliente.get("/v1/planes", params={"fecha": fecha}).json()) == 1
    # Una jornada sin Planes es una consulta legitima, no un error.
    vacio = cliente.get("/v1/planes", params={"fecha": otro_dia})
    assert vacio.status_code == http.HTTPStatus.OK
    assert vacio.json() == []


def test_el_detalle_trae_las_rutas_y_el_deposito(cliente: TestClient) -> None:
    cuerpo = _planificar(cliente)

    detalle = cliente.get(f"/v1/planes/{cuerpo['plan']['id']}").json()

    assert len(detalle["rutas"]) == cuerpo["plan"]["cantidad_rutas"]
    assert detalle["deposito"]["rotulo"]["texto"]
    # Sin geometria: el trazado va en el detalle de cada Ruta.
    assert all("trazado" not in ruta for ruta in detalle["rutas"])


def test_un_plan_inexistente_devuelve_404(cliente: TestClient) -> None:
    respuesta = cliente.get("/v1/planes/plan-inexistente")

    assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
    assert respuesta.json()["codigo"] == "plan_no_encontrado"


# --- Un Vehiculo, un Plan por jornada -------------------------------------


def test_reutilizar_un_vehiculo_del_dia_se_rechaza(cliente: TestClient) -> None:
    """Un Vehiculo en dos Planes del mismo dia tendria que recorrer dos Rutas
    a la vez."""
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    primero = _planificar(cliente, ordenes=ordenes[:5], vehiculos={"v-a": 20})

    respuesta = cliente.post(
        "/v1/planificaciones", json={"ordenes": ordenes[5:10], "vehiculos": {"v-a": 20}}
    )

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "vehiculos_comprometidos"
    assert cuerpo["detalle"]["vehiculos"] == [
        {"vehiculo_id": "v-a", "plan_id": primero["plan"]["id"]}
    ]
    assert cuerpo["detalle"]["fecha"] == primero["plan"]["fecha_planificacion"]


def test_no_se_planifica_parcialmente_con_los_libres(cliente: TestClient) -> None:
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    _planificar(cliente, ordenes=ordenes[:5], vehiculos={"v-a": 20})

    respuesta = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ordenes[5:10], "vehiculos": {"v-a": 20, "v-libre": 20}},
    )

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert len(cliente.get("/v1/planes").json()) == 1


def test_con_otros_vehiculos_los_planes_conviven(cliente: TestClient) -> None:
    """Planificar dos veces sigue siendo correcto: lo prohibido es solapar
    Vehiculos, no planificar de nuevo."""
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    primero = _planificar(cliente, ordenes=ordenes[:5], vehiculos={"v-a": 20})
    segundo = _planificar(cliente, ordenes=ordenes[5:10], vehiculos={"v-b": 20})

    planes = {p["id"] for p in cliente.get("/v1/planes").json()}
    assert planes == {primero["plan"]["id"], segundo["plan"]["id"]}


# --- La seleccion se hace dentro de la Flota registrada -------------------


@pytest.fixture()
def cliente_con_flota(base: ContextoDeLaApi) -> TestClient:
    """Como el proceso que sirve la API: la Flota queda registrada en el
    servicio de planificacion, y no solo publicada en /v1/flota."""
    from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
    from hoppit.planificacion_rutas.puerto import crear_servicio as crear_planificacion
    from hoppit.shared.bus import Bus
    from tools.escenario_demostracion import construir_ordenes

    bus = Bus()
    ordenes = crear_ordenes(bus)
    for orden in construir_ordenes(
        base.cartografia.nodos_de_mayor_componente_fuerte()
    ):
        ordenes.dar_de_alta(orden)
    return cliente_autenticado(
        ContextoDeLaApi(
            ordenes=ordenes,
            planificacion=crear_planificacion(
                bus, base.cartografia, ordenes, flota=base.flota
            ),
            cartografia=base.cartografia,
            deposito=base.deposito,
            flota=base.flota,
            identidad=identidad_efimera(),
        )
    )


def test_un_vehiculo_ajeno_a_la_flota_se_rechaza(
    cliente_con_flota: TestClient, base: ContextoDeLaApi
) -> None:
    """Un identificador que no es de la Flota no designa ningun Vehiculo.

    Se rechaza la seleccion entera aunque traiga Vehiculos validos, y el
    detalle nombra a cada ajeno."""
    matricula, nominal = sorted(base.flota.items())[0]
    respuesta = cliente_con_flota.post(
        "/v1/planificaciones",
        json={"vehiculos": {matricula: nominal, "v-ajeno": 20, "v-otro": 5}},
    )

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "vehiculos_fuera_de_la_flota"
    assert cuerpo["detalle"]["vehiculos"] == ["v-ajeno", "v-otro"]
    assert cliente_con_flota.get("/v1/planes").json() == []


def test_una_capacidad_distinta_de_la_nominal_se_rechaza(
    cliente_con_flota: TestClient, base: ContextoDeLaApi
) -> None:
    """La capacidad es un atributo del Vehiculo, no un parametro de la
    corrida: ni mas, que resolveria con una carga que no admite, ni menos."""
    (primera, nominal_1), (segunda, nominal_2), *resto = sorted(base.flota.items())
    seleccion = {primera: nominal_1 + 7, segunda: nominal_2 - 1}
    seleccion.update(dict(resto))

    respuesta = cliente_con_flota.post(
        "/v1/planificaciones", json={"vehiculos": seleccion}
    )

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "capacidad_distinta_de_la_nominal"
    assert cuerpo["detalle"]["vehiculos"] == [
        {"vehiculo_id": primera, "capacidad_pedida": nominal_1 + 7, "capacidad_nominal": nominal_1},
        {"vehiculo_id": segunda, "capacidad_pedida": nominal_2 - 1, "capacidad_nominal": nominal_2},
    ]
    assert cliente_con_flota.get("/v1/planes").json() == []


def test_la_flota_con_su_capacidad_nominal_se_planifica(
    cliente_con_flota: TestClient,
) -> None:
    """Lo que publica /v1/flota es exactamente lo que se acepta."""
    flota = cliente_con_flota.get("/v1/flota").json()
    seleccion = {v["id"]: v["capacidad"] for v in flota["vehiculos"]}

    cuerpo = _planificar(cliente_con_flota, vehiculos=seleccion)

    assert cuerpo["capacidad_total"] == flota["capacidad_total"]


# --- Descarte a nivel de Plan ---------------------------------------------


def test_descartar_un_plan_libera_ordenes_y_vehiculos(cliente: TestClient) -> None:
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    primero = _planificar(cliente, ordenes=ordenes[:5], vehiculos={"v-a": 20})

    respuesta = cliente.delete(f"/v1/planes/{primero['plan']['id']}")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert len(respuesta.json()["ordenes_liberadas"]) == 5
    assert cliente.get("/v1/planes").json() == []
    assert cliente.get("/v1/rutas").json() == []
    assert len(cliente.get("/v1/ordenes", params={"estado": "creada"}).json()) == (
        ORDENES_DE_LA_INSTANCIA
    )
    # Y el Vehiculo vuelve a estar disponible para la jornada.
    assert (
        cliente.post(
            "/v1/planificaciones", json={"ordenes": ordenes[:5], "vehiculos": {"v-a": 20}}
        ).status_code
        == http.HTTPStatus.CREATED
    )


def test_descartar_un_plan_no_toca_los_demas(cliente: TestClient) -> None:
    ordenes = [o["id"] for o in cliente.get("/v1/ordenes").json()]
    primero = _planificar(cliente, ordenes=ordenes[:5], vehiculos={"v-a": 20})
    segundo = _planificar(cliente, ordenes=ordenes[5:10], vehiculos={"v-b": 20})

    cliente.delete(f"/v1/planes/{primero['plan']['id']}")

    assert [p["id"] for p in cliente.get("/v1/planes").json()] == [
        segundo["plan"]["id"]
    ]
    assert cliente.get("/v1/rutas").json()


def test_descartar_un_plan_inexistente_devuelve_404(cliente: TestClient) -> None:
    respuesta = cliente.delete("/v1/planes/plan-inexistente")

    assert respuesta.status_code == http.HTTPStatus.NOT_FOUND
    assert respuesta.json()["codigo"] == "plan_no_encontrado"


def test_el_descarte_por_ruta_sigue_disponible(cliente: TestClient) -> None:
    cuerpo = _planificar(cliente)

    una = cuerpo["rutas"][0]["id"]
    assert cliente.delete(f"/v1/rutas/{una}").status_code == http.HTTPStatus.OK


# --- Jornadas (RB-11) -----------------------------------------------------


def test_las_jornadas_con_plan_se_enumeran(cliente: TestClient) -> None:
    """Es lo que permite navegar entre jornadas sin adivinar."""
    assert cliente.get("/v1/jornadas").json() == {"fechas": []}

    cuerpo = _planificar(cliente)

    assert cliente.get("/v1/jornadas").json() == {
        "fechas": [cuerpo["plan"]["fecha_planificacion"]]
    }


def test_al_descartar_el_ultimo_plan_la_jornada_desaparece(
    cliente: TestClient,
) -> None:
    """Una jornada tiene contenido cuando hubo una corrida que sigue vigente."""
    cuerpo = _planificar(cliente)
    cliente.delete(f"/v1/planes/{cuerpo['plan']['id']}")

    assert cliente.get("/v1/jornadas").json() == {"fechas": []}
