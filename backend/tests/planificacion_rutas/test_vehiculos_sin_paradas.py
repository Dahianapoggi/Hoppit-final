"""Un Vehiculo sin Paradas no produce Ruta (FE-13, parte de backend).

El glosario define la Ruta como la secuencia ordenada de Paradas que parte
del Deposito y retorna a el. Una Ruta sin Paradas no recorre nada: no hay
secuencia que ordenar. Emitirla igual dejaba una Ruta de distancia y carga
cero, con trazado vacio, y publicaba por el bus un evento sin Ordenes que
transicionar.

Una corrida produce una Ruta por Vehiculo UTILIZADO, no por Vehiculo de la
Flota.
"""
from __future__ import annotations

import logging

import pytest

from hoppit.gestion_ordenes.puerto import Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import EnsambladorInstancia
from hoppit.planificacion_rutas._publicador import PublicadorRuta
from hoppit.planificacion_rutas._receptor import ReceptorSolicitudes
from hoppit.planificacion_rutas._repositorio import RepositorioRutasEnMemoria
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.puerto import ServicioPlanificacionRutas
from hoppit.shared.bus import Bus
from hoppit.shared.eventos import RutaPublicada
from hoppit.shared.ids import OrdenId, VehiculoId

from .test_ensamblador_instancia import DEPOSITO, DESTINOS, GrafoDePrueba

_LOG = logging.getLogger(__name__)

# Una sola Orden y una Flota de cinco Vehiculos con capacidad de sobra: el
# solver solo puede usar uno. Es la forma de forzar el caso.
UNA_ORDEN = OrdenId("orden-unica")
CAPACIDAD_HOLGADA = 50
VEHICULOS_DE_LA_FLOTA = 5

# Matriculas de una Flota inventada para esta prueba, en el mismo formato
# Mercosur que las reales. No son las de la instancia de demostracion a
# proposito: lo que se verifica aca es una regla del motor sobre una Flota
# cualquiera, no el escenario que se expone.
MATRICULAS_SINTETICAS = (
    VehiculoId("AA100BB"),
    VehiculoId("AA200CC"),
    VehiculoId("AA300DD"),
    VehiculoId("AA400EE"),
    VehiculoId("AA500FF"),
)


def _sistema(
    cuantas_ordenes: int, cuantos_vehiculos: int
) -> tuple[ServicioPlanificacionRutas, list[RutaPublicada], dict[VehiculoId, int]]:
    bus = Bus()
    publicados: list[RutaPublicada] = []
    bus.suscribirse("ruta_publicada", publicados.append)  # type: ignore[arg-type]

    ordenes = crear_ordenes(bus)
    for posicion in range(cuantas_ordenes):
        ordenes.dar_de_alta(
            Orden(
                id=OrdenId(f"orden-{posicion}"),
                demanda=1,
                destino=DESTINOS[posicion % len(DESTINOS)],
            )
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
    )
    flota = {
        MATRICULAS_SINTETICAS[n]: CAPACIDAD_HOLGADA
        for n in range(cuantos_vehiculos)
    }
    return servicio, publicados, flota


def test_con_mas_vehiculos_que_los_necesarios_hay_menos_rutas_que_vehiculos() -> None:
    """El caso que el defecto producia: Rutas vacias, una por Vehiculo ocioso."""
    servicio, _, flota = _sistema(cuantas_ordenes=1, cuantos_vehiculos=VEHICULOS_DE_LA_FLOTA)

    solucion = servicio.planificar(DEPOSITO, flota)

    assert len(solucion.rutas) < len(flota)
    _LOG.info(
        "Flota de %d Vehiculos, 1 Orden | rutas emitidas=%d",
        len(flota),
        len(solucion.rutas),
    )


def test_ninguna_ruta_emitida_esta_vacia() -> None:
    """Toda Ruta publicada recorre algo: Paradas, distancia y carga."""
    servicio, _, flota = _sistema(cuantas_ordenes=2, cuantos_vehiculos=VEHICULOS_DE_LA_FLOTA)

    solucion = servicio.planificar(DEPOSITO, flota)

    for ruta in solucion.rutas:
        assert ruta.paradas
        assert ruta.distancia_m > 0
        assert ruta.carga > 0
        assert ruta.ordenes


def test_no_se_publica_un_evento_sin_ordenes() -> None:
    """Un evento sin Ordenes que transicionar es un mensaje sin significado
    circulando por el bus."""
    servicio, publicados, flota = _sistema(
        cuantas_ordenes=1, cuantos_vehiculos=VEHICULOS_DE_LA_FLOTA
    )

    solucion = servicio.planificar(DEPOSITO, flota)

    assert len(publicados) == len(solucion.rutas)
    for evento in publicados:
        assert evento.asignaciones
        assert evento.ordenes_incluidas


def test_no_se_persiste_ninguna_ruta_vacia() -> None:
    """Lo que la interfaz lee: el repositorio no guarda Rutas sin Paradas."""
    servicio, _, flota = _sistema(cuantas_ordenes=1, cuantos_vehiculos=VEHICULOS_DE_LA_FLOTA)

    servicio.planificar(DEPOSITO, flota)

    persistidas = servicio.rutas_persistidas()
    assert persistidas
    assert all(p.ruta.paradas for p in persistidas)


def test_toda_orden_sigue_quedando_en_alguna_ruta() -> None:
    """Descartar Vehiculos ociosos no puede dejar Ordenes sin planificar."""
    servicio, _, flota = _sistema(cuantas_ordenes=3, cuantos_vehiculos=VEHICULOS_DE_LA_FLOTA)

    solucion = servicio.planificar(DEPOSITO, flota)

    assert set(solucion.ordenes) == {OrdenId(f"orden-{n}") for n in range(3)}


def test_con_la_flota_justa_todos_los_vehiculos_producen_ruta() -> None:
    """El cambio no descarta Vehiculos que si trabajan.

    Con capacidad ajustada para que ninguno pueda llevar todo, los tres
    reciben Paradas y los tres producen Ruta.
    """
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
    )
    # Capacidad 2: cada Vehiculo entra exactamente una Orden.
    flota = {MATRICULAS_SINTETICAS[n]: 2 for n in range(len(DESTINOS))}

    solucion = servicio.planificar(DEPOSITO, flota)

    assert len(solucion.rutas) == len(flota)
