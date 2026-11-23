"""El grafo del producto se indica al arrancar, sin valor por omision: CABA o
San Isidro, por argumento o por variable (B-220, puntos 4 y 7; B-244, punto
2; 2026-10-27B, K; 2026-10-27C, F1).

San Isidro sigue siendo el grafo de la instancia canonica, de la huella y de
las mediciones; sobre CABA el sistema arranca con la Flota de demostracion
declarada y sin Ordenes sorteadas.
"""
from __future__ import annotations

import http
import logging
import pathlib

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import ContextoDeLaApi
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.escenario_demostracion import (
    MATRICULAS,
    capacidad_por_vehiculo,
    construir_ordenes,
    flota_de_demostracion,
)
from tools.recrear_instancia import recrear
from tools import recrear_instancia, servir_api
from tools.servir_api import (
    ARTEFACTO_DEPOSITO_CABA,
    ARTEFACTO_DEPOSITO_PREDETERMINADO,
    GRAFO_CABA,
    GRAFO_DE_LA_CIUDAD,
    GRAFO_SAN_ISIDRO,
    GRAFOS_DEL_PRODUCTO,
    VARIABLE_DEL_GRAFO,
    BaseDeOtroGrafo,
    MENSAJE_GRAFO_NO_INDICADO,
    GrafoDesconocido,
    GrafoNoIndicado,
    construir_contexto,
    grafo_elegido,
)

# El nodo del Deposito de CABA, el mas cercano al predio way/1347897760 dentro
# de la componente mayor: el mismo de la etapa 1 del DQN.
NODO_DEL_DEPOSITO_CABA = 3647513546
SHA256_DEL_GRAFO_CABA = "03c7b8ff183132324067a63a91c297b259d41be74ba5a7a6b21ffd4d5d049f79"
LARGO_ABREVIADO = 12
# Pocas Ordenes, en nodos de la componente mayor elegidos por posicion fija:
# la prueba verifica que la planificacion anda sobre CABA, no mide nada.
POSICIONES_DE_LOS_DESTINOS = (100, 2000, 5000, 9000)
DEMANDA = 2


@pytest.fixture(scope="module")
def caba() -> ContextoDeLaApi:
    """Un solo armado sobre CABA para el modulo: cargar el grafo es lo caro."""
    return construir_contexto(
        base_de_datos=None, grafo=GRAFO_CABA, deposito=ARTEFACTO_DEPOSITO_CABA, clave_privada=clave_efimera()
    )


def test_sin_grafo_indicado_no_hay_eleccion_por_omision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VARIABLE_DEL_GRAFO, raising=False)
    with pytest.raises(GrafoNoIndicado, match="Indicá el grafo: --grafo caba o --grafo san_isidro"):
        grafo_elegido()
    assert grafo_elegido(GRAFO_DE_LA_CIUDAD).grafo == GRAFO_CABA


@pytest.mark.parametrize("comando", [servir_api, recrear_instancia], ids=["servir_api", "recrear_instancia"])
def test_las_herramientas_sin_grafo_terminan_con_codigo_1_y_lo_dicen(
    comando: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Termina antes de cargar nada: la base indicada no llega a crearse."""
    monkeypatch.delenv(VARIABLE_DEL_GRAFO, raising=False)
    base = tmp_path / "sin_grafo.sqlite3"
    with caplog.at_level(logging.ERROR):
        assert comando.main(["--base-de-datos", str(base)]) == 1  # type: ignore[attr-defined]
    assert MENSAJE_GRAFO_NO_INDICADO in caplog.text
    assert not base.exists()


def test_construir_contexto_exige_el_grafo_y_el_deposito() -> None:
    with pytest.raises(TypeError, match="grafo"):
        construir_contexto(base_de_datos=None, clave_privada=clave_efimera())  # type: ignore[call-arg]


def test_san_isidro_se_elige_por_variable_o_por_argumento(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE_DEL_GRAFO, GRAFO_SAN_ISIDRO)
    assert grafo_elegido().grafo == GRAFO_PREDETERMINADO
    assert grafo_elegido(GRAFO_DE_LA_CIUDAD).clave == GRAFO_DE_LA_CIUDAD
    assert grafo_elegido(GRAFO_SAN_ISIDRO).deposito == ARTEFACTO_DEPOSITO_PREDETERMINADO


def test_un_grafo_que_no_es_del_producto_se_rechaza(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE_DEL_GRAFO, "tigre")
    with pytest.raises(GrafoDesconocido):
        grafo_elegido()


def test_sobre_caba_arranca_con_la_flota_de_demostracion_y_sin_ordenes(caba: ContextoDeLaApi) -> None:
    assert caba.flota == flota_de_demostracion()
    assert not caba.ordenes.listar()
    assert caba.deposito == NODO_DEL_DEPOSITO_CABA


def test_la_flota_declarada_es_la_de_la_instancia_canonica() -> None:
    san_isidro = construir_contexto(
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    derivada = capacidad_por_vehiculo(
        construir_ordenes(san_isidro.cartografia.nodos_de_mayor_componente_fuerte())
    )
    assert flota_de_demostracion() == derivada == san_isidro.flota
    assert len(san_isidro.ordenes.listar()) == 20
    estado = _estado(san_isidro)
    assert estado["grafo"]["archivo"] == GRAFO_PREDETERMINADO.name
    assert estado["grafo"]["verificado"] is False


def test_el_escenario_sorteado_no_se_carga_sobre_caba(caba: ContextoDeLaApi) -> None:
    with pytest.raises(ValueError, match="instancia canonica"):
        construir_contexto(
            base_de_datos=None,
            con_escenario=True,
            grafo=GRAFO_CABA,
            deposito=ARTEFACTO_DEPOSITO_CABA,
            cartografia=caba.cartografia,
            clave_privada=clave_efimera(),
        )


def test_el_estado_publica_el_grafo_activo_verificado(caba: ContextoDeLaApi) -> None:
    estado = _estado(caba)
    assert estado["grafo"] == {
        "archivo": GRAFO_CABA.name,
        "ambito": "Ciudad Autónoma de Buenos Aires",
        "sha256": SHA256_DEL_GRAFO_CABA[:LARGO_ABREVIADO],
        "verificado": True,
    }


def test_el_deposito_publicado_es_el_de_caba(caba: ContextoDeLaApi) -> None:
    cliente = cliente_autenticado(caba)
    respuesta = cliente.get("/v1/depositos")
    assert respuesta.status_code == http.HTTPStatus.OK
    assert respuesta.json()["depositos"][0]["punto"]["nodo"] == NODO_DEL_DEPOSITO_CABA


def test_alta_y_planificacion_sobre_caba(caba: ContextoDeLaApi) -> None:
    """Alta por coordenadas, que ancla a la componente mayor de CABA, y un
    plan con un Vehiculo de la Flota declarada."""
    cliente = cliente_autenticado(caba)
    componente = sorted(caba.cartografia.nodos_de_mayor_componente_fuerte())
    ids = []
    for posicion in POSICIONES_DE_LOS_DESTINOS:
        nodo = caba.cartografia.coordenadas(componente[posicion])
        alta = cliente.post("/v1/ordenes", json={"lat": nodo.lat, "lon": nodo.lon, "demanda": DEMANDA})
        assert alta.status_code == http.HTTPStatus.CREATED, alta.text
        assert alta.json()["destino"]["nodo"] in set(componente)
        ids.append(alta.json()["id"])
    matricula = str(MATRICULAS[0])
    plan = cliente.post(
        "/v1/planificaciones",
        json={"ordenes": ids, "vehiculos": {matricula: caba.flota[MATRICULAS[0]]}},
    )
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    rutas = plan.json()["rutas"]
    assert len(rutas) == 1
    assert plan.json()["ordenes_no_planificadas"] == []


def test_una_base_de_san_isidro_no_se_sirve_sobre_caba(
    tmp_path: pathlib.Path, caba: ContextoDeLaApi
) -> None:
    base = tmp_path / "san_isidro.sqlite3"
    construir_contexto(
        base_de_datos=base,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    with pytest.raises(BaseDeOtroGrafo, match="recrear_instancia.py --grafo"):
        construir_contexto(
            base_de_datos=base,
            grafo=GRAFO_CABA,
            deposito=ARTEFACTO_DEPOSITO_CABA,
            cartografia=caba.cartografia,
            clave_privada=clave_efimera(),
        )


def test_recrear_sobre_caba_deja_la_flota_y_ninguna_orden(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "caba.sqlite3"
    assert recrear(base, grafo=GRAFOS_DEL_PRODUCTO[GRAFO_DE_LA_CIUDAD]) == 0
    recreada = construir_contexto(
        base_de_datos=base, grafo=GRAFO_CABA, deposito=ARTEFACTO_DEPOSITO_CABA, clave_privada=clave_efimera()
    )
    assert not recreada.ordenes.listar()
    assert recreada.flota == flota_de_demostracion()


def _estado(contexto: ContextoDeLaApi) -> dict:
    """El cuerpo de `/estado`, que no exige sesion."""
    from hoppit.frontera.api_rest.puerto import crear_app

    respuesta = TestClient(crear_app(contexto)).get("/estado")
    assert respuesta.status_code == http.HTTPStatus.OK
    return respuesta.json()
