"""Reglas de alerta (CU-08; 2026-10-27B, A), como funciones puras: cada una
con un caso que la dispara y otro que no, y una sola alerta por condicion."""
from __future__ import annotations

import datetime as dt

from hoppit.shared.ids import IncidenteId, RutaId
from hoppit.shared.tiempo import ZONA_OPERATIVA
from hoppit.vigilancia_sla.puerto import (
    UMBRAL_DE_PERMANENCIA_EN_CREADA_H,
    AlcanceDelIncidente,
    Incidente,
    OrdenObservada,
    RutaObservada,
    TipoDeAlerta,
    TipoDeIncidente,
    evaluar_alertas,
)

JORNADA = dt.date(2026, 11, 3)
# 15:00 en Buenos Aires de la jornada.
AHORA = dt.datetime(2026, 11, 3, 18, 0, tzinfo=dt.timezone.utc)


def _incidente(rutas: tuple[str, ...], declarado_en: dt.datetime = AHORA) -> Incidente:
    return Incidente(
        id=IncidenteId("incidente-1"),
        tipo=TipoDeIncidente.CORTE_DE_CALLE,
        alcance=AlcanceDelIncidente.PARCIAL,
        descripcion="prueba",
        rutas_afectadas=tuple(RutaId(r) for r in rutas),
        declarado_en=declarado_en,
    )


def _evaluar(incidentes=(), rutas=(), ordenes=(), ahora: dt.datetime = AHORA):  # type: ignore[no-untyped-def]
    return evaluar_alertas(JORNADA, ahora, ZONA_OPERATIVA, incidentes, rutas, ordenes)


def _orden(**campos: object) -> OrdenObservada:
    base: dict[str, object] = {
        "id": "orden-1",
        "en_creada": False,
        "planificada_o_en_transito": False,
        "ultima_transicion_en": AHORA - dt.timedelta(hours=1),
    }
    return OrdenObservada(**{**base, **campos})  # type: ignore[arg-type]


def test_un_incidente_sobre_una_ruta_con_ordenes_sin_terminar_da_una_alerta() -> None:
    (alerta,) = _evaluar([_incidente(("ruta-a",))], [RutaObservada("ruta-a", ("orden-1", "orden-2"))])
    assert alerta.tipo is TipoDeAlerta.INCIDENTE_EN_RUTA
    assert alerta.ruta == "ruta-a"
    assert alerta.ordenes == ("orden-1", "orden-2")
    assert alerta.desde == AHORA


def test_no_hay_alerta_de_incidente_si_la_ruta_termino_o_es_de_otra_jornada() -> None:
    assert _evaluar([_incidente(("ruta-a",))], [RutaObservada("ruta-a", ())]) == ()
    ayer = AHORA - dt.timedelta(days=1)
    assert _evaluar([_incidente(("ruta-a",), ayer)], [RutaObservada("ruta-a", ("orden-1",))]) == ()
    assert _evaluar([_incidente(("otra",))], [RutaObservada("ruta-a", ("orden-1",))]) == ()


def test_la_permanencia_en_creada_dispara_pasado_el_umbral_y_no_antes() -> None:
    umbral = dt.timedelta(hours=UMBRAL_DE_PERMANENCIA_EN_CREADA_H)
    vieja = _orden(en_creada=True, ultima_transicion_en=AHORA - umbral - dt.timedelta(minutes=1))
    (alerta,) = _evaluar(ordenes=[vieja])
    assert alerta.tipo is TipoDeAlerta.PERMANENCIA_EN_CREADA
    assert alerta.desde == vieja.ultima_transicion_en + umbral  # type: ignore[operator]
    reciente = _orden(en_creada=True, ultima_transicion_en=AHORA - umbral + dt.timedelta(minutes=1))
    assert _evaluar(ordenes=[reciente]) == ()
    planificada = _orden(planificada_o_en_transito=True, ultima_transicion_en=AHORA - 2 * umbral)
    assert _evaluar(ordenes=[planificada]) == ()


def test_el_riesgo_de_plazo_dispara_si_el_p90_pasa_el_dia_del_plazo() -> None:
    # 00:30 del 4/11 en Buenos Aires: despues del dia del plazo, el 3/11.
    tarde = dt.datetime(2026, 11, 4, 3, 30, tzinfo=dt.timezone.utc)
    en_riesgo = _orden(
        planificada_o_en_transito=True,
        plazo_comprometido=JORNADA,
        eta_p50=AHORA,
        eta_p90=tarde,
    )
    (alerta,) = _evaluar(ordenes=[en_riesgo])
    assert alerta.tipo is TipoDeAlerta.RIESGO_DE_PLAZO
    assert (alerta.eta_p50, alerta.eta_p90) == (AHORA, tarde)
    assert "no está calibrada" in alerta.motivo
    # 23:30 del 3/11 en Buenos Aires: todavia dentro del dia del plazo.
    a_tiempo = _orden(
        planificada_o_en_transito=True,
        plazo_comprometido=JORNADA,
        eta_p90=dt.datetime(2026, 11, 4, 2, 30, tzinfo=dt.timezone.utc),
    )
    assert _evaluar(ordenes=[a_tiempo]) == ()
    sin_plazo = _orden(planificada_o_en_transito=True, eta_p90=tarde)
    assert _evaluar(ordenes=[sin_plazo]) == ()


def test_una_condicion_vigente_es_una_sola_alerta_aunque_se_consulte_dos_veces() -> None:
    incidentes = [_incidente(("ruta-a", "ruta-a"))]
    rutas = [RutaObservada("ruta-a", ("orden-1",))]
    primera = _evaluar(incidentes, rutas)
    assert len(primera) == 1
    assert _evaluar(incidentes, rutas) == primera
