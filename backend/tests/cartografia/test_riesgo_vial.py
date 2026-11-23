"""Capa opcional de riesgo vial (D25, encargo 2026-11-23A).

Sobre el grafo de San Isidro con una tabla de riesgo sintetica (la del
producto es de CABA y no se carga en San Isidro, porque su sidecar declara el
grafo de CABA): con mu = 0 nada cambia; con mu > 0 el camino entre dos puntos
esquiva un arco de riesgo artificialmente alto, y la distancia que se informa
es la del camino elegido.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import (
    ConfiguracionDeRiesgoInvalida,
    GrafoVialAMBA,
    ParametrosDeRiesgoVial,
    clave_del_arco,
)

RIESGO_ARTIFICIAL = 1_000.0


@pytest.fixture(scope="module")
def base() -> GrafoVialAMBA:
    g = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=0.0, tabla=None))
    g.construir()
    return g


@pytest.fixture(scope="module")
def caso(base: GrafoVialAMBA) -> tuple[int, int, int, int, float]:
    """Un par (origen, destino) cuyo camino minimo tiene al menos 3 arcos, el
    arco del medio (a, b) y su largo."""
    nodos = base.nodos_de_mayor_componente_fuerte()
    for origen in nodos[:40]:
        for destino in nodos[-40:]:
            camino = base.matriz_de_costos([origen, destino]).camino(origen, destino)
            if camino.cantidad_de_arcos >= 3:
                medio = camino.arcos[camino.cantidad_de_arcos // 2]
                return origen, destino, medio.origen, medio.destino, medio.distancia_m
    raise AssertionError("no hay par con camino de 3 arcos")


def _tabla(directorio: Path, base: GrafoVialAMBA, arco: str, grafo_sha256: str | None = None) -> Path:
    tabla = directorio / "riesgo.csv"
    tabla.write_text(f"arco,riesgo_hechos_por_anio\n{arco},{RIESGO_ARTIFICIAL}\n", encoding="utf-8")
    Path(f"{tabla}.procedencia.json").write_text(json.dumps({
        "sha256": hashlib.sha256(tabla.read_bytes()).hexdigest(),
        "grafo_sha256": grafo_sha256 or base.artefacto().sha256}), encoding="utf-8")
    return tabla


def test_con_mu_0_la_matriz_y_el_camino_no_cambian(base, caso, tmp_path) -> None:  # type: ignore[no-untyped-def]
    origen, destino, a, b, largo = caso
    tabla = _tabla(tmp_path, base, clave_del_arco(a, b, largo))
    con_tabla = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=0.0, tabla=tabla))
    con_tabla.construir()
    assert con_tabla.capa_de_riesgo_vial() is not None
    nodos = [origen, destino, *base.nodos_de_mayor_componente_fuerte()[100:105]]
    assert con_tabla.matriz_de_costos(nodos).como_matriz() == base.matriz_de_costos(nodos).como_matriz()
    assert (con_tabla.matriz_de_costos(nodos).camino(origen, destino)
            == base.matriz_de_costos(nodos).camino(origen, destino))
    # La exposicion se informa igual: el camino pasa por el arco riesgoso.
    exposicion = con_tabla.exposicion_al_riesgo_vial([origen, destino])
    assert exposicion is not None and exposicion.hechos_por_anio == pytest.approx(RIESGO_ARTIFICIAL)
    assert exposicion.mu == 0.0 and base.exposicion_al_riesgo_vial([origen, destino]) is None


def test_con_mu_positivo_el_camino_esquiva_el_arco_riesgoso(base, caso, tmp_path) -> None:  # type: ignore[no-untyped-def]
    origen, destino, a, b, largo = caso
    tabla = _tabla(tmp_path, base, clave_del_arco(a, b, largo))
    con_riesgo = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=1.0, tabla=tabla))
    con_riesgo.construir()
    antes = base.matriz_de_costos([origen, destino]).camino(origen, destino)
    despues = con_riesgo.matriz_de_costos([origen, destino]).camino(origen, destino)
    assert (a, b) in [(x.origen, x.destino) for x in antes.arcos]
    assert (a, b) not in [(x.origen, x.destino) for x in despues.arcos]
    # La distancia informada es la del camino elegido, no el costo generalizado.
    assert despues.distancia_m == pytest.approx(sum(x.distancia_m for x in despues.arcos))
    assert despues.distancia_m >= antes.distancia_m
    assert despues.costo_m == pytest.approx(despues.distancia_m)  # los demas arcos tienen riesgo 0
    assert despues.costo_m < antes.distancia_m + RIESGO_ARTIFICIAL
    matriz = con_riesgo.matriz_de_costos([origen, destino])
    assert matriz.como_matriz()[0][1] == pytest.approx(despues.costo_m)
    assert matriz.distancia_m(origen, destino) == pytest.approx(despues.distancia_m)
    # El trazado (lo que se dibuja y la distancia de la Ruta) sigue el camino elegido.
    assert con_riesgo.trazado([origen, destino]).distancia_m == pytest.approx(despues.distancia_m)
    exposicion = con_riesgo.exposicion_al_riesgo_vial([origen, destino])
    assert exposicion is not None and exposicion.hechos_por_anio == 0.0


def test_configuracion_invalida_y_tabla_de_otro_grafo(base, caso, tmp_path) -> None:  # type: ignore[no-untyped-def]
    _, _, a, b, largo = caso
    with pytest.raises(ConfiguracionDeRiesgoInvalida):
        GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=1.0, tabla=None)).construir()
    with pytest.raises(ConfiguracionDeRiesgoInvalida):
        GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=-1.0, tabla=None)).construir()
    ajena = _tabla(tmp_path, base, clave_del_arco(a, b, largo), grafo_sha256="0" * 64)
    g = GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=0.0, tabla=ajena))
    g.construir()
    assert g.capa_de_riesgo_vial() is None
    with pytest.raises(ConfiguracionDeRiesgoInvalida):
        GrafoVialAMBA(GRAFO_PREDETERMINADO, ParametrosDeRiesgoVial(mu=1.0, tabla=ajena)).construir()


def test_la_tabla_del_producto_es_de_caba_y_tiene_sidecar() -> None:
    """Desde el encargo 2026-11-23B (bloque R) la tabla por omision es la del
    modelo elegido con protocolo; la del 23A sigue versionada con su sidecar."""
    from hoppit.cartografia._riesgo_vial import DIRECTORIO_RIESGO_VIAL, TABLA_PREDETERMINADA

    assert TABLA_PREDETERMINADA.name == "riesgo_por_arco_caba_2026-11-23B.csv"
    sidecar = json.loads(Path(f"{TABLA_PREDETERMINADA}.procedencia.json").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == hashlib.sha256(TABLA_PREDETERMINADA.read_bytes()).hexdigest()
    assert sidecar["grafo"] == "grafo_ciudad_autonoma_de_buenos_aires_2026-10-14.graphml"
    assert sidecar["modelo"]["adoptado"] == sidecar["adopcion"]["adoptado"]
    assert sidecar["modelo"]["adoptado"] in ("a", "b", "c", "d", "e")
    assert sidecar["datos"]["anios_usados"] == [2019, 2022, 2023, 2024, 2025]
    artefacto = DIRECTORIO_RIESGO_VIAL / sidecar["modelo"]["artefacto"]["archivo"]
    assert sidecar["modelo"]["artefacto"]["sha256"] == hashlib.sha256(artefacto.read_bytes()).hexdigest()
    anterior = DIRECTORIO_RIESGO_VIAL / "riesgo_por_arco_caba_2026-11-23A.csv"
    sidecar_anterior = json.loads(Path(f"{anterior}.procedencia.json").read_text(encoding="utf-8"))
    assert sidecar_anterior["sha256"] == hashlib.sha256(anterior.read_bytes()).hexdigest()
    assert sidecar_anterior["grafo_sha256"] == sidecar["grafo_sha256"]
