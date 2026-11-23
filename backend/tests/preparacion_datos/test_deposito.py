"""Verifica la ubicacion del Deposito (B-59).

El criterio de eleccion se prueba en aislamiento, sin tocar la red; el
artefacto ya generado se verifica contra el grafo real, que es donde importa
que el nodo sea utilizable.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import crear_servicio
from hoppit.preparacion_datos.puerto import (
    CRITERIO_DEL_DEPOSITO,
    TAGS_ACTIVIDAD_LOGISTICA,
    PoligonoUsoSuelo,
    elegir_poligono_de_deposito,
    leer_deposito,
    seleccionar_deposito,
)
from hoppit.shared.errores import EntidadNoEncontrada
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO

DIRECTORIO_INSTANCIA = pathlib.Path("datos/instancia")
SUFIJO_SIDECAR = ".procedencia.json"

# El predio decidido para el Deposito: DPD, unico operador de paqueteria pura
# entre los `industrial=logistics` de San Isidro.
PREDIO_DEL_DEPOSITO = "way/1525656127"


def _predio(id_osm: int, area_m2: float) -> PoligonoUsoSuelo:
    return PoligonoUsoSuelo(
        element_osm="way",
        id_osm=id_osm,
        nombre=None,
        clave="industrial",
        valor="logistics",
        area_m2=area_m2,
        lat=-34.46,
        lon=-58.52,
    )


def test_se_elige_el_predio_indicado_por_su_referencia_osm() -> None:
    """La eleccion entre operadores de paqueteria es decision de proyecto: el
    codigo la recibe como parametro, no la infiere de la superficie ni de
    ningun otro atributo que OSM traiga."""
    elegido = elegir_poligono_de_deposito(
        (_predio(1, 100.0), _predio(2, 900.0), _predio(3, 500.0)), "way/3"
    )
    assert elegido.id_osm == 3


def test_la_eleccion_no_depende_del_orden_de_overpass() -> None:
    """NFR-07: el resultado no puede cambiar con el orden en que Overpass
    devuelva los elementos."""
    a, b = _predio(77, 500.0), _predio(11, 900.0)
    assert elegir_poligono_de_deposito((a, b), "way/11").id_osm == 11
    assert elegir_poligono_de_deposito((b, a), "way/11").id_osm == 11


def test_un_predio_que_ya_no_lleva_la_etiqueta_falla_explicito() -> None:
    """Si OSM deja de devolver el predio decidido, el Deposito no se ubica en
    otro por su cuenta: el criterio registrado dejaria de sostenerse."""
    with pytest.raises(EntidadNoEncontrada) as error:
        elegir_poligono_de_deposito((_predio(1, 100.0),), PREDIO_DEL_DEPOSITO)
    assert PREDIO_DEL_DEPOSITO in str(error.value)


def test_sin_predios_no_se_inventa_una_ubicacion() -> None:
    with pytest.raises(EntidadNoEncontrada):
        elegir_poligono_de_deposito((), PREDIO_DEL_DEPOSITO)


def test_la_consulta_discrimina_actividad_logistica_y_no_uso_de_suelo() -> None:
    """El criterio viejo -- `landuse=industrial` -- era reproducible pero no
    distinguia logistica de cualquier otro uso industrial."""
    assert TAGS_ACTIVIDAD_LOGISTICA == {"industrial": ["logistics"]}


def test_el_artefacto_del_deposito_existe_y_es_legible() -> None:
    ruta = seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    assert leer_deposito(ruta) > 0


def test_la_procedencia_cita_el_predio_concreto_de_osm() -> None:
    """B-59 pide la referencia OSM registrada, no solo unas coordenadas."""
    ruta = seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    sidecar = ruta.with_suffix(ruta.suffix + SUFIJO_SIDECAR)
    registro = json.loads(sidecar.read_text(encoding="utf-8"))

    parametros = registro["parametros"]
    assert registro["artefacto"] == "deposito"
    assert parametros["predio_referencia_osm"] == PREDIO_DEL_DEPOSITO
    assert parametros["predio_clave"] == "industrial"
    assert parametros["predio_valor"] == "logistics"
    assert parametros["nodo"] == leer_deposito(ruta)


def test_el_deposito_esta_en_la_componente_fuerte_mayor() -> None:
    """Un Deposito fuera de la componente mayor deja pares sin camino y
    vuelve infactible el CVRP: es la precondicion de toda la instancia."""
    nodo = leer_deposito(
        seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    )

    grafo = crear_servicio(GRAFO_PREDETERMINADO)
    grafo.construir()
    assert nodo in set(grafo.nodos_de_mayor_componente_fuerte())


def test_el_artefacto_declara_el_criterio_y_el_predio() -> None:
    """El artefacto tiene que poder explicarse solo: sin esto, quien lo lea no
    sabria por que el Deposito esta donde esta."""
    ruta = seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    registro = json.loads(ruta.read_text(encoding="utf-8"))

    assert registro["criterio"] == CRITERIO_DEL_DEPOSITO
    assert registro["predio_referencia_osm"] == PREDIO_DEL_DEPOSITO
    assert registro["predio_nombre"]


def test_el_artefacto_ya_no_declara_la_ubicacion_como_hipotetica() -> None:
    """Dejo de serlo: el predio es un centro de paqueteria relevado en OSM, no
    una zona industrial elegida por superficie."""
    ruta = seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    registro = json.loads(ruta.read_text(encoding="utf-8"))
    sidecar = ruta.with_suffix(ruta.suffix + SUFIJO_SIDECAR)
    parametros = json.loads(sidecar.read_text(encoding="utf-8"))["parametros"]

    assert "caracter" not in registro
    assert "caracter" not in parametros
    assert "hipotet" not in CRITERIO_DEL_DEPOSITO.lower()


def test_la_procedencia_registra_el_criterio_vigente() -> None:
    ruta = seleccionar_deposito(DIRECTORIO_INSTANCIA, ARTEFACTO_DEPOSITO_PREDETERMINADO)
    sidecar = ruta.with_suffix(ruta.suffix + SUFIJO_SIDECAR)
    parametros = json.loads(sidecar.read_text(encoding="utf-8"))["parametros"]

    assert parametros["criterio"] == CRITERIO_DEL_DEPOSITO
    assert parametros["tags_consultados"] == TAGS_ACTIVIDAD_LOGISTICA


def test_el_criterio_nombra_la_etiqueta_que_lo_hace_verificable() -> None:
    """La redaccion es parte del contrato: sin la etiqueta nombrada, el
    criterio no se puede contrastar contra OSM."""
    minuscula = CRITERIO_DEL_DEPOSITO.lower()
    assert "industrial=logistics" in minuscula
    assert "paqueteria" in minuscula
