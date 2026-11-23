"""Levanta el servicio de telemetria y seguimiento (ARQ-47, ARQ-52;
2026-10-27B, T).

Es el segundo punto de ensamblado del sistema, en su propio proceso y con su
propia base (`telemetria.sqlite3`). Arma solo Seguimiento de Flota, el
adaptador de telemetria y la verificacion de tokens de Identidad con la
clave publica: no carga el grafo, ni Planificacion de Rutas, ni Gestion de
Ordenes, ni Vigilancia de SLA (seccion 1.2; una prueba lo verifica).

La retencion de 30 dias se aplica al arrancar y cada 6 horas (seccion 3.6).

    python tools/servir_telemetria.py
    python tools/servir_telemetria.py --puerto 8041 --base-de-datos datos/tmp/telemetria.sqlite3
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sqlite3
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import fastapi
import uvicorn

from hoppit.frontera.identidad.puerto import ClavesDeFirmaAusentes, directorio_de_claves, leer_clave_publica
from hoppit.frontera.telemetria.puerto import crear_app_de_telemetria
from hoppit.seguimiento_flota.puerto import (
    RepositorioDeSeguimientoSQLite,
    ServicioDeSeguimiento,
    crear_servicio,
)
from hoppit.shared.almacen import conectar
from tools.proxies_del_borde import proxies_confiables

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.servir_telemetria")

RAIZ_DEL_BACKEND = pathlib.Path(__file__).resolve().parents[1]
BASE_PREDETERMINADA = RAIZ_DEL_BACKEND / "datos" / "telemetria.sqlite3"
HOST_PREDETERMINADO = "127.0.0.1"
PUERTO_PREDETERMINADO = 8001
# Cada cuanto se vuelve a aplicar la retencion (seccion 3.6).
INTERVALO_DE_RETENCION_S = 6 * 60 * 60


def construir_app(base_de_datos: pathlib.Path | str | None) -> tuple[fastapi.FastAPI, ServicioDeSeguimiento]:
    """El servicio y su aplicacion. Sin base, en memoria (pruebas)."""
    clave_publica = leer_clave_publica(directorio_de_claves())
    conexion = None if base_de_datos is None else conectar(base_de_datos)
    seguimiento = crear_servicio(None if conexion is None else RepositorioDeSeguimientoSQLite(conexion))

    def base_disponible() -> bool:
        if conexion is None:
            return True
        try:
            conexion.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    return crear_app_de_telemetria(seguimiento, clave_publica, base_disponible), seguimiento


def retener_periodicamente(seguimiento: ServicioDeSeguimiento, intervalo_s: float = INTERVALO_DE_RETENCION_S) -> threading.Event:
    """Aplica la retencion ahora y despues cada `intervalo_s`, en un hilo de fondo."""
    detener = threading.Event()

    def ciclo() -> None:
        while True:
            try:
                seguimiento.aplicar_retencion()
            except sqlite3.Error:
                _LOG.exception("La retencion no se pudo aplicar")
            if detener.wait(intervalo_s):
                return

    threading.Thread(target=ciclo, name="retencion-de-posiciones", daemon=True).start()
    return detener


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=HOST_PREDETERMINADO)
    parser.add_argument("--puerto", type=int, default=PUERTO_PREDETERMINADO)
    parser.add_argument("--base-de-datos", type=pathlib.Path, default=BASE_PREDETERMINADA)
    args = parser.parse_args(argumentos)
    try:
        app, seguimiento = construir_app(args.base_de_datos)
    except ClavesDeFirmaAusentes as error:
        _LOG.error("%s", error)
        return 1
    retener_periodicamente(seguimiento)
    _LOG.info("Telemetria disponible en http://%s:%d | base %s", args.host, args.puerto, args.base_de_datos)
    confiables = proxies_confiables()
    _LOG.info("X-Forwarded-For aceptado desde: %s", confiables or "el valor por omision de uvicorn")
    uvicorn.run(app, host=args.host, port=args.puerto, forwarded_allow_ips=confiables)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
