"""Ciclo de vida del reemplazo de una Ruta en ejecucion (CU-07, pasos 2 a 6 y
flujos 3a y 5a; glosario, estado "Reemplazada"; D8 a D13 del encargo
2026-11-20A), sobre la instancia canonica en San Isidro, en memoria."""
from __future__ import annotations

import http
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import CaminoInexistente, GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.gestion_ordenes.puerto import EstadoOrden, RepositorioOrdenesSQLite
from hoppit.planificacion_rutas.puerto import (
    SIN_MARGEN,
    EstadoDePropuesta,
    FuenteDelOrigen,
    PosicionDelVehiculo,
    PropuestaVencida,
    PropuestaYaResuelta,
    RepositorioDeReemplazosSQLite,
    RutaNoEnEjecucion,
    RutaYaReemplazada,
    SinAlternativaFactible,
)
from hoppit.shared.ids import OrdenId, RutaId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, dict[str, Any]]:
    """La instancia canonica planificada y la Ruta mas larga despachada."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO, margen_de_capacidad=SIN_MARGEN)
    cliente = cliente_autenticado(contexto)
    assert cliente.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [cliente.get(f"/v1/rutas/{r['id']}").json() for r in cliente.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    assert cliente.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    return contexto, cliente, ruta


def _resultado(cliente: TestClient, ruta: str, secuencia: int, resultado: str = "cumplida") -> int:
    return cliente.post(f"/v1/rutas/{ruta}/paradas/{secuencia}/resultado", json={"resultado": resultado}).status_code


def test_avance_cumplidas_en_curso_y_pendientes(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo
    assert _resultado(cliente, ruta["id"], 1) == 200
    assert _resultado(cliente, ruta["id"], 3, "fallida") == 200
    avance = servicio.avance(RutaId(ruta["id"]))
    n = len(ruta["paradas"])
    assert avance.cumplidas == (1, 3)
    assert avance.en_curso == 2
    assert avance.pendientes == tuple(s for s in range(4, n + 1))


def test_reemplazo_de_punta_a_punta_segun_el_glosario(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo
    vieja = RutaId(ruta["id"])
    assert _resultado(cliente, ruta["id"], 1) == 200
    propuesta = servicio.proponer(vieja, "incidente", "inc-1", None)
    assert propuesta.estado is EstadoDePropuesta.PENDIENTE
    assert propuesta.origen.fuente is FuenteDelOrigen.PARADA_EN_CURSO
    assert propuesta.secuencia_propuesta[0] == 2 and sorted(propuesta.secuencia_propuesta) == list(
        range(2, len(ruta["paradas"]) + 1))
    assert propuesta.distancia_propuesta_m <= propuesta.distancia_vigente_m + 1e-6
    # Proponer no toca nada vigente.
    assert servicio.reemplazo_de_la_reemplazada(vieja) is None

    reemplazo = servicio.aprobar(propuesta.id)
    nueva = reemplazo.ruta_nueva
    assert nueva != vieja and reemplazo.paradas_cumplidas == (1,)
    assert servicio.vigente(vieja) == nueva
    assert servicio.propuesta(propuesta.id).estado is EstadoDePropuesta.APROBADA
    persistida_nueva = contexto.planificacion.ruta_persistida(nueva)
    persistida_vieja = contexto.planificacion.ruta_persistida(vieja)
    # La reemplazada se conserva entera; la nueva tiene la en curso primera y las pendientes.
    assert len(persistida_vieja.ruta.paradas) == len(ruta["paradas"])
    assert len(persistida_nueva.ruta.paradas) == len(ruta["paradas"]) - 1
    assert persistida_nueva.ruta.paradas[0].nodo == persistida_vieja.ruta.paradas[1].nodo
    assert persistida_nueva.plan_id == persistida_vieja.plan_id
    assert persistida_nueva.ruta.vehiculo_id == persistida_vieja.ruta.vehiculo_id
    # Las Ordenes cumplidas se quedan; las demas se mudan sin cambiar de estado (D9).
    cumplidas = persistida_vieja.ruta.paradas[0].ordenes
    for o in cumplidas:
        orden = contexto.ordenes.consultar(o)
        assert orden.ruta_id == vieja and orden.estado is EstadoOrden.COMPLETADA
    for parada in persistida_nueva.ruta.paradas:
        for o in parada.ordenes:
            orden = contexto.ordenes.consultar(o)
            assert orden.ruta_id == nueva and orden.secuencia_parada == parada.secuencia
            assert orden.estado is EstadoOrden.EN_TRANSITO
            ultimo = orden.historial[-1]
            assert ultimo.estado_anterior is ultimo.estado_nuevo is EstadoOrden.EN_TRANSITO
            assert ultimo.reasignacion is not None and ultimo.reasignacion.ruta_anterior == vieja
            assert ultimo.reasignacion.ruta_nueva == nueva and ultimo.reasignacion.motivo == "incidente"
    # La ejecucion de la nueva queda abierta con el mismo transportista y su aviso a telemetria.
    assert contexto.planificacion.transportista_de(nueva) == contexto.planificacion.transportista_de(vieja)
    tipos = [n.tipo for n in contexto.planificacion._ejecucion.sin_entregar() if n.agregado_id == str(nueva)]
    assert tipos == ["RutaReemplazada"]
    # La reemplazada es terminal: no se le declaran resultados ni se vuelve a reemplazar.
    assert _resultado(cliente, ruta["id"], 2) == http.HTTPStatus.CONFLICT
    with pytest.raises(RutaYaReemplazada):
        servicio.proponer(vieja, "incidente", "inc-1", None)
    # La nueva se ejecuta como cualquier otra.
    assert _resultado(cliente, str(nueva), 1) == 200


def test_rechazar_conserva_el_plan_5a(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo
    vieja = RutaId(ruta["id"])
    antes = [(str(o.id), o.ruta_id, o.estado, len(o.historial)) for o in contexto.ordenes.listar()]
    propuesta = servicio.proponer(vieja, "incidente", "inc-2", None)
    rechazada = servicio.rechazar(propuesta.id)
    assert rechazada.estado is EstadoDePropuesta.RECHAZADA and rechazada.resuelta_en is not None
    assert servicio.reemplazo_de_la_reemplazada(vieja) is None
    assert [(str(o.id), o.ruta_id, o.estado, len(o.historial)) for o in contexto.ordenes.listar()] == antes
    with pytest.raises(PropuestaYaResuelta):
        servicio.aprobar(propuesta.id)
    with pytest.raises(PropuestaYaResuelta):
        servicio.rechazar(propuesta.id)


def test_una_propuesta_vence_si_cambia_el_avance(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo
    propuesta = servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-3", None)
    assert _resultado(cliente, ruta["id"], 1) == 200
    with pytest.raises(PropuestaVencida):
        servicio.aprobar(propuesta.id)
    assert servicio.reemplazo_de_la_reemplazada(RutaId(ruta["id"])) is None


def test_una_ruta_sin_despachar_no_se_replanifica(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    otra = next(r for r in cliente.get("/v1/rutas").json() if r["id"] != ruta["id"])
    with pytest.raises(RutaNoEnEjecucion):
        contexto.planificacion.reemplazo.proponer(RutaId(otra["id"]), "incidente", "inc-4", None)


def test_sin_camino_informa_y_conserva_el_plan_3a(sistema, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo

    def sin_camino(nodos):  # type: ignore[no-untyped-def]
        raise CaminoInexistente("sin camino de prueba")

    monkeypatch.setattr(servicio._contexto, "matriz_de_costos", sin_camino)
    with pytest.raises(SinAlternativaFactible):
        servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-5", None)
    assert servicio.propuestas_de(RutaId(ruta["id"])) == ()
    assert servicio.reemplazo_de_la_reemplazada(RutaId(ruta["id"])) is None


def test_origen_por_telemetria_reciente_y_su_tolerancia(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, cliente, ruta = sistema
    servicio = contexto.planificacion.reemplazo
    parada = ruta["paradas"][-1]["punto"]
    reciente = PosicionDelVehiculo(parada["lat"], parada["lon"], antiguedad_s=30.0)
    p = servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-6", reciente)
    assert p.origen.fuente is FuenteDelOrigen.TELEMETRIA and p.origen.antiguedad_s == 30.0
    assert p.origen.distancia_del_anclaje_m is not None and p.origen.distancia_del_anclaje_m < 5.0
    vieja = PosicionDelVehiculo(parada["lat"], parada["lon"], antiguedad_s=121.0)
    q = servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-6", vieja)
    assert q.origen.fuente is FuenteDelOrigen.PARADA_EN_CURSO and q.origen.antiguedad_s == 121.0
    futura = PosicionDelVehiculo(parada["lat"], parada["lon"], antiguedad_s=-1.0)
    assert servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-6", futura).origen.fuente is \
        FuenteDelOrigen.PARADA_EN_CURSO


def test_el_reemplazo_y_la_reasignacion_persisten_en_sqlite(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from hoppit.gestion_ordenes.puerto import Orden
    from hoppit.planificacion_rutas.puerto import (
        OrigenDelRecalculo,
        PropuestaDeReemplazo,
        ReemplazoDeRuta,
    )
    import datetime as dt

    conexion = sqlite3.connect(tmp_path / "b.db", check_same_thread=False)
    conexion.row_factory = sqlite3.Row
    repo = RepositorioDeReemplazosSQLite(conexion)
    instante = dt.datetime(2026, 11, 20, 12, tzinfo=dt.timezone.utc)
    origen = OrigenDelRecalculo(5, FuenteDelOrigen.TELEMETRIA, 12.0, 3.5)
    p = PropuestaDeReemplazo("prop-1", RutaId("ruta-a"), "incidente", "inc-1", instante, EstadoDePropuesta.PENDIENTE,
                             origen, (1,), 2, (2, 3, 4), (2, 4, 3), 1000.0, 900.0, "exacto", 0.1, "h")
    repo.guardar_propuesta(p)
    assert repo.propuesta("prop-1") == p
    r = ReemplazoDeRuta(RutaId("ruta-a"), RutaId("ruta-b"), "incidente", "inc-1", "prop-1", instante, 5,
                        FuenteDelOrigen.TELEMETRIA, (1,))
    repo.guardar_reemplazo(r)
    otro = RepositorioDeReemplazosSQLite(conexion)
    assert otro.reemplazo_de_la_reemplazada(RutaId("ruta-a")) == r == otro.reemplazo_de_la_nueva(RutaId("ruta-b"))
    assert otro.propuestas_de(RutaId("ruta-a")) == (p,)

    ordenes = RepositorioOrdenesSQLite(conexion)
    orden = Orden(id=OrdenId("ord-1"), demanda=1, destino=7)
    orden.asignar_a_ruta(RutaId("ruta-a"), 3)
    orden.transicionar(EstadoOrden.EN_TRANSITO)
    orden.reasignar_a_ruta(RutaId("ruta-b"), 2, "incidente", ahora=instante)
    ordenes.guardar(orden)
    leida = RepositorioOrdenesSQLite(conexion).obtener(OrdenId("ord-1"))
    assert leida.ruta_id == RutaId("ruta-b") and leida.secuencia_parada == 2
    assert leida.historial[-1].reasignacion == orden.historial[-1].reasignacion
    assert leida.historial[-1].reasignacion.secuencia_anterior == 3  # type: ignore[union-attr]
    assert [t.reasignacion is None for t in leida.historial] == [True, True, True, False]


def test_solo_una_orden_en_transito_se_reasigna() -> None:
    from hoppit.gestion_ordenes.puerto import Orden
    from hoppit.shared.errores import TransicionInvalida

    orden = Orden(id=OrdenId("o"), demanda=1, destino=7)
    orden.asignar_a_ruta(RutaId("r"), 1)
    with pytest.raises(TransicionInvalida):
        orden.reasignar_a_ruta(RutaId("r2"), 1, "incidente")
