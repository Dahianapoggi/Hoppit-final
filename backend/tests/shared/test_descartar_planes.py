"""El guion que deja el sistema listo para volver a planificar.

Existe para que una demostracion pueda repetirse. Lo que se verifica es que
componga la misma operacion que expone la API --y no una via paralela que
podria divergir--, que no toque el disco mas alla de la base, y que despues
de correrlo replanificar de verdad funcione.

El grafo se carga una sola vez para todo el modulo y se comparte: es lo
unico caro del armado y es identico en cada uno.
"""
from __future__ import annotations

import pathlib

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import (
    GrafoVialAMBA,
    crear_servicio as crear_cartografia,
)
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.gestion_ordenes.puerto import EstadoOrden
from tools.descartar_planes import descartar
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

RUTAS_ESPERADAS = 3

# Una corrida produce un unico Plan de Distribucion, con sus tres Rutas.
PLANES_ESPERADOS = 1
ORDENES_DE_LA_INSTANCIA = 20

# Nodo real de la componente fuerte mayor, distinto del Deposito.
OTRO_NODO = 299362038


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def _armar(grafo: GrafoVialAMBA, base: pathlib.Path) -> ContextoDeLaApi:
    return construir_contexto(
        base_de_datos=base,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


def _planificar(grafo: GrafoVialAMBA, base: pathlib.Path) -> ContextoDeLaApi:
    contexto = _armar(grafo, base)
    contexto.planificacion.planificar_plan(contexto.deposito, contexto.flota)
    return contexto


@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    """Base temporal: el guion nunca corre contra la de desarrollo."""
    return tmp_path / "demostracion.sqlite3"


def test_descarta_todos_los_planes_vigentes(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """La unidad de descarte es el Plan, no la Ruta.

    Una corrida produce un Plan con sus tres Rutas: descartar ese Plan las
    elimina todas y ademas libera sus Vehiculos para la jornada.
    """
    _planificar(grafo, base)

    descartados = descartar(base, cartografia=grafo)

    assert descartados == PLANES_ESPERADOS
    despues = _armar(grafo, base).planificacion
    assert despues.rutas_persistidas() == ()
    assert despues.planes() == ()


def test_devuelve_las_ordenes_a_creada(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """Es lo que las vuelve planificables: sin esto, descartar las Rutas
    dejaria las Ordenes trabadas en Planificada."""
    _planificar(grafo, base)

    descartar(base, cartografia=grafo)

    planificables = _armar(grafo, base).ordenes.listar(EstadoOrden.CREADA)
    assert len(planificables) == ORDENES_DE_LA_INSTANCIA
    assert all(not orden.esta_asignada for orden in planificables)


def test_tras_descartar_se_replanifica_con_el_mismo_resultado(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """El objetivo del guion, verificado de punta a punta (NFR-07)."""
    antes = _planificar(grafo, base).planificacion.rutas_persistidas()
    esperado = {p.ruta.id: p.ruta.distancia_m for p in antes}

    descartar(base, cartografia=grafo)

    contexto = _armar(grafo, base)
    solucion = contexto.planificacion.planificar(contexto.deposito, contexto.flota)
    assert {r.id: r.distancia_m for r in solucion.rutas} == pytest.approx(esperado)


def test_correrlo_sin_planes_no_es_un_error(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """Un sistema ya listo es un estado legitimo, no una condicion de fallo.

    Importa porque el guion se corre antes de una demostracion sin saber en
    que estado quedo la anterior.
    """
    _armar(grafo, base)

    assert descartar(base, cartografia=grafo) == 0


def test_es_idempotente(grafo: GrafoVialAMBA, base: pathlib.Path) -> None:
    """Correrlo dos veces deja el mismo estado que correrlo una."""
    _planificar(grafo, base)

    descartar(base, cartografia=grafo)
    descartar(base, cartografia=grafo)

    contexto = _armar(grafo, base)
    assert contexto.planificacion.rutas_persistidas() == ()
    assert len(contexto.ordenes.listar(EstadoOrden.CREADA)) == ORDENES_DE_LA_INSTANCIA


def test_conserva_el_historial_y_las_ordenes_dadas_de_alta(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """La diferencia con borrar el archivo, que es la razon de que exista.

    Borrar la base perderia el historial de transiciones y toda Orden dada de
    alta durante la demostracion. Descartar los planes conserva las dos
    cosas.
    """
    contexto = _planificar(grafo, base)
    agregada = contexto.ordenes.registrar(2, OTRO_NODO)
    del contexto

    descartar(base, cartografia=grafo)

    contexto = _armar(grafo, base)
    assert contexto.ordenes.consultar(agregada.id).demanda == 2
    historial = contexto.ordenes.consultar(contexto.ordenes.listar()[0].id).historial
    assert [(t.estado_anterior, t.estado_nuevo) for t in historial] == [
        (None, EstadoOrden.CREADA),
        (EstadoOrden.CREADA, EstadoOrden.PLANIFICADA),
        (EstadoOrden.PLANIFICADA, EstadoOrden.CREADA),
    ]


def test_la_base_es_parametro_y_no_toca_otras(
    grafo: GrafoVialAMBA, tmp_path: pathlib.Path
) -> None:
    """Descartar en una base no altera otra."""
    una = tmp_path / "una.sqlite3"
    otra = tmp_path / "otra.sqlite3"
    _planificar(grafo, una)
    _planificar(grafo, otra)

    descartar(una, cartografia=grafo)

    assert _armar(grafo, una).planificacion.rutas_persistidas() == ()
    assert _armar(grafo, otra).planificacion.rutas_persistidas()
