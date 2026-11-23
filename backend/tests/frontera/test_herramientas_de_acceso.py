"""Las herramientas de claves y de usuarios (sesion 2026-10-19B, bloque I).

Las contrasenas se generan dentro de cada prueba y se pasan por una variable
de entorno, como en el uso no interactivo; no se escriben en ningun archivo.
"""
from __future__ import annotations

import pathlib

import secrets

import pytest

from hoppit.frontera.identidad.puerto import (
    ARCHIVO_CLAVE_PRIVADA,
    ClavesDeFirmaAusentes,
    CredencialesInvalidas,
    Rol,
    crear_repositorio,
    crear_servicio,
    generar_clave_privada,
    leer_clave_privada,
)
from tools import (
    crear_usuario,
    desactivar_usuario,
    generar_claves_de_firma,
    restablecer_contrasena,
)

VARIABLE = "HOPPIT_PRUEBA_CONTRASENA"


def test_generar_claves_no_reemplaza_las_existentes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    assert generar_claves_de_firma.main(["--directorio", str(tmp_path)]) == 0
    primera = (tmp_path / ARCHIVO_CLAVE_PRIVADA).read_bytes()
    assert generar_claves_de_firma.main(["--directorio", str(tmp_path)]) == 1
    assert (tmp_path / ARCHIVO_CLAVE_PRIVADA).read_bytes() == primera
    leer_clave_privada(tmp_path)


def test_sin_claves_el_armado_falla_y_remite_a_la_herramienta(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ClavesDeFirmaAusentes, match="generar_claves_de_firma"):
        leer_clave_privada(tmp_path)


def test_alta_baja_y_restablecimiento_por_herramienta(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "nucleo.sqlite3"
    contrasena = secrets.token_urlsafe(24)
    monkeypatch.setenv(VARIABLE, contrasena)
    argumentos = ["--usuario", "op", "--base-de-datos", str(base)]

    assert crear_usuario.main([*argumentos, "--rol", "operador", "--nombre", "Operador", "--contrasena-desde-variable", VARIABLE]) == 0
    # El mismo usuario otra vez se rechaza.
    assert crear_usuario.main([*argumentos, "--rol", "operador", "--nombre", "Operador", "--contrasena-desde-variable", VARIABLE]) == 1

    servicio = crear_servicio(crear_repositorio(base), generar_clave_privada())
    assert servicio.ingresar("op", contrasena).usuario.rol is Rol.OPERADOR

    nueva = secrets.token_urlsafe(24)
    monkeypatch.setenv(VARIABLE, nueva)
    assert restablecer_contrasena.main([*argumentos, "--contrasena-desde-variable", VARIABLE]) == 0
    with pytest.raises(CredencialesInvalidas):
        servicio.ingresar("op", contrasena)
    assert servicio.ingresar("op", nueva).usuario.usuario == "op"

    assert desactivar_usuario.main(argumentos) == 0
    with pytest.raises(CredencialesInvalidas):
        servicio.ingresar("op", nueva)


def test_la_herramienta_rechaza_una_variable_vacia_o_una_contrasena_corta(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "nucleo.sqlite3"
    argumentos = ["--usuario", "op", "--rol", "operador", "--nombre", "Operador", "--base-de-datos", str(base)]
    monkeypatch.delenv(VARIABLE, raising=False)
    assert crear_usuario.main([*argumentos, "--contrasena-desde-variable", VARIABLE]) == 1
    monkeypatch.setenv(VARIABLE, "corta")
    assert crear_usuario.main([*argumentos, "--contrasena-desde-variable", VARIABLE]) == 1


# --- Ubicacion de las claves y rutas del servidor (2026-10-20B, A1) ---------


def test_sin_variable_las_claves_van_a_la_carpeta_local_del_usuario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    from hoppit.frontera.identidad.puerto import VARIABLE_DE_ENTORNO_DE_CLAVES, directorio_de_claves

    monkeypatch.delenv(VARIABLE_DE_ENTORNO_DE_CLAVES, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    directorio = directorio_de_claves()
    assert directorio.is_absolute()
    assert directorio.parts[-2:] == ("hoppit", "claves")
    assert str(tmp_path) in str(directorio)
    assert "datos" not in directorio.parts


def test_la_variable_de_entorno_tiene_prioridad(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    from hoppit.frontera.identidad.puerto import VARIABLE_DE_ENTORNO_DE_CLAVES, directorio_de_claves

    monkeypatch.setenv(VARIABLE_DE_ENTORNO_DE_CLAVES, str(tmp_path / "otras"))
    assert directorio_de_claves() == tmp_path / "otras"


def test_las_rutas_del_servidor_no_dependen_del_directorio_de_trabajo() -> None:
    from tools import servir_api

    raiz = pathlib.Path(servir_api.__file__).resolve().parents[1]
    for ruta in (servir_api.BASE_PREDETERMINADA, servir_api.DIRECTORIO_INSTANCIA, servir_api.TABLA_DE_INTERVALOS_VIGENTE):
        assert ruta.is_absolute()
        assert raiz in ruta.parents

