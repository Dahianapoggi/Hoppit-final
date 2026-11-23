"""Contrato REST: camino feliz de cada endpoint y las dos condiciones de
error de CU-02 (RB-05).

Se ejercita la app real con TestClient, en proceso y sin servidor, sobre la
instancia de demostracion vigente. No se sustituye ningun container por un
doble: lo que hay que verificar es el contrato que se publica, y un doble
podria responder algo que el sistema real no responde.

Lo que se afirma es la forma y el significado del payload --que los campos
esten, que signifiquen lo que dicen y que los codigos de error distingan los
casos-- y no las cifras concretas de la instancia, que ya estan verificadas
donde se calculan.
"""
from __future__ import annotations

import datetime as dt
import http
import logging

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi, crear_app
from hoppit.gestion_ordenes.puerto import Orden
from hoppit.shared.ids import OrdenId
from tools.acceso_en_proceso import cliente_autenticado, identidad_efimera
from tools.escenario_demostracion import MATRICULAS
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

_LOG = logging.getLogger(__name__)

# Coordenada sobre la via publica de San Isidro. No es un nodo del grafo a
# proposito: el alta recibe lo que se marca en el mapa y el sistema resuelve
# el anclaje.
LAT_DE_ALTA = -34.4718
LON_DE_ALTA = -58.5074
DEMANDA_DE_ALTA = 2

# Nodo real del artefacto que queda fuera de la mayor componente fuertemente
# conexa: es lo que provoca el flujo 3a.
NODO_FUERA_DE_COMPONENTE = 113094208
ORDEN_INCOMUNICADA = OrdenId("orden-incomunicada")

# La Flota real de la instancia: sus Vehiculos se identifican por
# matricula. Se toma de ahi y no de un nombre inventado para que la
# peticion de la prueba sea una que el sistema acepte de verdad.
MATRICULA = str(MATRICULAS[0])
CAPACIDAD_INSUFICIENTE = {MATRICULA: 1}

ORDENES_DE_LA_INSTANCIA = 20
VEHICULOS_DE_LA_INSTANCIA = 3


@pytest.fixture(scope="module")
def contexto_base() -> ContextoDeLaApi:
    """Un unico armado del sistema para todo el modulo.

    Construirlo carga el grafo vial desde disco, que es lo caro; las pruebas
    que necesitan un sistema sin planificar usan `contexto_limpio`.

    Sin base de datos: lo que estas pruebas verifican es el contrato, no la
    persistencia --que tiene sus propias pruebas--. Apuntar a la base de
    desarrollo haria que el resultado dependiera del estado que dejo la
    ultima corrida del servidor, y ademas la ensuciaria.
    """
    return construir_contexto(
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


@pytest.fixture()
def cliente(contexto_base: ContextoDeLaApi) -> TestClient:
    return cliente_autenticado(contexto_base)


@pytest.fixture()
def contexto_limpio(contexto_base: ContextoDeLaApi) -> ContextoDeLaApi:
    """Sistema recien armado, sin nada planificado todavia.

    Reusa el grafo ya cargado --lo unico caro-- y rearma los servicios con
    estado propio, de modo que una planificacion hecha en una prueba no
    afecte a la siguiente.
    """
    from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
    from hoppit.planificacion_rutas.puerto import crear_servicio as crear_planificacion
    from hoppit.shared.bus import Bus
    from tools.escenario_demostracion import construir_ordenes

    bus = Bus()
    ordenes = crear_ordenes(bus)
    for orden in construir_ordenes(
        contexto_base.cartografia.nodos_de_mayor_componente_fuerte()
    ):
        ordenes.dar_de_alta(orden)
    return ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=crear_planificacion(bus, contexto_base.cartografia, ordenes),
        cartografia=contexto_base.cartografia,
        deposito=contexto_base.deposito,
        flota=contexto_base.flota,
        identidad=identidad_efimera(),
    )


def _planificar(cliente: TestClient) -> dict:
    respuesta = cliente.post("/v1/planificaciones", json={})
    assert respuesta.status_code == http.HTTPStatus.CREATED
    return respuesta.json()


# --- Ordenes: alta y consulta (CU-01, CU-04) ------------------------------


def test_alta_de_orden_ancla_el_destino_y_lo_informa(cliente: TestClient) -> None:
    """CU-01. La coordenada se traduce al nodo mas cercano de la red vial."""
    respuesta = cliente.post(
        "/v1/ordenes",
        json={"lat": LAT_DE_ALTA, "lon": LON_DE_ALTA, "demanda": DEMANDA_DE_ALTA},
    )

    assert respuesta.status_code == http.HTTPStatus.CREATED
    cuerpo = respuesta.json()
    assert cuerpo["demanda"] == DEMANDA_DE_ALTA
    assert cuerpo["estado"] == "creada"
    assert cuerpo["destino"]["nodo"] > 0
    assert cuerpo["destino"]["rotulo"]["texto"]
    # Todavia no fue planificada: la ausencia se declara, no se omite.
    assert cuerpo["asignacion"] is None
    # RB-06, CU-01 paso 6: nace con su transicion inicial asentada.
    assert [(t["estado_anterior"], t["estado_nuevo"]) for t in cuerpo["historial"]] == [
        (None, "creada")
    ]
    _LOG.info(
        "Alta | id=%s nodo=%s rotulo=%s",
        cuerpo["id"],
        cuerpo["destino"]["nodo"],
        cuerpo["destino"]["rotulo"]["texto"],
    )


def test_el_alta_asigna_el_identificador(cliente: TestClient) -> None:
    """Quien da de alta no aporta el identificador ni podria saber cual esta
    libre."""
    primera = cliente.post(
        "/v1/ordenes", json={"lat": LAT_DE_ALTA, "lon": LON_DE_ALTA, "demanda": 1}
    ).json()
    segunda = cliente.post(
        "/v1/ordenes", json={"lat": LAT_DE_ALTA, "lon": LON_DE_ALTA, "demanda": 1}
    ).json()

    assert primera["id"] != segunda["id"]


def test_listar_ordenes_devuelve_las_de_la_instancia(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    cliente = cliente_autenticado(contexto_limpio)
    respuesta = cliente.get("/v1/ordenes")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert len(respuesta.json()) == ORDENES_DE_LA_INSTANCIA


def test_listar_ordenes_filtra_por_estado(contexto_limpio: ContextoDeLaApi) -> None:
    """CU-04. Antes de planificar, todas estan Creadas y ninguna Planificada."""
    cliente = cliente_autenticado(contexto_limpio)

    creadas = cliente.get("/v1/ordenes", params={"estado": "creada"}).json()
    planificadas = cliente.get("/v1/ordenes", params={"estado": "planificada"}).json()

    assert len(creadas) == ORDENES_DE_LA_INSTANCIA
    assert planificadas == []


def test_consultar_una_orden_por_su_identificador(cliente: TestClient) -> None:
    respuesta = cliente.get("/v1/ordenes/orden-00")

    assert respuesta.status_code == http.HTTPStatus.OK
    assert respuesta.json()["id"] == "orden-00"


# --- Planificacion (CU-02) ------------------------------------------------


def test_planificar_devuelve_una_ruta_por_vehiculo(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """CU-02. El plan agrupa las Rutas pero no tiene identidad propia."""
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)

    assert len(plan["rutas"]) == VEHICULOS_DE_LA_INSTANCIA
    assert plan["distancia_total_m"] == pytest.approx(
        sum(ruta["distancia_m"] for ruta in plan["rutas"])
    )
    assert plan["demanda_total"] <= plan["capacidad_total"]
    assert "id" not in plan, "el plan de flota no es una entidad y no tiene id"


def test_planificar_deja_las_ordenes_informando_su_ruta_y_su_parada(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """RB-09, CU-04 pasos 2 y 4: la relacion directa despues de planificar."""
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)
    identificadores = {ruta["id"] for ruta in plan["rutas"]}

    planificadas = cliente.get("/v1/ordenes", params={"estado": "planificada"}).json()
    assert len(planificadas) == ORDENES_DE_LA_INSTANCIA
    for orden in planificadas:
        assert orden["asignacion"] is not None
        assert orden["asignacion"]["ruta_id"] in identificadores
        assert orden["asignacion"]["secuencia_parada"] >= 1


def test_planificar_un_subconjunto_de_ordenes_y_de_vehiculos(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """La seleccion acota la corrida: el resto de las Ordenes sigue Creada."""
    cliente = cliente_autenticado(contexto_limpio)
    respuesta = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ["orden-00", "orden-01"], "vehiculos": {MATRICULA: 20}},
    )

    assert respuesta.status_code == http.HTTPStatus.CREATED
    plan = respuesta.json()
    assert len(plan["rutas"]) == 1
    assert plan["rutas"][0]["cantidad_ordenes"] == 2

    restantes = cliente.get("/v1/ordenes", params={"estado": "creada"}).json()
    assert len(restantes) == ORDENES_DE_LA_INSTANCIA - 2


# --- Rutas y geometria (CU-03) --------------------------------------------


def test_listar_rutas_y_filtrar_por_fecha(contexto_limpio: ContextoDeLaApi) -> None:
    """RB-01. La fecha es un atributo de la Ruta y un criterio de filtro."""
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)
    fecha = plan["fecha_planificacion"]

    todas = cliente.get("/v1/rutas").json()
    del_dia = cliente.get("/v1/rutas", params={"fecha": fecha}).json()
    de_otro_dia = cliente.get(
        "/v1/rutas", params={"fecha": str(dt.date.fromisoformat(fecha) - dt.timedelta(days=1))}
    )

    assert len(todas) == VEHICULOS_DE_LA_INSTANCIA
    assert len(del_dia) == VEHICULOS_DE_LA_INSTANCIA
    # Un dia sin Rutas es una consulta legitima, no un error.
    assert de_otro_dia.status_code == http.HTTPStatus.OK
    assert de_otro_dia.json() == []


def test_el_listado_de_rutas_no_trae_la_geometria(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """Son cientos de coordenadas por Ruta: el trazado va en el detalle."""
    cliente = cliente_autenticado(contexto_limpio)
    _planificar(cliente)

    for ruta in cliente.get("/v1/rutas").json():
        assert "trazado" not in ruta


def test_la_ruta_trae_su_geometria_dibujable(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """FR-02, NFR-08. Lo mas distintivo del contrato.

    La polilinea tiene que tener muchos mas puntos que Paradas: si tuviera
    uno por Parada, seria la recta entre ellas y no el recorrido real.
    """
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)
    id_ruta = plan["rutas"][0]["id"]

    respuesta = cliente.get(f"/v1/rutas/{id_ruta}")
    assert respuesta.status_code == http.HTTPStatus.OK
    ruta = respuesta.json()
    trazado = ruta["trazado"]

    assert len(trazado["coordenadas"]) > len(ruta["paradas"])
    assert trazado["arcos"] > 0
    # Se dibuja lo que se ruteo.
    assert trazado["distancia_m"] == pytest.approx(ruta["distancia_m"])
    # Pares (latitud, longitud), no un formato de libreria de mapas.
    assert all(len(punto) == 2 for punto in trazado["coordenadas"])
    _LOG.info(
        "Trazado publicado | ruta=%s paradas=%d puntos=%d arcos=%d",
        id_ruta,
        len(ruta["paradas"]),
        len(trazado["coordenadas"]),
        trazado["arcos"],
    )


def test_el_recorrido_sale_del_deposito_y_vuelve(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """La Ruta parte del Deposito y retorna a el (glosario 3)."""
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)
    ruta = cliente.get(f"/v1/rutas/{plan['rutas'][0]['id']}").json()

    deposito = (ruta["deposito"]["lat"], ruta["deposito"]["lon"])
    coordenadas = ruta["trazado"]["coordenadas"]
    assert tuple(coordenadas[0]) == deposito
    assert tuple(coordenadas[-1]) == deposito


def test_las_paradas_llevan_su_rotulo_y_sus_ordenes(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """RB-02 y RB-04 en el payload: como se nombra la Parada y que entrega."""
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)

    paradas = [
        parada
        for ruta in plan["rutas"]
        for parada in cliente.get(f"/v1/rutas/{ruta['id']}").json()["paradas"]
    ]
    for parada in paradas:
        assert parada["punto"]["rotulo"]["texto"]
        assert parada["punto"]["rotulo"]["origen"] in {
            "cruce_de_calles",
            "via_unica",
            "coordenadas",
        }
        assert parada["ordenes"]
        assert parada["demanda"] == sum(o["demanda"] for o in parada["ordenes"])
        # Este contexto se arma sin servicio de Prediccion de ETA: el ETA y
        # su banda van ausentes, declarados y no en cero. Con servicio, ver
        # la seccion del ETA.
        assert parada["eta_estimado_s"] is None
        assert parada["eta_p10_s"] is None
        assert parada["eta_p90_s"] is None

    agrupadas = [parada for parada in paradas if len(parada["ordenes"]) > 1]
    assert len(agrupadas) >= 2, "la instancia vigente agrupa Ordenes (RB-04)"
    for parada in agrupadas:
        # Una sola visita: todas sus Ordenes comparten numero de Parada.
        for orden in parada["ordenes"]:
            publicada = cliente.get(f"/v1/ordenes/{orden['id']}").json()
            assert publicada["asignacion"]["secuencia_parada"] == parada["secuencia"]


# --- ETA de las Paradas (B-193, punto 3) ----------------------------------


def _con_eta(
    contexto: ContextoDeLaApi, base: ContextoDeLaApi, hora_de_salida: dt.time | None = None
) -> ContextoDeLaApi:
    """El mismo sistema, con el servicio de Prediccion de ETA que arma
    `construir_contexto` sobre la tabla vigente, con su velocidad supuesta."""
    return ContextoDeLaApi(
        ordenes=contexto.ordenes,
        planificacion=contexto.planificacion,
        cartografia=contexto.cartografia,
        deposito=contexto.deposito,
        flota=contexto.flota,
        prediccion_eta=base.prediccion_eta,
        hora_de_salida=hora_de_salida or base.hora_de_salida,
        velocidad_km_h=base.velocidad_km_h,
        identidad=identidad_efimera(),
    )


def _rutas_publicadas(cliente: TestClient) -> list[dict]:
    plan = _planificar(cliente)
    return [cliente.get(f"/v1/rutas/{ruta['id']}").json() for ruta in plan["rutas"]]


def test_con_servicio_cada_parada_publica_su_eta_y_su_banda(
    contexto_limpio: ContextoDeLaApi, contexto_base: ContextoDeLaApi
) -> None:
    """La Ruta publica la hora de salida, y cada Parada la mediana y la banda
    de su primera entrega, en segundos desde la salida: p10 <= mediana <= p90,
    y la mediana crece a lo largo de la Ruta."""
    cliente = cliente_autenticado(_con_eta(contexto_limpio, contexto_base))

    for ruta in _rutas_publicadas(cliente):
        assert ruta["hora_de_salida"] == contexto_base.hora_de_salida.isoformat()
        medianas = []
        for parada in ruta["paradas"]:
            assert parada["eta_p10_s"] <= parada["eta_estimado_s"] <= parada["eta_p90_s"]
            medianas.append(parada["eta_estimado_s"])
        assert medianas == sorted(medianas)
        assert len(set(medianas)) == len(medianas)


def test_sin_servicio_el_eta_y_la_hora_de_salida_van_ausentes(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """Como arma el sistema `bootstrap`: sin servicio no hay ETA que publicar,
    y no se publica uno inventado."""
    cliente = cliente_autenticado(contexto_limpio)

    for ruta in _rutas_publicadas(cliente):
        assert ruta["hora_de_salida"] is None
        for parada in ruta["paradas"]:
            assert (parada["eta_estimado_s"], parada["eta_p10_s"], parada["eta_p90_s"]) == (
                None,
                None,
                None,
            )


def test_dos_lecturas_de_la_misma_ruta_dan_lo_mismo(
    contexto_limpio: ContextoDeLaApi, contexto_base: ContextoDeLaApi
) -> None:
    """El ETA se calcula al responder con semilla fija: leer dos veces la
    misma Ruta no lo cambia (NFR-07)."""
    cliente = cliente_autenticado(_con_eta(contexto_limpio, contexto_base))
    plan = _planificar(cliente)

    for ruta in plan["rutas"]:
        primera = cliente.get(f"/v1/rutas/{ruta['id']}").json()
        segunda = cliente.get(f"/v1/rutas/{ruta['id']}").json()
        assert primera == segunda


def test_si_el_estimador_rechaza_la_ruta_el_eta_va_ausente_y_queda_en_el_log(
    contexto_limpio: ContextoDeLaApi,
    contexto_base: ContextoDeLaApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Saliendo a las 23:00, la primera entrega necesita la franja de las 23,
    fuera de la jornada: las Paradas van sin ETA, sin valor de reemplazo, y el
    rechazo se asienta en el log."""
    cliente = cliente_autenticado(_con_eta(contexto_limpio, contexto_base, hora_de_salida=dt.time(23)))

    with caplog.at_level(logging.WARNING):
        rutas = _rutas_publicadas(cliente)

    for ruta in rutas:
        for parada in ruta["paradas"]:
            assert (parada["eta_estimado_s"], parada["eta_p10_s"], parada["eta_p90_s"]) == (
                None,
                None,
                None,
            )
        assert any(
            "ETA ausente" in registro.getMessage() and ruta["id"] in registro.getMessage()
            for registro in caplog.records
        )


# --- Desasignacion y replanificacion (FR-03 inverso) ----------------------


def test_invalidar_una_ruta_libera_todas_sus_ordenes(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    cliente = cliente_autenticado(contexto_limpio)
    plan = _planificar(cliente)
    id_ruta = plan["rutas"][0]["id"]
    cantidad = plan["rutas"][0]["cantidad_ordenes"]

    respuesta = cliente.delete(f"/v1/rutas/{id_ruta}")

    assert respuesta.status_code == http.HTTPStatus.OK
    cuerpo = respuesta.json()
    assert cuerpo["ruta_invalidada"] == id_ruta
    assert len(cuerpo["ordenes_liberadas"]) == cantidad
    # La Ruta deja de existir, no queda marcada.
    assert cliente.get(f"/v1/rutas/{id_ruta}").status_code == http.HTTPStatus.NOT_FOUND
    for id_orden in cuerpo["ordenes_liberadas"]:
        orden = cliente.get(f"/v1/ordenes/{id_orden}").json()
        assert orden["estado"] == "creada"
        assert orden["asignacion"] is None


def test_desasignar_una_orden_invalida_su_ruta_e_informa_el_alcance(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """Desasignar una libera a todas las de su Ruta, y la respuesta lo dice."""
    cliente = cliente_autenticado(contexto_limpio)
    _planificar(cliente)
    orden = cliente.get("/v1/ordenes", params={"estado": "planificada"}).json()[0]
    su_ruta = orden["asignacion"]["ruta_id"]

    respuesta = cliente.post(f"/v1/ordenes/{orden['id']}/desasignacion")

    assert respuesta.status_code == http.HTTPStatus.OK
    cuerpo = respuesta.json()
    assert cuerpo["ruta_invalidada"] == su_ruta
    assert orden["id"] in cuerpo["ordenes_liberadas"]
    assert len(cuerpo["ordenes_liberadas"]) > 1


def test_se_puede_planificar_de_nuevo_tras_liberar_todo(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """El riesgo de la demostracion, resuelto de punta a punta.

    Sin liberar, la segunda corrida no tiene nada que planificar y lo dice
    con su codigo. Liberadas las Ordenes, vuelve a producir el mismo plan
    (NFR-07).
    """
    cliente = cliente_autenticado(contexto_limpio)
    primera = _planificar(cliente)

    trabada = cliente.post("/v1/planificaciones", json={})
    assert trabada.status_code == http.HTTPStatus.CONFLICT
    assert trabada.json()["codigo"] == "sin_ordenes_planificables"

    for ruta in primera["rutas"]:
        cliente.delete(f"/v1/rutas/{ruta['id']}")
    assert cliente.get("/v1/rutas").json() == []
    assert len(cliente.get("/v1/ordenes", params={"estado": "creada"}).json()) == (
        ORDENES_DE_LA_INSTANCIA
    )

    segunda = _planificar(cliente)
    assert segunda["distancia_total_m"] == pytest.approx(primera["distancia_total_m"])
    assert [r["id"] for r in segunda["rutas"]] == [r["id"] for r in primera["rutas"]]


def test_el_historial_conserva_la_ida_y_la_vuelta(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """FR-03: la desasignacion es una transicion y queda asentada."""
    cliente = cliente_autenticado(contexto_limpio)
    primera = _planificar(cliente)
    for ruta in primera["rutas"]:
        cliente.delete(f"/v1/rutas/{ruta['id']}")
    _planificar(cliente)

    historial = cliente.get("/v1/ordenes/orden-00").json()["historial"]
    assert [(t["estado_anterior"], t["estado_nuevo"]) for t in historial] == [
        (None, "creada"),
        ("creada", "planificada"),
        ("planificada", "creada"),
        ("creada", "planificada"),
    ]


def test_desasignar_una_orden_no_planificada_se_rechaza(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    cliente = cliente_autenticado(contexto_limpio)
    respuesta = cliente.post("/v1/ordenes/orden-00/desasignacion")

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "orden_no_asignada"


# --- Condiciones de error -------------------------------------------------


def test_flujo_4b_rechaza_una_orden_con_destino_en_el_deposito(
    cliente: TestClient, contexto_base: ContextoDeLaApi
) -> None:
    """CU-01 flujo 4b: el Deposito es el origen de la Ruta, no un destino."""
    deposito = contexto_base.cartografia.coordenadas(contexto_base.deposito)
    respuesta = cliente.post(
        "/v1/ordenes", json={"lat": deposito.lat, "lon": deposito.lon, "demanda": 1}
    )

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    assert respuesta.json()["codigo"] == "destino_es_el_deposito"


@pytest.mark.parametrize("demanda", [0, -1, 1.5])
def test_una_demanda_que_no_es_un_entero_positivo_se_rechaza(
    cliente: TestClient, demanda: object
) -> None:
    """La demanda es una cantidad de bultos, entero y positiva (B-55)."""
    respuesta = cliente.post(
        "/v1/ordenes", json={"lat": LAT_DE_ALTA, "lon": LON_DE_ALTA, "demanda": demanda}
    )

    assert respuesta.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY


def test_flujo_4a_demanda_que_excede_la_capacidad(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """CU-02 flujo 4a (RB-05) cuando no entra ninguna Orden: con su causa,
    sin plan. Si entra alguna, el Plan es parcial (B-220, punto 2)."""
    cliente = cliente_autenticado(contexto_limpio)
    alta = cliente.post(
        "/v1/ordenes", json={"lat": LAT_DE_ALTA, "lon": LON_DE_ALTA, "demanda": 2}
    )
    respuesta = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": [alta.json()["id"]], "vehiculos": CAPACIDAD_INSUFICIENTE},
    )

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "demanda_excede_capacidad"
    detalle = cuerpo["detalle"]
    assert detalle["demanda_total"] > detalle["capacidad_total"]
    assert detalle["faltante"] == detalle["demanda_total"] - detalle["capacidad_total"]

    # No se produjo plan: ni completo ni parcial.
    assert cliente.get("/v1/rutas").json() == []
    _LOG.info("Flujo 4a por API | %s", detalle)


def test_flujo_4a_con_alguna_orden_que_entra_da_un_plan_parcial(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """B-220, punto 2: la demanda que excede la capacidad ya no rechaza la
    planificacion. El Plan sale con 201, declara las Ordenes que quedaron
    afuera con su criticidad y su motivo, y esas Ordenes siguen en Creada."""
    cliente = cliente_autenticado(contexto_limpio)
    respuesta = cliente.post(
        "/v1/planificaciones", json={"vehiculos": CAPACIDAD_INSUFICIENTE}
    )

    assert respuesta.status_code == http.HTTPStatus.CREATED
    cuerpo = respuesta.json()
    afuera = cuerpo["ordenes_no_planificadas"]
    planificadas = {
        orden["id"]
        for orden in cliente.get("/v1/ordenes", params={"estado": "planificada"}).json()
    }
    creadas = {
        orden["id"]
        for orden in cliente.get("/v1/ordenes", params={"estado": "creada"}).json()
    }
    assert cuerpo["demanda_total"] <= CAPACIDAD_INSUFICIENTE[MATRICULA]
    assert planificadas and afuera
    assert {fuera["orden"] for fuera in afuera} == creadas
    assert all(fuera["motivo"] == "demanda_excede_capacidad" for fuera in afuera)
    # Sin prioridad ni plazo declarados, todas son de prioridad media.
    assert all(fuera["criticidad"] == 2 for fuera in afuera)
    _LOG.info("Plan parcial por API | %d afuera", len(afuera))


def test_flujo_3a_una_parada_sin_camino_con_el_resto(
    contexto_limpio: ContextoDeLaApi,
) -> None:
    """CU-02 flujo 3a (RB-05): con la Parada causante rotulada, sin plan.

    La Orden incomunicada se da de alta por el servicio y no por la API: el
    alta ancla siempre dentro de la componente fuertemente conexa, justamente
    para que no se puedan crear Ordenes inalcanzables. El caso se prepara
    desde adentro, pero la respuesta es la que la API devuelve.
    """
    contexto_limpio.ordenes.dar_de_alta(
        Orden(id=ORDEN_INCOMUNICADA, demanda=1, destino=NODO_FUERA_DE_COMPONENTE)
    )
    cliente = cliente_autenticado(contexto_limpio)
    respuesta = cliente.post("/v1/planificaciones", json={})

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "paradas_incomunicadas"
    detalle = cuerpo["detalle"]
    assert detalle["motivo"] == "fuera_de_la_componente"
    assert [p["nodo"] for p in detalle["paradas"]] == [NODO_FUERA_DE_COMPONENTE]
    # La Parada viene rotulada para poder senalarla en la pantalla.
    assert detalle["paradas"][0]["rotulo"]

    assert cliente.get("/v1/rutas").json() == []
    _LOG.info("Flujo 3a por API | %s", detalle)


def test_los_dos_flujos_de_cu02_se_distinguen_por_codigo(
    contexto_base: ContextoDeLaApi,
) -> None:
    """RB-05: la interfaz tiene que poder mostrar causa y correccion
    distintas, y el codigo --no el texto-- es lo que se lo permite."""
    from hoppit.frontera.api_rest.puerto import CodigoDeError

    assert (
        CodigoDeError.PARADAS_INCOMUNICADAS.value
        != CodigoDeError.DEMANDA_EXCEDE_CAPACIDAD.value
    )


def test_consultar_lo_que_no_existe_devuelve_404(cliente: TestClient) -> None:
    orden = cliente.get("/v1/ordenes/orden-inexistente")
    ruta = cliente.get("/v1/rutas/ruta-inexistente")

    assert orden.status_code == http.HTTPStatus.NOT_FOUND
    assert orden.json()["codigo"] == "orden_no_encontrada"
    assert ruta.status_code == http.HTTPStatus.NOT_FOUND
    assert ruta.json()["codigo"] == "ruta_no_encontrada"


def test_planificar_sin_ordenes_planificables_lo_declara(
    contexto_base: ContextoDeLaApi,
) -> None:
    """Un sistema sin Ordenes no tiene instancia que resolver."""
    from hoppit.gestion_ordenes.puerto import crear_servicio as crear_ordenes
    from hoppit.planificacion_rutas.puerto import crear_servicio as crear_planificacion
    from hoppit.shared.bus import Bus

    bus = Bus()
    ordenes = crear_ordenes(bus)
    vacio = ContextoDeLaApi(
        ordenes=ordenes,
        planificacion=crear_planificacion(bus, contexto_base.cartografia, ordenes),
        cartografia=contexto_base.cartografia,
        deposito=contexto_base.deposito,
        flota=contexto_base.flota,
        identidad=identidad_efimera(),
    )
    respuesta = cliente_autenticado(vacio).post("/v1/planificaciones", json={})

    assert respuesta.status_code == http.HTTPStatus.CONFLICT
    assert respuesta.json()["codigo"] == "sin_ordenes_planificables"


# --- El contrato publicado ------------------------------------------------


def test_el_esquema_openapi_se_genera(cliente: TestClient) -> None:
    """NFR-06: contrato documentado y autogenerado, no redactado a mano."""
    esquema = cliente.get("/openapi.json")

    assert esquema.status_code == http.HTTPStatus.OK
    rutas = esquema.json()["paths"]
    assert {"/v1/ordenes", "/v1/ordenes/{id_orden}", "/v1/planificaciones", "/v1/rutas",
            "/v1/rutas/{id_ruta}"} <= set(rutas)


def test_toda_ruta_de_dominio_esta_bajo_el_prefijo_de_version(
    cliente: TestClient,
) -> None:
    """Sin version en la ruta, un cambio incompatible dejaria a quien consume
    la API sin forma de saber contra que version construyo.

    La unica excepcion es el endpoint de descubrimiento, que tiene su propia
    prueba: preguntar que version corre no puede exigir conocerla.
    """
    from hoppit.frontera.api_rest._aplicacion import PREFIJO_DE_VERSION, RUTA_DE_ESTADO

    rutas = cliente.get("/openapi.json").json()["paths"]
    assert rutas
    de_dominio = [ruta for ruta in rutas if ruta != RUTA_DE_ESTADO]
    assert all(ruta.startswith(PREFIJO_DE_VERSION) for ruta in de_dominio)
    assert len(de_dominio) < len(rutas), "el endpoint de descubrimiento tiene que estar"


def test_la_ruta_de_dominio_sin_version_no_responde(cliente: TestClient) -> None:
    """Que no responda es la senal: obliga a nombrar la version."""
    assert cliente.get("/ordenes").status_code == http.HTTPStatus.NOT_FOUND


def test_el_estado_responde_con_y_sin_prefijo_de_version(
    cliente: TestClient,
) -> None:
    """Descubrimiento: saber que contrato corre sin conocerlo de antemano.

    Las dos rutas se declaran con la misma funcion, de modo que no puedan
    responder cosas distintas. `/v1/estado` se conserva porque el frontend ya
    lo consume y romperlo sin necesidad seria gratuito.
    """
    sin_version = cliente.get("/estado")
    con_version = cliente.get("/v1/estado")

    assert sin_version.status_code == http.HTTPStatus.OK
    assert con_version.status_code == http.HTTPStatus.OK
    assert sin_version.json() == con_version.json()


def test_cors_habilitado_para_un_origen_distinto(cliente: TestClient) -> None:
    """El frontend corre en otro origen: sin CORS falla toda llamada."""
    respuesta = cliente.options(
        "/v1/ordenes",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert respuesta.status_code == http.HTTPStatus.OK
    assert respuesta.headers["access-control-allow-origin"] == "*"
