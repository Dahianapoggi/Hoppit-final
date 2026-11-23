"""Tiempo de punta a punta del reruteo por desvio contra los 3 s de NFR-09, en el
grafo de CABA del producto (encargo 2026-11-22A, bloque 2d, D17 a D19).

Mide en el proceso (cliente de prueba, sin red ni telemetria real: las
posiciones las da un lector de prueba) `GET /v1/alertas`, que detecta el
desvio (anclaje de los reportes, trazado del tramo en curso por la red) y
genera la propuesta (matriz de costos con caminos y recalculo de todas las
pendientes desde la posicion), y despues `POST /v1/propuestas/{id}/aprobacion`
(Ruta nueva, mudanza de las Ordenes y apertura de su ejecucion). Escenarios: una
Ruta despachada de 13 Paradas y una de 20 (sin Parada fija, D19; la posicion
cae sobre una pendiente, que va primera, y quedan 12 y 19 por ordenar: exacto
y heuristico). Un precalentamiento y
`--repeticiones` detecciones por escenario, con un rechazo entre una y otra
(la espera entre propuestas se pone en 0 solo en la medicion); la aprobacion
se mide una vez, al final. Tiempos no controlados salvo que se mida con el
protocolo de `condiciones_de_medicion`; la salida lo rotula.

Uso: python tools/medir_desvio.py --salida <archivo.json>
"""
from __future__ import annotations

import argparse
import datetime as dt
import http
import json
import logging
import pathlib
import platform
import statistics
import sys
import time
from collections.abc import Sequence
from typing import Any, Final

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.cartografia.puerto import crear_servicio as crear_cartografia  # noqa: E402
from hoppit.planificacion_rutas.puerto import (  # noqa: E402
    ParametrosDeDesvio,
    ReporteDePosicion,
    distancia_a_polilinea_m,
)
from hoppit.shared.ids import RutaId  # noqa: E402
from tools.acceso_en_proceso import cliente_autenticado  # noqa: E402
from tools.servir_api import GRAFO_DE_LA_CIUDAD, GRAFOS_DEL_PRODUCTO, construir_contexto  # noqa: E402

SEMILLA: Final[int] = 20261122
CAPACIDAD: Final[int] = 30
PATENTE: Final[str] = "AB123CD"
ESCENARIOS: Final[tuple[int, ...]] = (13, 20)
UMBRAL_NFR09_S: Final[float] = 3.0
REPORTES_HACE_S: Final[tuple[float, float]] = (20.0, 5.0)
ROTULO: Final[str] = ("tiempos no controlados (equipo en uso, sin el protocolo de condiciones_de_medicion); "
                      "simulación sobre instancias sorteadas en CABA; no es validación contra datos reales")

_LOG = logging.getLogger("hoppit.tools.medir_desvio")


def _escenario(grafo: Any, paradas: int, repeticiones: int) -> dict[str, Any]:
    elegido = GRAFOS_DEL_PRODUCTO[GRAFO_DE_LA_CIUDAD]
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=elegido.grafo,
                                  deposito=elegido.deposito)
    operador = cliente_autenticado(contexto)
    assert operador.post("/v1/vehiculos", json={"id": PATENTE, "capacidad": CAPACIDAD}).status_code == 201
    nodos = sorted(set(grafo.nodos_de_mayor_componente_fuerte()) - {contexto.deposito})
    azar = np.random.default_rng([SEMILLA, paradas])
    ordenes = []
    for nodo in azar.choice(nodos, size=paradas, replace=False):
        r = grafo.rotulo(int(nodo))
        respuesta = operador.post("/v1/ordenes", json={"lat": r.lat, "lon": r.lon, "demanda": 1})
        assert respuesta.status_code == http.HTTPStatus.CREATED, respuesta.text
        ordenes.append(respuesta.json()["id"])
    plan = operador.post("/v1/planificaciones", json={"ordenes": ordenes, "vehiculos": {PATENTE: CAPACIDAD}})
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    (ruta,) = operador.get("/v1/rutas").json()
    detalle = operador.get(f"/v1/rutas/{ruta['id']}").json()
    assert len(detalle["paradas"]) == paradas and operador.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == 200
    vigilante = contexto.planificacion.desvio
    vigilante.parametros = ParametrosDeDesvio(espera_tras_una_propuesta_s=0.0)
    desde, hasta = vigilante.tramo_vigente(RutaId(ruta["id"]))
    trazado = grafo.trazado([desde, hasta]).coordenadas
    lejana = max(detalle["paradas"][1:], key=lambda p: distancia_a_polilinea_m((p["punto"]["lat"], p["punto"]["lon"]),
                                                                              trazado))
    punto = (lejana["punto"]["lat"], lejana["punto"]["lon"])

    def lector(_ruta: str) -> list[ReporteDePosicion]:
        ahora = dt.datetime.now(dt.timezone.utc)
        return [ReporteDePosicion(punto[0], punto[1], ahora - dt.timedelta(seconds=s)) for s in REPORTES_HACE_S]

    contexto.lector_de_posiciones_recientes = lector
    deteccion, metodos, distancias, cambia = [], set(), set(), set()
    propuesta: dict[str, Any] = {}
    for i in range(repeticiones + 1):
        antes = time.perf_counter()
        alertas = operador.get("/v1/alertas").json()["alertas"]
        transcurrido = time.perf_counter() - antes
        (alerta,) = [a for a in alertas if a["tipo"] == "desvio_de_ruta"]
        propuesta = operador.get(f"/v1/propuestas/{alerta['propuesta']}").json()
        if i > 0:
            deteccion.append(transcurrido)
            metodos.add(propuesta["metodo"])
            distancias.add(round(alerta["distancia_m"], 1))
            cambia.add(not propuesta["orden_sin_cambios"])
        if i < repeticiones:
            assert operador.post(f"/v1/propuestas/{alerta['propuesta']}/rechazo").status_code == 200
    antes = time.perf_counter()
    aprobacion = operador.post(f"/v1/propuestas/{propuesta['id']}/aprobacion")
    aprobar_s = time.perf_counter() - antes
    assert aprobacion.status_code == http.HTTPStatus.OK, aprobacion.text
    mediana_total = statistics.median(deteccion) + aprobar_s
    maximo_total = max(deteccion) + aprobar_s
    return {
        "paradas_de_la_ruta": paradas, "metodo": sorted(metodos), "repeticiones": repeticiones,
        "distancia_del_desvio_m": sorted(distancias), "orden_distinto_del_vigente": sorted(cambia),
        "diferencia_m": propuesta["diferencia_m"],
        "deteccion_y_propuesta_s": {"mediana": statistics.median(deteccion), "minimo": min(deteccion),
                                    "maximo": max(deteccion), "todas": deteccion},
        "aprobacion_s": aprobar_s,
        "punta_a_punta_s": {"mediana": mediana_total, "maximo": maximo_total},
        "cumple_nfr09": mediana_total < UMBRAL_NFR09_S and maximo_total < UMBRAL_NFR09_S,
    }


def main(argumentos: Sequence[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    lector = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    lector.add_argument("--salida", type=pathlib.Path, required=True)
    lector.add_argument("--repeticiones", type=int, default=10)
    args = lector.parse_args(argumentos)
    elegido = GRAFOS_DEL_PRODUCTO[GRAFO_DE_LA_CIUDAD]
    grafo = crear_cartografia(elegido.grafo)
    grafo.construir()
    resultado = {"rotulo": ROTULO, "grafo": elegido.grafo.name, "umbral_nfr09_s": UMBRAL_NFR09_S,
                 "semilla": SEMILLA, "plataforma": platform.platform(), "python": platform.python_version(),
                 "escenarios": [_escenario(grafo, n, args.repeticiones) for n in ESCENARIOS]}
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for e in resultado["escenarios"]:
        _LOG.info("%d Paradas | %s | deteccion y propuesta %.3f s (max %.3f) | aprobacion %.3f s | cumple %s",
                  e["paradas_de_la_ruta"], e["metodo"], e["deteccion_y_propuesta_s"]["mediana"],
                  e["deteccion_y_propuesta_s"]["maximo"], e["aprobacion_s"], e["cumple_nfr09"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
