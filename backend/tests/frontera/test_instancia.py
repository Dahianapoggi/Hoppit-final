"""La Flota, el Deposito y el anclaje, legibles antes de que exista plan.

Los tres resuelven el mismo hueco visto desde lugares distintos: componer una
planificacion exige conocer los Vehiculos, ubicar el Deposito y saber a que
nodo engancha una coordenada, y todo eso ocurre **antes** de que haya Rutas.
Hasta ahora el contrato solo entregaba esos datos dentro del detalle de una
Ruta ya publicada (FE-02, FE-08, FE-10, FE-11, FE-15).
"""
from __future__ import annotations

import http
import logging

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.escenario_demostracion import MATRICULAS
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

_LOG = logging.getLogger(__name__)

VEHICULOS_DE_LA_INSTANCIA = 3
CAPACIDAD_POR_VEHICULO = 13
# floor(13 x 0,95): la capacidad efectiva con el margen de la operacion.
CAPACIDAD_EFECTIVA_CON_MARGEN = 12

# Coordenada sobre la via publica de San Isidro.
LAT = -34.4718
LON = -58.5074

# Cota generosa: lo que se verifica es que el enganche sea a una distancia
# razonable de un punto sobre la calle, no un valor exacto.
METROS_RAZONABLES = 100.0

# Un Vehiculo de la Flota real, identificado por su matricula. Las peticiones
# de estas pruebas nombran uno que el sistema tiene de verdad, no un nombre
# inventado que solo funcionaba porque el campo acepta cualquier clave.
MATRICULA = str(MATRICULAS[0])


@pytest.fixture(scope="module")
def base() -> ContextoDeLaApi:
    """Un solo armado para todo el modulo: cargar el grafo es lo caro."""
    return construir_contexto(
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


@pytest.fixture()
def contexto(base: ContextoDeLaApi) -> ContextoDeLaApi:
    """Sistema propio por prueba, reutilizando el grafo ya cargado.

    Las pruebas que planifican dejan las Ordenes en Planificada; sin estado
    propio, la siguiente encontraria el sistema trabado y estaria verificando
    algo distinto de lo que dice verificar.
    """
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


# --- Flota (FR-15, FE-10) -------------------------------------------------


def test_la_flota_se_lee_sin_que_exista_ningun_plan(cliente: TestClient) -> None:
    """El caso que bloqueaba: G1-B compone la instancia antes de calcular."""
    assert cliente.get("/v1/rutas").json() == []

    respuesta = cliente.get("/v1/flota")

    assert respuesta.status_code == http.HTTPStatus.OK
    flota = respuesta.json()
    assert len(flota["vehiculos"]) == VEHICULOS_DE_LA_INSTANCIA
    _LOG.info("Flota | %s", flota)


def test_sin_margen_la_capacidad_efectiva_es_la_nominal(cliente: TestClient) -> None:
    """El contexto de estas pruebas planifica sin margen (2026-10-20B, F1.4a)."""
    flota = cliente.get("/v1/flota").json()

    assert flota["margen_de_capacidad"] == 0
    for vehiculo in flota["vehiculos"]:
        assert vehiculo["capacidad_efectiva"] == vehiculo["capacidad"]
    assert flota["capacidad_efectiva_total"] == flota["capacidad_total"]


def test_con_el_margen_de_la_operacion_la_flota_publica_la_efectiva(
    base: ContextoDeLaApi,
) -> None:
    """Con el margen del producto (0,05), 13 bultos nominales son 12 efectivos,
    los mismos que cada Ruta informa como `capacidad_vehiculo` (B-242, punto
    5, propuesta 3)."""
    from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
    from hoppit.planificacion_rutas.puerto import (
        MARGEN_DE_LA_OPERACION,
        crear_servicio as crear_planificacion,
    )
    from hoppit.shared.bus import Bus
    from tools.escenario_demostracion import construir_ordenes

    bus = Bus()
    ordenes = crear_ordenes(bus)
    for orden in construir_ordenes(base.cartografia.nodos_de_mayor_componente_fuerte()):
        ordenes.dar_de_alta(orden)
    contexto = ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=crear_planificacion(
            bus, base.cartografia, ordenes, flota=base.flota,
            margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        ),
        cartografia=base.cartografia,
        deposito=base.deposito,
        flota=base.flota,
        identidad=identidad_efimera(),
    )
    cliente = cliente_autenticado(contexto)

    flota = cliente.get("/v1/flota").json()

    assert flota["margen_de_capacidad"] == MARGEN_DE_LA_OPERACION
    assert all(v["capacidad_efectiva"] == CAPACIDAD_EFECTIVA_CON_MARGEN for v in flota["vehiculos"])
    assert flota["capacidad_efectiva_total"] == CAPACIDAD_EFECTIVA_CON_MARGEN * VEHICULOS_DE_LA_INSTANCIA
    assert flota["capacidad_efectiva_disponible"] == flota["capacidad_efectiva_total"]

    seleccion = {v["id"]: v["capacidad"] for v in flota["vehiculos"]}
    respuesta = cliente.post("/v1/planificaciones", json={"vehiculos": seleccion})
    assert respuesta.status_code == http.HTTPStatus.CREATED
    for ruta in cliente.get("/v1/rutas").json():
        assert ruta["capacidad_vehiculo"] == CAPACIDAD_EFECTIVA_CON_MARGEN


def test_cada_vehiculo_declara_su_capacidad(cliente: TestClient) -> None:
    """Es lo que permite comparar demanda seleccionada contra capacidad."""
    flota = cliente.get("/v1/flota").json()

    for vehiculo in flota["vehiculos"]:
        assert vehiculo["id"]
        assert vehiculo["capacidad"] == CAPACIDAD_POR_VEHICULO


def test_la_capacidad_total_es_la_suma_y_no_un_dato_aparte(
    cliente: TestClient,
) -> None:
    """Derivada, para que no pueda discrepar de sus partes."""
    flota = cliente.get("/v1/flota").json()

    assert flota["capacidad_total"] == sum(
        v["capacidad"] for v in flota["vehiculos"]
    )


def test_los_vehiculos_de_la_flota_son_los_que_acepta_la_planificacion(
    cliente: TestClient,
) -> None:
    """Lo que se lee tiene que servir para lo que se envia: el contrato
    aceptaba la Flota en la solicitud y no la devolvia."""
    flota = cliente.get("/v1/flota").json()
    seleccion = {v["id"]: v["capacidad"] for v in flota["vehiculos"]}

    respuesta = cliente.post("/v1/planificaciones", json={"vehiculos": seleccion})

    assert respuesta.status_code == http.HTTPStatus.CREATED
    assert respuesta.json()["capacidad_total"] == flota["capacidad_total"]


# --- Disponibilidad de la Flota por jornada --------------------------------


def test_sin_planes_toda_la_flota_esta_disponible(cliente: TestClient) -> None:
    """Una jornada sin ningun Plan registrado no compromete a nadie."""
    flota = cliente.get("/v1/flota").json()

    assert all(v["disponible"] for v in flota["vehiculos"])
    assert all(v["comprometido_en"] is None for v in flota["vehiculos"])
    assert flota["capacidad_disponible"] == flota["capacidad_total"]


def test_un_vehiculo_con_ruta_en_la_jornada_queda_comprometido(
    cliente: TestClient,
) -> None:
    """La regla del glosario, publicada: un Vehiculo, un Plan por jornada.

    Se planifica con un solo Vehiculo y se vuelve a consultar la Flota: ese
    Vehiculo aparece comprometido y nombra el Plan que lo ocupa; los demas
    siguen libres.
    """
    plan = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ["orden-00"], "vehiculos": {MATRICULA: 20}},
    ).json()

    flota = cliente.get("/v1/flota").json()
    por_id = {v["id"]: v for v in flota["vehiculos"]}

    assert por_id[MATRICULA]["disponible"] is False
    assert por_id[MATRICULA]["comprometido_en"] == plan["plan"]["id"]
    otros = [v for v in flota["vehiculos"] if v["id"] != MATRICULA]
    assert all(v["disponible"] for v in otros)
    assert flota["capacidad_disponible"] == sum(v["capacidad"] for v in otros)
    _LOG.info("Flota con un Vehiculo comprometido | %s", flota)


def test_la_disponibilidad_depende_de_la_jornada_consultada(
    cliente: TestClient,
) -> None:
    """Un Vehiculo comprometido hoy esta libre en otra jornada.

    Es lo que hace que el dato sea util para componer: cambiar la jornada
    recalcula que esta disponible.
    """
    cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ["orden-00"], "vehiculos": {MATRICULA: 20}},
    )
    hoy = cliente.get("/v1/flota").json()
    otra = cliente.get("/v1/flota", params={"fecha": "2020-01-01"}).json()

    assert not next(v for v in hoy["vehiculos"] if v["id"] == MATRICULA)["disponible"]
    assert all(v["disponible"] for v in otra["vehiculos"])
    assert otra["fecha"] == "2020-01-01"
    assert otra["capacidad_disponible"] == otra["capacidad_total"]


def test_la_respuesta_declara_la_jornada_que_evaluo(cliente: TestClient) -> None:
    """Sin la fecha, la disponibilidad no significa nada: quien consulta sin
    indicarla no tiene por que adivinar contra que dia se resolvio."""
    from hoppit.shared.tiempo import fecha_operativa

    assert cliente.get("/v1/flota").json()["fecha"] == fecha_operativa().isoformat()


def test_descartar_el_plan_devuelve_el_vehiculo_a_disponible(
    cliente: TestClient,
) -> None:
    """La disponibilidad se deriva de las Rutas vigentes, no de una lista
    congelada: descartado el Plan, el Vehiculo vuelve a estar libre."""
    plan = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ["orden-00"], "vehiculos": {MATRICULA: 20}},
    ).json()
    cliente.delete(f"/v1/planes/{plan['plan']['id']}")

    flota = cliente.get("/v1/flota").json()

    assert all(v["disponible"] for v in flota["vehiculos"])
    assert flota["capacidad_disponible"] == flota["capacidad_total"]


def test_una_fecha_mal_formada_se_rechaza_con_el_formato_del_contrato(
    cliente: TestClient,
) -> None:
    respuesta = cliente.get("/v1/flota", params={"fecha": "no-es-una-fecha"})

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"


# --- Deposito (FR-14, FE-08, FE-11) ---------------------------------------


def test_los_depositos_se_publican_como_coleccion(cliente: TestClient) -> None:
    """Coleccion de un elemento, no objeto unico.

    El glosario admite varios Depositos (entidad 6) y FR-12 los contempla, de
    modo que publicarlo como objeto obligaria a un cambio incompatible el dia
    que haya mas de uno.
    """
    cuerpo = cliente.get("/v1/depositos").json()

    assert isinstance(cuerpo["depositos"], list)


def test_el_deposito_se_lee_sin_que_exista_ningun_plan(cliente: TestClient) -> None:
    """PUX-33: en ausencia de Rutas el mapa conserva visible el Deposito.

    Hasta ahora solo se obtenia dentro del detalle de una Ruta, asi que un
    sistema sin planificar no podia ni ubicarlo.
    """
    respuesta = cliente.get("/v1/depositos")

    assert respuesta.status_code == http.HTTPStatus.OK
    depositos = respuesta.json()["depositos"]
    # Fase 1 opera con un Deposito unico (B-12); la coleccion tiene uno.
    assert len(depositos) == 1
    punto = depositos[0]["punto"]
    assert punto["nodo"] > 0
    assert punto["rotulo"]["texto"]
    _LOG.info("Deposito | nodo=%s %s", punto["nodo"], punto["rotulo"]["texto"])


def test_el_deposito_publicado_es_el_que_usan_las_rutas(
    cliente: TestClient,
) -> None:
    """Si difirieran, el mapa dibujaria el origen en otro lugar que la Ruta."""
    del_recurso = cliente.get("/v1/depositos").json()["depositos"][0]["punto"]
    plan = cliente.post("/v1/planificaciones", json={}).json()
    de_la_ruta = cliente.get(f"/v1/rutas/{plan['rutas'][0]['id']}").json()["deposito"]

    assert del_recurso == de_la_ruta


# --- Anclaje (FE-15) ------------------------------------------------------


def test_el_anclaje_resuelve_el_nodo_y_la_distancia(cliente: TestClient) -> None:
    """El control previo: a que nodo engancha y a cuantos metros."""
    respuesta = cliente.get("/v1/anclajes", params={"lat": LAT, "lon": LON})

    assert respuesta.status_code == http.HTTPStatus.OK
    anclaje = respuesta.json()
    assert anclaje["consultado"] == {"lat": LAT, "lon": LON}
    assert anclaje["nodo"]["nodo"] > 0
    assert 0 < anclaje["distancia_m"] < METROS_RAZONABLES
    assert anclaje["nodo"]["rotulo"]["texto"]
    _LOG.info(
        "Anclaje | nodo=%s a %.1f m | %s",
        anclaje["nodo"]["nodo"],
        anclaje["distancia_m"],
        anclaje["nodo"]["rotulo"]["texto"],
    )


def test_consultar_el_anclaje_no_crea_nada(cliente: TestClient) -> None:
    """Es una consulta sin efecto: esa es toda su razon de ser."""
    antes = len(cliente.get("/v1/ordenes").json())

    cliente.get("/v1/anclajes", params={"lat": LAT, "lon": LON})

    assert len(cliente.get("/v1/ordenes").json()) == antes


def test_el_anclaje_previsualiza_lo_que_hara_el_alta(cliente: TestClient) -> None:
    """Tiene que resolver al mismo nodo que resolveria el alta.

    Si difirieran, el control previo mostraria una cosa y el registro haria
    otra, que es peor que no tener el control.
    """
    previsto = cliente.get("/v1/anclajes", params={"lat": LAT, "lon": LON}).json()

    creada = cliente.post(
        "/v1/ordenes", json={"lat": LAT, "lon": LON, "demanda": 1}
    ).json()

    assert creada["destino"]["nodo"] == previsto["nodo"]["nodo"]
    assert creada["destino"]["rotulo"] == previsto["nodo"]["rotulo"]


def test_el_anclaje_avisa_cuando_la_coordenada_cae_en_el_deposito(
    cliente: TestClient, contexto: ContextoDeLaApi
) -> None:
    """Permite advertirlo antes del intento, en vez de que el alta lo
    rechace despues (CU-01, flujo 4b)."""
    deposito = contexto.cartografia.coordenadas(contexto.deposito)

    anclaje = cliente.get(
        "/v1/anclajes", params={"lat": deposito.lat, "lon": deposito.lon}
    ).json()

    assert anclaje["es_el_deposito"]
    # Y en efecto, el alta sobre esa coordenada se rechaza.
    rechazo = cliente.post(
        "/v1/ordenes",
        json={"lat": deposito.lat, "lon": deposito.lon, "demanda": 1},
    )
    assert rechazo.json()["codigo"] == "destino_es_el_deposito"


def test_una_coordenada_fuera_de_rango_se_rechaza(cliente: TestClient) -> None:
    respuesta = cliente.get("/v1/anclajes", params={"lat": 200, "lon": 0})

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "peticion_mal_formada"


# --- Capacidad en los esquemas de Ruta (FE-02) ----------------------------


def test_la_ruta_publica_la_capacidad_del_vehiculo(cliente: TestClient) -> None:
    """El denominador de la ocupacion, en listado y en detalle.

    Sin este campo la carga se publicaba sin con que compararla, y HU-05 no
    se podia dar por cumplida.
    """
    plan = cliente.post("/v1/planificaciones", json={}).json()

    for ruta in cliente.get("/v1/rutas").json():
        assert ruta["capacidad_vehiculo"] == CAPACIDAD_POR_VEHICULO
        assert ruta["carga"] <= ruta["capacidad_vehiculo"]
        detalle = cliente.get(f"/v1/rutas/{ruta['id']}").json()
        assert detalle["capacidad_vehiculo"] == ruta["capacidad_vehiculo"]
    assert plan["rutas"]


def test_la_capacidad_de_la_ruta_es_la_que_se_uso_al_resolver(
    cliente: TestClient,
) -> None:
    """Viaja con la Ruta y no se lee de la Flota vigente al consultarla.

    Se planifica con una capacidad distinta de la de la Flota: la Ruta tiene
    que informar la que se uso, no la que la Flota declara ahora.
    """
    capacidad_usada = 40
    plan = cliente.post(
        "/v1/planificaciones", json={"vehiculos": {MATRICULA: capacidad_usada}}
    ).json()

    assert plan["rutas"][0]["capacidad_vehiculo"] == capacidad_usada
    assert cliente.get("/v1/flota").json()["vehiculos"][0]["capacidad"] == (
        CAPACIDAD_POR_VEHICULO
    )


# --- Estado de la Orden en el detalle de la Ruta --------------------------


def test_las_ordenes_de_una_parada_informan_su_estado(cliente: TestClient) -> None:
    """Evita una consulta adicional solo para mostrarlo.

    El modelo lo implica --una Orden que aparece en una Ruta esta
    Planificada-- pero implicarlo no es informarlo: sin el campo hay que
    deducirlo, y deducir un estado es afirmarlo sin dato.
    """
    plan = cliente.post("/v1/planificaciones", json={}).json()

    for ruta in plan["rutas"]:
        detalle = cliente.get(f"/v1/rutas/{ruta['id']}").json()
        for parada in detalle["paradas"]:
            for orden in parada["ordenes"]:
                assert orden["estado"] == "planificada"
                assert orden["demanda"] > 0


def test_el_estado_publicado_coincide_con_el_de_la_orden(
    cliente: TestClient,
) -> None:
    """Es el mismo dato visto desde dos lugares: no pueden discrepar."""
    plan = cliente.post("/v1/planificaciones", json={}).json()
    detalle = cliente.get(f"/v1/rutas/{plan['rutas'][0]['id']}").json()

    for parada in detalle["paradas"]:
        for orden in parada["ordenes"]:
            aparte = cliente.get(f"/v1/ordenes/{orden['id']}").json()
            assert orden["estado"] == aparte["estado"]
            assert orden["demanda"] == aparte["demanda"]
