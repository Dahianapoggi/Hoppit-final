"""La Orden informa a que Ruta y a que Parada fue asignada (RB-09, CU-04).

La relacion inversa --la Parada sabe que Ordenes entrega-- ya existia. Estas
pruebas cubren la directa, y sobre todo que las dos no puedan discrepar: la
asignacion se establece en el mismo acto que la transicion a Planificada,
porque el glosario define ese estado como que la Orden quedo asignada a una
Parada dentro de una Ruta.
"""
from __future__ import annotations

import pytest

from hoppit.gestion_ordenes._dominio import PRIMERA_SECUENCIA_DE_PARADA
from hoppit.gestion_ordenes.puerto import (
    AsignacionInvalida,
    EstadoOrden,
    Orden,
)
from hoppit.shared.errores import TransicionInvalida
from hoppit.shared.ids import OrdenId, RutaId

# Nodo real del grafo de San Isidro: el proyecto no admite ubicaciones
# inventadas ni como dato de prueba.
NODO_DESTINO = 1855553614

RUTA = RutaId("ruta-abc123")
SECUENCIA = 4


def _orden(id_orden: str = "o1") -> Orden:
    return Orden(id=OrdenId(id_orden), demanda=1, destino=NODO_DESTINO)


def test_una_orden_recien_creada_no_informa_ruta_ni_parada() -> None:
    """Ausencia declarada, no cero: todavia no fue planificada."""
    orden = _orden()
    assert orden.ruta_id is None
    assert orden.secuencia_parada is None
    assert not orden.esta_asignada


def test_una_orden_recien_creada_ya_tiene_su_asiento_inicial() -> None:
    """RB-06, CU-01 paso 6: el historial contiene la transicion inicial.

    `estado_anterior` es None porque no hay estado previo: la Orden no venia
    de ningun lado. Es distinto de un estado desconocido.
    """
    orden = _orden()

    assert len(orden.historial) == 1
    inicial = orden.historial[0]
    assert inicial.estado_anterior is None
    assert inicial.estado_nuevo is EstadoOrden.CREADA
    assert inicial.marca_temporal.tzinfo is not None


def test_al_asignarse_queda_planificada_y_sabe_donde() -> None:
    orden = _orden()
    orden.asignar_a_ruta(RUTA, SECUENCIA)

    assert orden.estado is EstadoOrden.PLANIFICADA
    assert orden.ruta_id == RUTA
    assert orden.secuencia_parada == SECUENCIA
    assert orden.esta_asignada


def test_la_asignacion_queda_en_el_historial_como_cualquier_transicion() -> None:
    """FR-03 exige el historial completo, y esta transicion no es excepcion."""
    orden = _orden()
    orden.asignar_a_ruta(RUTA, SECUENCIA)

    assert len(orden.historial) == 2
    asiento = orden.historial[-1]
    assert asiento.estado_anterior is EstadoOrden.CREADA
    assert asiento.estado_nuevo is EstadoOrden.PLANIFICADA


def test_una_secuencia_por_debajo_de_uno_se_rechaza() -> None:
    """La numeracion de Paradas arranca en 1 en cada Ruta (ARQ-05)."""
    orden = _orden()
    with pytest.raises(AsignacionInvalida):
        orden.asignar_a_ruta(RUTA, PRIMERA_SECUENCIA_DE_PARADA - 1)

    # Y la Orden no quedo a medias: sigue Creada y sin asignar.
    assert orden.estado is EstadoOrden.CREADA
    assert not orden.esta_asignada


def test_no_se_puede_asignar_una_orden_ya_planificada() -> None:
    """La regla sigue siendo la maquina de estados, no una excepcion nueva.

    Reasignar una Orden a otra Ruta exige primero devolverla a Creada, que es
    el inverso de FR-03 y no esta ejercitado en fase 1.
    """
    orden = _orden()
    orden.asignar_a_ruta(RUTA, SECUENCIA)
    with pytest.raises(TransicionInvalida):
        orden.asignar_a_ruta(RutaId("ruta-otra"), 1)

    assert orden.ruta_id == RUTA
    assert orden.secuencia_parada == SECUENCIA
