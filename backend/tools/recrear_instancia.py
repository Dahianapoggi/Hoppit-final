"""Deja el sistema en el estado de demostracion conocido, desde cero.

Base limpia y las Ordenes de la instancia vigente en estado Creada, sin
planificar. Es el punto de partida reproducible: despues de correrlo, una
planificacion da siempre el mismo plan (NFR-07).

Se distingue de `descartar_planes.py`, que conserva el historial y toda Orden
dada de alta durante una demostracion. Este **borra la base y la rehace**:
es lo que hace falta cuando el esquema cambio --una base creada por una
version anterior no se puede seguir usando y no hay migracion automatica-- o
cuando se quiere volver al estado exacto de la instancia sin arrastrar nada.

El grafo se indica igual que en el servidor, con `--grafo` o con
`HOPPIT_GRAFO`, sin valor por omision (B-220, punto 4; 2026-10-27C, F1): sin
ninguno de los dos, termina con codigo 1 y lo dice. Sobre San Isidro la base queda con las Ordenes de la instancia
canonica; sobre CABA, con la Flota de demostracion y sin Ordenes, que da de
alta el operador: no se cargan Ordenes sorteadas (2026-10-27B, K).

    python tools/recrear_instancia.py --grafo caba
    python tools/recrear_instancia.py --grafo san_isidro
    python tools/recrear_instancia.py --grafo caba --base-de-datos datos/otra.sqlite3
"""
from __future__ import annotations

import argparse
import contextlib
import gc
import logging
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
from tools.acceso_en_proceso import clave_efimera
from tools.servir_api import (
    BASE_PREDETERMINADA,
    DIRECTORIO_INSTANCIA,
    GRAFOS_DEL_PRODUCTO,
    VARIABLE_DEL_GRAFO,
    GrafoDelProducto,
    GrafoDesconocido,
    GrafoNoIndicado,
    construir_contexto,
    es_el_grafo_de_la_instancia_canonica,
    grafo_elegido,
)

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.recrear_instancia")

# SQLite deja dos archivos auxiliares junto a la base. Borrar solo el
# principal dejaria un diario apuntando a una base que ya no existe.
_SUFIJOS_AUXILIARES = ("-wal", "-shm")

# Con el servidor levantado la base esta abierta por otro proceso: en Windows
# no se puede borrar, y antes el comando terminaba con un PermissionError sin
# explicacion (2026-10-20A, Z.5; 2026-10-20B, F1.6). Ahora se detecta antes de
# tocar nada y se dice que hacer.
MENSAJE_BASE_EN_USO = (
    "La base está en uso: detené el servidor (Ctrl+C en su ventana) y volvé a correr este comando."
)
CODIGO_BASE_EN_USO = 3
_SUFIJO_DE_LA_PRUEBA_DE_USO = ".prueba-de-uso"


class BaseEnUso(RuntimeError):
    """Otro proceso tiene la base abierta: no se borra nada."""


def verificar_que_no_este_en_uso(base: pathlib.Path) -> None:
    """Intenta mover la base y sus auxiliares a un nombre de prueba y los
    devuelve a su lugar. En Windows, un archivo que otro proceso tiene
    abierto --el servidor-- no se puede mover, y eso es justo lo que hay que
    saber antes de borrar: no se borran tablas ni archivos con el servidor
    levantado. Antes se liberan las conexiones de este proceso que ya nadie
    usa, como hace el borrado."""
    gc.collect()
    candidatos = [base] + [base.with_name(base.name + sufijo) for sufijo in _SUFIJOS_AUXILIARES]
    for archivo in candidatos:
        if not archivo.exists():
            continue
        prueba = archivo.with_name(archivo.name + _SUFIJO_DE_LA_PRUEBA_DE_USO)
        try:
            os.replace(archivo, prueba)
        except PermissionError as error:
            raise BaseEnUso(MENSAJE_BASE_EN_USO) from error
        os.replace(prueba, archivo)


def _describir_lo_existente(base: pathlib.Path) -> None:
    """Informa que habia antes de borrarlo.

    Se intenta leer, y si no se puede se dice: una base de una version
    anterior --el caso que motiva este comando-- no se puede consultar con el
    esquema de hoy, y eso no es motivo para no rehacerla.
    """
    if not base.exists():
        _LOG.info("No habia base en %s: se crea de cero.", base)
        return
    try:
        # `with sqlite3.connect(...)` solo delimita una transaccion: no cierra
        # la conexion, que queda abierta hasta que el recolector de ciclos la
        # libere. En Windows un archivo abierto no se puede borrar, y el
        # borrado viene justo despues de esta lectura. `closing` la cierra al
        # salir del bloque, tambien si la lectura falla.
        with contextlib.closing(sqlite3.connect(base)) as conexion:
            ordenes = conexion.execute("SELECT COUNT(*) FROM ordenes").fetchone()[0]
            rutas = conexion.execute("SELECT COUNT(*) FROM rutas").fetchone()[0]
        _LOG.info(
            "Se elimina la base anterior en %s | contenia %d Orden(es) y "
            "%d Ruta(s).",
            base,
            ordenes,
            rutas,
        )
    except sqlite3.Error as error:
        _LOG.info(
            "Se elimina la base anterior en %s | no se pudo leer su contenido "
            "(%s): probablemente fue creada por una version anterior, que es "
            "justamente el caso que este comando resuelve.",
            base,
            error,
        )


def _eliminar(base: pathlib.Path) -> None:
    # Una conexion sqlite3 sin `close()` no se libera al perder su ultima
    # referencia: su cache de sentencias la referencia a ella, y el ciclo solo
    # lo rompe el recolector. Hasta entonces el archivo sigue abierto, y en
    # Windows un archivo abierto no se puede borrar. Las conexiones que ya
    # nadie usa en este proceso --las del contexto que arma `recrear`, si se
    # lo llama dos veces-- se liberan aca. Una conexion todavia en uso no se
    # toca y el borrado falla, que es lo correcto.
    gc.collect()
    try:
        base.unlink(missing_ok=True)
        for sufijo in _SUFIJOS_AUXILIARES:
            base.with_name(base.name + sufijo).unlink(missing_ok=True)
    except PermissionError as error:
        raise BaseEnUso(MENSAJE_BASE_EN_USO) from error


def recrear(
    base_de_datos: pathlib.Path,
    directorio_instancia: pathlib.Path = DIRECTORIO_INSTANCIA,
    semilla: int = SEMILLA_DE_INSTANCIA,
    *,
    grafo: GrafoDelProducto,
) -> int:
    """Rehace la base con la instancia de demostracion. Devuelve las Ordenes
    creadas: las de la instancia canonica sobre San Isidro, ninguna sobre
    CABA. `grafo` se indica siempre (B-220, punto 4).

    Si otro proceso tiene la base abierta, levanta `BaseEnUso` sin haber
    tocado nada."""
    elegido = grafo
    verificar_que_no_este_en_uso(base_de_datos)
    _describir_lo_existente(base_de_datos)
    _eliminar(base_de_datos)

    # Sobre una base inexistente, el armado crea el esquema y carga el
    # escenario. Es el mismo camino que recorre el arranque del servidor: no
    # se duplica la carga aca, para que lo que deja este comando sea
    # exactamente lo que el servidor esperaria encontrar.
    contexto = construir_contexto(
        directorio_instancia, semilla, base_de_datos=base_de_datos,
        grafo=elegido.grafo,
        deposito=elegido.deposito,
        clave_privada=clave_efimera(),
    )
    ordenes = contexto.ordenes.listar()
    creadas = contexto.ordenes.listar(EstadoOrden.CREADA)
    rutas = contexto.planificacion.rutas_persistidas()

    destinos = {orden.destino for orden in ordenes}
    _LOG.info(
        "Instancia recreada sobre el grafo %s | %d Ordenes sobre %d Paradas | demanda total %d "
        "bultos | semilla %d",
        elegido.clave,
        len(ordenes),
        len(destinos),
        sum(orden.demanda for orden in ordenes),
        semilla,
    )
    _LOG.info(
        "Flota | %d Vehiculos de %s bultos | Deposito en el nodo %d",
        len(contexto.flota),
        sorted({capacidad for capacidad in contexto.flota.values()}),
        contexto.deposito,
    )
    _LOG.info(
        "Estado | Ordenes en Creada=%d | Rutas=%d | base=%s",
        len(creadas),
        len(rutas),
        base_de_datos,
    )

    if len(creadas) != len(ordenes) or rutas or (
        not es_el_grafo_de_la_instancia_canonica(elegido.grafo) and ordenes
    ):
        _LOG.error(
            "El sistema no quedo en el estado de demostracion: se esperaba "
            "que todas las Ordenes estuvieran en Creada y no hubiera Rutas."
        )
    else:
        _LOG.info("Listo para planificar.")
    return len(ordenes)


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-de-datos",
        type=pathlib.Path,
        default=BASE_PREDETERMINADA,
        help="Archivo SQLite a rehacer. La ruta es siempre parametro.",
    )
    parser.add_argument(
        "--directorio-instancia", type=pathlib.Path, default=DIRECTORIO_INSTANCIA
    )
    parser.add_argument("--semilla", type=int, default=SEMILLA_DE_INSTANCIA)
    parser.add_argument(
        "--grafo",
        choices=sorted(GRAFOS_DEL_PRODUCTO),
        default=None,
        help=f"Grafo de la base. Sin indicarlo, el de {VARIABLE_DEL_GRAFO}; sin ninguno, termina con codigo 1.",
    )
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in (
        "hoppit.cartografia",
        "hoppit.planificacion_rutas",
        "hoppit.tools.servir_api",
    ):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    args = parsear_argumentos(argumentos)
    try:
        recrear(args.base_de_datos, args.directorio_instancia, args.semilla, grafo=grafo_elegido(args.grafo))
    except (GrafoNoIndicado, GrafoDesconocido) as error:
        _LOG.error("%s", error)
        return 1
    except BaseEnUso as error:
        _LOG.error("%s", error)
        return CODIGO_BASE_EN_USO
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
