"""Garantia de terminacion del motor y planificacion por prioridad (B-220,
punto 2).

Fixtures minimos escritos a mano, sobre el grafo de prueba de distancias
predecibles: la distancia entre dos nodos es la diferencia de sus
identificadores. Sin instancias de CVRPLIB, que no se versionan.
"""
from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from hoppit.gestion_ordenes.puerto import (
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    PRIORIDAD_MEDIA,
    Orden,
    PlazoInvalido,
    PrioridadInvalida,
    RepositorioOrdenesSQLite,
    ServicioGestionOrdenes,
    crear_servicio as crear_ordenes,
)
from hoppit.planificacion_rutas.puerto import (
    LIMITE_DE_RAMAS,
    SIN_MARGEN,
    InstanciaProblema,
    MotivoNoPlanificada,
    MotivoSinSolucion,
    RepositorioRutasSQLite,
    ServicioPlanificacionRutas,
    SinSolucion,
    SinSolucionConTodosLosVehiculos,
    SolverVRPDeterminista,
    atraso_en_dias,
    capacidad_efectiva,
    crear_servicio as crear_planificacion,
    criticidad,
)
from hoppit.shared.almacen import conectar
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, JORNADA_DE_PRUEBA, GrafoDePrueba

VEHICULO = VehiculoId("v1")
OTRO_VEHICULO = VehiculoId("v2")
REPETICIONES_DE_DETERMINISMO = 5
LIMITE_BAJO_DE_RAMAS = 10
PARADAS_PARA_EL_LIMITE = 20


def _destino(posicion: int) -> int:
    return DEPOSITO + 100 * (posicion + 1)


def _sistema(
    ordenes: list[Orden], margen: float = SIN_MARGEN, base: pathlib.Path | None = None
) -> tuple[ServicioGestionOrdenes, ServicioPlanificacionRutas]:
    """Ordenes dadas de alta y el servicio de planificacion por el puerto."""
    bus = Bus()
    gestion = crear_ordenes(
        bus, RepositorioOrdenesSQLite(conectar(base)) if base is not None else None
    )
    for orden in ordenes:
        gestion.dar_de_alta(orden)
    destinos = tuple(orden.destino for orden in ordenes)
    planificacion = crear_planificacion(
        bus,
        GrafoDePrueba((DEPOSITO,) + destinos),
        gestion,
        repositorio=RepositorioRutasSQLite(conectar(base)) if base is not None else None,
        reloj=lambda: JORNADA_DE_PRUEBA,
        margen_de_capacidad=margen,
    )
    return gestion, planificacion


def _orden(
    posicion: int,
    demanda: int = 1,
    prioridad: int = PRIORIDAD_MEDIA,
    plazo: dt.date | None = None,
) -> Orden:
    return Orden(
        id=OrdenId(f"o{posicion}"),
        demanda=demanda,
        destino=_destino(posicion),
        prioridad=prioridad,
        plazo_comprometido=plazo,
    )


def _instancia(ordenes: list[Orden], capacidades: dict[VehiculoId, int]) -> InstanciaProblema:
    _, planificacion = _sistema(ordenes)
    return planificacion.ensamblar_instancia(DEPOSITO, capacidades, None, JORNADA_DE_PRUEBA)


# --- Limite de ramas -------------------------------------------------------


def test_el_limite_de_ramas_es_el_decidido() -> None:
    assert LIMITE_DE_RAMAS == 1_000_000


def test_un_limite_de_ramas_bajo_corta_la_busqueda_con_su_motivo() -> None:
    """Con un limite que no alcanza para la primera solucion, ninguna de las
    tres etapas encuentra una, y el motivo dice que se alcanzo el limite: no
    que la instancia sea infactible, que no lo es."""
    ordenes = [_orden(i) for i in range(PARADAS_PARA_EL_LIMITE)]
    instancia = _instancia(ordenes, {VEHICULO: PARADAS_PARA_EL_LIMITE})

    with pytest.raises(SinSolucion) as capturado:
        SolverVRPDeterminista(limite_de_ramas=LIMITE_BAJO_DE_RAMAS).resolver(instancia)

    assert capturado.value.motivo is MotivoSinSolucion.LIMITE_DE_BUSQUEDA
    assert MotivoSinSolucion.LIMITE_DE_BUSQUEDA.value in str(capturado.value)
    # Con el limite del producto, la misma instancia se resuelve completa.
    completa = SolverVRPDeterminista().resolver(instancia)
    assert set(completa.ordenes) == set(instancia.ordenes)
    assert not completa.es_parcial


def test_con_todos_los_vehiculos_el_limite_tambien_lleva_su_motivo() -> None:
    ordenes = [_orden(i) for i in range(PARADAS_PARA_EL_LIMITE)]
    instancia = _instancia(
        ordenes, {VEHICULO: PARADAS_PARA_EL_LIMITE, OTRO_VEHICULO: PARADAS_PARA_EL_LIMITE}
    )

    with pytest.raises(SinSolucionConTodosLosVehiculos) as capturado:
        SolverVRPDeterminista(
            limite_de_ramas=LIMITE_BAJO_DE_RAMAS, usar_todos_los_vehiculos=True
        ).resolver(instancia)

    assert capturado.value.motivo is MotivoSinSolucion.LIMITE_DE_BUSQUEDA


def test_una_infactibilidad_conocida_por_aritmetica_no_es_el_limite() -> None:
    """Ninguna Orden entra en el Vehiculo: no hay plan, y el motivo es la
    infactibilidad, no el limite."""
    instancia = _instancia([_orden(0, demanda=3), _orden(1, demanda=3)], {VEHICULO: 2})

    with pytest.raises(SinSolucion) as capturado:
        SolverVRPDeterminista().resolver(instancia)

    assert capturado.value.motivo is MotivoSinSolucion.INFACTIBLE


# --- Estrategia de respaldo ------------------------------------------------


def _etapas(
    monkeypatch: pytest.MonkeyPatch, instancia: InstanciaProblema
) -> list[tuple[str, bool]]:
    """Las etapas que recorre el motor: estrategia y si las Paradas eran
    opcionales, en orden."""
    from ortools.constraint_solver import routing_enums_pb2

    original = SolverVRPDeterminista._intentar
    llamadas: list[tuple[str, bool]] = []

    def espia(self, instancia, vehiculos, estrategia, opcionales):  # type: ignore[no-untyped-def]
        llamadas.append(
            (routing_enums_pb2.FirstSolutionStrategy.Value.Name(estrategia), opcionales)
        )
        return original(self, instancia, vehiculos, estrategia, opcionales)

    monkeypatch.setattr(SolverVRPDeterminista, "_intentar", espia)
    try:
        SolverVRPDeterminista().resolver(instancia)
    except SinSolucion:
        pass
    return llamadas


def test_el_respaldo_no_se_usa_si_la_principal_encuentra_solucion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instancia = _instancia([_orden(i) for i in range(4)], {VEHICULO: 4})

    assert _etapas(monkeypatch, instancia) == [("PATH_CHEAPEST_ARC", False)]


def test_el_respaldo_se_usa_si_la_principal_no_encuentra_solucion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tres Ordenes de 2 bultos en dos Vehiculos de 3: la capacidad total
    alcanza, pero no hay reparto. Principal, respaldo y, como tampoco
    encuentra, Paradas opcionales."""
    instancia = _instancia(
        [_orden(i, demanda=2) for i in range(3)], {VEHICULO: 3, OTRO_VEHICULO: 3}
    )

    assert _etapas(monkeypatch, instancia) == [
        ("PATH_CHEAPEST_ARC", False),
        ("PARALLEL_CHEAPEST_INSERTION", False),
        ("PATH_CHEAPEST_ARC", True),
    ]


def test_con_demanda_mayor_que_la_capacidad_se_va_directo_a_paradas_opcionales(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La aritmetica ya dice que no entran todas: no se busca una solucion
    completa que no existe."""
    instancia = _instancia([_orden(i) for i in range(4)], {VEHICULO: 3})

    assert _etapas(monkeypatch, instancia) == [("PATH_CHEAPEST_ARC", True)]


# --- Paradas opcionales por criticidad -------------------------------------


def test_queda_afuera_primero_la_parada_de_menor_criticidad() -> None:
    """Tres Ordenes de un bulto y lugar para dos. La de prioridad baja esta
    en la Parada mas cercana al Deposito, la mas barata de visitar: queda
    afuera igual, porque la penalizacion supera cualquier ahorro de
    distancia."""
    ordenes = [
        _orden(0, prioridad=PRIORIDAD_BAJA),
        _orden(1, prioridad=PRIORIDAD_MEDIA),
        _orden(2, prioridad=PRIORIDAD_ALTA),
    ]
    instancia = _instancia(ordenes, {VEHICULO: 2})

    solucion = SolverVRPDeterminista().resolver(instancia)

    assert set(solucion.ordenes) == {OrdenId("o1"), OrdenId("o2")}
    assert [(f.orden, f.criticidad, f.motivo) for f in solucion.no_planificadas] == [
        (OrdenId("o0"), PRIORIDAD_BAJA, MotivoNoPlanificada.DEMANDA_EXCEDE_CAPACIDAD)
    ]


def test_el_atraso_sube_la_criticidad_y_cambia_cual_queda_afuera() -> None:
    """Misma prioridad; la de la Parada lejana tiene el plazo vencido hace
    dos dias, y sube de criticidad 2 a 4. Queda afuera otra."""
    vencido = JORNADA_DE_PRUEBA - dt.timedelta(days=2)
    ordenes = [_orden(0), _orden(1), _orden(2, plazo=vencido)]
    instancia = _instancia(ordenes, {VEHICULO: 2})

    solucion = SolverVRPDeterminista().resolver(instancia)

    assert OrdenId("o2") in solucion.ordenes
    assert len(solucion.no_planificadas) == 1
    assert solucion.no_planificadas[0].criticidad == PRIORIDAD_MEDIA


def test_con_igual_criticidad_el_plan_parcial_es_determinista() -> None:
    ordenes = [_orden(i) for i in range(6)]
    instancia = _instancia(ordenes, {VEHICULO: 2, OTRO_VEHICULO: 2})

    resultados = {
        (
            tuple((str(r.id), tuple(p.nodo for p in r.paradas)) for r in s.rutas),
            s.no_planificadas,
        )
        for s in (
            SolverVRPDeterminista().resolver(instancia)
            for _ in range(REPETICIONES_DE_DETERMINISMO)
        )
    }

    assert len(resultados) == 1
    (_, afuera), = resultados
    assert len(afuera) == 2


def test_la_opcion_de_k_se_aplica_tambien_con_paradas_opcionales() -> None:
    ordenes = [_orden(i) for i in range(5)]
    instancia = _instancia(ordenes, {VEHICULO: 2, OTRO_VEHICULO: 2})

    solucion = SolverVRPDeterminista(usar_todos_los_vehiculos=True).resolver(instancia)

    assert len(solucion.rutas) == 2
    assert len(solucion.no_planificadas) == 1


# --- Criticidad, atraso y margen -------------------------------------------


def test_el_atraso_se_cuenta_contra_la_fecha_de_planificacion() -> None:
    plazo = dt.date(2026, 8, 20)

    assert atraso_en_dias(None, dt.date(2026, 8, 25)) == 0
    assert atraso_en_dias(plazo, dt.date(2026, 8, 19)) == 0
    assert atraso_en_dias(plazo, plazo) == 0
    assert atraso_en_dias(plazo, dt.date(2026, 8, 21)) == 1
    assert atraso_en_dias(plazo, dt.date(2026, 8, 25)) == 5
    assert criticidad(PRIORIDAD_BAJA, None, dt.date(2026, 8, 25)) == 1
    assert criticidad(PRIORIDAD_ALTA, plazo, dt.date(2026, 8, 21)) == 4
    # El atraso suma como mucho 2: la criticidad va de 1 a 5.
    assert criticidad(PRIORIDAD_ALTA, plazo, dt.date(2026, 8, 25)) == 5


def test_el_ensamblado_usa_la_fecha_de_planificacion_y_no_el_reloj() -> None:
    """La misma Orden, ensamblada para dos jornadas, da dos criticidades: la
    que corresponde a cada fecha de planificacion, no a la de hoy."""
    plazo = JORNADA_DE_PRUEBA
    _, planificacion = _sistema([_orden(0, prioridad=PRIORIDAD_BAJA, plazo=plazo)])

    a_tiempo = planificacion.ensamblar_instancia(DEPOSITO, {VEHICULO: 1}, None, plazo)
    un_dia_despues = planificacion.ensamblar_instancia(
        DEPOSITO, {VEHICULO: 1}, None, plazo + dt.timedelta(days=1)
    )

    assert a_tiempo.criticidad_por_orden == {OrdenId("o0"): 1}
    assert un_dia_despues.criticidad_por_orden == {OrdenId("o0"): 2}


def test_la_capacidad_efectiva_es_el_piso_de_la_nominal_menos_el_margen() -> None:
    assert capacidad_efectiva(13, 0.05) == 12
    assert capacidad_efectiva(20, 0.05) == 19
    assert capacidad_efectiva(13, SIN_MARGEN) == 13
    assert capacidad_efectiva(1, 0.05) == 0
    with pytest.raises(ValueError):
        capacidad_efectiva(13, 1.0)
    with pytest.raises(ValueError):
        capacidad_efectiva(13, -0.1)


def test_con_margen_la_instancia_lleva_la_capacidad_efectiva() -> None:
    _, planificacion = _sistema([_orden(0)], margen=0.05)

    instancia = planificacion.ensamblar_instancia(
        DEPOSITO, {VEHICULO: 13}, None, JORNADA_DE_PRUEBA
    )

    assert instancia.capacidad_por_vehiculo == {VEHICULO: 12}


def test_con_margen_la_flota_se_verifica_contra_la_capacidad_nominal() -> None:
    """La Flota registrada declara la nominal; la instancia lleva la efectiva.
    Pedir la nominal no es una capacidad distinta de la nominal."""
    bus = Bus()
    gestion = crear_ordenes(bus)
    gestion.dar_de_alta(_orden(0))
    planificacion = crear_planificacion(
        bus,
        GrafoDePrueba((DEPOSITO, _destino(0))),
        gestion,
        reloj=lambda: JORNADA_DE_PRUEBA,
        flota={VEHICULO: 13},
        margen_de_capacidad=0.05,
    )

    plan = planificacion.planificar_plan(DEPOSITO, {VEHICULO: 13})

    assert plan.cantidad_rutas == 1


# --- La Orden y el Plan parcial ---------------------------------------------


def test_la_orden_admite_solo_las_prioridades_y_un_plazo_fecha() -> None:
    assert Orden(id=OrdenId("o"), demanda=1, destino=1).prioridad == PRIORIDAD_MEDIA
    assert Orden(id=OrdenId("o"), demanda=1, destino=1).plazo_comprometido is None
    for invalida in (0, 4, True, "2"):
        with pytest.raises(PrioridadInvalida):
            Orden(id=OrdenId("o"), demanda=1, destino=1, prioridad=invalida)  # type: ignore[arg-type]
    with pytest.raises(PlazoInvalido):
        Orden(
            id=OrdenId("o"),
            demanda=1,
            destino=1,
            plazo_comprometido=dt.datetime(2026, 8, 25, 12),
        )


def test_el_plan_parcial_deja_sus_ordenes_en_creada_y_se_persiste(
    tmp_path: pathlib.Path,
) -> None:
    base = tmp_path / "parcial.sqlite3"
    ordenes = [
        # Prioridad baja con el plazo vencido hace un dia: criticidad 2,
        # menor que la 3 de la otra.
        _orden(0, prioridad=PRIORIDAD_BAJA, plazo=JORNADA_DE_PRUEBA - dt.timedelta(days=1)),
        _orden(1, prioridad=PRIORIDAD_ALTA),
    ]
    gestion, planificacion = _sistema(ordenes, base=base)

    plan = planificacion.planificar_plan(DEPOSITO, {VEHICULO: 1})

    assert plan.es_parcial
    assert [(str(f.orden), f.criticidad) for f in plan.ordenes_no_planificadas] == [("o0", 2)]
    assert gestion.consultar(OrdenId("o0")).estado.value == "creada"
    assert gestion.consultar(OrdenId("o0")).historial[-1].estado_anterior is None
    assert gestion.consultar(OrdenId("o1")).estado.value == "planificada"
    # Leido de la base, el Plan sigue declarando lo que dejo afuera.
    releido = RepositorioRutasSQLite(conectar(base)).obtener_plan(plan.id)
    assert releido.ordenes_no_planificadas == plan.ordenes_no_planificadas
    # Y la prioridad y el plazo de la Orden tambien vuelven de la base.
    for orden in ordenes:
        assert RepositorioOrdenesSQLite(conectar(base)).obtener(orden.id).prioridad == orden.prioridad
        assert (
            RepositorioOrdenesSQLite(conectar(base)).obtener(orden.id).plazo_comprometido
            == orden.plazo_comprometido
        )
