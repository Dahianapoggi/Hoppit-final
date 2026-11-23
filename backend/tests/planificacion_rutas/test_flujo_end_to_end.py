"""Escenario completo del diagrama de proceso, de punta a punta (ARQ-P1).

Recorre los pasos reales y por los puertos reales: alta de Ordenes por la
Fachada de Integracion, planificacion disparada por el Puerto de Operacion,
Ruta calculada por el motor, Ruta persistida, evento publicado en el bus y
Ordenes marcadas como Planificada al consumirlo.

Corre sobre el grafo vial real y sobre el Deposito real del artefacto: es la
prueba de que las piezas encajan con los datos con los que van a correr, no
con dobles.
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO, ensamblar
from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.preparacion_datos.puerto import leer_deposito, seleccionar_deposito
from hoppit.shared.eventos import RutaPublicada
from tools.escenario_demostracion import (
    CANTIDAD_ORDENES,
    CANTIDAD_PARADAS,
    CANTIDAD_VEHICULOS,
    capacidad_por_vehiculo,
    construir_ordenes,
)
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO

_LOG = logging.getLogger(__name__)

DIRECTORIO_INSTANCIA = pathlib.Path("datos/instancia")


@pytest.fixture(scope="module")
def sistema() -> dict[str, object]:
    """El sistema ensamblado por su composition root, sin sustituciones."""
    return ensamblar(GRAFO_PREDETERMINADO)


@pytest.fixture(scope="module")
def corrida(sistema: dict[str, object]) -> dict[str, object]:
    """Ejecuta el escenario una vez y deja lo necesario para inspeccionarlo."""
    cartografia = sistema["cartografia"]
    ordenes = sistema["gestion_ordenes"]
    fachada = sistema["fachada"]
    operacion = sistema["operacion"]
    bus = sistema["bus"]

    publicados: list[RutaPublicada] = []
    bus.suscribirse("ruta_publicada", publicados.append)  # type: ignore[arg-type]

    deposito = leer_deposito(
        seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    )
    generadas = construir_ordenes(cartografia.nodos_de_mayor_componente_fuerte())

    # Pasos 1-2: las Ordenes entran por la Fachada de Integracion, no por el
    # servicio de dominio -- es el camino que recorreria un cliente real.
    for orden in generadas:
        fachada.post_ordenes(orden)

    capacidades = capacidad_por_vehiculo(generadas)
    # Pasos 5-6: el disparador llega por el Puerto de Operacion.
    solucion = operacion.iniciar_planificacion(deposito, capacidades)

    _LOG.info(
        "Flujo end-to-end | deposito=%d ordenes=%d vehiculos=%d rutas=%s "
        "distancia_total_m=%.1f",
        deposito,
        len(generadas),
        len(capacidades),
        [str(ruta.id) for ruta in solucion.rutas],
        solucion.distancia_total_m,
    )
    return {
        "solucion": solucion,
        "generadas": generadas,
        "capacidades": capacidades,
        "publicados": publicados,
        "ordenes": ordenes,
        "planificacion": sistema["planificacion_rutas"],
    }


def test_el_alta_de_ordenes_las_deja_planificables(corrida: dict[str, object]) -> None:
    """Pasos 1-2 y 9: toda Orden nace Creada y por eso es planificable."""
    generadas = corrida["generadas"]
    ordenes = corrida["ordenes"]
    assert len(generadas) == CANTIDAD_ORDENES
    for orden in generadas:
        assert ordenes.consultar(orden.id) is not None


def test_la_planificacion_produce_una_ruta_por_vehiculo(
    corrida: dict[str, object],
) -> None:
    """Pasos 14-15: cada Vehiculo recibe su Ruta, con identidad propia."""
    solucion = corrida["solucion"]
    capacidades = corrida["capacidades"]

    assert len(solucion.rutas) == CANTIDAD_VEHICULOS
    assert sorted(r.vehiculo_id for r in solucion.rutas) == sorted(capacidades)
    assert len({r.id for r in solucion.rutas}) == CANTIDAD_VEHICULOS


def test_las_rutas_cubren_todas_las_ordenes_sin_exceder_capacidad(
    corrida: dict[str, object],
) -> None:
    """Cada Orden queda en exactamente una Ruta, y ninguna Ruta lleva mas
    bultos que la capacidad de su Vehiculo."""
    solucion = corrida["solucion"]
    generadas = corrida["generadas"]
    capacidades = corrida["capacidades"]

    asignadas = [
        orden
        for ruta in solucion.rutas
        for parada in ruta.paradas
        for orden in parada.ordenes
    ]
    assert sorted(asignadas) == sorted(orden.id for orden in generadas)
    for ruta in solucion.rutas:
        assert ruta.carga <= capacidades[ruta.vehiculo_id]


def test_cada_ruta_queda_persistida_y_se_puede_consultar(
    corrida: dict[str, object],
) -> None:
    """Paso 16: el almacen es propio del container y cada Ruta se recupera por
    su identificador, que es el de una Ruta y no el de la corrida."""
    solucion = corrida["solucion"]
    planificacion = corrida["planificacion"]

    for ruta in solucion.rutas:
        persistida = planificacion.ruta_persistida(ruta.id)
        assert persistida.ruta == ruta
        assert persistida.prioridad_contingencia == 0


def test_se_publica_un_evento_por_ruta(corrida: dict[str, object]) -> None:
    """Paso 17: un RutaPublicada por Ruta. El evento nombra una sola Ruta y su
    ruta_id tiene que resolver contra el repositorio; con N Rutas por corrida,
    un unico evento no tendria que poner ahi."""
    publicados = corrida["publicados"]
    solucion = corrida["solucion"]
    planificacion = corrida["planificacion"]

    assert len(publicados) == len(solucion.rutas)
    assert all(evento.tipo == "ruta_publicada" for evento in publicados)
    assert sorted(evento.ruta_id for evento in publicados) == sorted(
        str(ruta.id) for ruta in solucion.rutas
    )
    for evento in publicados:
        assert planificacion.ruta_persistida(evento.ruta_id) is not None


def test_entre_los_eventos_se_nombran_todas_las_ordenes_una_sola_vez(
    corrida: dict[str, object],
) -> None:
    """La consecuencia que le importa a Gestion de Ordenes (ARQ-31): repartir
    el evento en N no puede dejar Ordenes afuera ni nombrarlas dos veces."""
    publicados = corrida["publicados"]
    generadas = corrida["generadas"]

    nombradas = [
        id_orden for evento in publicados for id_orden in evento.ordenes_incluidas
    ]
    assert sorted(nombradas) == sorted(orden.id for orden in generadas)


def test_las_ordenes_quedan_planificadas_al_consumir_el_evento(
    corrida: dict[str, object],
) -> None:
    """Paso 18 (FR-03): Gestion de Ordenes es el unico que escribe el estado,
    y lo hace al consumir el evento del bus (ARQ-31), no porque alguien se lo
    pida directamente."""
    ordenes = corrida["ordenes"]
    for orden in corrida["generadas"]:
        actual = ordenes.consultar(orden.id)
        assert actual.estado is EstadoOrden.PLANIFICADA
        assert actual.historial[-1].estado_anterior is EstadoOrden.CREADA
        assert actual.historial[-1].estado_nuevo is EstadoOrden.PLANIFICADA


def test_ya_no_quedan_ordenes_planificables(corrida: dict[str, object]) -> None:
    """Consecuencia del paso 18: una segunda planificacion no volveria a
    tomar las mismas Ordenes."""
    ordenes = corrida["ordenes"]
    assert list(ordenes.ordenes_planificables()) == []


def test_ensamblar_instancia_no_muta_nada(sistema: dict[str, object]) -> None:
    """Es la operacion que usa la medicion: si publicara o transicionara,
    medir cambiaria el estado del sistema. Se ejecuta sobre un sistema propio
    para no interferir con la corrida del escenario completo."""
    propio = ensamblar(GRAFO_PREDETERMINADO)
    cartografia = propio["cartografia"]
    ordenes = propio["gestion_ordenes"]
    planificacion = propio["planificacion_rutas"]

    publicados: list[RutaPublicada] = []
    propio["bus"].suscribirse("ruta_publicada", publicados.append)  # type: ignore[arg-type]

    deposito = leer_deposito(
        seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    )
    generadas = construir_ordenes(cartografia.nodos_de_mayor_componente_fuerte())
    for orden in generadas:
        ordenes.dar_de_alta(orden)

    instancia = planificacion.ensamblar_instancia(
        deposito, capacidad_por_vehiculo(generadas)
    )

    # Ordenes y Paradas ya no coinciden: dos Paradas agrupan dos Ordenes
    # cada una (RB-04).
    assert len(instancia.ordenes) == CANTIDAD_ORDENES
    assert len(instancia.paradas) == CANTIDAD_PARADAS
    assert publicados == []
    assert len(list(ordenes.ordenes_planificables())) == CANTIDAD_ORDENES
    for orden in generadas:
        assert ordenes.consultar(orden.id).estado is EstadoOrden.CREADA
