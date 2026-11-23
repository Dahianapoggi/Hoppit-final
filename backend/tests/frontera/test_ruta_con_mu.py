"""Una Ruta nacida de una propuesta por riesgo vial conserva su criterio (D36
del encargo 2026-11-23F): su mu queda guardado y publicado, su ETA por Parada
sale de los caminos con que se dibujo, y sus recalculos usan el mismo costo
generalizado. Con la tabla artificial de `test_propuesta_por_riesgo_vial.py`."""
from __future__ import annotations

from typing import Any

from hoppit.cartografia.puerto import GrafoVialAMBA
from hoppit.planificacion_rutas.puerto import MOTIVO_DESVIO, MU_DE_LA_PROPUESTA_M
from hoppit.prediccion_eta.puerto import ParadaARecorrer
from hoppit.shared.ids import RutaId
from hoppit.shared.intervalo_por_celda import FranjaFueraDeLaJornada
from tests.frontera.test_propuesta_por_riesgo_vial import _alertas_de_riesgo, _armar, caso, sin_tabla  # noqa: F401


def _aprobada(caso: dict[str, Any]) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    contexto, operador, ruta = _armar(caso["grafo"])
    (alerta,) = _alertas_de_riesgo(operador)
    r = operador.post(f"/v1/propuestas/{alerta['propuesta']}/aprobacion")
    assert r.status_code == 200, r.text
    nueva = operador.get(f"/v1/rutas/{r.json()['ruta_nueva']}").json()
    return contexto, operador, ruta, nueva


def test_el_mu_queda_guardado_y_publicado_y_la_eta_sale_de_sus_caminos(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta, nueva = _aprobada(caso)
    grafo: GrafoVialAMBA = caso["grafo"]
    assert nueva["reemplaza_a"]["mu_m"] == MU_DE_LA_PROPUESTA_M
    assert nueva["mu_de_los_caminos_m"] == MU_DE_LA_PROPUESTA_M
    # La ETA publicada es la del estimador con las distancias de los caminos de mu.
    persistida = contexto.planificacion.ruta_persistida(RutaId(nueva["id"]))
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    reemplazo = servicio.reemplazo_de_la_nueva(RutaId(nueva["id"]))
    assert reemplazo is not None and reemplazo.mu_m == MU_DE_LA_PROPUESTA_M
    nodos = [reemplazo.origen_nodo, *(p.nodo for p in persistida.ruta.paradas)]
    matriz = grafo.matriz_de_costos_con_riesgo(nodos, MU_DE_LA_PROPUESTA_M)
    salida = nueva["hora_de_salida"]
    h, m, s = (int(x) for x in salida.split(":"))
    salida_min = h * 60 + m + s / 60
    publicadas = [p["eta_estimado_s"] for p in nueva["paradas"]]
    try:
        esperadas = contexto.prediccion_eta.estimar(
            [ParadaARecorrer(distancia_por_la_red_m=matriz.distancia_m(o, p.nodo), ordenes=len(p.ordenes))
             for o, p in zip(nodos, persistida.ruta.paradas)], salida_min, contexto.velocidad_km_h)
    except FranjaFueraDeLaJornada:
        # La Ruta sale a la hora real: de tarde su recorrido pasa el fin de la
        # jornada que cubre la tabla, y entonces no se publica ninguna ETA.
        assert all(p is None for p in publicadas)
        return
    assert publicadas == [float(round((e.mediana_min - salida_min) * 60)) for e in esperadas]
    assert any(p is not None for p in publicadas)


def test_un_desvio_posterior_propone_con_el_mismo_mu_y_la_ruta_siguiente_lo_hereda(caso) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta, nueva = _aprobada(caso)
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    desvio = servicio.proponer(RutaId(nueva["id"]), MOTIVO_DESVIO, "desvio-prueba", None, fijar_en_curso=False)
    assert desvio.mu_m == MU_DE_LA_PROPUESTA_M
    assert operador.get(f"/v1/propuestas/{desvio.id}").status_code == 200
    reemplazo = servicio.aprobar(desvio.id)
    siguiente = operador.get(f"/v1/rutas/{reemplazo.ruta_nueva}").json()
    assert siguiente["mu_de_los_caminos_m"] == MU_DE_LA_PROPUESTA_M
    # La distancia publicada es la de los caminos de mu, no el costo generalizado.
    assert siguiente["distancia_m"] == desvio.distancia_propuesta_m
    assert siguiente["trazado"]["distancia_m"] == desvio.distancia_propuesta_m or abs(
        siguiente["trazado"]["distancia_m"] - desvio.distancia_propuesta_m) < 1e-6


def test_una_ruta_normal_no_cambia(sin_tabla) -> None:  # type: ignore[no-untyped-def]
    contexto, operador, ruta = _armar(sin_tabla)
    detalle = operador.get(f"/v1/rutas/{ruta['id']}").json()
    assert "mu_de_los_caminos_m" not in detalle
    servicio = contexto.planificacion.reemplazo
    assert servicio is not None
    desvio = servicio.proponer(RutaId(ruta["id"]), MOTIVO_DESVIO, "desvio-prueba", None, fijar_en_curso=False)
    assert desvio.mu_m is None
    reemplazo = servicio.aprobar(desvio.id)
    assert reemplazo.mu_m is None
    siguiente = operador.get(f"/v1/rutas/{reemplazo.ruta_nueva}").json()
    assert "mu_de_los_caminos_m" not in siguiente and "mu_m" not in siguiente["reemplaza_a"]
