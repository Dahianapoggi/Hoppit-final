"""Reruteo por desvio de la Ruta (D17 a D19 del encargo 2026-11-22A): deteccion
con el umbral de D18 (2a), alerta en CU-08 sin duplicar (2b), propuesta de
CU-07 con todas las pendientes recalculadas desde la posicion (2c) y punta a
punta hasta la Ruta nueva y la vista del transportista (2d). Instancia
canonica en San Isidro, en memoria; las posiciones se inyectan con un lector
de prueba en lugar del de telemetria."""
from __future__ import annotations

import datetime as dt
import http
import logging
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.identidad.puerto import Rol
from hoppit.planificacion_rutas.puerto import (
    UMBRAL_DE_DESVIO_M,
    ReporteDePosicion,
    distancia_a_polilinea_m,
)
from hoppit.shared.ids import RutaId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto, iniciar_vigilancia_de_desvios

UMBRAL_NFR09_S = 3.0
_LOG = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def sistema(grafo: GrafoVialAMBA) -> tuple[Any, TestClient, TestClient, dict[str, Any], dict[str, list[ReporteDePosicion]]]:
    """La Ruta mas larga despachada al transportista y un lector de posiciones
    de prueba (lo que telemetria devolveria en sus ultimos 120 s)."""
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=GRAFO_PREDETERMINADO,
                                  deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO)
    operador = cliente_autenticado(contexto)
    transportista = cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    rutas = [operador.get(f"/v1/rutas/{r['id']}").json() for r in operador.get("/v1/rutas").json()]
    ruta = max(rutas, key=lambda r: len(r["paradas"]))
    (persona,) = operador.get("/v1/transportistas").json()
    assert operador.post(f"/v1/rutas/{ruta['id']}/despacho", json={"transportista": persona["id"]}).status_code == 200
    posiciones: dict[str, list[ReporteDePosicion]] = {}
    contexto.lector_de_posiciones_recientes = lambda ruta_id: list(posiciones.get(ruta_id, []))
    return contexto, operador, transportista, ruta, posiciones


def _punto(ruta: dict[str, Any], secuencia: int) -> tuple[float, float]:
    p = next(p for p in ruta["paradas"] if p["secuencia"] == secuencia)["punto"]
    return float(p["lat"]), float(p["lon"])


def _reporte(punto: tuple[float, float], hace_s: float, ahora: dt.datetime) -> ReporteDePosicion:
    return ReporteDePosicion(punto[0], punto[1], ahora - dt.timedelta(seconds=hace_s))


def _parada_lejana(contexto: Any, ruta: dict[str, Any]) -> int:
    """La Parada pendiente mas lejana del tramo Deposito -> Parada 1 (se
    verifica que quede a mas del doble del umbral)."""
    vigilante = contexto.planificacion.desvio
    desde, hasta = vigilante.tramo_vigente(RutaId(ruta["id"]))
    trazado = contexto.cartografia.trazado([desde, hasta]).coordenadas
    secuencia = max(range(2, len(ruta["paradas"]) + 1),
                    key=lambda s: distancia_a_polilinea_m(_punto(ruta, s), trazado))
    assert distancia_a_polilinea_m(_punto(ruta, secuencia), trazado) > 2 * UMBRAL_DE_DESVIO_M
    return secuencia


def _lejos(contexto: Any, ruta: dict[str, Any]) -> tuple[float, float]:
    return _punto(ruta, _parada_lejana(contexto, ruta))


def test_distancia_a_polilinea() -> None:
    tramo = [(-34.50, -58.50), (-34.50, -58.49)]
    assert distancia_a_polilinea_m((-34.50, -58.495), tramo) == pytest.approx(0.0, abs=1e-6)
    # 0,003 grados de latitud son unos 333,6 m.
    assert distancia_a_polilinea_m((-34.503, -58.495), tramo) == pytest.approx(333.6, abs=0.5)
    assert distancia_a_polilinea_m((-34.50, -58.48), tramo) == pytest.approx(916.9, abs=2.0)


def test_deteccion_segun_d18_2a(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, _, _, ruta, _ = sistema
    vigilante = contexto.planificacion.desvio
    ahora = dt.datetime.now(dt.timezone.utc)
    cerca, lejos = _punto(ruta, 1), _lejos(contexto, ruta)
    r = RutaId(ruta["id"])
    # Dentro del umbral: no dispara.
    assert vigilante.evaluar(r, [_reporte(cerca, 30, ahora), _reporte(cerca, 15, ahora)], ahora) is None
    # Un reporte suelto afuera no dispara (ni el ultimo, ni uno intermedio).
    assert vigilante.evaluar(r, [_reporte(lejos, 15, ahora)], ahora) is None
    assert vigilante.evaluar(r, [_reporte(cerca, 30, ahora), _reporte(lejos, 15, ahora)], ahora) is None
    assert vigilante.evaluar(r, [_reporte(lejos, 30, ahora), _reporte(cerca, 15, ahora)], ahora) is None
    # Dos consecutivos afuera: dispara, desde el primero de la racha.
    desvio = vigilante.evaluar(r, [_reporte(cerca, 45, ahora), _reporte(lejos, 30, ahora),
                                  _reporte(lejos, 15, ahora)], ahora)
    assert desvio is not None and desvio.distancia_m > UMBRAL_DE_DESVIO_M
    assert desvio.desde == ahora - dt.timedelta(seconds=30) and desvio.vehiculo_id == ruta["vehiculo_id"]
    # Una posicion de mas de 120 s no cuenta.
    assert vigilante.evaluar(r, [_reporte(lejos, 200, ahora), _reporte(lejos, 150, ahora)], ahora) is None
    assert vigilante.evaluar(r, [_reporte(lejos, 150, ahora), _reporte(lejos, 15, ahora)], ahora) is None


def test_la_alerta_avisa_al_operador_sin_duplicar_2b(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta, posiciones = sistema
    assert all(a["tipo"] != "desvio_de_ruta" for a in operador.get("/v1/alertas").json()["alertas"])
    ahora = dt.datetime.now(dt.timezone.utc)
    lejos = _lejos(contexto, ruta)
    posiciones[ruta["id"]] = [_reporte(lejos, 20, ahora), _reporte(lejos, 5, ahora)]
    primera = [a for a in operador.get("/v1/alertas").json()["alertas"] if a["tipo"] == "desvio_de_ruta"]
    assert len(primera) == 1
    alerta = primera[0]
    assert alerta["ruta"] == ruta["id"] and alerta["vehiculo"] == ruta["vehiculo_id"]
    assert alerta["distancia_m"] > UMBRAL_DE_DESVIO_M and alerta["propuesta"]
    assert dt.datetime.fromisoformat(alerta["desde"]) == (ahora - dt.timedelta(seconds=20))
    # Mientras siga vigente, una sola alerta y la misma propuesta (CU-08, 4a).
    segunda = [a for a in operador.get("/v1/alertas").json()["alertas"] if a["tipo"] == "desvio_de_ruta"]
    assert segunda == primera
    assert len(contexto.planificacion.reemplazo.propuestas_de(RutaId(ruta["id"]))) == 1
    # Vuelve a la Ruta: la alerta deja de estar vigente.
    posiciones[ruta["id"]] = [_reporte(_punto(ruta, 1), 3, dt.datetime.now(dt.timezone.utc))] * 2
    assert all(a["tipo"] != "desvio_de_ruta" for a in operador.get("/v1/alertas").json()["alertas"])


def test_la_posicion_desviada_cambia_el_orden_de_las_pendientes_2c(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta, posiciones = sistema
    ahora = dt.datetime.now(dt.timezone.utc)
    lejos = _lejos(contexto, ruta)
    posiciones[ruta["id"]] = [_reporte(lejos, 20, ahora), _reporte(lejos, 5, ahora)]
    (alerta,) = [a for a in operador.get("/v1/alertas").json()["alertas"] if a["tipo"] == "desvio_de_ruta"]
    propuesta = operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()
    assert propuesta["motivo"] == "desvio" and propuesta["fija_en_curso"] is False
    assert propuesta["origen"]["fuente"] == "telemetria"
    vigente = [p["secuencia_vigente"] for p in propuesta["plan_vigente"]]
    propuesto = [p["secuencia_vigente"] for p in propuesta["plan_propuesto"]]
    n = len(ruta["paradas"])
    assert vigente == list(range(1, n + 1)) and sorted(propuesto) == vigente
    # Desde esa Parada, lo optimo es empezar por ella: la Parada en curso no queda fija.
    assert propuesto[0] == _parada_lejana(contexto, ruta) and propuesto != vigente and propuesta["orden_sin_cambios"] is False
    assert propuesta["distancia_propuesta_m"] < propuesta["distancia_vigente_m"]


def test_si_el_orden_no_cambia_la_propuesta_lo_dice(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, _, ruta, _ = sistema
    from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo
    cerca = _punto(ruta, 1)
    p = contexto.planificacion.reemplazo.proponer(RutaId(ruta["id"]), "desvio", "desvio-prueba",
                                                  PosicionDelVehiculo(cerca[0], cerca[1], 5.0), fijar_en_curso=False)
    publicada = operador.get(f"/v1/propuestas/{p.id}").json()
    assert publicada["orden_sin_cambios"] == (p.secuencia_propuesta == p.secuencia_vigente)
    assert publicada["orden_sin_cambios"] == (publicada["paradas_que_cambian_de_lugar"] == [])


def test_punta_a_punta_hasta_la_ruta_vigente_del_transportista_2d(sistema) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, transportista, ruta, posiciones = sistema
    vieja = ruta["id"]
    ahora = dt.datetime.now(dt.timezone.utc)
    lejana = _parada_lejana(contexto, ruta)
    lejos = _punto(ruta, lejana)
    antes = time.perf_counter()
    posiciones[vieja] = [_reporte(lejos, 20, ahora), _reporte(lejos, 5, ahora)]
    (alerta,) = [a for a in operador.get("/v1/alertas").json()["alertas"] if a["tipo"] == "desvio_de_ruta"]
    detectado = time.perf_counter()
    aprobacion = operador.post(f"/v1/propuestas/{alerta['propuesta']}/aprobacion")
    aprobado = time.perf_counter()
    assert aprobacion.status_code == http.HTTPStatus.OK, aprobacion.text
    nueva = aprobacion.json()["ruta_nueva"]
    assert aprobacion.json()["motivo"] == "desvio"
    assert operador.get(f"/v1/rutas/{vieja}").json()["reemplazada_por"]["ruta_nueva"] == nueva
    vigente = operador.get(f"/v1/rutas/{nueva}").json()
    assert vigente["reemplaza_a"]["ruta_reemplazada"] == vieja
    assert vigente["paradas"][0]["punto"]["nodo"] == ruta["paradas"][lejana - 1]["punto"]["nodo"]
    assert all(o["estado"] == "en_transito" for p in vigente["paradas"] for o in p["ordenes"])
    suya = transportista.get("/v1/yo/ruta-en-ejecucion").json()
    assert suya["ruta"]["id"] == nueva
    # Con la Ruta reemplazada, ya no hay desvio de la vieja; la nueva sale de la posicion.
    assert all(a["tipo"] != "desvio_de_ruta" or a["ruta"] != vieja
               for a in operador.get("/v1/alertas").json()["alertas"])
    total = aprobado - antes
    _LOG.info("Punta a punta del desvio: deteccion y propuesta %.3f s, aprobacion %.3f s, total %.3f s",
              detectado - antes, aprobado - detectado, total)
    assert total < UMBRAL_NFR09_S


def test_la_vigilancia_periodica_propone_sin_que_nadie_consulte(sistema) -> None:  # type: ignore[no-untyped-def]
    """El hilo del proceso del nucleo (`servir_api`) genera la propuesta al
    llegar los reportes, sin esperar a que el operador mire las alertas."""
    contexto, _, _, ruta, posiciones = sistema
    detener = iniciar_vigilancia_de_desvios(contexto, intervalo_s=0.05)
    try:
        ahora = dt.datetime.now(dt.timezone.utc)
        lejos = _lejos(contexto, ruta)
        posiciones[ruta["id"]] = [_reporte(lejos, 20, ahora), _reporte(lejos, 5, ahora)]
        limite = time.monotonic() + 5.0
        while time.monotonic() < limite and not contexto.planificacion.reemplazo.propuestas_de(RutaId(ruta["id"])):
            time.sleep(0.05)
    finally:
        detener.set()
    (propuesta,) = contexto.planificacion.reemplazo.propuestas_de(RutaId(ruta["id"]))
    assert propuesta.motivo == "desvio" and not propuesta.fija_en_curso


def test_dos_consultas_a_la_vez_dan_una_sola_propuesta(sistema) -> None:  # type: ignore[no-untyped-def]
    """2026-11-23A, bloque B: en el navegador, la recarga de la pantalla de
    alertas y su consulta periodica crearon dos propuestas para el mismo
    desvio. Revisar y proponer es ahora una sola operacion."""
    import threading

    contexto, _, _, ruta, _ = sistema
    vigilante = contexto.planificacion.desvio
    ahora = dt.datetime.now(dt.timezone.utc)
    lejos = _lejos(contexto, ruta)
    desvio = vigilante.evaluar(RutaId(ruta["id"]), [_reporte(lejos, 20, ahora), _reporte(lejos, 5, ahora)], ahora)
    assert desvio is not None
    barrera = threading.Barrier(4)
    devueltas: list[str] = []

    def consultar() -> None:
        barrera.wait()
        p = vigilante.proponer_si_corresponde(desvio, ahora)
        assert p is not None
        devueltas.append(str(p.id))

    hilos = [threading.Thread(target=consultar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(devueltas) == 4 and len(set(devueltas)) == 1
    assert len(contexto.planificacion.reemplazo.propuestas_de(RutaId(ruta["id"]))) == 1
