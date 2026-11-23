"""Registro y consulta de Incidentes (FR-18; CU-07, paso 1), sesion
2026-10-15A, bloque I: alta, consulta, tipo y alcance invalidos, Ruta
inexistente, cero Rutas afectadas, evento en el bus, persistencia propia y
verificacion de las Rutas por el puerto de Planificacion de Rutas."""
from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3
from collections.abc import Sequence

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO, rutas_inexistentes_segun
from hoppit.shared.almacen import conectar
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.eventos import IncidenteDeclarado
from hoppit.shared.ids import IncidenteId, RutaId
from hoppit.shared.tiempo import instante_actual
from hoppit.vigilancia_sla.puerto import (
    AlcanceDeIncidenteInvalido,
    AlcanceDelIncidente,
    IncidenteNoEncontrado,
    RepositorioIncidentesSQLite,
    RutasAfectadasInexistentes,
    TipoDeIncidente,
    TipoDeIncidenteInvalido,
    crear_servicio_de_incidentes,
)
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

INSTANTE = dt.datetime(2026, 10, 15, 6, 0, tzinfo=dt.timezone.utc)
EXISTENTES = {RutaId("ruta-a"), RutaId("ruta-b")}


class _Reloj:
    def __init__(self) -> None:
        self.lecturas = 0

    def __call__(self) -> dt.datetime:
        self.lecturas += 1
        return INSTANTE + dt.timedelta(minutes=self.lecturas)


def _faltan(rutas: Sequence[RutaId]) -> list[RutaId]:
    return [r for r in rutas if r not in EXISTENTES]


def _servicio(bus: Bus | None = None, **otros: object):  # type: ignore[no-untyped-def]
    contador = iter(range(1, 1000))
    return crear_servicio_de_incidentes(
        bus or Bus(),
        _faltan,
        reloj=otros.get("reloj", _Reloj()),  # type: ignore[arg-type]
        generar_identificador=lambda: IncidenteId(f"incidente-{next(contador)}"),
        repositorio=otros.get("repositorio"),  # type: ignore[arg-type]
    )


def test_alta_y_consulta() -> None:
    eventos: list[IncidenteDeclarado] = []
    bus = Bus()
    bus.suscribirse("incidente_declarado", eventos.append)  # type: ignore[arg-type]
    servicio = _servicio(bus)
    incidente = servicio.declarar("corte_de_calle", "parcial", "Corte por obra", ["ruta-a"])
    assert incidente.tipo is TipoDeIncidente.CORTE_DE_CALLE
    assert incidente.alcance is AlcanceDelIncidente.PARCIAL
    assert incidente.rutas_afectadas == (RutaId("ruta-a"),)
    assert incidente.declarado_en == INSTANTE + dt.timedelta(minutes=1)
    assert servicio.obtener(str(incidente.id)) == incidente
    segundo = servicio.declarar("siniestro", "total", "", ["ruta-b", "ruta-a", "ruta-b"])
    assert segundo.rutas_afectadas == (RutaId("ruta-b"), RutaId("ruta-a"))
    assert servicio.listar() == (incidente, segundo)
    assert [e.incidente_id for e in eventos] == ["incidente-1", "incidente-2"]
    assert eventos[0].tipo_de_incidente == "corte_de_calle" and eventos[0].alcance == "parcial"
    assert eventos[1].rutas_afectadas == ("ruta-b", "ruta-a")


def test_consultar_un_incidente_que_no_existe() -> None:
    with pytest.raises(IncidenteNoEncontrado):
        _servicio().obtener("incidente-99")
    assert issubclass(IncidenteNoEncontrado, EntidadNoEncontrada)


@pytest.mark.parametrize(
    ("tipo", "alcance", "error"),
    [
        ("inundacion", "total", TipoDeIncidenteInvalido),
        ("Corte de calle", "total", TipoDeIncidenteInvalido),
        ("desvio", "parcialmente", AlcanceDeIncidenteInvalido),
        ("desvio", "", AlcanceDeIncidenteInvalido),
    ],
)
def test_tipo_y_alcance_invalidos_no_registran_nada(tipo: str, alcance: str, error: type[Exception]) -> None:
    eventos: list[IncidenteDeclarado] = []
    bus = Bus()
    bus.suscribirse("incidente_declarado", eventos.append)  # type: ignore[arg-type]
    servicio = _servicio(bus)
    with pytest.raises(error):
        servicio.declarar(tipo, alcance, "x", ["ruta-a"])
    assert servicio.listar() == ()
    assert eventos == []


def test_una_ruta_afectada_que_no_existe() -> None:
    servicio = _servicio()
    with pytest.raises(RutasAfectadasInexistentes) as error:
        servicio.declarar("cancelacion", "total", "x", ["ruta-a", "ruta-z"])
    assert error.value.rutas == (RutaId("ruta-z"),)
    assert servicio.listar() == ()


def test_cero_rutas_afectadas_es_valido() -> None:
    llamadas: list[Sequence[RutaId]] = []

    def faltan(rutas: Sequence[RutaId]) -> list[RutaId]:
        llamadas.append(rutas)
        return []

    servicio = crear_servicio_de_incidentes(Bus(), faltan)
    incidente = servicio.declarar("condicion_climatica_adversa", "parcial", "Tormenta", [])
    assert incidente.rutas_afectadas == ()
    assert llamadas == []


def test_el_reloj_por_omision_es_el_del_historial_de_la_orden() -> None:
    servicio = crear_servicio_de_incidentes(Bus(), _faltan)
    antes = instante_actual()
    incidente = servicio.declarar("desvio", "parcial", "x", [])
    assert antes <= incidente.declarado_en <= instante_actual()
    assert incidente.declarado_en.tzinfo is not None


def test_persistencia_en_tablas_propias(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "incidentes.sqlite3"
    servicio = _servicio(repositorio=RepositorioIncidentesSQLite(conectar(base)))
    incidente = servicio.declarar("siniestro", "total", "Choque", ["ruta-b", "ruta-a"])
    otro = _servicio(repositorio=RepositorioIncidentesSQLite(conectar(base)))
    assert otro.obtener(str(incidente.id)) == incidente
    assert otro.listar() == (incidente,)
    with sqlite3.connect(base) as conexion:
        tablas = {fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        claves = conexion.execute("PRAGMA foreign_key_list(rutas_afectadas_por_incidente)").fetchall()
    assert tablas == {"incidentes", "rutas_afectadas_por_incidente", "zonas_de_incidente"}
    assert {clave[2] for clave in claves} == {"incidentes"}


def test_las_rutas_se_verifican_por_el_puerto_de_planificacion() -> None:
    contexto = construir_contexto(
        base_de_datos=None, grafo=GRAFO_PREDETERMINADO, deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO
    )
    ruta = contexto.planificacion.planificar_plan(contexto.deposito, dict(contexto.flota))
    existente = contexto.planificacion.rutas_persistidas()[0].ruta.id
    servicio = crear_servicio_de_incidentes(Bus(), rutas_inexistentes_segun(contexto.planificacion))
    assert ruta.cantidad_rutas >= 1
    incidente = servicio.declarar("corte_de_calle", "parcial", "x", [str(existente)])
    assert incidente.rutas_afectadas == (existente,)
    with pytest.raises(RutasAfectadasInexistentes):
        servicio.declarar("corte_de_calle", "parcial", "x", [str(existente), "ruta-inexistente"])
    # La declaracion no toca la Ruta.
    assert contexto.planificacion.ruta_persistida(existente).ruta.id == existente
