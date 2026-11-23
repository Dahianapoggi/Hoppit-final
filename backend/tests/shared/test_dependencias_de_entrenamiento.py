"""Las bibliotecas de entrenamiento (scikit-learn, scipy y PyTorch) se
declaran solo como extra opcional `entrenamiento` y ningun modulo de
`src/hoppit` las importa (sesion 2026-10-19A, regla 9; DAT-38 punto 8,
B-233 punto 6). Analisis sintactico de cada modulo, sin importarlos."""
from __future__ import annotations

import ast
import pathlib
import tomllib

RAIZ = pathlib.Path(__file__).resolve().parents[2]
FUENTES = RAIZ / "src" / "hoppit"
DE_ENTRENAMIENTO = {"sklearn", "scipy", "torch"}
DISTRIBUCIONES = {"scikit-learn", "scipy", "torch"}


def _modulos_importados(ruta: pathlib.Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            nombres.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module and nodo.level == 0:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def test_ningun_modulo_de_src_importa_bibliotecas_de_entrenamiento() -> None:
    modulos = sorted(FUENTES.rglob("*.py"))
    assert modulos
    infractores = {
        str(ruta.relative_to(RAIZ)): sorted(_modulos_importados(ruta) & DE_ENTRENAMIENTO)
        for ruta in modulos
        if _modulos_importados(ruta) & DE_ENTRENAMIENTO
    }
    assert infractores == {}


def test_se_declaran_solo_en_el_extra_de_entrenamiento() -> None:
    proyecto = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    def nombres(requisitos: list[str]) -> set[str]:
        return {r.split(">")[0].split("=")[0].split("<")[0].strip().lower() for r in requisitos}

    assert nombres(proyecto["dependencies"]).isdisjoint(DISTRIBUCIONES)
    extras = proyecto["optional-dependencies"]
    assert nombres(extras["entrenamiento"]) == DISTRIBUCIONES
    for nombre, requisitos in extras.items():
        if nombre != "entrenamiento":
            assert nombres(requisitos).isdisjoint(DISTRIBUCIONES), nombre
