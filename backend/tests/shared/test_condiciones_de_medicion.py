"""La linea de comandos de tools/condiciones_de_medicion.py (sesion
2026-10-13F, R3): un argumento que empieza con `-` no se toma como ruta."""
from __future__ import annotations

import pathlib

import pytest

from tools.condiciones_de_medicion import main


@pytest.mark.parametrize(("argumento", "codigo"), [("--help", 0), ("-h", 0), ("--otra", 2)])
def test_una_opcion_no_se_toma_como_ruta_ni_escribe_archivos(
    argumento: str,
    codigo: int,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as salida:
        main([argumento])

    assert salida.value.code == codigo
    assert list(tmp_path.iterdir()) == []
    if codigo == 0:
        assert "medicion" in capsys.readouterr().out


def test_sin_argumentos_es_un_error_de_uso(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as salida:
        main([])

    assert salida.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_una_ruta_escribe_las_condiciones_junto_a_la_medicion(tmp_path: pathlib.Path) -> None:
    medicion = tmp_path / "medicion.json"
    medicion.write_text("{}", encoding="utf-8")

    assert main([str(medicion)]) == 0

    assert (tmp_path / "medicion.json.condiciones.json").is_file()
