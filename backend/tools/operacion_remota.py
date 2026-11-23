"""Lo comun a las herramientas de despliegue y operacion de la maquina
(ARQ-47 a ARQ-49; secciones 1.5, 1.7 y 1.10 de ARQ_Pedido2; 2026-10-27B, D).

Cada herramienta arma una lista de ordenes y despues las ejecuta, o, con
`--simular`, solo las imprime: asi se puede ver exactamente que haria sin
tocar la maquina ni ninguna cuenta. Las ordenes son listas de argumentos y
no texto para una consola, porque el equipo del usuario es Windows.

Nada de esto tiene valores de una cuenta: el destino SSH, el dominio, el
grupo de recursos y la maquina llegan por argumento o por variable de
entorno, sin valor por omision que nombre a nadie.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

from hoppit.shared.red import USER_AGENT_DEL_PROYECTO

_LOG = logging.getLogger("hoppit.tools.operacion_remota")

# Donde vive todo en la maquina (seccion 1.3).
RAIZ_EN_LA_MAQUINA = "/srv/hoppit"
DIRECTORIO_COMPOSE = f"{RAIZ_EN_LA_MAQUINA}/compose"
DIRECTORIO_RESPALDOS = f"{RAIZ_EN_LA_MAQUINA}/respaldos"
# Las bases de cada servicio, vistas desde la maquina.
BASES_EN_LA_MAQUINA = {
    "nucleo": f"{RAIZ_EN_LA_MAQUINA}/datos/nucleo/nucleo.sqlite3",
    "telemetria": f"{RAIZ_EN_LA_MAQUINA}/datos/telemetria/telemetria.sqlite3",
}
# Las variables del .env de la maquina con la etiqueta de cada imagen.
VARIABLES_DE_VERSION = ("VERSION_BORDE", "VERSION_NUCLEO", "VERSION_TELEMETRIA")
# Compose con sus tres servicios.
COMPOSE = ["docker", "compose"]
# Compose como el usuario del servicio, desde `az vm run-command`, que corre
# como root (2026-11-02B: asi se levanto la maquina sin SSH).
USUARIO_DEL_SERVICIO = "hoppit"
COMPOSE_COMO_SERVICIO = f"sudo -u {USUARIO_DEL_SERVICIO} {' '.join(COMPOSE)}"

VARIABLE_DESTINO_SSH = "HOPPIT_SSH_DESTINO"
VARIABLE_DOMINIO = "HOPPIT_DOMINIO"

# Espera de /estado: cada cuanto se pregunta y hasta cuando.
INTERVALO_DE_ESPERA_S = 5.0
PLAZO_DE_ESPERA_S = 180.0
TIEMPO_DE_ESPERA_HTTP_S = 5.0


class FaltaDato(RuntimeError):
    """Falta un dato que no tiene valor por omision (destino, dominio, cuenta)."""


@dataclasses.dataclass(frozen=True)
class Orden:
    """Una orden a ejecutar en el equipo del usuario, con su proposito."""

    argumentos: tuple[str, ...]
    proposito: str

    def como_texto(self, ocultar: Sequence[str] = ()) -> str:
        texto = " ".join(self.argumentos)
        for secreto in ocultar:
            if secreto:
                texto = texto.replace(secreto, "<oculto>")
        return texto


def dato_requerido(valor: str | None, variable: str, descripcion: str) -> str:
    """El valor dado, o el de la variable de entorno; si no hay ninguno, falla."""
    elegido = valor or os.environ.get(variable)
    if not elegido:
        raise FaltaDato(f"Falta {descripcion}: pasalo por argumento o en la variable {variable}.")
    return elegido


def por_ssh(destino: str, comando: str, proposito: str) -> Orden:
    return Orden(("ssh", destino, comando), proposito)


def por_run_command(grupo: str, maquina: str, guion: str, proposito: str) -> Orden:
    """Un guion de shell en la maquina por `az vm run-command`, sin SSH: anda
    tambien desde una red que no deja salir el 22 (2026-11-02B)."""
    return Orden(
        (
            "az", "vm", "run-command", "invoke", "--resource-group", grupo, "--name", maquina,
            "--command-id", "RunShellScript", "--scripts", guion,
        ),
        proposito,
    )


def ordenes_de_respaldo(destino: str) -> list[Orden]:
    """Respaldo consistente de las dos bases, en la maquina (seccion 1.7).

    Corre `respaldar_bases.py` con el Python de la maquina y no dentro de los
    contenedores, porque el directorio de respaldos no esta montado en
    ninguno (decision de 2026-10-27B). La copia usa la API de respaldo de
    SQLite, que es consistente con WAL y con la base abierta.
    """
    return [
        por_ssh(
            destino,
            f"sudo python3 {DIRECTORIO_COMPOSE}/respaldar_bases.py --base {base} "
            f"--directorio {DIRECTORIO_RESPALDOS}/{servicio}",
            f"respaldo de la base de {servicio}",
        )
        for servicio, base in BASES_EN_LA_MAQUINA.items()
    ]


def ejecutar(ordenes: Sequence[Orden], simular: bool, ocultar: Sequence[str] = ()) -> None:
    """Ejecuta cada orden en orden, y se detiene en la primera que falla.

    Con `simular`, solo las imprime, sin ejecutar nada. Lo que se pase en
    `ocultar` (un token) no aparece en lo impreso. El programa se busca con
    `shutil.which`: en Windows `az` es `az.cmd`, y `subprocess` sin consola
    no lo encuentra por su nombre.
    """
    for numero, orden in enumerate(ordenes, start=1):
        _LOG.info("%d. %s", numero, orden.proposito)
        _LOG.info("   %s", orden.como_texto(ocultar))
        if simular:
            continue
        subprocess.run([programa(orden.argumentos[0]), *orden.argumentos[1:]], check=True)


def programa(nombre: str) -> str:
    """La ruta del programa en el PATH; si no esta, falla diciendo cual falta."""
    ruta = shutil.which(nombre)
    if ruta is None:
        raise FaltaDato(f"No se encuentra {nombre} en el PATH: instalarlo o abrir una consola donde ande.")
    return ruta


def esperar_estado(
    url: str,
    version_esperada: str | None,
    plazo_s: float = PLAZO_DE_ESPERA_S,
    pedir: Callable[[str], dict[str, object]] | None = None,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
) -> bool:
    """Espera a que `url` (un `/estado`) responda, con la version esperada si se da."""
    consultar = pedir or _pedir_json
    limite = reloj() + plazo_s
    while True:
        try:
            cuerpo = consultar(url)
            if version_esperada is None or cuerpo.get("version") == version_esperada:
                _LOG.info("%s responde con la version %s", url, cuerpo.get("version"))
                return True
            _LOG.info("%s responde con la version %s; se espera %s", url, cuerpo.get("version"), version_esperada)
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            _LOG.info("%s todavia no responde: %s", url, error)
        if reloj() >= limite:
            return False
        dormir(INTERVALO_DE_ESPERA_S)


def _pedir_json(url: str) -> dict[str, object]:
    pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT_DEL_PROYECTO})
    with urllib.request.urlopen(pedido, timeout=TIEMPO_DE_ESPERA_HTTP_S) as respuesta:
        cuerpo = json.loads(respuesta.read().decode("utf-8"))
    if not isinstance(cuerpo, dict):
        raise ValueError("La respuesta de /estado no es un objeto")
    return cuerpo
