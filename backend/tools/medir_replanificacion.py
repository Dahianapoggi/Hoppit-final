"""Tiempo de punta a punta de la propuesta de recalculo de CU-07 contra los 3 s
de NFR-09, en el grafo de CABA del producto (encargo 2026-11-20A, bloque 2c,
D12).

Mide `POST /v1/incidentes/{id}/propuestas` en el proceso (cliente de prueba,
sin red): avance, anclaje de la posicion, matriz de costos por la red con
caminos, recalculo y respuesta. Escenarios: una Ruta despachada de 13 Paradas
(la en curso mas 12 pendientes, el mayor caso exacto) y una de 20 (el tamano
de NFR-09, heuristico). Un precalentamiento y `--repeticiones` mediciones por
escenario. Tiempos no controlados salvo que se mida con el protocolo de
`condiciones_de_medicion`; la salida lo rotula.

Uso: python tools/medir_replanificacion.py --salida <archivo.json>
"""
from __future__ import annotations

import argparse
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
from hoppit.planificacion_rutas.puerto import PosicionDelVehiculo  # noqa: E402
from tools.acceso_en_proceso import cliente_autenticado  # noqa: E402
from tools.servir_api import GRAFO_DE_LA_CIUDAD, GRAFOS_DEL_PRODUCTO, construir_contexto  # noqa: E402

SEMILLA: Final[int] = 20261120
CAPACIDAD: Final[int] = 30
PATENTE: Final[str] = "AB123CD"
ESCENARIOS: Final[tuple[int, ...]] = (13, 20)
UMBRAL_NFR09_S: Final[float] = 3.0
ANTIGUEDAD_DE_LA_POSICION_S: Final[float] = 10.0
ROTULO: Final[str] = "tiempos no controlados (equipo en uso, sin el protocolo de condiciones_de_medicion)"

_LOG = logging.getLogger("hoppit.tools.medir_replanificacion")


def _escenario(grafo: Any, paradas: int, repeticiones: int) -> dict[str, Any]:
    elegido = GRAFOS_DEL_PRODUCTO[GRAFO_DE_LA_CIUDAD]
    contexto = construir_contexto(base_de_datos=None, cartografia=grafo, grafo=elegido.grafo,
                                  deposito=elegido.deposito)
    operador = cliente_autenticado(contexto)
    alta = operador.post("/v1/vehiculos", json={"id": PATENTE, "capacidad": CAPACIDAD})
    assert alta.status_code == http.HTTPStatus.CREATED, alta.text
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
    assert len(detalle["paradas"]) == paradas, len(detalle["paradas"])
    assert operador.post(f"/v1/rutas/{ruta['id']}/despacho").status_code == http.HTTPStatus.OK
    tercera = detalle["paradas"][2]["punto"]
    contexto.lector_de_posicion = lambda _r: PosicionDelVehiculo(tercera["lat"], tercera["lon"],
                                                                 ANTIGUEDAD_DE_LA_POSICION_S)
    incidente = operador.post("/v1/incidentes", json={"tipo": "corte_de_calle", "alcance": "parcial",
                                                     "descripcion": "medicion", "rutas_afectadas": [ruta["id"]]})
    assert incidente.status_code == http.HTTPStatus.CREATED, incidente.text
    url = f"/v1/incidentes/{incidente.json()['id']}/propuestas"
    tiempos, calculo, metodos, diferencias = [], [], set(), []
    for i in range(repeticiones + 1):
        antes = time.perf_counter()
        respuesta = operador.post(url, json={"ruta_id": ruta["id"]})
        transcurrido = time.perf_counter() - antes
        assert respuesta.status_code == http.HTTPStatus.CREATED, respuesta.text
        cuerpo = respuesta.json()
        if i == 0:
            continue  # precalentamiento
        tiempos.append(transcurrido)
        calculo.append(cuerpo["segundos_de_calculo"])
        metodos.add(cuerpo["metodo"])
        diferencias.append(cuerpo["diferencia_m"])
    return {
        "paradas_de_la_ruta": paradas, "pendientes_ademas_de_la_en_curso": paradas - 1,
        "origen": cuerpo["origen"]["fuente"], "metodo": sorted(metodos), "repeticiones": repeticiones,
        "punta_a_punta_s": {"mediana": statistics.median(tiempos), "minimo": min(tiempos), "maximo": max(tiempos),
                            "todas": tiempos},
        "segundos_de_calculo": {"mediana": statistics.median(calculo), "maximo": max(calculo)},
        "diferencia_m": sorted(set(round(d, 3) for d in diferencias)),
        "cumple_nfr09": statistics.median(tiempos) < UMBRAL_NFR09_S and max(tiempos) < UMBRAL_NFR09_S,
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
        _LOG.info("%d Paradas | %s | mediana %.3f s, maximo %.3f s | cumple %s", e["paradas_de_la_ruta"],
                  e["metodo"], e["punta_a_punta_s"]["mediana"], e["punta_a_punta_s"]["maximo"], e["cumple_nfr09"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
