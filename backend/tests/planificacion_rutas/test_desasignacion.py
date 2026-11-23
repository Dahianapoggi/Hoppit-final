"""Desasignar una Orden y volver a planificar (FR-03 inverso).

El glosario admite el retorno de Planificada a Creada: "admite el retorno a
Creada ante una desasignacion, mecanismo que soporta la reasignacion de
proveedor y la replanificacion". Sin esa transicion, una vez planificadas
las Ordenes quedaban trabadas y una segunda corrida no tomaba nada.

Lo que estas pruebas fijan, ademas de la transicion, es la decision sobre la
Ruta: se elimina entera y todas sus Ordenes quedan libres. Las razones estan
en el docstring de `invalidar_ruta`; aca se verifica que efectivamente sea
asi y que no queden estados intermedios incoherentes.
"""
from __future__ import annotations

import datetime as dt
import logging

import pytest

from hoppit.gestion_ordenes.puerto import (
    EstadoOrden,
    Orden,
    crear_servicio as crear_ordenes,
)
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import (
    OrdenNoAsignada,
    ServicioPlanificacionRutas,
)
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada, TransicionInvalida
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, GrafoDePrueba

_LOG = logging.getLogger(__name__)

CAPACIDAD = 10
VEHICULO = VehiculoId("AA100BB")
DIA = dt.date(2026, 8, 23)


def _sistema() -> tuple[ServicioPlanificacionRutas, object]:
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    repositorio = RepositorioRutasEnMemoria()
    servicio = ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(
            ordenes, AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
        ),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
        reloj=lambda: DIA,
    )
    return servicio, ordenes


def test_sin_desasignar_una_segunda_corrida_no_tiene_nada_que_planificar() -> None:
    """El problema que esto resuelve, declarado como prueba.

    Es el riesgo de una demostracion en vivo: planificar dos veces y que la
    segunda no haga nada. Ahora falla con una causa nombrada en vez de
    devolver un plan vacio.
    """
    from hoppit.planificacion_rutas.puerto import InstanciaInvalida

    servicio, _ = _sistema()
    servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    with pytest.raises(InstanciaInvalida):
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})


def test_desasignar_devuelve_la_orden_a_creada(_=None) -> None:
    servicio, ordenes = _sistema()
    servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    objetivo = OrdenId("orden-0")

    ruta_id, liberadas = servicio.desasignar_orden(objetivo)

    orden = ordenes.consultar(objetivo)  # type: ignore[attr-defined]
    assert orden.estado is EstadoOrden.CREADA
    assert orden.ruta_id is None
    assert orden.secuencia_parada is None
    assert objetivo in liberadas
    _LOG.info(
        "Desasignacion | ruta=%s liberadas=%d", ruta_id, len(liberadas)
    )


def test_la_ruta_que_la_contenia_deja_de_existir() -> None:
    """No queda mutada ni marcada: deja de existir.

    Una Ruta a la que se le quita una Parada tendria una distancia que ya no
    corresponde a su recorrido, y un identificador derivado de un contenido
    que ya no tiene.
    """
    servicio, _ = _sistema()
    servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    ruta_id, _ = servicio.desasignar_orden(OrdenId("orden-0"))

    with pytest.raises(EntidadNoEncontrada):
        servicio.ruta_persistida(ruta_id)
    assert all(p.ruta.id != ruta_id for p in servicio.rutas_persistidas())


def test_todas_las_ordenes_de_esa_ruta_quedan_libres() -> None:
    """Ninguna queda apuntando a una Ruta que ya no existe.

    Planificada significa estar asignada a una Parada dentro de una Ruta:
    con la Ruta eliminada, ese estado seria falso para las demas.
    """
    servicio, ordenes = _sistema()
    solucion = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    de_la_ruta = {
        orden for ruta in solucion.rutas for orden in ruta.ordenes
    }

    _, liberadas = servicio.desasignar_orden(OrdenId("orden-0"))

    assert set(liberadas) == de_la_ruta
    for id_orden in liberadas:
        orden = ordenes.consultar(id_orden)  # type: ignore[attr-defined]
        assert orden.estado is EstadoOrden.CREADA
        assert orden.ruta_id is None


def test_la_desasignacion_queda_en_el_historial() -> None:
    """FR-03 exige el historial completo: la desasignacion no se borra.

    Sin el asiento, el historial diria que la Orden nunca estuvo
    planificada.
    """
    servicio, ordenes = _sistema()
    servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    servicio.desasignar_orden(OrdenId("orden-0"))

    historial = ordenes.consultar(OrdenId("orden-0")).historial  # type: ignore[attr-defined]
    assert [(t.estado_anterior, t.estado_nuevo) for t in historial] == [
        (None, EstadoOrden.CREADA),
        (EstadoOrden.CREADA, EstadoOrden.PLANIFICADA),
        (EstadoOrden.PLANIFICADA, EstadoOrden.CREADA),
    ]


def test_tras_desasignar_se_puede_volver_a_planificar() -> None:
    """El objetivo de todo esto, y ademas NFR-07: la misma instancia vuelve a
    dar la misma solucion."""
    servicio, _ = _sistema()
    primera = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    for ruta in list(primera.rutas):
        servicio.invalidar_ruta(ruta.id)

    segunda = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    assert [r.id for r in segunda.rutas] == [r.id for r in primera.rutas]
    assert segunda.distancia_total_m == pytest.approx(primera.distancia_total_m)


def test_desasignar_una_orden_no_planificada_se_rechaza() -> None:
    """No hay nada que desasignar, y decirlo es mejor que no hacer nada."""
    servicio, _ = _sistema()

    with pytest.raises(OrdenNoAsignada):
        servicio.desasignar_orden(OrdenId("orden-0"))


def test_desasignar_una_orden_inexistente_se_rechaza() -> None:
    servicio, _ = _sistema()

    with pytest.raises(EntidadNoEncontrada):
        servicio.desasignar_orden(OrdenId("no-existe"))


def test_invalidar_una_ruta_inexistente_se_rechaza() -> None:
    servicio, _ = _sistema()

    with pytest.raises(EntidadNoEncontrada):
        servicio.invalidar_ruta(RutaId("ruta-no-existe"))


def test_una_orden_creada_no_admite_desasignarse_dos_veces() -> None:
    """La regla sigue siendo la maquina de estados del glosario."""
    servicio, ordenes = _sistema()
    servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    servicio.desasignar_orden(OrdenId("orden-0"))

    orden = ordenes.consultar(OrdenId("orden-0"))  # type: ignore[attr-defined]
    with pytest.raises(TransicionInvalida):
        orden.desasignar()
