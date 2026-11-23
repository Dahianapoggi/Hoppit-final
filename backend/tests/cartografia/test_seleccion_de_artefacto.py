"""Seleccion explicita del grafo vial y del Deposito (B-209, punto 6).

Cartografia cargaba el ultimo `grafo_*.graphml` de datos/grafos/ en orden
lexicografico: con los grafos de otros partidos en esa carpeta, el producto
paso a cargar Tigre sin que nadie lo pidiera. Ahora el ensamblado indica el
artefacto; sin indicacion, solo vale si hay un unico candidato.

Las carpetas de prueba se arman con copias de artefactos reales: el grafo de
San Isidro, el de Lanus y el Deposito de San Isidro. El segundo Deposito es
una copia del mismo archivo con otro nombre, porque solo hay uno: la
seleccion mira los nombres, no el contenido.
"""
from __future__ import annotations

import pathlib
import shutil

import osmnx as ox
import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO, ensamblar
from hoppit.cartografia.puerto import GrafoVialAMBA, seleccionar_artefacto
from hoppit.preparacion_datos.puerto import leer_deposito, seleccionar_deposito
from hoppit.shared.errores import EntidadNoEncontrada, SeleccionDeArtefactoAmbigua

DIRECTORIO_GRAFOS_POR_PARTIDO = pathlib.Path("datos/grafos_por_partido")
GRAFO_DE_LANUS = DIRECTORIO_GRAFOS_POR_PARTIDO / "grafo_lanus_2026-10-12.graphml"
DIRECTORIO_INSTANCIA = pathlib.Path("datos/instancia")
DEPOSITO_DE_SAN_ISIDRO = "deposito_san_isidro_2026-08-17.json"
# Nombre que queda despues del real en orden lexicografico: si volviera el
# respaldo por nombre, seria el elegido.
SEGUNDO_DEPOSITO = "deposito_san_isidro_2026-12-31.json"


@pytest.fixture(scope="module")
def carpeta_con_dos_grafos(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    carpeta = tmp_path_factory.mktemp("grafos")
    shutil.copy(GRAFO_PREDETERMINADO, carpeta / GRAFO_PREDETERMINADO.name)
    shutil.copy(GRAFO_DE_LANUS, carpeta / GRAFO_DE_LANUS.name)
    return carpeta


@pytest.fixture()
def carpeta_con_dos_depositos(tmp_path: pathlib.Path) -> pathlib.Path:
    shutil.copy(DIRECTORIO_INSTANCIA / DEPOSITO_DE_SAN_ISIDRO, tmp_path / DEPOSITO_DE_SAN_ISIDRO)
    shutil.copy(DIRECTORIO_INSTANCIA / DEPOSITO_DE_SAN_ISIDRO, tmp_path / SEGUNDO_DEPOSITO)
    return tmp_path


def test_el_ensamblado_indica_el_grafo_de_san_isidro() -> None:
    assert GRAFO_PREDETERMINADO.name == "grafo_san_isidro_2026-08-16.graphml"
    assert GRAFO_PREDETERMINADO.is_file()


def test_con_dos_grafos_se_carga_el_indicado(carpeta_con_dos_grafos: pathlib.Path) -> None:
    """El indicado es el que va primero por nombre (Lanus antes que San
    Isidro): el orden de la carpeta no interviene."""
    indicado = carpeta_con_dos_grafos / GRAFO_DE_LANUS.name
    assert seleccionar_artefacto(indicado, carpeta_con_dos_grafos) == indicado

    grafo = GrafoVialAMBA(indicado)
    grafo.construir()
    esperados = {int(nodo) for nodo in ox.load_graphml(indicado).nodes()}
    cargados = set(grafo.nodos_de_mayor_componente_fuerte())
    assert cargados and cargados <= esperados


def test_con_dos_grafos_y_sin_indicacion_falla(carpeta_con_dos_grafos: pathlib.Path) -> None:
    with pytest.raises(SeleccionDeArtefactoAmbigua, match="no se indico cual cargar"):
        seleccionar_artefacto(None, carpeta_con_dos_grafos)


def test_con_el_grafo_indicado_ausente_falla(carpeta_con_dos_grafos: pathlib.Path) -> None:
    ausente = carpeta_con_dos_grafos / "grafo_tigre_2026-10-12.graphml"
    with pytest.raises(EntidadNoEncontrada, match="No existe el artefacto de grafo indicado"):
        GrafoVialAMBA(ausente).construir()
    with pytest.raises(EntidadNoEncontrada, match="No existe el artefacto de grafo indicado"):
        ensamblar(ausente)


def test_con_dos_depositos_se_lee_el_indicado(carpeta_con_dos_depositos: pathlib.Path) -> None:
    ruta = seleccionar_deposito(carpeta_con_dos_depositos, DEPOSITO_DE_SAN_ISIDRO)
    assert ruta == carpeta_con_dos_depositos / DEPOSITO_DE_SAN_ISIDRO
    assert leer_deposito(ruta) > 0


def test_con_dos_depositos_y_sin_indicacion_falla(carpeta_con_dos_depositos: pathlib.Path) -> None:
    with pytest.raises(SeleccionDeArtefactoAmbigua, match="no se indico cual leer"):
        seleccionar_deposito(carpeta_con_dos_depositos)


def test_con_el_deposito_indicado_ausente_falla(carpeta_con_dos_depositos: pathlib.Path) -> None:
    with pytest.raises(EntidadNoEncontrada, match="No existe el artefacto de Deposito indicado"):
        seleccionar_deposito(carpeta_con_dos_depositos, "deposito_tigre_2026-10-12.json")


def test_el_sidecar_no_cuenta_como_candidato(tmp_path: pathlib.Path) -> None:
    shutil.copy(DIRECTORIO_INSTANCIA / DEPOSITO_DE_SAN_ISIDRO, tmp_path / DEPOSITO_DE_SAN_ISIDRO)
    sidecar = DEPOSITO_DE_SAN_ISIDRO + ".procedencia.json"
    shutil.copy(DIRECTORIO_INSTANCIA / sidecar, tmp_path / sidecar)
    assert seleccionar_deposito(tmp_path) == tmp_path / DEPOSITO_DE_SAN_ISIDRO


def _copia_con_sidecar(carpeta: pathlib.Path, sha256: str | None) -> pathlib.Path:
    """Copia del grafo de San Isidro con un sidecar que registra `sha256`."""
    import json

    copia = carpeta / GRAFO_PREDETERMINADO.name
    shutil.copy(GRAFO_PREDETERMINADO, copia)
    parametros = {} if sha256 is None else {"sha256": sha256}
    (carpeta / (copia.name + ".procedencia.json")).write_text(
        json.dumps({"artefacto": "grafo", "parametros": parametros}), encoding="utf-8"
    )
    return copia


def test_un_grafo_cuyo_sha256_registrado_coincide_se_carga_verificado(tmp_path: pathlib.Path) -> None:
    import hashlib

    registrado = hashlib.sha256(GRAFO_PREDETERMINADO.read_bytes()).hexdigest()
    grafo = GrafoVialAMBA(_copia_con_sidecar(tmp_path, registrado))
    grafo.construir()
    assert grafo.artefacto().verificado is True
    assert grafo.artefacto().sha256 == registrado


def test_un_grafo_cuyo_sha256_no_coincide_no_se_carga(tmp_path: pathlib.Path) -> None:
    from hoppit.cartografia.puerto import ArtefactoNoVerificado

    with pytest.raises(ArtefactoNoVerificado, match="no coincide"):
        GrafoVialAMBA(_copia_con_sidecar(tmp_path, "0" * 64)).construir()


def test_sin_sha256_registrado_se_carga_sin_verificar(tmp_path: pathlib.Path) -> None:
    grafo = GrafoVialAMBA(_copia_con_sidecar(tmp_path, None))
    grafo.construir()
    assert grafo.artefacto().verificado is False
    assert grafo.artefacto().archivo == GRAFO_PREDETERMINADO.name
