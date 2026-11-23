"""Condiciones de una corrida que mide tiempos (encargo del 13 de octubre, M4;
B-174, B-208, punto 8).

Escribe, junto al archivo que guarda una medicion de tiempos, un sidecar con
las condiciones en que se tomo: CPU, memoria fisica total y libre, sistema
operativo, version de Python y de OR-Tools, estado de energia y plan,
commit y si el arbol de trabajo esta limpio. No mide nada por si mismo.

Se usa desde otra herramienta (`escribir_condiciones(ruta)`) o por linea de
comandos, sobre el archivo de una corrida ya hecha:

    python tools/condiciones_de_medicion.py RUTA_DE_LA_MEDICION

`sin_suspension()` es un contexto que, en Windows, pide al sistema que no
entre en suspension (ni en modo de espera moderno) mientras dura la serie,
con `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
ES_DISPLAY_REQUIRED)`. No cambia la configuracion de energia del equipo: el
pedido dura lo que el proceso. El 12 de octubre una serie quedo invalidada
porque el equipo entro en espera moderna enchufado, a los siete minutos de
empezar (sesion 2026-10-13C).

Sin dependencias fuera de la biblioteca estandar y del proyecto: en Windows,
la memoria sale de `GlobalMemoryStatusEx`, el estado de energia de
`GetSystemPowerStatus` y el plan de `powercfg /getactivescheme`, y el nombre
de la CPU del registro.
"""
from __future__ import annotations

import argparse
import ctypes
import contextlib
import dataclasses
import datetime as dt
import json
import logging
import os
import pathlib
import platform
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

FORMATO_LOG = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.condiciones_de_medicion")

RAIZ_DEL_REPOSITORIO = pathlib.Path(__file__).resolve().parents[2]
SUFIJO_DEL_SIDECAR = ".condiciones.json"
BYTES_POR_GB = 1024**3
DECIMALES = 2
CODIFICACION = "utf-8"
SANGRIA = 2
CLAVE_DE_LA_CPU = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"

# GetSystemPowerStatus: ACLineStatus 0 desconectado, 1 conectado, 255
# desconocido.
_LINEA_DE_ALIMENTACION = {0: "bateria", 1: "red electrica", 255: "desconocido"}
_BATERIA_DESCONOCIDA = 255

# SetThreadExecutionState: mantener el sistema despierto mientras dure el
# pedido, y ES_CONTINUOUS sola para soltarlo.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
# En un equipo con espera moderna, apagar la pantalla por inactividad lleva a
# la espera: tambien se pide la pantalla encendida.
ES_DISPLAY_REQUIRED = 0x00000002


@contextlib.contextmanager
def sin_suspension() -> Iterator[bool]:
    """Pide que el equipo no se suspenda mientras dura el bloque. Devuelve
    si el pedido se pudo hacer."""
    if sys.platform != "win32":
        yield False
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_uint
    kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    previo = kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
    try:
        yield previo != 0
    finally:
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)


class _EstadoDeMemoria(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _EstadoDeEnergia(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def memoria_fisica_gb() -> tuple[float | None, float | None]:
    """Total y libre (disponible), en GB; None si no se puede leer."""
    if sys.platform == "win32":
        estado = _EstadoDeMemoria()
        estado.dwLength = ctypes.sizeof(_EstadoDeMemoria)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
            return estado.ullTotalPhys / BYTES_POR_GB, estado.ullAvailPhys / BYTES_POR_GB
        return None, None
    try:
        pagina = os.sysconf("SC_PAGE_SIZE")
        return (
            os.sysconf("SC_PHYS_PAGES") * pagina / BYTES_POR_GB,
            os.sysconf("SC_AVPHYS_PAGES") * pagina / BYTES_POR_GB,
        )
    except (ValueError, OSError, AttributeError):
        return None, None


def _energia() -> dict[str, Any]:
    if sys.platform != "win32":
        return {"alimentacion": "desconocido", "plan": None}
    estado = _EstadoDeEnergia()
    ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(estado))
    try:
        plan = subprocess.run(
            # powercfg escribe en la pagina de codigos OEM de la consola.
            ["powercfg", "/getactivescheme"], capture_output=True, encoding="oem", errors="replace", check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        plan = None
    return {
        "alimentacion": _LINEA_DE_ALIMENTACION.get(estado.ACLineStatus, "desconocido"),
        "carga_de_la_bateria_por_ciento": (
            None if estado.BatteryLifePercent == _BATERIA_DESCONOCIDA else estado.BatteryLifePercent
        ),
        "plan": plan,
    }


def _cpu() -> dict[str, Any]:
    nombre = platform.processor()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CLAVE_DE_LA_CPU) as clave:
                nombre = str(winreg.QueryValueEx(clave, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return {"nombre": nombre, "nucleos_logicos": os.cpu_count()}


def _git(*argumentos: str) -> str | None:
    try:
        return subprocess.run(
            ["git", *argumentos], cwd=RAIZ_DEL_REPOSITORIO, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _version_de_ortools() -> str | None:
    try:
        import ortools

        return str(ortools.__version__)
    except ImportError:
        return None


@dataclasses.dataclass(frozen=True)
class Condiciones:
    momento_utc: str
    cpu: dict[str, Any]
    memoria_total_gb: float | None
    memoria_libre_gb: float | None
    sistema_operativo: str
    python: str
    ortools: str | None
    energia: dict[str, Any]
    commit: str | None
    arbol_limpio: bool | None


def condiciones_actuales() -> Condiciones:
    total, libre = memoria_fisica_gb()
    estado = _git("status", "--porcelain")
    return Condiciones(
        momento_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        cpu=_cpu(),
        memoria_total_gb=None if total is None else round(total, DECIMALES),
        memoria_libre_gb=None if libre is None else round(libre, DECIMALES),
        sistema_operativo=platform.platform(),
        python=platform.python_version(),
        ortools=_version_de_ortools(),
        energia=_energia(),
        commit=_git("rev-parse", "HEAD"),
        arbol_limpio=None if estado is None else estado == "",
    )


def escribir_condiciones(ruta_de_la_medicion: pathlib.Path) -> pathlib.Path:
    """Escribe el sidecar de condiciones junto a la medicion y devuelve su ruta."""
    ruta = ruta_de_la_medicion.with_name(ruta_de_la_medicion.name + SUFIJO_DEL_SIDECAR)
    contenido = json.dumps(dataclasses.asdict(condiciones_actuales()), ensure_ascii=False, indent=SANGRIA)
    ruta.write_bytes((contenido + "\n").encode(CODIFICACION))
    return ruta


def main(argumentos: list[str]) -> int:
    """Con argparse, un argumento que empieza con `-` es una opcion y no una
    ruta: `--help` muestra la ayuda y una opcion desconocida es un error de
    uso, sin escribir ningun archivo. Antes, `--help` escribia
    `--help.condiciones.json` (sesion 2026-10-13D)."""
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "medicion", type=pathlib.Path, help="Archivo de la medicion junto al que se escriben las condiciones."
    )
    args = parser.parse_args(argumentos)
    ruta = escribir_condiciones(args.medicion)
    _LOG.info("Condiciones escritas en %s", ruta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
