"""CU-07 con zona afectada (encargo 2026-11-23B, bloque K): el Incidente puede
declarar un punto y un radio; la propuesta de recalculo no usa los tramos que
pasan por ahi, y si una Parada pendiente o el regreso quedan sin camino se
informa la imposibilidad (3a) y se conserva el plan vigente. Instancia
canonica en San Isidro."""
from __future__ import annotations

import http
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, ZonaCircular, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from hoppit.shared.ids import RutaId
from hoppit.vigilancia_sla.puerto import (
    AlcanceDelIncidente,
    Incidente,
    RepositorioIncidentesSQLite,
    TipoDeIncidente,
    ZonaAfectada,
    ZonaAfectadaInvalida,
)
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

RADIO_DEL_CORTE_M = 25.0
RADIO_QUE_AISLA_M = 60.0


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def armado(grafo: GrafoVialAMBA) -> tuple[ContextoDeLaApi, TestClient, dict[str, Any]]:
    """Operador y la Ruta mas larga despachada, con la Parada 1 cumplida (la 2
    en curso)."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    operador = cliente_autenticado(contexto)
    cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [operador.get(f"/v1/rutas/{r['id']}").json() for r in operador.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    (persona,) = operador.get("/v1/transportistas").json()
    assert operador.post(f"/v1/rutas/{ruta['id']}/despacho", json={"transportista": persona["id"]}).status_code == 200
    assert operador.post(f"/v1/rutas/{ruta['id']}/paradas/1/resultado", json={"resultado": "cumplida"}).status_code == 200
    return contexto, operador, ruta


def _incidente(operador: TestClient, ruta: str, zona: dict[str, float] | None) -> Any:
    cuerpo: dict[str, Any] = {"tipo": "corte_de_calle", "alcance": "parcial", "descripcion": "Corte",
                              "rutas_afectadas": [ruta]}
    if zona is not None:
        cuerpo["zona_afectada"] = zona
    return operador.post("/v1/incidentes", json=cuerpo)


def _proponer(operador: TestClient, ruta: str, zona: dict[str, float] | None) -> Any:
    incidente = _incidente(operador, ruta, zona)
    assert incidente.status_code == http.HTTPStatus.CREATED, incidente.text
    return operador.post(f"/v1/incidentes/{incidente.json()['id']}/propuestas", json={"ruta_id": ruta})


def test_una_zona_que_corta_un_tramo_se_esquiva_y_la_distancia_sube(armado, grafo) -> None:  # type: ignore[no-untyped-def]
    _, operador, ruta = armado
    nodos_de_paradas = {p["punto"]["nodo"] for p in ruta["paradas"]}
    en_curso, siguiente = ruta["paradas"][1]["punto"]["nodo"], ruta["paradas"][2]["punto"]["nodo"]
    camino = grafo.matriz_de_costos([en_curso, siguiente]).camino(en_curso, siguiente)
    intermedios = [a.destino for a in camino.arcos[:-1] if a.destino not in nodos_de_paradas]
    assert intermedios, "el tramo de la Parada en curso a la siguiente no tiene nodos intermedios"
    corte = grafo.coordenadas(intermedios[len(intermedios) // 2])
    zona = {"lat": corte.lat, "lon": corte.lon, "radio_m": RADIO_DEL_CORTE_M}
    excluidos = grafo.arcos_en_la_zona(ZonaCircular(corte.lat, corte.lon, RADIO_DEL_CORTE_M))
    assert excluidos
    esquiva = grafo.matriz_de_costos_evitando([en_curso, siguiente], ZonaCircular(corte.lat, corte.lon, RADIO_DEL_CORTE_M))
    assert not {(a.origen, a.destino) for a in esquiva.camino(en_curso, siguiente).arcos} & {(u, v) for u, v, _ in excluidos}
    assert esquiva.distancia_m(en_curso, siguiente) > camino.distancia_m

    sin_zona = _proponer(operador, ruta["id"], None)
    con_zona = _proponer(operador, ruta["id"], zona)
    assert sin_zona.status_code == con_zona.status_code == http.HTTPStatus.CREATED, con_zona.text
    assert con_zona.json()["distancia_vigente_m"] == pytest.approx(sin_zona.json()["distancia_vigente_m"])
    assert con_zona.json()["distancia_propuesta_m"] > sin_zona.json()["distancia_propuesta_m"]
    publicado = operador.get(f"/v1/incidentes/{con_zona.json()['incidente_id']}").json()
    assert publicado["zona_afectada"] == zona


def test_una_zona_que_aisla_una_parada_pendiente_informa_3a_y_conserva_el_plan(armado, grafo) -> None:  # type: ignore[no-untyped-def]
    _, operador, ruta = armado
    pendiente = ruta["paradas"][-1]["punto"]
    antes = operador.get(f"/v1/rutas/{ruta['id']}").json()
    r = _proponer(operador, ruta["id"], {"lat": pendiente["lat"], "lon": pendiente["lon"], "radio_m": RADIO_QUE_AISLA_M})
    assert r.json()["codigo"] == "sin_alternativa_factible", r.text
    assert "zona afectada" in r.json()["mensaje"] and "se conserva el plan vigente" in r.json()["mensaje"]
    assert operador.get(f"/v1/rutas/{ruta['id']}").json() == antes


def test_sin_zona_el_incidente_y_la_propuesta_son_los_de_siempre(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, ruta = armado
    incidente = _incidente(operador, ruta["id"], None).json()
    assert "zona_afectada" not in incidente
    primera = operador.post(f"/v1/incidentes/{incidente['id']}/propuestas", json={"ruta_id": ruta["id"]}).json()
    segunda = _proponer(operador, ruta["id"], None).json()
    assert primera["distancia_propuesta_m"] == segunda["distancia_propuesta_m"]
    assert primera["plan_propuesto"] == segunda["plan_propuesto"]


def test_el_radio_fuera_de_rango_se_rechaza(armado) -> None:  # type: ignore[no-untyped-def]
    _, operador, ruta = armado
    for radio in (19.9, 1000.1):
        r = _incidente(operador, ruta["id"], {"lat": -34.47, "lon": -58.52, "radio_m": radio})
        assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY
    with pytest.raises(ZonaAfectadaInvalida):
        ZonaAfectada(-34.47, -58.52, 5.0)


def test_la_zona_persiste_en_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import datetime as dt

    from hoppit.shared.ids import IncidenteId

    conexion = sqlite3.connect(tmp_path / "i.db")
    repo = RepositorioIncidentesSQLite(conexion)
    zona = ZonaAfectada(-34.47, -58.52, 120.0)
    momento = dt.datetime(2026, 11, 23, 12, tzinfo=dt.timezone.utc)
    repo.guardar(Incidente(IncidenteId("inc-1"), TipoDeIncidente.CORTE_DE_CALLE, AlcanceDelIncidente.PARCIAL, "",
                           (RutaId("r1"),), momento, zona))
    repo.guardar(Incidente(IncidenteId("inc-2"), TipoDeIncidente.CORTE_DE_CALLE, AlcanceDelIncidente.PARCIAL, "",
                           (RutaId("r1"),), momento))
    otro = RepositorioIncidentesSQLite(conexion)
    assert otro.obtener(IncidenteId("inc-1")).zona == zona
    assert otro.obtener(IncidenteId("inc-2")).zona is None


def test_una_zona_centrada_en_la_posicion_del_vehiculo_da_el_error_nuevo_y_no_3a(armado) -> None:  # type: ignore[no-untyped-def]
    """D33 (encargo 2026-11-23E): el Vehiculo informo su posicion (la de la
    Parada 1, recien cumplida, hace 5 s) y el Incidente declara una zona
    centrada ahi: 422 `zona_contiene_al_vehiculo`, no 3a, y nada cambia."""
    from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo

    contexto, operador, ruta = armado
    cumplida = ruta["paradas"][0]["punto"]
    contexto.lector_de_posicion = lambda _ruta: PosicionDelVehiculo(cumplida["lat"], cumplida["lon"], 5.0)
    antes = operador.get(f"/v1/rutas/{ruta['id']}").json()
    r = _proponer(operador, ruta["id"], {"lat": cumplida["lat"], "lon": cumplida["lon"], "radio_m": RADIO_QUE_AISLA_M})
    assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY, r.text
    assert r.json()["codigo"] == "zona_contiene_al_vehiculo"
    assert r.json()["mensaje"] == ("La zona afectada contiene la posición del Vehículo: declarar el punto del corte, "
                                   "no la posición del Vehículo")
    assert operador.get(f"/v1/rutas/{ruta['id']}").json() == antes
    # Sin telemetria el origen es la Parada en curso: una zona ahi da el mismo error.
    contexto.lector_de_posicion = None
    en_curso = ruta["paradas"][1]["punto"]
    r = _proponer(operador, ruta["id"], {"lat": en_curso["lat"], "lon": en_curso["lon"], "radio_m": RADIO_DEL_CORTE_M})
    assert r.json()["codigo"] == "zona_contiene_al_vehiculo", r.text


def test_sin_telemetria_el_texto_nombra_la_parada_en_curso(armado) -> None:  # type: ignore[no-untyped-def]
    """Consejo del 23E, J18: sin posicion reciente el origen es la Parada en
    curso y el texto lo dice."""
    _, operador, ruta = armado
    en_curso = ruta["paradas"][1]["punto"]
    r = _proponer(operador, ruta["id"], {"lat": en_curso["lat"], "lon": en_curso["lon"], "radio_m": RADIO_DEL_CORTE_M})
    assert r.json()["codigo"] == "zona_contiene_al_vehiculo", r.text
    assert r.json()["mensaje"].startswith("La zona afectada contiene el origen del recálculo (la Parada en curso")


def test_una_zona_que_corta_una_salida_pero_no_todas_calcula_la_propuesta(armado, grafo) -> None:  # type: ignore[no-untyped-def]
    """D34 (encargo 2026-11-23F): el Vehiculo informa su posicion en un nodo con
    salidas de al menos 60 m (el mas cercano a la Parada en curso); una zona de
    20 m en la punta de una salida corta esa y no todas: la propuesta se
    calcula sobre el grafo sin la zona y su camino no usa el arco cortado."""
    from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo

    contexto, operador, ruta = armado
    en_curso = ruta["paradas"][1]["punto"]
    componente = set(grafo.nodos_de_mayor_componente_fuerte())

    def apto(n: int) -> bool:
        salidas = grafo.arcos_de_salida(n)
        return (len({v for _, v, _ in salidas}) >= 2 and all(v in componente for _, v, _ in salidas)
                and min(grafo.costo(u, v).distancia_m for u, v, _ in salidas) >= 60.0)

    def cerca(n: int) -> float:
        c = grafo.coordenadas(n)
        return abs(c.lat - en_curso["lat"]) + abs(c.lon - en_curso["lon"])
    origen = min((n for n in componente if apto(n)), key=cerca)
    salidas = grafo.arcos_de_salida(origen)
    _, v, _ = sorted(salidas)[0]
    punta = grafo.coordenadas(v)
    zona = ZonaCircular(punta.lat, punta.lon, 20.0)
    cortadas = grafo.arcos_en_la_zona(zona)
    assert (origen, v) in {(a, b) for a, b, _ in cortadas} and not salidas <= cortadas
    o = grafo.coordenadas(origen)
    contexto.lector_de_posicion = lambda _ruta: PosicionDelVehiculo(o.lat, o.lon, 5.0)
    r = _proponer(operador, ruta["id"], {"lat": punta.lat, "lon": punta.lon, "radio_m": 20.0})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    assert r.json()["origen"]["punto"]["nodo"] == origen
    nodos = [origen, *(p["punto"]["nodo"] for p in ruta["paradas"][1:])]
    sin_zona = grafo.matriz_de_costos_evitando(nodos, zona)
    assert all((a.origen, a.destino) != (origen, v) for d in nodos[1:] if d != origen
               for a in sin_zona.camino(origen, d).arcos)


def test_una_zona_que_corta_todas_las_salidas_da_422(armado, grafo) -> None:  # type: ignore[no-untyped-def]
    """D34: con todas las salidas del origen cortadas, el Vehiculo no puede salir."""
    _, operador, ruta = armado
    origen = ruta["paradas"][1]["punto"]["nodo"]
    punto = grafo.coordenadas(origen)
    zona = ZonaCircular(punto.lat, punto.lon, 20.0)
    assert grafo.arcos_de_salida(origen) <= grafo.arcos_en_la_zona(zona)
    r = _proponer(operador, ruta["id"], {"lat": punto.lat, "lon": punto.lon, "radio_m": 20.0})
    assert r.status_code == http.HTTPStatus.UNPROCESSABLE_ENTITY and r.json()["codigo"] == "zona_contiene_al_vehiculo"
