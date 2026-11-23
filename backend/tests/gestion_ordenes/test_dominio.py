"""Verifica el ciclo de vida de la Orden -- unico contrato de FR-03."""
from __future__ import annotations

import pytest

from hoppit.gestion_ordenes._dominio import DemandaInvalida, EstadoOrden, Orden
from hoppit.shared.errores import TransicionInvalida
from hoppit.shared.ids import OrdenId


# Nodo real del grafo de San Isidro (el Deposito de datos/instancia).
# Estas pruebas no consultan el grafo, pero el proyecto no admite
# ubicaciones inventadas ni siquiera como dato de prueba.
NODO_DESTINO = 1855553614


def test_creada_a_planificada_es_valida() -> None:
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO)
    orden.transicionar(EstadoOrden.PLANIFICADA)
    assert orden.estado is EstadoOrden.PLANIFICADA
    # Dos asientos: el inicial, que la Orden trae desde que nace (RB-06), y
    # el de esta transicion.
    assert len(orden.historial) == 2
    assert orden.historial[-1].estado_anterior is EstadoOrden.CREADA


def test_creada_a_en_transito_es_invalida() -> None:
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO)
    with pytest.raises(TransicionInvalida):
        orden.transicionar(EstadoOrden.EN_TRANSITO)


def test_solo_creada_es_planificable() -> None:
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO)
    assert orden.es_planificable()
    orden.transicionar(EstadoOrden.PLANIFICADA)
    assert not orden.es_planificable()


def test_cancelada_es_terminal() -> None:
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO)
    orden.transicionar(EstadoOrden.CANCELADA)
    with pytest.raises(TransicionInvalida):
        orden.transicionar(EstadoOrden.PLANIFICADA)


def test_la_demanda_es_un_entero_de_bultos() -> None:
    """B-55: ni peso ni volumen, y no fraccionable."""
    orden = Orden(id=OrdenId("o1"), demanda=3, destino=NODO_DESTINO)
    assert orden.demanda == 3
    assert isinstance(orden.demanda, int)


def test_una_demanda_fraccionaria_se_rechaza() -> None:
    with pytest.raises(DemandaInvalida):
        Orden(id=OrdenId("o1"), demanda=2.5, destino=NODO_DESTINO)  # type: ignore[arg-type]


def test_una_demanda_nula_o_negativa_se_rechaza() -> None:
    """Una Orden sin bultos ocuparia una Parada sin consumir capacidad."""
    for invalida in (0, -1):
        with pytest.raises(DemandaInvalida):
            Orden(id=OrdenId("o1"), demanda=invalida, destino=NODO_DESTINO)


def test_la_transicion_toma_la_hora_de_la_misma_fuente_que_el_alta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un solo reloj para todo el historial: fijarlo fija el asiento inicial
    y el de cada transicion, no solo el primero."""
    import datetime as dt

    from hoppit.gestion_ordenes import _dominio

    fijo = dt.datetime(2026, 9, 30, 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(_dominio, "instante_actual", lambda: fijo)

    orden = Orden(id=OrdenId("o-reloj"), demanda=1, destino=NODO_DESTINO)
    orden.transicionar(EstadoOrden.PLANIFICADA)

    assert [asiento.marca_temporal for asiento in orden.historial] == [fijo, fijo]
