"""La jornada de la planificacion: como se registra, como se filtra y como
entra en la identidad (RB-01).

El reloj se inyecta en todas: una prueba que dependiera del reloj del sistema
no podria afirmar una fecha concreta, y las que comparan dos dias distintos
serian imposibles de escribir sin esperar a mañana.

La jornada es **parte de la instancia**. Dos corridas equivalentes del mismo
dia siguen dando los mismos identificadores --NFR-07--, y dos corridas
equivalentes de dias distintos dan identificadores distintos, porque son dos
viajes. Antes no era asi y la corrida de hoy pisaba a la de ayer en el
almacen, que las indexa por identificador.
"""
from __future__ import annotations

import datetime as dt
import logging

from hoppit.gestion_ordenes.puerto import Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import ServicioPlanificacionRutas
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId
from hoppit.shared.tiempo import ZONA_OPERATIVA, fecha_operativa

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, GrafoDePrueba

DIA_DE_LA_CORRIDA = dt.date(2026, 8, 21)
OTRO_DIA = dt.date(2026, 8, 22)
DIA_SIN_CORRIDAS = dt.date(2026, 8, 20)

CAPACIDAD = 10
VEHICULO = VehiculoId("AA100BB")

_LOG = logging.getLogger(__name__)


def _servicio(
    reloj: object, repositorio: RepositorioRutasEnMemoria | None = None
) -> ServicioPlanificacionRutas:
    """Servicio armado a mano, con el reloj bajo control de la prueba.

    El repositorio se admite por parametro para poder compartirlo entre dos
    sistemas: es la unica forma de que dos jornadas distintas escriban en el
    mismo almacen y se pueda afirmar que no se pisan.
    """
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    if repositorio is None:
        repositorio = RepositorioRutasEnMemoria()
    return ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(ordenes, contexto),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
        reloj=reloj,  # type: ignore[arg-type]
    )


def test_la_ruta_persiste_el_dia_en_que_se_planifico() -> None:
    servicio = _servicio(lambda: DIA_DE_LA_CORRIDA)
    solucion = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    for ruta in solucion.rutas:
        persistida = servicio.ruta_persistida(ruta.id)
        assert persistida.fecha_planificacion == DIA_DE_LA_CORRIDA
        assert persistida.deposito == DEPOSITO
    _LOG.info(
        "Rutas persistidas | cantidad=%d fecha=%s",
        len(solucion.rutas),
        DIA_DE_LA_CORRIDA,
    )


def test_el_filtro_por_fecha_devuelve_solo_las_de_ese_dia() -> None:
    """Dos corridas en dias distintos, filtradas por cada dia.

    Cada corrida va sobre un servicio propio con su repositorio propio: lo
    que se verifica aca es el filtro, no la acumulacion. Que las dos
    convivan en un mismo almacen lo verifica la prueba de mas abajo.
    """
    primero = _servicio(lambda: DIA_DE_LA_CORRIDA)
    primero.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    segundo = _servicio(lambda: OTRO_DIA)
    segundo.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    del_primer_dia = primero.rutas_persistidas(DIA_DE_LA_CORRIDA)
    assert del_primer_dia
    assert all(p.fecha_planificacion == DIA_DE_LA_CORRIDA for p in del_primer_dia)

    assert primero.rutas_persistidas(OTRO_DIA) == ()
    assert primero.fechas_planificadas() == (DIA_DE_LA_CORRIDA,)
    assert segundo.fechas_planificadas() == (OTRO_DIA,)


def test_sin_filtro_devuelve_todas_y_un_dia_vacio_no_es_un_error() -> None:
    """Un dia sin Rutas devuelve la lista vacia, no un fallo.

    Preguntar por un dia en el que no se planifico es una consulta legitima
    de la interfaz, no una condicion de error.
    """
    servicio = _servicio(lambda: DIA_DE_LA_CORRIDA)
    solucion = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    assert len(servicio.rutas_persistidas()) == len(solucion.rutas)
    assert servicio.rutas_persistidas(DIA_SIN_CORRIDAS) == ()


def test_la_misma_instancia_en_la_misma_jornada_da_los_mismos_identificadores() -> None:
    """NFR-07, la mitad que no se toca.

    Dos corridas equivalentes del mismo dia siguen produciendo exactamente
    las mismas Rutas y el mismo Plan. Es lo que el requisito exige y es lo
    que hace que el resultado sea reproducible en una defensa.
    """
    primera = _servicio(lambda: DIA_DE_LA_CORRIDA).planificar_plan(
        DEPOSITO, {VEHICULO: CAPACIDAD}
    )
    segunda = _servicio(lambda: DIA_DE_LA_CORRIDA).planificar_plan(
        DEPOSITO, {VEHICULO: CAPACIDAD}
    )

    assert primera.id == segunda.id
    assert primera.rutas == segunda.rutas


def test_la_misma_instancia_en_otra_jornada_da_identificadores_distintos() -> None:
    """La otra mitad, que antes estaba mal.

    El identificador se derivaba sin la jornada, asi que replanificar hoy la
    misma instancia que ayer producia un Plan y unas Rutas con los mismos
    identificadores que los de ayer. Como el almacen los indexa por
    identificador, la corrida de hoy **sobreescribia** a la de ayer: la
    jornada anterior perdia su Plan sin que nada lo declarara.

    Ahora la jornada es parte de la instancia. Dos jornadas distintas son dos
    viajes distintos aunque el recorrido coincida punto por punto, y el
    almacen conserva los dos.
    """
    de_hoy = _servicio(lambda: DIA_DE_LA_CORRIDA)
    plan_de_hoy = de_hoy.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    de_mañana = _servicio(lambda: OTRO_DIA)
    plan_de_mañana = de_mañana.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})

    assert plan_de_hoy.id != plan_de_mañana.id
    assert set(plan_de_hoy.rutas).isdisjoint(plan_de_mañana.rutas)
    _LOG.info(
        "Misma instancia, dos jornadas | %s -> %s | %s -> %s",
        DIA_DE_LA_CORRIDA,
        plan_de_hoy.id,
        OTRO_DIA,
        plan_de_mañana.id,
    )


def test_dos_jornadas_conviven_en_el_mismo_almacen_sin_pisarse() -> None:
    """"Sin sobreescritura", que es la consecuencia que importaba.

    El almacen indexa por identificador y persistir hace DELETE + INSERT: con
    identificadores iguales, la corrida de la segunda jornada no convivia con
    la primera sino que **ocupaba su lugar**, y la Ruta de ayer pasaba a
    figurar como Ruta de hoy.

    Se comparte el repositorio entre dos sistemas con jornadas distintas.
    Cada uno tiene sus propias Ordenes en Creada, de modo que los dos pueden
    planificar la misma instancia; lo que comparten es el lugar donde queda
    guardada.
    """
    repositorio = RepositorioRutasEnMemoria()
    de_ayer = _servicio(lambda: DIA_DE_LA_CORRIDA, repositorio)
    de_hoy = _servicio(lambda: OTRO_DIA, repositorio)

    plan_de_ayer = de_ayer.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    plan_de_hoy = de_hoy.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})

    assert {p.id for p in de_ayer.planes()} == {plan_de_ayer.id, plan_de_hoy.id}
    assert de_ayer.planes(DIA_DE_LA_CORRIDA) == (plan_de_ayer,)
    assert de_ayer.planes(OTRO_DIA) == (plan_de_hoy,)

    # Y cada Ruta conserva la jornada en la que se planifico.
    for ruta_id in plan_de_ayer.rutas:
        assert de_ayer.ruta_persistida(ruta_id).fecha_planificacion == DIA_DE_LA_CORRIDA
    for ruta_id in plan_de_hoy.rutas:
        assert de_ayer.ruta_persistida(ruta_id).fecha_planificacion == OTRO_DIA


def test_la_fecha_de_la_ruta_es_la_operativa_local_y_no_la_utc() -> None:
    """RB-01 sobre el caso que rompe el filtro: una corrida de la noche.

    A las 22:30 hora local ya es el dia siguiente en UTC. La Ruta tiene que
    quedar registrada en el dia local, que es el que el operador consulta
    cuando pide "las rutas de hoy".
    """
    de_la_noche = dt.datetime(2026, 8, 21, 22, 30, tzinfo=ZONA_OPERATIVA)
    assert de_la_noche.astimezone(dt.timezone.utc).date() == dt.date(2026, 8, 22)

    servicio = _servicio(lambda: fecha_operativa(de_la_noche))
    solucion = servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})

    for ruta in solucion.rutas:
        assert servicio.ruta_persistida(ruta.id).fecha_planificacion == dt.date(
            2026, 8, 21
        )
    assert servicio.rutas_persistidas(dt.date(2026, 8, 21))
    assert servicio.rutas_persistidas(dt.date(2026, 8, 22)) == ()


def test_el_historial_de_la_orden_sigue_marcandose_en_utc() -> None:
    """Los instantes no se tocan: solo la fecha operativa es local."""
    from hoppit.gestion_ordenes.puerto import Orden as _Orden

    orden = _Orden(id=OrdenId("o1"), demanda=1, destino=DESTINOS[0])
    orden.asignar_a_ruta(RutaId("ruta-x"), 1)

    assert orden.historial[0].marca_temporal.tzinfo is dt.timezone.utc
