"""Logica pura de `tools/candidatos_deposito_desde_extracto.py` (sesion
2026-10-13F, versionada en 2026-10-15A, bloque S): reconocimiento de comunas
y partidos limitrofes por el nombre del limite. Sin extracto ni pyosmium."""
from __future__ import annotations

from tools.candidatos_deposito_desde_extracto import es_comuna, partido_del_limite, sin_tildes


def test_sin_tildes() -> None:
    assert sin_tildes("Avenida General Paz") == "avenida general paz"
    assert sin_tildes("Lanús") == "lanus"


def test_comunas_por_nombre() -> None:
    assert es_comuna("Comuna 1")
    assert es_comuna("Comuna 15")
    assert not es_comuna("Comuna 150")
    assert not es_comuna("Comuna de San Isidro")


def test_partidos_limitrofes_por_nombre() -> None:
    assert partido_del_limite("Partido de Lanús") == "Lanús"
    assert partido_del_limite("partido de lanus") == "Lanús"
    assert partido_del_limite("Partido de General San Martín") == "General San Martín"
    assert partido_del_limite("Partido de San Isidro") is None
    assert partido_del_limite("Lanús") is None
