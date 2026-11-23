"""El ETA se guarda por identificador de Ruta (B-260, punto 5c; 2026-10-27C,
F3): la respuesta de `GET /v1/rutas/{id}` es la misma con memoria y sin
ella, y la memoria descarta la Ruta menos usada al llenarse."""
from __future__ import annotations

import http

import pytest

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.api_rest.puerto import CAPACIDAD_DE_MEMORIA_DE_ETA, ContextoDeLaApi, MemoriaDeEta
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


def test_la_memoria_calcula_una_vez_por_ruta_y_descarta_la_menos_usada() -> None:
    calculos: list[str] = []

    def calcular(ruta: str) -> str:
        calculos.append(ruta)
        return f"eta-{ruta}"

    memoria: MemoriaDeEta[str] = MemoriaDeEta(capacidad=2)
    assert memoria.obtener("a", lambda: calcular("a")) == "eta-a"
    assert memoria.obtener("b", lambda: calcular("b")) == "eta-b"
    assert memoria.obtener("a", lambda: calcular("a")) == "eta-a"  # "a" pasa a ser la mas usada
    memoria.obtener("c", lambda: calcular("c"))  # descarta "b"
    memoria.obtener("a", lambda: calcular("a"))
    memoria.obtener("b", lambda: calcular("b"))
    assert calculos == ["a", "b", "c", "b"]
    assert len(memoria) == 2


def test_con_capacidad_0_no_guarda_nada_y_una_negativa_se_rechaza() -> None:
    calculos: list[int] = []
    memoria: MemoriaDeEta[int] = MemoriaDeEta(capacidad=0)
    for _ in range(3):
        memoria.obtener("a", lambda: calculos.append(1) or len(calculos))
    assert len(calculos) == 3 and len(memoria) == 0
    with pytest.raises(ValueError):
        MemoriaDeEta(capacidad=-1)


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def test_la_respuesta_de_la_ruta_es_identica_con_memoria_y_sin_ella(grafo: GrafoVialAMBA) -> None:
    contexto: ContextoDeLaApi = construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    cliente = cliente_autenticado(contexto)
    plan = cliente.post("/v1/planificaciones", json={})
    assert plan.status_code == http.HTTPStatus.CREATED, plan.text
    ids = [ruta["id"] for ruta in plan.json()["rutas"]]

    con_memoria = [cliente.get(f"/v1/rutas/{i}").content for i in ids]
    repetidas = [cliente.get(f"/v1/rutas/{i}").content for i in ids]
    assert len(contexto.memoria_de_eta) == len(ids)

    contexto.memoria_de_eta = MemoriaDeEta(capacidad=0)
    sin_memoria = [cliente.get(f"/v1/rutas/{i}").content for i in ids]

    assert con_memoria == repetidas == sin_memoria
    assert CAPACIDAD_DE_MEMORIA_DE_ETA == 256
    # La comparacion tiene sentido solo si hay ETA que comparar.
    primera = cliente.get(f"/v1/rutas/{ids[0]}").json()
    assert primera["paradas"][0]["eta_estimado_s"] is not None
