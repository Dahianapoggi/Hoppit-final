"""La persistencia sobrevive al reinicio del proceso (SQLite, sin ORM).

El riesgo concreto que esto elimina: hasta ahora todo vivia en memoria, de
modo que un reinicio entre el alta y la planificacion dejaba el sistema
vacio. Estas pruebas no verifican que las consultas devuelvan algo --eso lo
haria igual un diccionario-- sino que lo guardado siga estando **despues de
descartar todos los objetos y volver a armar el sistema**, que es lo unico
que distingue persistencia de memoria.

Cada prueba corre contra una base temporal propia. Ninguna toca la base de
desarrollo.
"""
from __future__ import annotations

import ast
import datetime as dt
import logging
import pathlib
import re
import sqlite3

import pytest

from hoppit.gestion_ordenes.puerto import (
    EstadoOrden,
    Orden,
    RepositorioOrdenesSQLite,
    crear_servicio as crear_ordenes,
)
from hoppit.planificacion_rutas.puerto import (
    RepositorioRutasSQLite,
    crear_servicio as crear_planificacion,
)
from hoppit.shared.almacen import BASE_EN_MEMORIA, conectar
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId

from tests.planificacion_rutas.test_ensamblador_instancia import (
    DEPOSITO,
    DESTINOS,
    GrafoDePrueba,
)

_LOG = logging.getLogger(__name__)

CAPACIDAD = 10
VEHICULO = VehiculoId("AA100BB")
DIA = dt.date(2026, 8, 23)

# Tablas de cada componente. Se enumeran para poder verificar que la
# propiedad del dato es real y no una intencion escrita en un comentario.
TABLAS_DE_ORDENES = {"ordenes", "transiciones_de_orden", "reasignaciones_de_orden", "ventanas_de_orden",
                     "modalidades_de_orden", "declaraciones_de_campo_de_orden"}
TABLAS_DE_RUTAS = {
    "rutas",
    "paradas_de_ruta",
    "ordenes_de_parada",
    "planes",
    "vehiculos_de_plan",
    "ordenes_no_planificadas_de_plan",
}


@pytest.fixture()
def base(tmp_path: pathlib.Path) -> pathlib.Path:
    """Una base temporal por prueba, nunca la de desarrollo."""
    return tmp_path / "prueba.sqlite3"


def _sistema(base: pathlib.Path):
    """Arma el sistema completo contra la base indicada.

    Cada llamada abre conexiones nuevas y repositorios nuevos: llamarla dos
    veces es exactamente lo que simula reiniciar el proceso.
    """
    bus = Bus()
    ordenes = crear_ordenes(bus, RepositorioOrdenesSQLite(conectar(base)))
    planificacion = crear_planificacion(
        bus,
        GrafoDePrueba((DEPOSITO,) + DESTINOS),
        ordenes,
        reloj=lambda: DIA,
        repositorio=RepositorioRutasSQLite(conectar(base)),
    )
    return ordenes, planificacion


# --- La Orden ------------------------------------------------------------


def test_una_orden_sobrevive_al_reinicio(base: pathlib.Path) -> None:
    primero, _ = _sistema(base)
    primero.dar_de_alta(Orden(id=OrdenId("o1"), demanda=3, destino=DESTINOS[0]))
    del primero

    segundo, _ = _sistema(base)
    recuperada = segundo.consultar(OrdenId("o1"))
    assert recuperada.demanda == 3
    assert recuperada.destino == DESTINOS[0]
    assert recuperada.estado is EstadoOrden.CREADA


def test_el_historial_de_transiciones_sobrevive(base: pathlib.Path) -> None:
    """FR-03 exige el historial completo, y un historial que se pierde al
    reiniciar no es un registro."""
    primero, _ = _sistema(base)
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=DESTINOS[0])
    primero.dar_de_alta(orden)
    orden.asignar_a_ruta(RutaId("ruta-x"), 2)
    primero.dar_de_alta(orden)
    del primero, orden

    segundo, _ = _sistema(base)
    recuperada = segundo.consultar(OrdenId("o1"))
    assert recuperada.estado is EstadoOrden.PLANIFICADA
    assert recuperada.ruta_id == RutaId("ruta-x")
    assert recuperada.secuencia_parada == 2
    # El asiento inicial y el de la asignacion, en ese orden.
    assert [
        (t.estado_anterior, t.estado_nuevo) for t in recuperada.historial
    ] == [
        (None, EstadoOrden.CREADA),
        (EstadoOrden.CREADA, EstadoOrden.PLANIFICADA),
    ]
    # La marca temporal se recupera con su zona: guardarla sin ella la
    # volveria ambigua.
    assert all(t.marca_temporal.tzinfo is not None for t in recuperada.historial)


def test_guardar_dos_veces_no_duplica_el_historial(base: pathlib.Path) -> None:
    """La Orden se reescribe entera en cada guardado; el historial no se
    acumula sobre si mismo."""
    servicio, _ = _sistema(base)
    orden = Orden(id=OrdenId("o1"), demanda=1, destino=DESTINOS[0])
    orden.asignar_a_ruta(RutaId("ruta-x"), 1)
    servicio.dar_de_alta(orden)
    servicio.dar_de_alta(orden)

    assert len(servicio.consultar(OrdenId("o1")).historial) == 2


def test_una_demanda_corrompida_en_disco_se_rechaza_al_leer(
    base: pathlib.Path,
) -> None:
    """La regla de dominio corre tambien sobre lo que sale de la base.

    Si alguien edito el archivo a mano, el sistema se entera al leer en vez
    de propagar una Orden invalida hacia el solver.
    """
    from hoppit.gestion_ordenes.puerto import DemandaInvalida

    servicio, _ = _sistema(base)
    servicio.dar_de_alta(Orden(id=OrdenId("o1"), demanda=1, destino=DESTINOS[0]))
    with conectar(base) as conexion:
        conexion.execute("UPDATE ordenes SET demanda = 0 WHERE id = 'o1'")

    otro, _ = _sistema(base)
    with pytest.raises(DemandaInvalida):
        otro.consultar(OrdenId("o1"))


# --- La Ruta -------------------------------------------------------------


def test_las_rutas_planificadas_sobreviven_al_reinicio(base: pathlib.Path) -> None:
    """El caso que motiva todo esto: planificar, reiniciar, y que el plan
    siga estando."""
    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    solucion = planificacion.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    esperadas = {
        ruta.id: (ruta.distancia_m, ruta.carga, len(ruta.paradas))
        for ruta in solucion.rutas
    }
    del ordenes, planificacion, solucion

    _, despues = _sistema(base)
    recuperadas = despues.rutas_persistidas()
    assert len(recuperadas) == len(esperadas)
    for persistida in recuperadas:
        distancia, carga, paradas = esperadas[persistida.ruta.id]
        assert persistida.ruta.distancia_m == pytest.approx(distancia)
        assert persistida.ruta.carga == carga
        assert len(persistida.ruta.paradas) == paradas
        assert persistida.fecha_planificacion == DIA
        assert persistida.deposito == DEPOSITO
    _LOG.info("Rutas recuperadas tras reinicio | %d", len(recuperadas))


def test_la_secuencia_de_paradas_se_recupera_en_orden(base: pathlib.Path) -> None:
    """El orden de visita es la Ruta: recuperarlo alterado seria otro
    recorrido."""
    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    solucion = planificacion.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    original = {
        ruta.id: [(p.secuencia, p.nodo, p.ordenes) for p in ruta.paradas]
        for ruta in solucion.rutas
    }
    del ordenes, planificacion, solucion

    _, despues = _sistema(base)
    for persistida in despues.rutas_persistidas():
        assert [
            (p.secuencia, p.nodo, p.ordenes) for p in persistida.ruta.paradas
        ] == original[persistida.ruta.id]


def test_el_filtro_por_fecha_funciona_sobre_la_base(base: pathlib.Path) -> None:
    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    planificacion.planificar(DEPOSITO, {VEHICULO: CAPACIDAD})
    del ordenes, planificacion

    _, despues = _sistema(base)
    assert despues.rutas_persistidas(DIA)
    assert despues.rutas_persistidas(DIA + dt.timedelta(days=1)) == ()
    assert despues.fechas_planificadas() == (DIA,)


# --- Propiedad exclusiva del almacen (ARQ-09) ----------------------------


def test_cada_componente_crea_solo_sus_propias_tablas(
    tmp_path: pathlib.Path,
) -> None:
    """La frontera es verificable: un repositorio no declara tablas ajenas.

    Se abre una base por componente para que lo que aparezca en cada una sea
    exactamente lo que ese componente declara. En el arranque real comparten
    archivo, que es una decision de despliegue y no una autorizacion para
    leer la tabla del vecino.
    """
    de_ordenes = tmp_path / "ordenes.sqlite3"
    de_rutas = tmp_path / "rutas.sqlite3"
    RepositorioOrdenesSQLite(conectar(de_ordenes))
    RepositorioRutasSQLite(conectar(de_rutas))

    assert _tablas(de_ordenes) == TABLAS_DE_ORDENES
    assert _tablas(de_rutas) == TABLAS_DE_RUTAS
    assert not (TABLAS_DE_ORDENES & TABLAS_DE_RUTAS)


# Clausulas de SQL tras las que viene un nombre de tabla. Es lo que hay que
# mirar: `ordenes` tambien es el nombre de un atributo del dominio, y buscar
# la palabra suelta en el archivo marcaria `parada.ordenes` como si fuera una
# consulta.
_CLAUSULAS_CON_TABLA = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|REFERENCES|TABLE(?:\s+IF\s+NOT\s+EXISTS)?|"
    r"INDEX(?:\s+IF\s+NOT\s+EXISTS)?\s+\w+\s+ON)\s+(\w+)",
    re.IGNORECASE,
)


# `... ON CONFLICT DO UPDATE SET` hace que el patron capture SET como si
# fuera el nombre de la tabla actualizada.
_PALABRAS_QUE_NO_SON_TABLA = frozenset({"set"})


def _tablas_que_nombra(modulo: object) -> set[str]:
    """Tablas que aparecen en las sentencias SQL de un modulo.

    Se recorren las cadenas de texto del modulo con `ast` y no el archivo
    entero: aplicar el patron sobre el texto crudo tomaria tambien los
    `from ... import` de Python, que no son consultas.
    """
    fuente = pathlib.Path(modulo.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    nombradas: set[str] = set()
    for nodo in ast.walk(ast.parse(fuente)):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            nombradas.update(
                nombre.lower()
                for nombre in _CLAUSULAS_CON_TABLA.findall(nodo.value)
                if nombre.lower() not in _PALABRAS_QUE_NO_SON_TABLA
            )
    return nombradas


def test_ninguna_consulta_nombra_una_tabla_de_otro_componente() -> None:
    """Ningun JOIN, ninguna lectura y ninguna clave foranea cruza la frontera.

    Se extraen los nombres de tabla de las sentencias SQL de cada repositorio
    --lo que sigue a FROM, JOIN, INTO, UPDATE, REFERENCES o CREATE TABLE-- y
    se verifica que cada uno solo nombre las suyas. El dia que alguien
    resuelva una consulta cruzando almacenes, esto falla.
    """
    import hoppit.gestion_ordenes._repositorio as de_ordenes
    import hoppit.planificacion_rutas._repositorio as de_rutas

    nombradas_por_ordenes = _tablas_que_nombra(de_ordenes)
    nombradas_por_rutas = _tablas_que_nombra(de_rutas)

    assert nombradas_por_ordenes == TABLAS_DE_ORDENES
    assert nombradas_por_rutas == TABLAS_DE_RUTAS
    assert not nombradas_por_ordenes & TABLAS_DE_RUTAS
    assert not nombradas_por_rutas & TABLAS_DE_ORDENES


def test_las_dos_implementaciones_del_almacen_se_comportan_igual(
    base: pathlib.Path,
) -> None:
    """El Protocol es real: la de memoria y la de SQLite responden lo mismo.

    Si una operacion se apoyara en un detalle de una implementacion, la otra
    dejaria de pasar esta prueba.
    """
    from hoppit.gestion_ordenes.puerto import RepositorioOrdenesEnMemoria

    for repositorio in (
        RepositorioOrdenesEnMemoria(),
        RepositorioOrdenesSQLite(conectar(base)),
    ):
        servicio = crear_ordenes(Bus(), repositorio)
        servicio.dar_de_alta(Orden(id=OrdenId("b"), demanda=2, destino=DESTINOS[0]))
        servicio.dar_de_alta(Orden(id=OrdenId("a"), demanda=1, destino=DESTINOS[1]))
        nueva = servicio.registrar(1, DESTINOS[0])

        assert [str(o.id) for o in servicio.listar()] == ["a", "b", str(nueva.id)]
        assert len(list(servicio.ordenes_planificables())) == 3
        assert servicio.consultar(OrdenId("a")).demanda == 1


def test_la_base_en_memoria_no_toca_el_disco() -> None:
    """La ruta es parametro: las pruebas rapidas no necesitan archivo."""
    repositorio = RepositorioOrdenesSQLite(conectar(BASE_EN_MEMORIA))
    servicio = crear_ordenes(Bus(), repositorio)
    servicio.dar_de_alta(Orden(id=OrdenId("o1"), demanda=1, destino=DESTINOS[0]))

    assert servicio.consultar(OrdenId("o1")).demanda == 1


def _tablas(base: pathlib.Path) -> set[str]:
    with sqlite3.connect(base) as conexion:
        return {
            fila[0]
            for fila in conexion.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }


# --- El Plan de Distribucion sobrevive al reinicio ------------------------


def test_el_plan_y_sus_totales_sobreviven_al_reinicio(base: pathlib.Path) -> None:
    """La corrida pasa a ser reconsultable: sus totales ya no viven solo en
    la respuesta del disparo (glosario, entidad 13)."""
    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    plan = planificacion.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    esperado = (
        plan.id,
        plan.cantidad_rutas,
        plan.cantidad_paradas,
        plan.demanda_total,
        plan.vehiculos,
    )
    del ordenes, planificacion

    _, despues = _sistema(base)
    recuperado = despues.plan(plan.id)
    assert (
        recuperado.id,
        recuperado.cantidad_rutas,
        recuperado.cantidad_paradas,
        recuperado.demanda_total,
        recuperado.vehiculos,
    ) == esperado
    assert recuperado.distancia_total_m == pytest.approx(plan.distancia_total_m)
    assert recuperado.fecha_planificacion == DIA


def test_las_rutas_recuperadas_referencian_su_plan(base: pathlib.Path) -> None:
    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    plan = planificacion.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    del ordenes, planificacion

    _, despues = _sistema(base)
    persistidas = despues.rutas_persistidas()
    assert persistidas
    assert all(p.plan_id == plan.id for p in persistidas)
    assert {p.ruta.id for p in despues.rutas_del_plan(plan.id)} == set(plan.rutas)


def test_la_ocupacion_de_vehiculos_sobrevive_al_reinicio(
    base: pathlib.Path,
) -> None:
    """La regla de un Vehiculo por jornada tiene que seguir rigiendo despues
    de reiniciar: si no, un reinicio la volveria evitable."""
    from hoppit.planificacion_rutas.puerto import VehiculosComprometidos

    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    planificacion.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    del ordenes, planificacion

    ordenes, despues = _sistema(base)
    ordenes.dar_de_alta(Orden(id=OrdenId("suelta"), demanda=1, destino=DESTINOS[0]))
    with pytest.raises(VehiculosComprometidos):
        despues.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})


def test_descartar_un_plan_lo_borra_de_la_base(base: pathlib.Path) -> None:
    from hoppit.shared.errores import EntidadNoEncontrada

    ordenes, planificacion = _sistema(base)
    for posicion, destino in enumerate(DESTINOS):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"orden-{posicion}"), demanda=1, destino=destino)
        )
    plan = planificacion.planificar_plan(DEPOSITO, {VEHICULO: CAPACIDAD})
    planificacion.descartar_plan(plan.id)
    del ordenes, planificacion

    _, despues = _sistema(base)
    with pytest.raises(EntidadNoEncontrada):
        despues.plan(plan.id)
    assert despues.rutas_persistidas() == ()
    assert despues.fechas_con_planificacion() == ()


def test_una_base_de_otra_version_remite_a_recrear_instancia() -> None:
    """El mensaje indica el procedimiento (B-117), no borrar el archivo a mano."""
    from hoppit.shared.almacen import EsquemaDesactualizado, verificar_columnas

    conexion = conectar(BASE_EN_MEMORIA)
    conexion.execute("CREATE TABLE ordenes (id TEXT PRIMARY KEY)")

    with pytest.raises(EsquemaDesactualizado, match="tools/recrear_instancia.py"):
        verificar_columnas(conexion, "ordenes", ["id", "demanda"])
    conexion.close()


def test_el_declarante_del_resultado_sobrevive_a_la_base(base: pathlib.Path) -> None:
    """ARQ-65: quien declaro el resultado queda en la transicion, en la tabla
    de Gestion de Ordenes, y vuelve igual al leer."""
    from hoppit.shared.eventos import Declarante, MedioDeDeclaracion

    declarante = Declarante(usuario_id="usuario-x", rol="operador", medio=MedioDeDeclaracion.WEB)
    orden = Orden(id=OrdenId("orden-0"), demanda=1, destino=DESTINOS[0])
    orden.asignar_a_ruta(RutaId("ruta-x"), 1)
    orden.transicionar(EstadoOrden.EN_TRANSITO)
    orden.transicionar(EstadoOrden.COMPLETADA, declarante=declarante)
    RepositorioOrdenesSQLite(conectar(base)).guardar(orden)

    leida = RepositorioOrdenesSQLite(conectar(base)).obtener(OrdenId("orden-0"))

    assert [t.declarante for t in leida.historial] == [None, None, None, declarante]


def test_una_base_sin_las_columnas_del_declarante_remite_a_recrear_instancia(tmp_path: pathlib.Path) -> None:
    """Las columnas del declarante entran en la deteccion de esquema
    desactualizado: una base de 0.14.0 falla al abrir, con la instruccion."""
    from hoppit.shared.almacen import EsquemaDesactualizado

    conexion = conectar(tmp_path / "anterior.sqlite3")
    conexion.execute(
        "CREATE TABLE transiciones_de_orden (orden_id TEXT, posicion INTEGER, estado_anterior TEXT, "
        "estado_nuevo TEXT, marca_temporal TEXT)"
    )
    conexion.commit()

    with pytest.raises(EsquemaDesactualizado, match="declarante_usuario_id.*detener el servidor"):
        RepositorioOrdenesSQLite(conexion)
    conexion.close()
