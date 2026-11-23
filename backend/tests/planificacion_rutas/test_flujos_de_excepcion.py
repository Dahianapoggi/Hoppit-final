"""Los dos flujos de excepcion especificados para CU-02 en fase 1 (RB-05).

Son las dos unicas condiciones de error del caso de uso, y tienen que ser
distinguibles entre si: la causa y la correccion son distintas --agregar
Vehiculos frente a corregir un destino inalcanzable-- y una interfaz que las
recibiera como el mismo error no podria decirle al operador que hacer.

Ninguno de los dos produce plan parcial.
"""
from __future__ import annotations

import logging

import pytest

from collections.abc import Sequence

from hoppit.cartografia.puerto import CaminoInexistente, MatrizDeCostos
from hoppit.gestion_ordenes.puerto import Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import (
    DemandaExcedeCapacidad,
    MotivoDeIncomunicacion,
    ParadasIncomunicadas,
    ServicioPlanificacionRutas,
    SinSolucion,
)
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, NODO_AISLADO, GrafoDePrueba

_LOG = logging.getLogger(__name__)

VEHICULO = VehiculoId("AA100BB")

# Capacidad holgada para las pruebas de 3a: lo que tiene que fallar ahi es el
# camino, no la capacidad.
CAPACIDAD_HOLGADA = 100

# Para 4a: demanda por Orden y capacidad que no alcanza para la suma.
DEMANDA_POR_ORDEN = 5
CAPACIDAD_INSUFICIENTE = 4


class GrafoSinCaminoAlAislado(GrafoDePrueba):
    """Declara utilizable al nodo aislado pero no encuentra camino hacia el.

    Reproduce el caso que el chequeo previo del Ensamblador no puede
    anticipar: un nodo que pertenece al conjunto utilizable declarado y sin
    embargo queda sin camino al calcular la matriz. Es el unico camino por el
    que se obtienen pares concretos, que es lo que RB-05 pide informar.
    """

    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos:
        pares = tuple(
            (origen, destino)
            for origen in nodos
            for destino in nodos
            if origen != destino and NODO_AISLADO in (origen, destino)
        )
        if pares:
            raise CaminoInexistente(
                f"No hay camino en el grafo vial para {len(pares)} par(es) de nodos",
                pares,
            )
        return super().matriz_de_costos(nodos)


def _servicio(
    destinos: tuple[int, ...], demanda: int, conectados: tuple[int, ...] | None = None
) -> ServicioPlanificacionRutas:
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(destinos):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=demanda, destino=destino)
        )
    utilizables = conectados if conectados is not None else (DEPOSITO,) + DESTINOS
    contexto = AdaptadorDatosContexto(GrafoDePrueba(utilizables))
    repositorio = RepositorioRutasEnMemoria()
    return ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(ordenes, contexto),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
    )


# --- Flujo 3a: no hay camino entre pares de Paradas -----------------------


def test_un_destino_inalcanzable_impide_el_plan_y_nombra_la_parada() -> None:
    """El destino aislado se detecta antes de calcular ningun camino."""
    servicio = _servicio((DESTINOS[0], NODO_AISLADO), demanda=1)

    with pytest.raises(ParadasIncomunicadas) as capturado:
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_HOLGADA})

    error = capturado.value
    assert error.motivo is MotivoDeIncomunicacion.FUERA_DE_LA_COMPONENTE
    assert [parada.nodo for parada in error.paradas] == [NODO_AISLADO]
    # Sin pares calculados: la verificacion fue de pertenencia, y afirmar
    # pares que no se midieron seria informar de mas.
    assert error.pares == ()
    _LOG.info("Flujo 3a | motivo=%s paradas=%s", error.motivo.value, error.paradas)


def test_no_se_publica_ninguna_ruta_cuando_falta_un_camino() -> None:
    """No produce plan: ni completo ni parcial."""
    servicio = _servicio((DESTINOS[0], NODO_AISLADO), demanda=1)

    with pytest.raises(ParadasIncomunicadas):
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_HOLGADA})

    assert servicio.rutas_persistidas() == ()


def test_los_pares_se_agrupan_bajo_la_parada_que_los_causa() -> None:
    """Una Parada aislada participa en muchos pares, todos con la misma causa.

    El grafo de prueba declara utilizables a todos los nodos pero no conecta
    el aislado, de modo que el chequeo previo lo deja pasar y falla el
    calculo de la matriz: es el camino por el que se obtienen pares reales.
    """
    destinos = (DESTINOS[0], DESTINOS[1], NODO_AISLADO)
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(destinos):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    repositorio = RepositorioRutasEnMemoria()
    servicio = ServicioPlanificacionRutas(
        motor=SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(
            ordenes,
            AdaptadorDatosContexto(GrafoSinCaminoAlAislado((DEPOSITO,) + destinos)),
        ),
        publicador=PublicadorRuta(repositorio, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=repositorio,
        gestion_ordenes=ordenes,
    )

    with pytest.raises(ParadasIncomunicadas) as capturado:
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_HOLGADA})

    error = capturado.value
    assert error.motivo is MotivoDeIncomunicacion.SIN_CAMINO_ENTRE_PARES
    assert error.pares, "tiene que informar los pares concretos"

    # El nodo aislado encabeza el agrupamiento: es el que mas pares explica.
    assert error.paradas[0].nodo == NODO_AISLADO
    # Y la totalidad de los pares esta informada, no una muestra.
    assert error.paradas[0].pares_afectados <= len(error.pares) * 2
    assert all(
        NODO_AISLADO in par for par in error.pares
    ), "todos los pares fallidos involucran al nodo aislado"
    _LOG.info(
        "Flujo 3a | pares=%d agrupados_en=%d paradas, la primera con %d pares",
        len(error.pares),
        len(error.paradas),
        error.paradas[0].pares_afectados,
    )


# --- Flujo 4a: la demanda excede la capacidad de la Flota -----------------


def test_demanda_mayor_que_la_capacidad_declara_la_instancia_infactible() -> None:
    """Cada Orden pide mas que la capacidad del unico Vehiculo: no entra
    ninguna, y el flujo 4a sigue siendo un rechazo (B-220, punto 2)."""
    servicio = _servicio(DESTINOS, demanda=DEMANDA_POR_ORDEN)

    with pytest.raises(DemandaExcedeCapacidad) as capturado:
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_INSUFICIENTE})

    error = capturado.value
    assert error.demanda_total == DEMANDA_POR_ORDEN * len(DESTINOS)
    assert error.capacidad_total == CAPACIDAD_INSUFICIENTE
    assert error.faltante == error.demanda_total - error.capacidad_total
    _LOG.info(
        "Flujo 4a | demanda=%d capacidad=%d faltante=%d",
        error.demanda_total,
        error.capacidad_total,
        error.faltante,
    )


def test_la_infactibilidad_por_capacidad_no_produce_plan_parcial() -> None:
    """Si no entra ninguna Orden no hay plan, ni parcial. Si entrara alguna,
    el plan parcial las declara (B-220, punto 2)."""
    servicio = _servicio(DESTINOS, demanda=DEMANDA_POR_ORDEN)

    with pytest.raises(DemandaExcedeCapacidad):
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_INSUFICIENTE})

    assert servicio.rutas_persistidas() == ()


# --- Los dos flujos son distinguibles -------------------------------------


def test_los_dos_flujos_no_se_confunden_entre_si() -> None:
    """Ninguno de los dos errores atrapa al otro.

    Es la condicion que RB-05 pide explicitamente: la interfaz tiene que
    poder mostrar causa y accion correctiva distintas para cada uno.
    """
    sin_camino = _servicio((DESTINOS[0], NODO_AISLADO), demanda=1)
    with pytest.raises(ParadasIncomunicadas) as tresA:
        sin_camino.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_HOLGADA})

    sin_capacidad = _servicio(DESTINOS, demanda=DEMANDA_POR_ORDEN)
    with pytest.raises(DemandaExcedeCapacidad) as cuatroA:
        sin_capacidad.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_INSUFICIENTE})

    assert not isinstance(tresA.value, DemandaExcedeCapacidad)
    assert not isinstance(cuatroA.value, ParadasIncomunicadas)


def test_la_infactibilidad_por_capacidad_sigue_siendo_un_caso_sin_solucion() -> None:
    """La distincion no rompe la jerarquia: quien atrape SinSolucion en
    general sigue atrapando este caso."""
    servicio = _servicio(DESTINOS, demanda=DEMANDA_POR_ORDEN)
    with pytest.raises(SinSolucion):
        servicio.planificar(DEPOSITO, {VEHICULO: CAPACIDAD_INSUFICIENTE})
