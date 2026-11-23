"""Busqueda por direccion y coordenada del alta (RB-10; B-259, punto 6;
2026-10-27C, G). El geocodificador es un simulado armado aca: ninguna prueba
sale a la red ni consulta Georef."""
from __future__ import annotations

import http
import pathlib

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import (
    ATRIBUCION_DE_GEOREF,
    CandidatoDeDireccion,
    GeocodificadorNoResponde,
    GrafoVialAMBA,
    LimiteDeConsultasAlcanzado,
    crear_servicio as crear_cartografia,
)
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from hoppit.frontera.identidad.puerto import Rol
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

# Un punto de prueba dentro de San Isidro.
PUNTO_DE_PRUEBA = (-34.4795, -58.5180)
SEGUNDOS_DE_ESPERA_DE_PRUEBA = 7


class GeocodificadorSimulado:
    """Devuelve un candidato fijo o levanta lo que se le pida. Solo pruebas."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.textos: list[str] = []

    def buscar(self, texto: str) -> tuple[CandidatoDeDireccion, ...]:
        self.textos.append(texto)
        if self.error is not None:
            raise self.error
        return (CandidatoDeDireccion("DIRECCION DE PRUEBA 123, San Isidro", *PUNTO_DE_PRUEBA),)


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def _contexto(grafo: GrafoVialAMBA, geocodificador: object = None, base: pathlib.Path | None = None) -> ContextoDeLaApi:
    return construir_contexto(
        base_de_datos=base,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
        geocodificador=geocodificador,  # type: ignore[arg-type]
    )


def test_los_candidatos_salen_anclados_a_la_componente_mayor_con_la_atribucion(grafo: GrafoVialAMBA) -> None:
    simulado = GeocodificadorSimulado()
    cliente = cliente_autenticado(_contexto(grafo, simulado))
    respuesta = cliente.get("/v1/direcciones", params={"texto": "Direccion de prueba 123"})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["atribucion"] == ATRIBUCION_DE_GEOREF == "Servicio Georef – argentina.gob.ar/georef"
    (candidato,) = cuerpo["candidatos"]
    assert candidato["consultado"] == {"lat": PUNTO_DE_PRUEBA[0], "lon": PUNTO_DE_PRUEBA[1]}
    assert candidato["nodo"]["nodo"] in grafo.nodos_de_mayor_componente_fuerte()
    assert candidato["distancia_m"] >= 0 and candidato["es_el_deposito"] is False
    assert simulado.textos == ["Direccion de prueba 123"]


@pytest.mark.parametrize(
    ("geocodificador", "estado", "codigo"),
    [
        (None, http.HTTPStatus.SERVICE_UNAVAILABLE, "geocodificador_no_disponible"),
        (GeocodificadorSimulado(GeocodificadorNoResponde("sin red")), http.HTTPStatus.SERVICE_UNAVAILABLE, "georef_no_responde"),
        (
            GeocodificadorSimulado(LimiteDeConsultasAlcanzado("limite", SEGUNDOS_DE_ESPERA_DE_PRUEBA)),
            http.HTTPStatus.TOO_MANY_REQUESTS,
            "limite_de_georef",
        ),
    ],
    ids=["sin_geocodificador", "georef_no_responde", "limite"],
)
def test_los_errores_declarados(grafo: GrafoVialAMBA, geocodificador: object, estado: int, codigo: str) -> None:
    respuesta = cliente_autenticado(_contexto(grafo, geocodificador)).get("/v1/direcciones", params={"texto": "Calle 1"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo)
    if codigo == "limite_de_georef":
        assert respuesta.json()["detalle"] == {"segundos_de_espera": SEGUNDOS_DE_ESPERA_DE_PRUEBA}


@pytest.mark.parametrize("texto", ["", "   ", "x" * 201], ids=["vacio", "espacios", "largo"])
def test_un_texto_vacio_o_largo_es_peticion_mal_formada(grafo: GrafoVialAMBA, texto: str) -> None:
    simulado = GeocodificadorSimulado()
    respuesta = cliente_autenticado(_contexto(grafo, simulado)).get("/v1/direcciones", params={"texto": texto})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.UNPROCESSABLE_ENTITY, "peticion_mal_formada")
    assert simulado.textos == []


def test_el_transportista_no_busca_direcciones(grafo: GrafoVialAMBA) -> None:
    respuesta = cliente_autenticado(_contexto(grafo, GeocodificadorSimulado()), Rol.TRANSPORTISTA).get(
        "/v1/direcciones", params={"texto": "Calle 1"}
    )
    assert respuesta.status_code == http.HTTPStatus.FORBIDDEN


def test_el_alta_guarda_la_coordenada_y_el_nodo_y_persisten(grafo: GrafoVialAMBA, tmp_path: pathlib.Path) -> None:
    base = tmp_path / "alta.sqlite3"
    cliente = cliente_autenticado(_contexto(grafo, base=base))
    alta = cliente.post("/v1/ordenes", json={"lat": PUNTO_DE_PRUEBA[0], "lon": PUNTO_DE_PRUEBA[1], "demanda": 2})
    assert alta.status_code == http.HTTPStatus.CREATED, alta.text
    orden = alta.json()
    assert orden["punto_del_alta"] == {"lat": PUNTO_DE_PRUEBA[0], "lon": PUNTO_DE_PRUEBA[1]}
    otra_vez = cliente_autenticado(_contexto(grafo, base=base)).get(f"/v1/ordenes/{orden['id']}")
    assert otra_vez.json()["punto_del_alta"] == orden["punto_del_alta"]
    assert otra_vez.json()["destino"]["nodo"] == orden["destino"]["nodo"]


def test_las_ordenes_de_la_instancia_no_tienen_coordenada_del_alta(grafo: GrafoVialAMBA) -> None:
    ordenes = cliente_autenticado(_contexto(grafo)).get("/v1/ordenes").json()
    assert ordenes and all(o["punto_del_alta"] is None for o in ordenes)
