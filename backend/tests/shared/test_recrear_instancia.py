"""Recrear la instancia de demostracion desde cero.

Es el punto de partida reproducible: base limpia, las Ordenes de la instancia
vigente en estado Creada, sin planificar. Despues de correrlo, una
planificacion da siempre el mismo plan.

Se distingue de descartar_planes, que conserva el historial y las Ordenes
dadas de alta durante una demostracion. Este borra la base y la rehace, que
es lo que hace falta cuando el esquema cambio.
"""
from __future__ import annotations

import contextlib
import logging
import pathlib
import sqlite3
import sys

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import (
    GrafoVialAMBA,
    crear_servicio as crear_cartografia,
)
from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.recrear_instancia import recrear
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    DIRECTORIO_INSTANCIA,
    GRAFO_SAN_ISIDRO,
    GRAFOS_DEL_PRODUCTO,
    construir_contexto,
)

ORDENES_DE_LA_INSTANCIA = 20
PARADAS_DE_LA_INSTANCIA = 18
DEMANDA_TOTAL = 32


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "demostracion.sqlite3"


def _recrear(grafo: GrafoVialAMBA, base: pathlib.Path) -> int:
    """Corre el comando reutilizando el grafo ya cargado."""
    import tools.servir_api as arranque

    original = arranque.construir_contexto
    try:
        arranque.construir_contexto = lambda *a, **k: original(  # type: ignore[assignment]
            *a, **{**k, "cartografia": grafo}
        )
        import tools.recrear_instancia as comando

        comando.construir_contexto = arranque.construir_contexto  # type: ignore[assignment]
        # San Isidro, explicito: es el grafo de la instancia canonica
        # (2026-10-27B, K; la omision del comando es CABA).
        return recrear(base, grafo=GRAFOS_DEL_PRODUCTO[GRAFO_SAN_ISIDRO])
    finally:
        arranque.construir_contexto = original  # type: ignore[assignment]
        comando.construir_contexto = original  # type: ignore[assignment]


def _armar(grafo: GrafoVialAMBA, base: pathlib.Path):
    # La instancia canonica con el margen de la operacion, la referencia
    # desde la sesion 2026-10-15A (B-227, punto 4); antes, margen 0.
    return construir_contexto(
        base_de_datos=base,
        cartografia=grafo,
        margen_de_capacidad=MARGEN_DE_LA_OPERACION,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


def test_desde_base_inexistente_deja_la_instancia_lista(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """El caso que motiva el comando: no hay nada y hay que partir de cero."""
    assert not base.exists()

    creadas = _recrear(grafo, base)

    assert creadas == ORDENES_DE_LA_INSTANCIA
    assert base.exists()
    contexto = _armar(grafo, base)
    assert len(contexto.ordenes.listar(EstadoOrden.CREADA)) == ORDENES_DE_LA_INSTANCIA
    assert contexto.planificacion.rutas_persistidas() == ()


def test_la_instancia_recreada_es_la_vigente(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """Las mismas 20 Ordenes sobre 18 Paradas y 32 bultos de demanda."""
    _recrear(grafo, base)

    ordenes = _armar(grafo, base).ordenes.listar()
    assert len(ordenes) == ORDENES_DE_LA_INSTANCIA
    assert len({orden.destino for orden in ordenes}) == PARADAS_DE_LA_INSTANCIA
    assert sum(orden.demanda for orden in ordenes) == DEMANDA_TOTAL


def test_borra_lo_que_hubiera_antes(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """Rehace, no acumula: las Rutas y las Ordenes agregadas no sobreviven."""
    contexto = _armar(grafo, base)
    contexto.planificacion.planificar(contexto.deposito, contexto.flota)
    agregada = contexto.ordenes.registrar(1, contexto.ordenes.listar()[0].destino)
    del contexto

    _recrear(grafo, base)

    contexto = _armar(grafo, base)
    assert len(contexto.ordenes.listar()) == ORDENES_DE_LA_INSTANCIA
    assert agregada.id not in {orden.id for orden in contexto.ordenes.listar()}
    assert contexto.planificacion.rutas_persistidas() == ()


def test_funciona_sobre_una_base_de_esquema_anterior(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """El caso concreto que lo hizo urgente.

    Una base creada por una version anterior no se puede consultar con el
    esquema de hoy. El comando no se detiene por no poder leerla: la rehace,
    que es exactamente lo que hay que hacer con ella.
    """
    # Cerrada de forma explicita: la prueba es quien la abre, y una conexion
    # que sigue viva impide en Windows que el comando borre el archivo.
    with contextlib.closing(sqlite3.connect(base)) as conexion:
        conexion.executescript(
            "CREATE TABLE ordenes (id TEXT PRIMARY KEY, demanda INTEGER);"
        )

    assert _recrear(grafo, base) == ORDENES_DE_LA_INSTANCIA
    assert (
        len(_armar(grafo, base).ordenes.listar(EstadoOrden.CREADA))
        == ORDENES_DE_LA_INSTANCIA
    )


def test_es_deterministico(grafo: GrafoVialAMBA, tmp_path: pathlib.Path) -> None:
    """Misma semilla, misma instancia: destinos y demandas (NFR-07)."""
    una, otra = tmp_path / "una.sqlite3", tmp_path / "otra.sqlite3"
    _recrear(grafo, una)
    _recrear(grafo, otra)

    def descripcion(base: pathlib.Path) -> list[tuple[str, int, int]]:
        return [
            (str(o.id), o.destino, o.demanda) for o in _armar(grafo, base).ordenes.listar()
        ]

    assert descripcion(una) == descripcion(otra)


def test_tras_recrear_el_plan_es_el_de_siempre(
    grafo: GrafoVialAMBA, base: pathlib.Path
) -> None:
    """Es para lo que existe: volver al punto de partida conocido y que la
    planificacion de el mismo resultado."""
    _recrear(grafo, base)
    contexto = _armar(grafo, base)

    solucion = contexto.planificacion.planificar(contexto.deposito, contexto.flota)

    assert len(solucion.rutas) == 3
    assert sorted(len(r.paradas) for r in solucion.rutas) == [4, 6, 8]
    assert solucion.distancia_total_m == pytest.approx(41445.1, abs=0.1)


def test_es_idempotente(grafo: GrafoVialAMBA, base: pathlib.Path) -> None:
    """Correrlo dos veces deja el mismo estado que correrlo una."""
    _recrear(grafo, base)
    _recrear(grafo, base)

    contexto = _armar(grafo, base)
    assert len(contexto.ordenes.listar()) == ORDENES_DE_LA_INSTANCIA
    assert contexto.planificacion.rutas_persistidas() == ()


def test_la_semilla_y_la_base_son_parametros(
    grafo: GrafoVialAMBA, tmp_path: pathlib.Path
) -> None:
    """Ninguna ruta ni semilla esta fija en el comando."""
    from tools.recrear_instancia import parsear_argumentos

    args = parsear_argumentos([])
    assert args.semilla == SEMILLA_DE_INSTANCIA
    assert args.directorio_instancia == DIRECTORIO_INSTANCIA

    otra = tmp_path / "otra.sqlite3"
    args = parsear_argumentos(["--base-de-datos", str(otra), "--semilla", "1"])
    assert args.base_de_datos == otra
    assert args.semilla == 1


# --- Base en uso (2026-10-20A, Z.5; 2026-10-20B, F1.6) ---------------------


@pytest.mark.skipif(sys.platform != "win32", reason="el bloqueo de un archivo abierto es de Windows")
def test_con_la_base_abierta_por_otra_conexion_no_borra_nada_y_lo_dice(
    grafo: GrafoVialAMBA, base: pathlib.Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Es el caso del servidor levantado: otra conexion tiene la base abierta.
    El comando termina con un codigo distinto de 0, el mensaje dice que hacer
    y la base queda como estaba."""
    import tools.recrear_instancia as comando

    _recrear(grafo, base)
    with contextlib.closing(sqlite3.connect(base)) as abierta:
        abierta.execute("INSERT INTO usuarios VALUES ('u-1', 'quien', 'Quien', 'operador', 'x', 1, '2026-10-20T00:00:00+00:00', 0, NULL)")
        abierta.commit()
        antes = abierta.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
        with caplog.at_level(logging.ERROR):
            codigo = comando.main(["--base-de-datos", str(base), "--grafo", GRAFO_SAN_ISIDRO])
        assert codigo == comando.CODIGO_BASE_EN_USO
        assert comando.MENSAJE_BASE_EN_USO in caplog.text
        assert "detené el servidor" in caplog.text
    with contextlib.closing(sqlite3.connect(base)) as despues:
        assert despues.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0] == antes
        assert despues.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 1
    assert not any(p.name.endswith(".prueba-de-uso") for p in base.parent.iterdir())


@pytest.mark.skipif(sys.platform != "win32", reason="el bloqueo de un archivo abierto es de Windows")
def test_la_verificacion_de_uso_falla_con_una_conexion_abierta(base: pathlib.Path) -> None:
    from tools.recrear_instancia import BaseEnUso, verificar_que_no_este_en_uso

    with contextlib.closing(sqlite3.connect(base)) as abierta:
        abierta.execute("CREATE TABLE t (x INTEGER)")
        abierta.commit()
        with pytest.raises(BaseEnUso):
            verificar_que_no_este_en_uso(base)
    verificar_que_no_este_en_uso(base)
    assert base.exists()

