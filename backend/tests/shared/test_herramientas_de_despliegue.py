"""Herramientas de despliegue y operacion (2026-10-27B, D): como arman las
ordenes, sin red ni maquina, y el respaldo sobre una base temporal."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import pathlib
import sqlite3
import urllib.error

import pytest

from tools.actualizar_duckdns import main as actualizar_duckdns, pedido_de_actualizacion
from tools.apagar import ordenes_de_apagado
from tools.desplegar import main as desplegar, ordenes_de_despliegue
from tools.encender import ordenes_de_encendido
from tools.operacion_remota import VARIABLE_DESTINO_SSH, VARIABLE_DOMINIO, FaltaDato, Orden, ejecutar, esperar_estado
from tools.recrear_bases_remotas import ordenes_de_recreacion
from tools.respaldar_bases import RETENCION, SUFIJO_SIDECAR, respaldar

DESTINO = "usuario@192.0.2.10"  # direccion de documentacion (RFC 5737), no una maquina real
VERSION = "abc1234"


def _texto(ordenes: list) -> list[str]:
    return [" ".join(o.argumentos) for o in ordenes]


def test_el_despliegue_respalda_copia_cambia_la_version_y_levanta_en_ese_orden() -> None:
    texto = _texto(ordenes_de_despliegue(DESTINO, VERSION))
    assert "respaldar_bases.py" in texto[0] and "nucleo.sqlite3" in texto[0]
    assert "telemetria.sqlite3" in texto[1]
    assert texto[2].startswith("scp ") and texto[2].endswith(f"{DESTINO}:/srv/hoppit/compose/")
    assert "VERSION_NUCLEO=abc1234" in texto[3] and "VERSION_TELEMETRIA=abc1234" in texto[3]
    assert texto[4].endswith("docker compose pull")
    assert texto[5].endswith("docker compose up -d")
    assert all(o.argumentos[0] in {"ssh", "scp"} for o in ordenes_de_despliegue(DESTINO, VERSION))


def test_simular_no_ejecuta_nada_y_sin_destino_falla(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import subprocess

    def prohibido(*a: object, **k: object) -> None:
        raise AssertionError("simular no ejecuta ordenes")

    monkeypatch.setattr(subprocess, "run", prohibido)
    monkeypatch.delenv(VARIABLE_DESTINO_SSH, raising=False)
    monkeypatch.delenv(VARIABLE_DOMINIO, raising=False)
    assert desplegar(["--version", VERSION, "--simular"]) == 1
    with caplog.at_level(logging.INFO):
        assert desplegar(["--version", VERSION, "--destino", DESTINO, "--dominio", "ejemplo.invalid", "--simular"]) == 0
    assert "docker compose up -d" in caplog.text


def test_apagar_respalda_detiene_y_desasigna() -> None:
    texto = _texto(ordenes_de_apagado(DESTINO, "grupo", "maquina"))
    assert "respaldar_bases.py" in texto[0]
    assert texto[2].endswith("docker compose stop")
    assert texto[3] == "az vm deallocate --resource-group grupo --name maquina"


def test_encender_arranca_la_maquina_y_levanta_los_servicios_sin_ssh() -> None:
    ordenes = ordenes_de_encendido("grupo", "maquina")
    assert _texto(ordenes)[0] == "az vm start --resource-group grupo --name maquina"
    levantar = ordenes[1].argumentos
    assert levantar[:8] == ("az", "vm", "run-command", "invoke", "--resource-group", "grupo", "--name", "maquina")
    assert levantar[-1] == "cd /srv/hoppit/compose && sudo -u hoppit docker compose up -d"
    assert all(o.argumentos[0] != "ssh" for o in ordenes)


def test_ejecutar_busca_el_programa_en_el_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """En Windows `az` es `az.cmd`: se ejecuta la ruta que da `shutil.which`."""
    import shutil
    import subprocess

    ejecutadas: list[list[str]] = []
    monkeypatch.setattr(shutil, "which", lambda nombre: f"C:/herramientas/{nombre}.cmd" if nombre == "az" else None)
    monkeypatch.setattr(subprocess, "run", lambda argumentos, check: ejecutadas.append(argumentos))
    ejecutar([Orden(("az", "vm", "start"), "arranque")], simular=False)
    assert ejecutadas == [["C:/herramientas/az.cmd", "vm", "start"]]
    with pytest.raises(FaltaDato, match="ssh"):
        ejecutar([Orden(("ssh", DESTINO, "true"), "prueba")], simular=False)
    assert len(ejecutadas) == 1


def test_recrear_las_bases_respalda_detiene_recrea_aparta_y_levanta_sin_ssh() -> None:
    (orden,) = ordenes_de_recreacion("grupo", "maquina")
    assert orden.argumentos[:4] == ("az", "vm", "run-command", "invoke")
    guion = orden.argumentos[-1]
    pasos = [
        "respaldar_bases.py --base /srv/hoppit/datos/nucleo/nucleo.sqlite3",
        "respaldar_bases.py --base /srv/hoppit/datos/telemetria/telemetria.sqlite3",
        "sudo -u hoppit docker compose stop nucleo telemetria",
        "run --rm --no-deps nucleo python tools/recrear_instancia.py --grafo caba",
        "mv \"$f\" /srv/hoppit/respaldos/telemetria/apartada-",
        "sudo -u hoppit docker compose up -d",
    ]
    posiciones = [guion.index(paso) for paso in pasos]
    assert posiciones == sorted(posiciones)


def test_la_espera_de_estado_reintenta_hasta_la_version_y_tiene_plazo() -> None:
    respuestas = iter([urllib.error.URLError("todavia no"), {"version": "0.15.0"}, {"version": "0.16.0"}])

    def pedir(_: str) -> dict:
        r = next(respuestas)
        if isinstance(r, Exception):
            raise r
        return r

    reloj = iter(range(100))
    assert esperar_estado("https://x/estado", "0.16.0", pedir=pedir, dormir=lambda _: None, reloj=lambda: next(reloj))
    nunca = iter(range(100))
    assert not esperar_estado(
        "https://x/estado", "9.9.9", plazo_s=3, pedir=lambda _: {"version": "0.16.0"}, dormir=lambda _: None,
        reloj=lambda: next(nunca),
    )


def test_duckdns_lleva_el_user_agent_y_no_muestra_el_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    token = "token-de-prueba-1234"
    pedido = pedido_de_actualizacion("subdominio", token, "192.0.2.10")
    assert pedido.get_header("User-agent") == "HOPPIT-PFI-UADE/0.11"
    monkeypatch.setenv("HOPPIT_DUCKDNS_SUBDOMINIO", "subdominio")
    monkeypatch.setenv("HOPPIT_DUCKDNS_TOKEN", token)
    with caplog.at_level(logging.INFO):
        assert actualizar_duckdns(["--ip", "192.0.2.10", "--simular"]) == 0
    assert token not in caplog.text
    assert "<oculto>" in caplog.text


def test_el_respaldo_es_una_copia_legible_con_su_sha256_y_conserva_las_mas_recientes(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "nucleo.sqlite3"
    with contextlib.closing(sqlite3.connect(base)) as conexion:
        conexion.execute("PRAGMA journal_mode=WAL")
        conexion.execute("CREATE TABLE t (x INTEGER)")
        conexion.execute("INSERT INTO t VALUES (7)")
        conexion.commit()
        # La base sigue abierta mientras se respalda.
        directorio = tmp_path / "respaldos"
        inicio = dt.datetime(2026, 10, 27, 3, 0, tzinfo=dt.timezone.utc)
        copias = [
            respaldar(base, directorio, instante=inicio + dt.timedelta(minutes=m)) for m in range(RETENCION + 2)
        ]
    ultima = copias[-1]
    with contextlib.closing(sqlite3.connect(ultima)) as copia:
        assert copia.execute("SELECT x FROM t").fetchall() == [(7,)]
    sidecar = json.loads((ultima.parent / (ultima.name + SUFIJO_SIDECAR)).read_text(encoding="utf-8"))
    assert sidecar["sha256"] == hashlib.sha256(ultima.read_bytes()).hexdigest()
    quedan = sorted(directorio.glob("nucleo_*.sqlite3"))
    assert quedan == copias[-RETENCION:]
