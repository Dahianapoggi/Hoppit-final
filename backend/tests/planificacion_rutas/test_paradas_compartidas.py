"""Dos Ordenes al mismo destino son una sola Parada (ARQ-05, relacion 3).

La instancia de demostracion tiene 20 destinos distintos, asi que no ejercita
este caso: sin estas pruebas el agrupamiento quedaria sin verificar. Se recorre
entero, hasta el estado de las Ordenes, porque el riesgo concreto de agrupar es
que una de las dos Ordenes agrupadas se quede sin transicionar.
"""
from __future__ import annotations

import pytest

from hoppit.gestion_ordenes.puerto import EstadoOrden, Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes, TipoSolicitud
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import ServicioPlanificacionRutas
from hoppit.shared.bus import Bus
from hoppit.shared.eventos import RutaPublicada
from hoppit.shared.ids import OrdenId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, GrafoDePrueba

# Tres Ordenes: dos comparten el primer destino, la tercera va a otro. La
# demanda esta elegida para que la suma de las agrupadas se distinga de
# cualquiera de las dos por separado (1 + 2 = 3, y ninguna vale 3).
DEMANDA_PRIMERA = 1
DEMANDA_SEGUNDA = 2
DEMANDA_TERCERA = 3
CAPACIDAD = 10

ORDENES_COMPARTIDAS = (OrdenId("comparte-a"), OrdenId("comparte-b"))
ORDEN_SOLA = OrdenId("sola")


def _sistema() -> tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]]:
    """Servicio de planificacion armado a mano sobre el grafo de prueba."""
    bus = Bus()
    ordenes = crear_ordenes(bus)
    publicados: list[RutaPublicada] = []
    bus.suscribirse("ruta_publicada", publicados.append)  # type: ignore[arg-type]

    ordenes.dar_de_alta(
        Orden(id=ORDENES_COMPARTIDAS[0], demanda=DEMANDA_PRIMERA, destino=DESTINOS[0])
    )
    ordenes.dar_de_alta(
        Orden(id=ORDENES_COMPARTIDAS[1], demanda=DEMANDA_SEGUNDA, destino=DESTINOS[0])
    )
    ordenes.dar_de_alta(Orden(id=ORDEN_SOLA, demanda=DEMANDA_TERCERA, destino=DESTINOS[1]))

    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    repositorio = RepositorioRutasEnMemoria()
    servicio = ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(ordenes, contexto),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
    )
    return servicio, ordenes, publicados


@pytest.fixture()
def sistema() -> tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]]:
    return _sistema()


def _capacidades() -> dict[VehiculoId, int]:
    return {VehiculoId("v1"): CAPACIDAD}


# --- Instancia ------------------------------------------------------------


def test_dos_ordenes_al_mismo_destino_producen_una_sola_parada(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    servicio, _, _ = sistema
    instancia = servicio.ensamblar_instancia(DEPOSITO, _capacidades())

    assert len(instancia.ordenes) == 3
    assert len(instancia.paradas) == 2

    compartida = next(p for p in instancia.paradas if p.nodo == DESTINOS[0])
    assert compartida.ordenes == ORDENES_COMPARTIDAS


def test_la_parada_compartida_suma_la_demanda_de_sus_ordenes(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    servicio, _, _ = sistema
    instancia = servicio.ensamblar_instancia(DEPOSITO, _capacidades())

    compartida = next(p for p in instancia.paradas if p.nodo == DESTINOS[0])
    assert compartida.demanda == DEMANDA_PRIMERA + DEMANDA_SEGUNDA
    assert instancia.demanda_total == DEMANDA_PRIMERA + DEMANDA_SEGUNDA + DEMANDA_TERCERA


def test_el_nodo_compartido_aparece_una_sola_vez_en_la_matriz(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """Si apareciera dos veces, el solver rutearia dos visitas al mismo punto."""
    servicio, _, _ = sistema
    instancia = servicio.ensamblar_instancia(DEPOSITO, _capacidades())

    assert instancia.matriz.nodos == (DEPOSITO, DESTINOS[0], DESTINOS[1])
    assert list(instancia.matriz.nodos).count(DESTINOS[0]) == 1


# --- Solucion -------------------------------------------------------------


def test_la_ruta_visita_el_nodo_compartido_una_sola_vez(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    servicio, _, _ = sistema
    solucion = servicio.planificar(DEPOSITO, _capacidades())

    visitados = [p.nodo for r in solucion.rutas for p in r.paradas]
    assert visitados.count(DESTINOS[0]) == 1
    assert sorted(visitados) == sorted(DESTINOS[:2])


def test_la_parada_planeada_referencia_las_dos_ordenes(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    servicio, _, _ = sistema
    solucion = servicio.planificar(DEPOSITO, _capacidades())

    ruta, parada = next(
        (r, p) for r in solucion.rutas for p in r.paradas if p.nodo == DESTINOS[0]
    )
    assert parada.ordenes == ORDENES_COMPARTIDAS
    assert parada.identidad == (ruta.id, parada.secuencia)


def test_la_carga_del_vehiculo_incluye_las_dos_ordenes_agrupadas(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """Agrupar no puede perder demanda: el Vehiculo carga los bultos de ambas."""
    servicio, _, _ = sistema
    solucion = servicio.planificar(DEPOSITO, _capacidades())

    total = DEMANDA_PRIMERA + DEMANDA_SEGUNDA + DEMANDA_TERCERA
    assert sum(solucion.carga_por_vehiculo.values()) == total


# --- Publicacion y estado de las Ordenes ----------------------------------


def test_el_evento_de_la_ruta_incluye_las_dos_ordenes_agrupadas(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """Con un solo Vehiculo hay una sola Ruta, y su evento nombra las tres
    Ordenes: agrupar dos en una Parada no puede dejar a ninguna afuera."""
    servicio, _, publicados = sistema
    solucion = servicio.planificar(DEPOSITO, _capacidades())

    assert len(publicados) == len(solucion.rutas) == 1
    assert sorted(publicados[0].ordenes_incluidas) == sorted(
        [*ORDENES_COMPARTIDAS, ORDEN_SOLA]
    )


def test_las_dos_ordenes_que_comparten_destino_quedan_planificadas(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """El riesgo concreto de agrupar: que una de las dos se quede en Creada
    porque el evento solo nombro a una."""
    servicio, ordenes, _ = sistema
    servicio.planificar(DEPOSITO, _capacidades())

    for id_orden in (*ORDENES_COMPARTIDAS, ORDEN_SOLA):
        actual = ordenes.consultar(id_orden)  # type: ignore[attr-defined]
        assert actual.estado is EstadoOrden.PLANIFICADA
    assert list(ordenes.ordenes_planificables()) == []  # type: ignore[attr-defined]


def test_las_ordenes_agrupadas_informan_la_misma_parada(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """RB-09 sobre el caso que puede romperlo: dos Ordenes, una sola visita.

    Las dos comparten Ruta y numero de Parada, porque el Vehiculo pasa una
    sola vez. Y lo que cada Orden informa tiene que coincidir con lo que la
    Parada dice de ella: son las dos direcciones de la misma relacion y no
    pueden discrepar.
    """
    servicio, ordenes, _ = sistema
    solucion = servicio.planificar(DEPOSITO, _capacidades())

    ruta, compartida = next(
        (r, p) for r in solucion.rutas for p in r.paradas if p.nodo == DESTINOS[0]
    )
    primera = ordenes.consultar(ORDENES_COMPARTIDAS[0])  # type: ignore[attr-defined]
    segunda = ordenes.consultar(ORDENES_COMPARTIDAS[1])  # type: ignore[attr-defined]

    assert primera.ruta_id == segunda.ruta_id == ruta.id
    assert primera.secuencia_parada == segunda.secuencia_parada == compartida.secuencia

    # La vuelta: la Parada que cada Orden dice ocupar la referencia a ella.
    for orden in (primera, segunda):
        parada = next(p for p in ruta.paradas if p.secuencia == orden.secuencia_parada)
        assert orden.id in parada.ordenes


def test_la_orden_sola_informa_su_propia_parada(
    sistema: tuple[ServicioPlanificacionRutas, object, list[RutaPublicada]],
) -> None:
    """La que no comparte destino queda en una Parada distinta de las otras."""
    servicio, ordenes, _ = sistema
    servicio.planificar(DEPOSITO, _capacidades())

    sola = ordenes.consultar(ORDEN_SOLA)  # type: ignore[attr-defined]
    compartida = ordenes.consultar(ORDENES_COMPARTIDAS[0])  # type: ignore[attr-defined]
    assert sola.esta_asignada
    assert sola.secuencia_parada != compartida.secuencia_parada


def test_el_agrupamiento_no_depende_del_orden_de_alta() -> None:
    """Dar de alta las Ordenes en otro orden tiene que dar la misma instancia
    (NFR-07): el agrupamiento ordena por nodo y por identificador."""
    servicio_a, _, _ = _sistema()
    instancia_a = servicio_a.ensamblar_instancia(DEPOSITO, _capacidades())

    bus = Bus()
    ordenes = crear_ordenes(bus)
    ordenes.dar_de_alta(Orden(id=ORDEN_SOLA, demanda=DEMANDA_TERCERA, destino=DESTINOS[1]))
    ordenes.dar_de_alta(
        Orden(id=ORDENES_COMPARTIDAS[1], demanda=DEMANDA_SEGUNDA, destino=DESTINOS[0])
    )
    ordenes.dar_de_alta(
        Orden(id=ORDENES_COMPARTIDAS[0], demanda=DEMANDA_PRIMERA, destino=DESTINOS[0])
    )
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    repositorio = RepositorioRutasEnMemoria()
    servicio_b = ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(ordenes, contexto),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
    )
    instancia_b = servicio_b.ensamblar_instancia(DEPOSITO, _capacidades())

    assert instancia_a.paradas == instancia_b.paradas
    assert instancia_a.matriz.nodos == instancia_b.matriz.nodos
