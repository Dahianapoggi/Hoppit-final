"""Plan de Distribucion: identidad, totales y la regla de un Vehiculo por
jornada (glosario, entidad 13).

Cierra lo que era un agregado sin identidad: `SolucionRuteo` existia como
plan de flota pero no se persistia ni se identificaba, de modo que una
corrida no se podia volver a consultar.
"""
from __future__ import annotations

import datetime as dt
import logging

import pytest

from hoppit.gestion_ordenes.puerto import EstadoOrden, Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import (
    ServicioPlanificacionRutas,
    VehiculosComprometidos,
)
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada
from hoppit.shared.ids import OrdenId, PlanId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, GrafoDePrueba

_LOG = logging.getLogger(__name__)

DIA = dt.date(2026, 8, 24)
OTRO_DIA = dt.date(2026, 8, 25)
CAPACIDAD = 30
# Demanda total de la instancia: 3 Ordenes de 2 bultos.
CAPACIDAD_REPARTIDA = 4


def _sistema(reloj=lambda: DIA):
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=2, destino=destino)
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
        reloj=reloj,
    )
    return servicio, ordenes


def _flota(*nombres: str) -> dict[VehiculoId, int]:
    return {VehiculoId(n): CAPACIDAD for n in nombres}


def _flota_repartida(*nombres: str) -> dict[VehiculoId, int]:
    """Capacidad justa, para que la corrida produzca mas de una Ruta.

    Con la capacidad holgada el solver mete las tres Ordenes en un solo
    Vehiculo --minimizar distancia lo pide-- y el Plan sale con una Ruta
    unica. Una prueba sobre "el Plan pierde una Ruta y sobrevive" necesita
    que haya mas de una.
    """
    return {VehiculoId(n): CAPACIDAD_REPARTIDA for n in nombres}


# --- Identidad y totales --------------------------------------------------


def test_la_corrida_produce_un_plan_con_identidad() -> None:
    servicio, _ = _sistema()

    plan = servicio.planificar_plan(DEPOSITO, _flota("v1"))

    assert plan.id.startswith("plan-")
    assert servicio.plan(plan.id) == plan
    _LOG.info("Plan | %s con %d Ruta(s)", plan.id, plan.cantidad_rutas)


def test_la_identidad_es_deterministica_y_no_sorteada() -> None:
    """NFR-07: la misma corrida produce el mismo identificador."""
    una, _ = _sistema()
    otra, _ = _sistema()

    assert una.planificar_plan(DEPOSITO, _flota("v1")).id == (
        otra.planificar_plan(DEPOSITO, _flota("v1")).id
    )


def test_los_totales_son_datos_del_plan_y_coinciden_con_sus_rutas() -> None:
    """Se guardan en el Plan en vez de derivarse al leer.

    Sumar el listado de Rutas deja de ser correcto en cuanto conviven varios
    Planes en una jornada, que es exactamente lo que el sistema admite.
    """
    servicio, _ = _sistema()

    plan = servicio.planificar_plan(DEPOSITO, _flota("v1", "v2"))

    rutas = [p.ruta for p in servicio.rutas_del_plan(plan.id)]
    assert plan.cantidad_rutas == len(rutas)
    assert plan.cantidad_paradas == sum(len(r.paradas) for r in rutas)
    assert plan.demanda_total == sum(r.carga for r in rutas)
    assert plan.distancia_total_m == pytest.approx(sum(r.distancia_m for r in rutas))


def test_el_plan_registra_su_fecha_operativa() -> None:
    servicio, _ = _sistema()

    assert servicio.planificar_plan(DEPOSITO, _flota("v1")).fecha_planificacion == DIA


def test_cada_ruta_referencia_su_plan() -> None:
    servicio, _ = _sistema()

    plan = servicio.planificar_plan(DEPOSITO, _flota("v1", "v2"))

    for persistida in servicio.rutas_persistidas():
        assert persistida.plan_id == plan.id
    assert {p.ruta.id for p in servicio.rutas_del_plan(plan.id)} == set(plan.rutas)


def test_un_plan_inexistente_no_se_inventa() -> None:
    servicio, _ = _sistema()

    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(PlanId("plan-no-existe"))


# --- Un Vehiculo, un Plan por jornada -------------------------------------


def test_planificar_con_un_vehiculo_ya_comprometido_se_rechaza() -> None:
    """Un Vehiculo en dos Planes del mismo dia tendria que recorrer dos Rutas
    a la vez."""
    servicio, ordenes = _sistema()
    primero = servicio.planificar_plan(DEPOSITO, _flota("v1"))
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))

    with pytest.raises(VehiculosComprometidos) as capturado:
        servicio.planificar_plan(DEPOSITO, _flota("v1"))

    error = capturado.value
    assert error.ocupados == {VehiculoId("v1"): primero.id}
    assert error.fecha == DIA
    _LOG.info("Rechazo | %s", error)


def test_el_rechazo_nombra_todos_los_vehiculos_comprometidos() -> None:
    """Con capacidad justa los dos Vehiculos trabajan, y los dos quedan
    comprometidos."""
    servicio, ordenes = _sistema()
    # Capacidad 4 con 6 bultos de demanda: ninguno solo alcanza.
    primero = servicio.planificar_plan(
        DEPOSITO, {VehiculoId("v1"): 4, VehiculoId("v2"): 4}
    )
    assert primero.cantidad_rutas == 2
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))

    with pytest.raises(VehiculosComprometidos) as capturado:
        servicio.planificar_plan(
            DEPOSITO, {VehiculoId("v1"): 4, VehiculoId("v2"): 4, VehiculoId("v3"): 4}
        )

    assert set(capturado.value.ocupados) == {VehiculoId("v1"), VehiculoId("v2")}


def test_un_vehiculo_seleccionado_pero_no_utilizado_no_queda_comprometido() -> None:
    """Participar de un Plan es tener Ruta en el.

    Un Vehiculo al que el solver no le asigno Paradas no produce Ruta, asi
    que no participa del Plan y sigue disponible para otra corrida de la
    misma jornada. Es consecuencia directa de que una corrida produce una
    Ruta por Vehiculo utilizado, no por Vehiculo de la Flota.
    """
    servicio, ordenes = _sistema()
    # Capacidad de sobra: al solver le alcanza con uno de los dos.
    plan = servicio.planificar_plan(DEPOSITO, _flota("usado", "ocioso"))
    assert plan.cantidad_rutas == 1

    ocioso = next(
        v for v in (VehiculoId("usado"), VehiculoId("ocioso")) if v not in plan.vehiculos
    )
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))

    segundo = servicio.planificar_plan(DEPOSITO, {ocioso: CAPACIDAD})

    assert segundo.vehiculos == (ocioso,)


def test_no_se_planifica_parcialmente_con_los_vehiculos_libres() -> None:
    """La instancia se declara inadmisible entera, con el mismo criterio que
    rige para la demanda que excede la capacidad."""
    servicio, ordenes = _sistema()
    servicio.planificar_plan(DEPOSITO, _flota("v1"))
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))
    planes_antes = len(servicio.planes())

    with pytest.raises(VehiculosComprometidos):
        servicio.planificar_plan(DEPOSITO, _flota("v1", "libre"))

    assert len(servicio.planes()) == planes_antes


def test_con_vehiculos_libres_una_segunda_corrida_procede() -> None:
    """Planificar dos veces sigue siendo correcto: lo que se prohibe es
    solapar Vehiculos, no planificar de nuevo."""
    servicio, ordenes = _sistema()
    primero = servicio.planificar_plan(DEPOSITO, _flota("v1"), (OrdenId("orden-0"),))
    ordenes.dar_de_alta(Orden(id=OrdenId("otra"), demanda=1, destino=DESTINOS[1]))

    segundo = servicio.planificar_plan(DEPOSITO, _flota("v2"), (OrdenId("otra"),))

    assert segundo.id != primero.id
    assert {p.id for p in servicio.planes()} == {primero.id, segundo.id}


def test_un_vehiculo_de_otra_jornada_esta_libre() -> None:
    """La regla es por jornada: lo de ayer no ocupa hoy."""
    jornada = [DIA]
    servicio, ordenes = _sistema(reloj=lambda: jornada[0])
    servicio.planificar_plan(DEPOSITO, _flota("v1"))
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))

    jornada[0] = OTRO_DIA
    del_dia_siguiente = servicio.planificar_plan(DEPOSITO, _flota("v1"))

    assert del_dia_siguiente.fecha_planificacion == OTRO_DIA
    assert len(servicio.planes()) == 2
    assert len(servicio.planes(DIA)) == 1


def test_liberar_un_plan_devuelve_sus_vehiculos_a_la_jornada() -> None:
    servicio, _ = _sistema()
    primero = servicio.planificar_plan(DEPOSITO, _flota("v1"))

    servicio.descartar_plan(primero.id)
    segundo = servicio.planificar_plan(DEPOSITO, _flota("v1"))

    assert segundo.fecha_planificacion == DIA
    assert servicio.plan(segundo.id)


# --- Descarte a nivel de Plan ---------------------------------------------


def test_descartar_un_plan_elimina_sus_rutas_y_libera_sus_ordenes() -> None:
    servicio, ordenes = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota("v1", "v2"))

    liberadas = servicio.descartar_plan(plan.id)

    assert servicio.rutas_persistidas() == ()
    assert len(liberadas) == len(DESTINOS)
    for id_orden in liberadas:
        assert ordenes.consultar(id_orden).estado is EstadoOrden.CREADA
    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(plan.id)


def test_descartar_un_plan_no_toca_los_demas() -> None:
    servicio, ordenes = _sistema()
    primero = servicio.planificar_plan(DEPOSITO, _flota("v1"), (OrdenId("orden-0"),))
    ordenes.dar_de_alta(Orden(id=OrdenId("otra"), demanda=1, destino=DESTINOS[1]))
    segundo = servicio.planificar_plan(DEPOSITO, _flota("v2"), (OrdenId("otra"),))

    servicio.descartar_plan(primero.id)

    assert [p.id for p in servicio.planes()] == [segundo.id]
    assert servicio.rutas_del_plan(segundo.id)


def test_descartar_un_plan_inexistente_se_rechaza() -> None:
    servicio, _ = _sistema()

    with pytest.raises(EntidadNoEncontrada):
        servicio.descartar_plan(PlanId("plan-no-existe"))


def test_el_descarte_por_ruta_sigue_disponible() -> None:
    """Se conserva porque ya esta en uso."""
    servicio, _ = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota_repartida("v1", "v2"))
    una = servicio.rutas_del_plan(plan.id)[0].ruta.id

    liberadas = servicio.invalidar_ruta(una)

    assert liberadas
    assert len(servicio.rutas_del_plan(plan.id)) == plan.cantidad_rutas - 1


# --- Jornadas con planificacion (RB-11) -----------------------------------


def test_se_enumeran_las_jornadas_con_plan() -> None:
    jornada = [DIA]
    servicio, ordenes = _sistema(reloj=lambda: jornada[0])
    servicio.planificar_plan(DEPOSITO, _flota("v1"), (OrdenId("orden-0"),))
    jornada[0] = OTRO_DIA
    ordenes.dar_de_alta(Orden(id=OrdenId("otra"), demanda=1, destino=DESTINOS[1]))
    servicio.planificar_plan(DEPOSITO, _flota("v2"), (OrdenId("otra"),))

    assert servicio.fechas_con_planificacion() == (DIA, OTRO_DIA)


def test_sin_planes_no_hay_jornadas_que_enumerar() -> None:
    servicio, _ = _sistema()

    assert servicio.fechas_con_planificacion() == ()


def test_descartar_la_ruta_de_un_vehiculo_lo_libera_para_la_jornada() -> None:
    """La ocupacion se deriva de las Rutas vigentes, no de una lista
    congelada en el Plan.

    Participar de un Plan es tener Ruta en el: si la Ruta se descarto, el
    Vehiculo ya no recorre nada. Con la lista congelada, descartar todas las
    Rutas de un Plan una por una dejaba sus Vehiculos comprometidos por un
    Plan que ya no tenia ninguna, y el sistema quedaba trabado para esa
    jornada.
    """
    servicio, ordenes = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota_repartida("v1", "v2"))
    una = servicio.rutas_del_plan(plan.id)[0].ruta
    assert una.vehiculo_id in plan.vehiculos

    servicio.invalidar_ruta(una.id)

    # El Vehiculo vuelve a estar disponible aunque el Plan siga existiendo
    # con las Rutas que le quedan.
    assert servicio.rutas_del_plan(plan.id)
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))
    segundo = servicio.planificar_plan(DEPOSITO, {una.vehiculo_id: CAPACIDAD})
    assert segundo.vehiculos == (una.vehiculo_id,)


# --- El Plan que se queda sin Rutas deja de ser plan -----------------------


def test_descartar_la_ultima_ruta_elimina_el_plan() -> None:
    """Un conjunto de Rutas sin Rutas no describe ninguna corrida.

    Es el caso que destapo el problema: descartar Ruta por Ruta dejaba un
    Plan existente y vacio. Marcarlo exigiria un ciclo de vida que el
    glosario no define para el Plan, y recalcular sus totales contradiria a
    la entidad 13, que los define como totales de la corrida y no como estado
    presente. Se elimina, por el mismo argumento con que se elimina la Ruta
    que se queda sin Ordenes.
    """
    servicio, _ = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota("v1"))
    rutas = [p.ruta.id for p in servicio.rutas_del_plan(plan.id)]
    assert len(rutas) == 1

    servicio.invalidar_ruta(rutas[0])

    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(plan.id)
    assert servicio.planes() == ()
    assert servicio.fechas_con_planificacion() == ()


def test_el_plan_sobrevive_mientras_le_quede_alguna_ruta() -> None:
    """Se elimina cuando se queda sin ninguna, no cuando pierde una."""
    servicio, _ = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota_repartida("v1", "v2"))
    rutas = [p.ruta.id for p in servicio.rutas_del_plan(plan.id)]
    assert len(rutas) > 1

    servicio.invalidar_ruta(rutas[0])
    assert servicio.plan(plan.id).id == plan.id

    for ruta_id in rutas[1:]:
        servicio.invalidar_ruta(ruta_id)
    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(plan.id)


def test_desasignar_la_ultima_orden_del_plan_tambien_lo_elimina() -> None:
    """La desasignacion llega al Plan por el mismo camino que el descarte.

    Desasignar una Orden elimina la Ruta que la contenia (FR-03 inverso); si
    era la unica Ruta del Plan, el Plan queda sin ninguna.
    """
    servicio, _ = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota("v1"))

    servicio.desasignar_orden(OrdenId("orden-0"))

    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(plan.id)


def test_descartar_un_plan_entero_sigue_eliminandolo_una_sola_vez() -> None:
    """El descarte del Plan no pasa por el camino nuevo y no se duplica."""
    servicio, _ = _sistema()
    plan = servicio.planificar_plan(DEPOSITO, _flota_repartida("v1", "v2"))

    liberadas = servicio.descartar_plan(plan.id)

    assert liberadas
    with pytest.raises(EntidadNoEncontrada):
        servicio.plan(plan.id)
    assert servicio.rutas_persistidas() == ()
