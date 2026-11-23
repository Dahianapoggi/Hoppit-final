"""Despliega una version en la maquina, desde el equipo del usuario
(seccion 1.5 de ARQ_Pedido2; 2026-10-27B, D).

Pasos, por SSH:
  1. respaldo de las dos bases (seccion 1.7);
  2. copia del `compose.yaml` versionado y de la herramienta de respaldo;
  3. cambio de las variables VERSION_* del .env de la maquina;
  4. `docker compose pull` y `docker compose up -d`;
  5. espera a que `/estado` responda con la version de producto esperada;
  6. si no responde en el plazo, vuelve a la version anterior y lo informa.

Con `--simular` imprime las ordenes sin ejecutar ninguna. El destino SSH y el
dominio no tienen valor por omision.

    python tools/desplegar.py --version <sha> --destino <usuario>@<ip> --dominio <dominio> --simular
"""
from __future__ import annotations

import argparse
import importlib.metadata
import logging
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools.operacion_remota import (
    COMPOSE,
    DIRECTORIO_COMPOSE,
    VARIABLE_DESTINO_SSH,
    VARIABLE_DOMINIO,
    VARIABLES_DE_VERSION,
    FaltaDato,
    Orden,
    dato_requerido,
    ejecutar,
    esperar_estado,
    ordenes_de_respaldo,
    por_ssh,
)

FORMATO_LOG = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_LOG = logging.getLogger("hoppit.tools.desplegar")

RAIZ_DEL_REPOSITORIO = pathlib.Path(__file__).resolve().parents[2]
COMPOSE_VERSIONADO = RAIZ_DEL_REPOSITORIO / "infra" / "compose.yaml"
HERRAMIENTA_DE_RESPALDO = RAIZ_DEL_REPOSITORIO / "backend" / "tools" / "respaldar_bases.py"
NOMBRE_DEL_PAQUETE = "hoppit"


def ordenes_para_cambiar_version(destino: str, version: str) -> list[Orden]:
    """Las VERSION_* del .env de la maquina pasan a `version`."""
    reemplazos = " ".join(f"-e 's/^{variable}=.*/{variable}={version}/'" for variable in VARIABLES_DE_VERSION)
    return [
        por_ssh(
            destino,
            f"sed -i {reemplazos} {DIRECTORIO_COMPOSE}/.env",
            f"VERSION_* del .env de la maquina a {version}",
        )
    ]


def ordenes_para_levantar(destino: str) -> list[Orden]:
    compose = " ".join(COMPOSE)
    return [
        por_ssh(destino, f"cd {DIRECTORIO_COMPOSE} && {compose} pull", "descarga de las imagenes"),
        por_ssh(destino, f"cd {DIRECTORIO_COMPOSE} && {compose} up -d", "arranque de los servicios"),
    ]


def ordenes_de_despliegue(destino: str, version: str) -> list[Orden]:
    """Los pasos 1 a 4, en orden."""
    return [
        *ordenes_de_respaldo(destino),
        Orden(
            ("scp", str(COMPOSE_VERSIONADO), str(HERRAMIENTA_DE_RESPALDO), f"{destino}:{DIRECTORIO_COMPOSE}/"),
            "copia del compose.yaml versionado y de la herramienta de respaldo",
        ),
        *ordenes_para_cambiar_version(destino, version),
        *ordenes_para_levantar(destino),
    ]


def version_anterior(destino: str) -> str | None:
    """La VERSION_NUCLEO vigente en la maquina, para poder volver a ella."""
    resultado = subprocess.run(
        ["ssh", destino, f"grep -E '^VERSION_NUCLEO=' {DIRECTORIO_COMPOSE}/.env"],
        check=True,
        capture_output=True,
        text=True,
    )
    linea = resultado.stdout.strip()
    return linea.split("=", 1)[1] if "=" in linea else None


def parsear_argumentos(argumentos: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", required=True, help="Etiqueta de las imagenes: el SHA del commit.")
    parser.add_argument("--destino", default=None, help=f"usuario@ip de la maquina, o {VARIABLE_DESTINO_SSH}.")
    parser.add_argument("--dominio", default=None, help=f"Dominio del sitio, o {VARIABLE_DOMINIO}.")
    parser.add_argument(
        "--version-de-producto",
        default=None,
        help="Version que /estado tiene que publicar. Por omision, la del paquete instalado.",
    )
    parser.add_argument("--simular", action="store_true", help="Imprime las ordenes sin ejecutarlas.")
    return parser.parse_args(argumentos)


def main(argumentos: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    args = parsear_argumentos(argumentos)
    try:
        destino = dato_requerido(args.destino, VARIABLE_DESTINO_SSH, "el destino SSH")
        dominio = dato_requerido(args.dominio, VARIABLE_DOMINIO, "el dominio")
    except FaltaDato as error:
        _LOG.error("%s", error)
        return 1
    esperada = args.version_de_producto or importlib.metadata.version(NOMBRE_DEL_PAQUETE)
    url = f"https://{dominio}/estado"

    if args.simular:
        ejecutar(ordenes_de_despliegue(destino, args.version), simular=True)
        _LOG.info("Despues esperaria a que %s publique la version %s", url, esperada)
        return 0

    anterior = version_anterior(destino)
    ejecutar(ordenes_de_despliegue(destino, args.version), simular=False)
    if esperar_estado(url, esperada):
        _LOG.info("Desplegada la version %s", args.version)
        return 0
    _LOG.error("%s no respondio con la version %s en el plazo", url, esperada)
    if anterior is None:
        _LOG.error("No hay version anterior registrada en el .env: no se puede volver atras")
        return 1
    _LOG.warning("Vuelvo a la version anterior, %s", anterior)
    ejecutar([*ordenes_para_cambiar_version(destino, anterior), *ordenes_para_levantar(destino)], simular=False)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
