"""El despliegue arranca el nucleo con el bloqueo por intentos fallidos
encendido (sesion 2026-11-03B): `servir_api.py` lo deja apagado por omision
desde la etapa de prueba, y la imagen y el Compose lo encienden con
`--con-bloqueo-por-intentos`. Se leen los dos archivos tal como estan y se
pasan sus argumentos por el mismo analizador del servidor. Sin red ni Docker."""
from __future__ import annotations

import json
import pathlib
import re

from tools import servir_api

RAIZ_DEL_REPOSITORIO = pathlib.Path(__file__).resolve().parents[3]
DOCKERFILE = RAIZ_DEL_REPOSITORIO / "backend" / "Dockerfile"
COMPOSE = RAIZ_DEL_REPOSITORIO / "infra" / "compose.yaml"
HERRAMIENTA_DEL_NUCLEO = "tools/servir_api.py"


def _cmd_del_dockerfile() -> list[str]:
    texto = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    coincidencia = re.search(r"^CMD\s+(\[.*\])\s*$", texto, flags=re.MULTILINE)
    assert coincidencia, "el Dockerfile no tiene un CMD en forma de lista"
    orden: list[str] = json.loads(coincidencia.group(1))
    return orden


def _command_del_nucleo_en_el_compose() -> list[str]:
    texto = COMPOSE.read_text(encoding="utf-8")
    bloque = re.search(r"^  nucleo:\n((?:    .*\n|\n)*)", texto, flags=re.MULTILINE)
    assert bloque, "el Compose no declara el servicio nucleo"
    coincidencia = re.search(r"^    command:\n((?:      - .*\n)+)", bloque.group(1), flags=re.MULTILINE)
    assert coincidencia, "el servicio nucleo del Compose no declara su command como lista"
    return [linea.strip()[2:].strip().strip('"') for linea in coincidencia.group(1).splitlines()]


def _argumentos_del_servidor(orden: list[str]) -> list[str]:
    assert HERRAMIENTA_DEL_NUCLEO in orden, f"el arranque no corre {HERRAMIENTA_DEL_NUCLEO}: {orden}"
    return orden[orden.index(HERRAMIENTA_DEL_NUCLEO) + 1 :]


def test_la_imagen_arranca_el_nucleo_con_el_bloqueo_por_intentos() -> None:
    argumentos = servir_api.parsear_argumentos(_argumentos_del_servidor(_cmd_del_dockerfile()))
    assert argumentos.con_bloqueo_por_intentos is True


def test_el_compose_arranca_el_nucleo_con_el_bloqueo_por_intentos() -> None:
    argumentos = servir_api.parsear_argumentos(_argumentos_del_servidor(_command_del_nucleo_en_el_compose()))
    assert argumentos.con_bloqueo_por_intentos is True


def test_el_compose_y_la_imagen_arrancan_el_nucleo_igual() -> None:
    assert _command_del_nucleo_en_el_compose() == _cmd_del_dockerfile()


def test_sin_la_opcion_el_servidor_arranca_sin_el_bloqueo() -> None:
    """La contraprueba: lo que enciende el bloqueo es la opcion, no el valor por omision."""
    argumentos = servir_api.parsear_argumentos([])
    assert argumentos.con_bloqueo_por_intentos is False
