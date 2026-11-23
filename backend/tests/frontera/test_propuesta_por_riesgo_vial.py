"""Propuesta de reruteo por riesgo vial con aprobacion del operador (D31 del
encargo 2026-11-23E). Sobre el grafo de San Isidro (el de la huella) con una
tabla de riesgo artificial: un solo arco del recorrido pendiente de la Ruta
mas larga, el de menor rodeo, con 100 hechos viales predichos por anio; el
resto, sin riesgo. Sin tabla para el grafo, la funcion queda inactiva."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import http
import json
from pathlib import Path
from typing import Any

import networkx as nx
import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, ParametrosDeRiesgoVial, clave_del_arco
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from hoppit.planificacion_rutas.puerto import (
    MOTIVO_DESVIO,
    MOTIVO_RIESGO_VIAL,
    MU_DE_LA_PROPUESTA_M,
    EstadoDePropuesta,
    UmbralDeRiesgoVial,
    VigilanteDeRiesgoVial,
)
from hoppit.shared.ids import RutaId
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

RIESGO_ARTIFICIAL = 100.0
RODEO_MAXIMO = 0.03
"""El arco elegido tiene un rodeo de a lo sumo el 3 % de lo pendiente: con
mu_p x 100 hechos (unos 2.835 m) la alternativa lo esquiva y queda dentro del 5 %."""


def _armar(grafo: GrafoVialAMBA) -> tuple[ContextoDeLaApi, TestClient, dict[str, Any]]:
    """Operador y la Ruta mas larga despachada, con la Parada 1 cumplida."""
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


def _recorrido_pendiente(contexto: ContextoDeLaApi, ruta_id: str) -> list[int]:
    """Origen (sin telemetria: la Parada en curso), las pendientes en el orden
    vigente y el Deposito."""
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    avance = servicio.avance(RutaId(ruta_id))
    persistida = contexto.planificacion.ruta_persistida(RutaId(ruta_id))
    nodo = {p.secuencia: p.nodo for p in persistida.ruta.paradas}
    assert avance.en_curso is not None
    return [nodo[avance.en_curso], *(nodo[s] for s in avance.pendientes), persistida.deposito]


@pytest.fixture(scope="module")
def sin_tabla() -> GrafoVialAMBA:
    g = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=0.0, tabla=None))
    g.construir()
    return g


@pytest.fixture(scope="module")
def caso(sin_tabla: GrafoVialAMBA, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """El arco del recorrido pendiente con menor rodeo (al menos 1 m), su tabla
    y el grafo con la tabla cargada (mu del proceso en 0)."""
    contexto, _, ruta = _armar(sin_tabla)
    recorrido = _recorrido_pendiente(contexto, ruta["id"])
    matriz = sin_tabla.matriz_de_costos(recorrido)
    total = sum(matriz.distancia_m(a, b) for a, b in zip(recorrido, recorrido[1:]) if a != b)
    red = sin_tabla._grafo_construido()  # noqa: SLF001 - la prueba busca el rodeo sobre el grafo mismo
    mejor: tuple[float, int, int, float] | None = None
    for a, b in zip(recorrido, recorrido[1:]):
        if a == b:
            continue
        camino = matriz.camino(a, b)
        for arco in camino.arcos:
            sin_el = nx.subgraph_view(red, filter_edge=lambda u, v, k, x=arco: {u, v} != {x.origen, x.destino})
            try:
                rodeo = nx.shortest_path_length(sin_el, a, b, weight="length") - camino.distancia_m
            except nx.NetworkXNoPath:
                continue
            if 1.0 <= rodeo <= RODEO_MAXIMO * total and (mejor is None or rodeo < mejor[0]):
                mejor = (rodeo, arco.origen, arco.destino, arco.distancia_m)
    assert mejor is not None, "ningun arco del recorrido pendiente tiene un rodeo chico"
    _, u, v, largo = mejor
    directorio = tmp_path_factory.mktemp("riesgo")
    tabla = directorio / "riesgo.csv"
    tabla.write_text(f"arco,riesgo_hechos_por_anio\n{clave_del_arco(u, v, largo)},{RIESGO_ARTIFICIAL}\n",
                     encoding="utf-8")
    Path(f"{tabla}.procedencia.json").write_text(json.dumps({
        "sha256": hashlib.sha256(tabla.read_bytes()).hexdigest(), "grafo_sha256": sin_tabla.artefacto().sha256,
    }), encoding="utf-8")
    con_tabla = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=0.0, tabla=tabla))
    con_tabla.construir()
    assert con_tabla.capa_de_riesgo_vial() is not None
    return {"grafo": con_tabla, "arco": (u, v), "total_m": total}


@pytest.fixture()
def armado(caso: dict[str, Any]) -> tuple[ContextoDeLaApi, TestClient, dict[str, Any]]:
    return _armar(caso["grafo"])


def _de_riesgo(contexto: ContextoDeLaApi, ruta_id: str) -> list[Any]:
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    return [p for p in servicio.propuestas_de(RutaId(ruta_id)) if p.motivo == MOTIVO_RIESGO_VIAL]


def _alertas_de_riesgo(operador: TestClient) -> list[dict[str, Any]]:
    respuesta = operador.get("/v1/alertas")
    assert respuesta.status_code == 200, respuesta.text
    return [a for a in respuesta.json()["alertas"] if a["tipo"] == "riesgo_vial_en_ruta"]


def _es(numero: float, decimales: int) -> str:
    return f"{numero:.{decimales}f}".replace(".", ",")


def test_una_alternativa_que_cumple_el_umbral_da_una_sola_propuesta_y_su_alerta(armado, caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = armado
    grafo: GrafoVialAMBA = caso["grafo"]
    primera = _alertas_de_riesgo(operador)
    segunda = _alertas_de_riesgo(operador)
    (propuesta,) = _de_riesgo(contexto, ruta["id"])
    assert len(primera) == len(segunda) == 1 and primera[0]["propuesta"] == segunda[0]["propuesta"] == propuesta.id
    c = propuesta.riesgo_vial
    assert c is not None and c.mu_m == MU_DE_LA_PROPUESTA_M

    # Lo vigente, recalculado aparte con las operaciones de siempre del grafo.
    recorrido = _recorrido_pendiente(contexto, ruta["id"])
    vigente = grafo.matriz_de_costos(recorrido)
    distancia_vigente = sum(vigente.distancia_m(a, b) for a, b in zip(recorrido, recorrido[1:]) if a != b)
    exposicion = grafo.exposicion_al_riesgo_vial([n for i, n in enumerate(recorrido) if i == 0 or n != recorrido[i - 1]])
    assert exposicion is not None
    assert c.distancia_vigente_m == pytest.approx(distancia_vigente)
    assert c.exposicion_vigente == pytest.approx(exposicion.hechos_por_anio) and c.exposicion_vigente >= RIESGO_ARTIFICIAL
    # La alternativa no pasa por el arco riesgoso, es mas larga y queda dentro del umbral.
    assert c.exposicion_propuesta == 0.0
    assert c.distancia_vigente_m < c.distancia_propuesta_m <= 1.05 * c.distancia_vigente_m
    assert c.minutos_propuestos > 0 and c.minutos_vigentes > 0
    # Los numeros de la alerta son los de la propuesta.
    (alerta,) = primera
    assert alerta["vehiculo"] == ruta["vehiculo_id"] and alerta["ruta"] == ruta["id"]
    km = (c.distancia_propuesta_m - c.distancia_vigente_m) / 1000
    assert alerta["motivo"].startswith(f"La Ruta de {ruta['vehiculo_id']} pasa por tramos con más hechos viales predichos.")
    assert f"Alternativa: +{_es(km, 1)} km (+{_es(100 * c.suba_de_distancia, 1)} %)" in alerta["motivo"]
    assert "exposición predicha −100 %" in alerta["motivo"]
    assert f"(de {_es(c.exposicion_vigente, 1)} a 0,0 hechos/año en los tramos recorridos)" in alerta["motivo"]
    # El detalle por la operacion existente de propuestas.
    detalle = operador.get(f"/v1/propuestas/{propuesta.id}").json()
    assert detalle["motivo"] == "riesgo_vial" and detalle["estado"] == "pendiente"
    assert detalle["riesgo_vial"]["exposicion_vigente"] == pytest.approx(c.exposicion_vigente)
    assert detalle["riesgo_vial"]["baja_de_exposicion"] == pytest.approx(1.0)
    assert detalle["distancia_propuesta_m"] == pytest.approx(c.distancia_propuesta_m)
    assert detalle["fija_en_curso"] is False


def test_una_alternativa_que_no_cumple_el_umbral_no_genera_nada(armado) -> None:  # type: ignore[no-untyped-def]
    """La misma alternativa, con un umbral de distancia que no cumple (sube mas
    de 0 %) o uno de exposicion que no puede cumplir (bajar mas del 100 %)."""
    contexto, operador, ruta = armado
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    for umbral in (UmbralDeRiesgoVial(suba_maxima_de_distancia=0.0),
                   UmbralDeRiesgoVial(baja_minima_de_exposicion=1.01)):
        vigilante = VigilanteDeRiesgoVial(servicio, reloj=lambda: dt.datetime.now(dt.timezone.utc), umbral=umbral)
        assert vigilante.revisar([RutaId(ruta["id"])]) == []
        assert _de_riesgo(contexto, ruta["id"]) == []
    assert servicio.proponer_por_riesgo_vial(RutaId(ruta["id"]), None, MU_DE_LA_PROPUESTA_M,
                                             UmbralDeRiesgoVial(suba_maxima_de_distancia=0.0)) is None
    assert _de_riesgo(contexto, ruta["id"]) == []


def test_aprobar_reemplaza_y_la_ruta_nueva_se_dibuja_con_la_alternativa(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = armado
    (alerta,) = _alertas_de_riesgo(operador)
    respuesta = operador.post(f"/v1/propuestas/{alerta['propuesta']}/aprobacion")
    assert respuesta.status_code == 200, respuesta.text
    (propuesta,) = _de_riesgo(contexto, ruta["id"])
    assert propuesta.estado is EstadoDePropuesta.APROBADA and propuesta.ruta_nueva is not None
    assert operador.get(f"/v1/rutas/{ruta['id']}/avance").json()["codigo"] == "ruta_reemplazada"
    nueva = operador.get(f"/v1/rutas/{propuesta.ruta_nueva}").json()
    assert nueva["reemplaza_a"]["ruta_reemplazada"] == ruta["id"] and nueva["reemplaza_a"]["motivo"] == "riesgo_vial"
    c = propuesta.riesgo_vial
    assert c is not None
    # Lo dibujado es lo propuesto: la distancia y la exposicion de la Ruta nueva.
    assert nueva["distancia_m"] == pytest.approx(c.distancia_propuesta_m)
    assert nueva["trazado"]["distancia_m"] == pytest.approx(c.distancia_propuesta_m)
    exposicion = operador.get(f"/v1/rutas/{propuesta.ruta_nueva}/exposicion-al-riesgo-vial").json()
    assert exposicion["exposicion_hechos_por_anio"] == pytest.approx(0.0) and exposicion["mu"] == c.mu_m
    # La Ruta nueva se evalua una vez y no hay otra alternativa que cumpla.
    assert _alertas_de_riesgo(operador) == []
    assert _de_riesgo(contexto, str(propuesta.ruta_nueva)) == []


def test_rechazar_conserva_la_ruta_y_no_se_repite(armado) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = armado
    (alerta,) = _alertas_de_riesgo(operador)
    assert operador.post(f"/v1/propuestas/{alerta['propuesta']}/rechazo").status_code == 200
    assert operador.get(f"/v1/rutas/{ruta['id']}/avance").status_code == 200
    assert _alertas_de_riesgo(operador) == []
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    # Ni otro vigilante (un proceso nuevo) la repite: la rechazada esta guardada.
    otro = VigilanteDeRiesgoVial(servicio, reloj=lambda: dt.datetime.now(dt.timezone.utc))
    assert otro.revisar([RutaId(ruta["id"])]) == []
    (rechazada,) = _de_riesgo(contexto, ruta["id"])
    assert rechazada.estado is EstadoDePropuesta.RECHAZADA


def test_con_un_desvio_pendiente_no_se_crea_y_un_desvio_posterior_la_vence(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(caso["grafo"])
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    desvio = servicio.proponer(RutaId(ruta["id"]), MOTIVO_DESVIO, "desvio-prueba", None, fijar_en_curso=False)
    assert _alertas_de_riesgo(operador) == [] and _de_riesgo(contexto, ruta["id"]) == []
    servicio.rechazar(desvio.id)
    # Sin la pendiente, se evalua y se propone; un desvio posterior la vence.
    (alerta,) = _alertas_de_riesgo(operador)
    otro = servicio.proponer(RutaId(ruta["id"]), MOTIVO_DESVIO, "desvio-posterior", None, fijar_en_curso=False)
    (vencida,) = _de_riesgo(contexto, ruta["id"])
    assert vencida.id == alerta["propuesta"] and vencida.estado is EstadoDePropuesta.VENCIDA
    assert operador.get(f"/v1/propuestas/{vencida.id}").json()["estado"] == "vencida"
    assert operador.post(f"/v1/propuestas/{vencida.id}/aprobacion").status_code == http.HTTPStatus.CONFLICT
    assert _alertas_de_riesgo(operador) == []
    assert servicio.propuesta(otro.id).estado is EstadoDePropuesta.PENDIENTE


def test_un_incidente_posterior_vence_la_propuesta_por_riesgo(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    incidente = operador.post("/v1/incidentes", json={"tipo": "corte_de_calle", "alcance": "parcial",
                                                     "descripcion": "Corte", "rutas_afectadas": [ruta["id"]]})
    assert incidente.status_code == http.HTTPStatus.CREATED, incidente.text
    propuesta = operador.post(f"/v1/incidentes/{incidente.json()['id']}/propuestas", json={"ruta_id": ruta["id"]})
    assert propuesta.status_code == http.HTTPStatus.CREATED, propuesta.text
    assert operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()["estado"] == "vencida"


def test_sin_tabla_para_el_grafo_no_pasa_nada(sin_tabla) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(sin_tabla)
    assert sin_tabla.capa_de_riesgo_vial() is None
    assert _alertas_de_riesgo(operador) == []
    assert _de_riesgo(contexto, ruta["id"]) == []
    vigilante = contexto.planificacion.riesgo_vial
    assert vigilante is not None and vigilante.revisar([RutaId(ruta["id"])]) == []
    detalle = operador.get(f"/v1/rutas/{ruta['id']}").json()
    assert "riesgo_vial" not in json.dumps(detalle)


def test_la_propuesta_persiste_con_su_compromiso(caso) -> None:  # type: ignore[no-untyped-def]
    """El compromiso viaja en el JSON de la tabla de propuestas (SQLite)."""
    from hoppit.planificacion_rutas.puerto import RepositorioDeReemplazosSQLite
    import sqlite3

    contexto, operador, ruta = _armar(caso["grafo"])
    (propuesta,) = (_alertas_de_riesgo(operador), _de_riesgo(contexto, ruta["id"]))[1]
    repo = RepositorioDeReemplazosSQLite(sqlite3.connect(":memory:", check_same_thread=False))
    repo.guardar_propuesta(propuesta)
    leida = repo.propuesta(propuesta.id)
    assert leida == propuesta and dataclasses.asdict(leida)["riesgo_vial"]["mu_m"] == MU_DE_LA_PROPUESTA_M


def test_un_resultado_declarado_despues_la_vence_y_no_se_vuelve_a_proponer(caso) -> None:  # type: ignore[no-untyped-def]
    """Calculada sobre otro avance, no se podria aprobar: vence en la pasada
    siguiente, la alerta desaparece y la Ruta no se vuelve a evaluar."""
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    assert operador.post(f"/v1/rutas/{ruta['id']}/paradas/2/resultado", json={"resultado": "cumplida"}).status_code == 200
    assert _alertas_de_riesgo(operador) == []
    (vencida,) = _de_riesgo(contexto, ruta["id"])
    assert vencida.id == alerta["propuesta"] and vencida.estado is EstadoDePropuesta.VENCIDA


def test_vencer_con_una_copia_vieja_no_pisa_una_aprobada(caso) -> None:  # type: ignore[no-untyped-def]
    """Consejo del 23E, E3: el vencimiento relee el estado dentro del candado."""
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    vieja = servicio.propuesta(alerta["propuesta"])
    assert operador.post(f"/v1/propuestas/{vieja.id}/aprobacion").status_code == 200
    assert servicio.vencer_si_cambio_el_avance(vieja).estado is EstadoDePropuesta.APROBADA
    assert servicio.propuesta(vieja.id).estado is EstadoDePropuesta.APROBADA


def test_si_aparece_otra_pendiente_mientras_se_calcula_la_ruta_no_queda_evaluada(caso, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Consejo del 23E, J7: un Incidente que propone mientras se calcula la de
    riesgo; la de riesgo no se guarda y la Ruta se reevalua despues."""
    contexto, operador, ruta = _armar(caso["grafo"])
    servicio = contexto.planificacion.reemplazo
    vigilante = contexto.planificacion.riesgo_vial
    assert servicio is not None and vigilante is not None
    original = servicio.proponer_por_riesgo_vial
    otras: list[Any] = []

    def con_un_incidente_en_el_medio(*args: Any, **kwargs: Any) -> Any:
        if not otras:
            otras.append(servicio.proponer(RutaId(ruta["id"]), "incidente", "inc-prueba", None))
        return original(*args, **kwargs)

    monkeypatch.setattr(servicio, "proponer_por_riesgo_vial", con_un_incidente_en_el_medio)
    assert vigilante.revisar([RutaId(ruta["id"])]) == [] and _de_riesgo(contexto, ruta["id"]) == []
    servicio.rechazar(otras[0].id)
    (propuesta,) = vigilante.revisar([RutaId(ruta["id"])])
    assert propuesta.motivo == MOTIVO_RIESGO_VIAL


def _declarar(operador: TestClient, rutas: list[str], zona: dict[str, float] | None = None) -> Any:
    cuerpo: dict[str, Any] = {"tipo": "corte_de_calle", "alcance": "parcial", "descripcion": "Corte",
                              "rutas_afectadas": rutas}
    if zona is not None:
        cuerpo["zona_afectada"] = zona
    r = operador.post("/v1/incidentes", json=cuerpo)
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    return r.json()


def test_un_incidente_en_3a_vence_la_propuesta_por_riesgo_y_no_se_puede_aprobar(caso) -> None:  # type: ignore[no-untyped-def]
    """D35 (encargo 2026-11-23F)."""
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    ultima = ruta["paradas"][-1]["punto"]
    incidente = _declarar(operador, [ruta["id"]], {"lat": ultima["lat"], "lon": ultima["lon"], "radio_m": 60.0})
    r = operador.post(f"/v1/incidentes/{incidente['id']}/propuestas", json={"ruta_id": ruta["id"]})
    assert r.json()["codigo"] == "sin_alternativa_factible", r.text
    detalle = operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()
    assert detalle["estado"] == "vencida" and incidente["id"] in detalle["motivo_del_vencimiento"]
    assert operador.post(f"/v1/propuestas/{alerta['propuesta']}/aprobacion").status_code == http.HTTPStatus.CONFLICT
    assert _alertas_de_riesgo(operador) == []


def test_un_incidente_con_propuesta_normal_la_vence_con_su_motivo(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    incidente = _declarar(operador, [ruta["id"]])
    r = operador.post(f"/v1/incidentes/{incidente['id']}/propuestas", json={"ruta_id": ruta["id"]})
    assert r.status_code == http.HTTPStatus.CREATED, r.text
    detalle = operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()
    assert detalle["estado"] == "vencida" and incidente["id"] in detalle["motivo_del_vencimiento"]


def _otra_ruta(operador: TestClient, ruta: dict[str, Any]) -> str:
    return next(r["id"] for r in operador.get("/v1/rutas").json() if r["id"] != ruta["id"])


def test_una_propuesta_cuyo_camino_cruza_la_zona_de_un_incidente_de_otra_ruta_vence(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(caso["grafo"])
    grafo: GrafoVialAMBA = caso["grafo"]
    (alerta,) = _alertas_de_riesgo(operador)
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    arcos = sorted(servicio.arcos_propuestos(servicio.propuesta(alerta["propuesta"])))
    u, _ = arcos[len(arcos) // 2]
    punto = grafo.coordenadas(u)
    incidente = _declarar(operador, [_otra_ruta(operador, ruta)], {"lat": punto.lat, "lon": punto.lon, "radio_m": 20.0})
    detalle = operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()
    assert detalle["estado"] == "vencida"
    assert detalle["motivo_del_vencimiento"] == f"el camino propuesto cruza la zona del Incidente {incidente['id']}"
    assert _alertas_de_riesgo(operador) == []


def test_una_propuesta_cuyo_camino_no_cruza_la_zona_no_vence(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(caso["grafo"])
    grafo: GrafoVialAMBA = caso["grafo"]
    (alerta,) = _alertas_de_riesgo(operador)
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    arcos = servicio.arcos_propuestos(servicio.propuesta(alerta["propuesta"]))
    usados = [grafo.coordenadas(n) for a in arcos for n in a]
    lejos = max(grafo.nodos_de_mayor_componente_fuerte(), key=lambda n: min(
        abs(grafo.coordenadas(n).lat - c.lat) + abs(grafo.coordenadas(n).lon - c.lon) for c in usados))
    punto = grafo.coordenadas(lejos)
    _declarar(operador, [_otra_ruta(operador, ruta)], {"lat": punto.lat, "lon": punto.lon, "radio_m": 20.0})
    assert operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()["estado"] == "pendiente"
    assert len(_alertas_de_riesgo(operador)) == 1
