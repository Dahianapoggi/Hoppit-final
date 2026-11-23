"""El esquema publicado tiene que poder importarse en un cliente de API.

No alcanza con que FastAPI lo genere: un documento que no cumple la
especificacion, o que no declara servidor, se importa mal o directamente no
se importa, y quien construye la interfaz se entera recien al intentarlo.

Lo que aca se verifica es lo que puede verificarse sin abrir Postman: que el
documento sea valido segun la especificacion, que declare a donde apuntar, y
que no tenga las malformaciones que rompen la importacion y la generacion de
clientes.
"""
from __future__ import annotations

import collections
import json
import logging
import pathlib

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import crear_app
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

_LOG = logging.getLogger(__name__)

ESQUEMA_VERSIONADO = pathlib.Path("contrato/openapi.json")

# FastAPI genera JSON Schema 2020-12, que solo OpenAPI 3.1 admite. Se fija
# como expectativa para que un cambio de version del documento --que puede
# dejar afuera a clientes viejos-- sea deliberado y no una sorpresa.
VERSION_DE_OPENAPI = "3.1.0"

# 19 desde la sesion 2026-10-13F: despacho de una Ruta y resultado de una
# Parada (FR-16, CU-06). 22 desde el parche de la sesion 2026-10-15B, bloque
# IA: alta, lista y consulta de Incidentes (FR-18). 25 desde la sesion
# 2026-10-19B, bloque I: ingreso, terminos vigentes y aceptacion (ARQ-55,
# ARQ-56). Cambiar esta cifra lo autorizo D-C3 (B-240). 26 desde la sesion
# 2026-10-20C, bloque R: registro con invitacion (B-246, B-248). 27 desde la
# sesion 2026-10-27B, bloque A: alertas por reglas (CU-08; B-259, punto 3). 29
# desde el bloque T: transportistas y Ruta en ejecucion del transportista (D-C2).
# 30 desde la sesion 2026-10-27C, bloque G: busqueda por direccion (RB-10).
# 32 desde la sesion 2026-11-02: el transportista declara el resultado de una
# Parada y un Incidente sobre su Ruta desde su aplicacion. 35 desde la misma
# sesion: el operador administra la Flota en /v1/vehiculos (listar, alta y cambio).
# 40 desde 2026-11-20A: CU-07 agrega el avance de una Ruta, la propuesta de
# recalculo por Incidente, su consulta, su aprobacion y su rechazo.
# 41 desde el bloque 3 del mismo encargo: el ETA de una Parada (CU-09).
# 43 con CU-06, flujo 1a: reprogramar y devolver una Orden Fallida.
# 48 con CU-11 (bloque 4): declarar, listar, consultar, evaluar por Ruta y activar contingencias.
# 56 con CU-10 (bloque 5): Clientes comerciales y sus Ordenes, Proveedores, acuerdos y su evaluacion.
# 59 con CU-12 (bloque 6): mapa de estados de un Proveedor, ingestion de su reporte y reportes rechazados.
# 62 desde 2026-11-22A: CU-04 por la integracion del Cliente comercial (vincular su usuario y sus dos consultas).
# 63 con el pedido nuevo durante la jornada (bloque 3 del mismo encargo).
# 65 con la brecha de optimalidad (D23) y la exposicion al riesgo vial (D25) de
# una Ruta, operaciones aparte para no cambiar la huella (2026-11-23A).
OPERACIONES_ESPERADAS = 65


@pytest.fixture(scope="module")
def esquema() -> dict:
    cliente = cliente_autenticado(
        construir_contexto(
            con_escenario=False,
            base_de_datos=None,
            grafo=GRAFO_PREDETERMINADO,
            deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        )
    )
    return cliente.get("/openapi.json").json()


def test_el_esquema_cumple_la_especificacion(esquema: dict) -> None:
    """Validado contra la especificacion, no solo generado."""
    validate(esquema)
    assert esquema["openapi"] == VERSION_DE_OPENAPI
    _LOG.info(
        "OpenAPI %s | %d operaciones", esquema["openapi"], _operaciones(esquema)
    )


def test_el_esquema_declara_a_donde_apuntar(esquema: dict) -> None:
    """Sin `servers`, la coleccion importada arma las peticiones sin host y
    hay que escribir la URL base a mano en cada una."""
    servidores = esquema["servers"]
    assert servidores

    url = servidores[0]["url"]
    for nombre, definicion in servidores[0].get("variables", {}).items():
        assert definicion["default"], f"la variable {nombre} necesita un valor"
        url = url.replace("{" + nombre + "}", definicion["default"])
    assert url.startswith("http://")


def test_ninguna_operacion_comparte_identificador(esquema: dict) -> None:
    """Los operationId duplicados rompen la generacion de clientes y hacen
    que un cliente de API muestre dos peticiones con el mismo nombre."""
    identificadores = [
        operacion["operationId"]
        for metodos in esquema["paths"].values()
        for operacion in metodos.values()
    ]

    repetidos = [
        identificador
        for identificador, cuantos in collections.Counter(identificadores).items()
        if cuantos > 1
    ]
    assert not repetidos
    assert len(identificadores) == OPERACIONES_ESPERADAS


def test_ninguna_referencia_queda_colgada(esquema: dict) -> None:
    """Una referencia a un esquema inexistente rompe la importacion."""
    import re

    texto = json.dumps(esquema)
    referidos = set(re.findall(r'"#/components/schemas/([^"]+)"', texto))
    declarados = set(esquema.get("components", {}).get("schemas", {}))

    assert referidos <= declarados


def test_toda_operacion_esta_documentada(esquema: dict) -> None:
    """Lo que se ve en /docs y en el cliente importado: cada peticion con su
    resumen y su descripcion."""
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            assert operacion.get("summary"), f"{metodo} {ruta} sin resumen"
            assert operacion.get("description"), f"{metodo} {ruta} sin descripcion"
            assert operacion.get("tags"), f"{metodo} {ruta} sin agrupar"


def test_toda_respuesta_de_error_declara_la_forma_real(esquema: dict) -> None:
    """El esquema no puede afirmar una forma de error que la API no devuelve.

    FastAPI declara por su cuenta un 422 con la forma de
    `HTTPValidationError`, que dejo de ser la real cuando los rechazos de
    validacion pasaron a responder como cualquier otro error del contrato.
    Quien construyera contra el esquema escribiria el manejador equivocado y
    se enteraria recien al recibir el primer 422.
    """
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            for codigo, respuesta in operacion["responses"].items():
                if not codigo.startswith(("4", "5")):
                    continue
                referida = (
                    respuesta.get("content", {})
                    .get("application/json", {})
                    .get("schema", {})
                    .get("$ref", "")
                )
                assert referida.endswith("/ErrorPublicado"), (
                    f"{metodo} {ruta} declara {codigo} con {referida or 'nada'}"
                )

    assert "HTTPValidationError" not in esquema["components"]["schemas"]


def test_los_errores_de_dominio_estan_declarados(esquema: dict) -> None:
    """FastAPI no puede inferirlos: se levantan dentro del cuerpo del
    endpoint y no aparecen en su firma.

    Sin declararlos, el contrato no dice que existen y quien lo consuma no
    sabe que contemplarlos.
    """
    declarados = {
        (metodo, ruta, codigo)
        for ruta, metodos in esquema["paths"].items()
        for metodo, operacion in metodos.items()
        for codigo in operacion["responses"]
    }

    # Los casos que la API devuelve de verdad, verificados en test_api_rest.
    for caso in [
        ("get", "/v1/anclajes", "422"),
        ("get", "/v1/planes/{id_plan}", "404"),
        ("delete", "/v1/planes/{id_plan}", "404"),
        ("get", "/v1/ordenes/{id_orden}", "404"),
        ("get", "/v1/rutas/{id_ruta}", "404"),
        ("delete", "/v1/rutas/{id_ruta}", "404"),
        ("post", "/v1/ordenes/{id_orden}/desasignacion", "404"),
        ("post", "/v1/ordenes/{id_orden}/desasignacion", "409"),
        ("post", "/v1/planificaciones", "409"),
        ("post", "/v1/ordenes", "422"),
    ]:
        assert caso in declarados, f"sin declarar: {caso}"


def test_cada_error_declarado_explica_sus_codigos(esquema: dict) -> None:
    """La descripcion nombra los codigos posibles, porque es contra el
    codigo --y no contra el estado HTTP-- que hay que decidir: dos casos
    distintos pueden compartir estado."""
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            for codigo, respuesta in operacion["responses"].items():
                if codigo.startswith(("4", "5")):
                    assert respuesta.get("description"), f"{metodo} {ruta} {codigo}"


# Catalogos cerrados que el contrato publica. Quien construye la interfaz los
# usa para poblar filtros y para saber que casos contemplar, de modo que
# tienen que estar en el esquema y no solo en el codigo del backend.
CATALOGOS_PUBLICADOS = {
    "EstadoOrden",
    "CodigoDeError",
    "OrigenRotulo",
    "MotivoDeIncomunicacion",
}


def test_el_contrato_publica_sus_catalogos_cerrados(esquema: dict) -> None:
    """Sin esto habria que escribir los valores a mano del otro lado.

    Y peor: agregar un estado o un codigo de error no se notaria hasta que
    apareciera uno desconocido en produccion.
    """
    declarados = esquema["components"]["schemas"]

    for catalogo in CATALOGOS_PUBLICADOS:
        assert catalogo in declarados, f"{catalogo} no esta en el esquema"
        assert declarados[catalogo].get("enum"), f"{catalogo} no declara sus valores"


def test_los_estados_publicados_son_los_del_glosario(esquema: dict) -> None:
    """El catalogo canonico completo, no solo los que fase 1 ejercita.

    CU-04 dice que el estado devuelto pertenece siempre al catalogo canonico;
    publicar solo los dos que hoy se usan haria que la interfaz no contemplara
    el resto cuando aparezcan.
    """
    from hoppit.gestion_ordenes.puerto import EstadoOrden

    publicados = esquema["components"]["schemas"]["EstadoOrden"]["enum"]

    assert set(publicados) == {estado.value for estado in EstadoOrden}


def test_el_esquema_versionado_esta_al_dia(esquema: dict) -> None:
    """El archivo del repositorio tiene que ser el que la API genera hoy.

    Es lo que hace confiable trabajar contra el archivo sin levantar el
    backend. Si divergen, quien construye la interfaz lo hace contra un
    contrato que ya no existe; se regenera con tools/exportar_contrato.py.
    """
    if not ESQUEMA_VERSIONADO.exists():
        pytest.skip("el contrato todavia no se exporto")

    publicado = json.loads(ESQUEMA_VERSIONADO.read_text(encoding="utf-8"))
    assert publicado["info"]["version"] == esquema["info"]["version"]
    assert publicado["openapi"] == esquema["openapi"]
    assert set(publicado["paths"]) == set(esquema["paths"])
    assert publicado["servers"] == esquema["servers"]


def _operaciones(esquema: dict) -> int:
    return sum(len(metodos) for metodos in esquema["paths"].values())
