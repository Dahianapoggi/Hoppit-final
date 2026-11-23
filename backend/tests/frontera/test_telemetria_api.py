"""El servicio de telemetria por su API (2026-10-27B, T): lotes con 413, 401
y 403, la ultima posicion, los eventos internos con token de servicio, y la
salida transaccional del nucleo entregada de punta a punta. Tokens y
posiciones de prueba, armados aca."""
from __future__ import annotations

import datetime as dt
import http
import subprocess
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.cartografia.puerto import GrafoVialAMBA, crear_servicio as crear_cartografia
from hoppit.frontera.identidad.puerto import (
    AUDIENCIA_TELEMETRIA,
    DocumentoDeTerminos,
    FirmanteDeSesiones,
    Rol,
    emitir_token_de_servicio,
    leer_documentos_vigentes,
)
from hoppit.frontera.telemetria.puerto import crear_app_de_telemetria
from hoppit.seguimiento_flota.puerto import MAX_POSICIONES_POR_LOTE, crear_servicio as crear_seguimiento
from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto

TRANSPORTISTA = "usuario-transportista"
# Identificadores de prueba en la forma que exige el servicio: UUID version 4
# (ARQ-52; 2026-10-27C, F2), fijos para que las respuestas se comparen.
DISPOSITIVO = str(uuid.UUID(int=0xD15, version=4))


def _id_de_posicion(i: int) -> str:
    return str(uuid.UUID(int=i + 1, version=4))


CONSENTIMIENTOS = ("transferencia_internacional", "geolocalizacion")


@pytest.fixture()
def clave():  # type: ignore[no-untyped-def]
    return clave_efimera()


@pytest.fixture()
def telemetria(clave) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(crear_app_de_telemetria(crear_seguimiento(), clave.public_key()))


def _token(clave, rol: Rol = Rol.TRANSPORTISTA, consentimientos=CONSENTIMIENTOS, terminos: int | None = None) -> str:  # type: ignore[no-untyped-def]
    version = leer_documentos_vigentes()[DocumentoDeTerminos.TERMINOS_DE_USO].version
    ahora = dt.datetime.now(dt.timezone.utc)
    return FirmanteDeSesiones(clave).emitir(
        usuario_id=TRANSPORTISTA,
        rol=rol,
        terminos=version if terminos is None else terminos,
        consentimientos=consentimientos,
        emitido_en=ahora,
        vence_en=ahora + dt.timedelta(hours=1),
    )


def _evento(cliente: TestClient, clave, ruta: str = "ruta-a", transportista: str = TRANSPORTISTA) -> None:  # type: ignore[no-untyped-def]
    despacho = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).isoformat()
    respuesta = cliente.post(
        "/interno/eventos",
        json={
            "evento_id": str(uuid.uuid4()),
            "tipo": "RutaDespachada",
            "agregado_id": ruta,
            "secuencia_del_agregado": 1,
            "cuerpo": {"vehiculo_id": "AE472LP", "transportista_id": transportista, "despachada_en": despacho},
        },
        headers={"Authorization": f"Bearer {emitir_token_de_servicio(clave, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))}"},
    )
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text


def _lote(n: int = 1, ruta: str = "ruta-a") -> dict:
    ahora = dt.datetime.now(dt.timezone.utc)
    return {
        "ruta_id": ruta,
        "dispositivo_id": DISPOSITIVO,
        "posiciones": [
            {"id": _id_de_posicion(i), "capturada_en": (ahora - dt.timedelta(seconds=30 * i)).isoformat(), "latitud": -34.6, "longitud": -58.4, "precision_m": 8.0}
            for i in range(n)
        ],
    }


def test_un_lote_valido_se_acepta_y_su_ultima_posicion_se_lee(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave)
    respuesta = telemetria.post("/v1/telemetria/lotes", json=_lote(2), headers={"Authorization": f"Bearer {_token(clave)}"})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    assert respuesta.json() == {"aceptadas": [_id_de_posicion(0), _id_de_posicion(1)], "duplicadas": [], "rechazadas": []}
    repetido = telemetria.post("/v1/telemetria/lotes", json=_lote(1), headers={"Authorization": f"Bearer {_token(clave)}"})
    assert repetido.json()["duplicadas"] == [_id_de_posicion(0)]
    ultima = telemetria.get(
        "/v1/telemetria/rutas/ruta-a/ultima-posicion", headers={"Authorization": f"Bearer {_token(clave, Rol.OPERADOR, ('transferencia_internacional',))}"}
    )
    assert ultima.status_code == http.HTTPStatus.OK
    assert ultima.json()["antiguedad_s"] >= 0
    assert ultima.json()["estado_de_ejecucion"] == "en_ejecucion"


def test_sin_posiciones_es_404(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    respuesta = telemetria.get(
        "/v1/telemetria/rutas/ruta-x/ultima-posicion", headers={"Authorization": f"Bearer {_token(clave, Rol.OPERADOR)}"}
    )
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.NOT_FOUND, "sin_posiciones")


def test_un_lote_demasiado_grande_es_413(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    encabezado = {"Authorization": f"Bearer {_token(clave)}"}
    por_cantidad = telemetria.post("/v1/telemetria/lotes", json=_lote(MAX_POSICIONES_POR_LOTE + 1), headers=encabezado)
    assert (por_cantidad.status_code, por_cantidad.json()["codigo"]) == (http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "lote_demasiado_grande")
    grande = _lote(1)
    grande["relleno"] = "x" * (64 * 1024)
    por_bytes = telemetria.post("/v1/telemetria/lotes", json=grande, headers=encabezado)
    assert por_bytes.status_code == http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_un_dispositivo_sin_uuid_v4_es_422_y_no_guarda_nada(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave)
    lote = _lote(1)
    lote["dispositivo_id"] = "dispositivo-de-prueba"
    respuesta = telemetria.post("/v1/telemetria/lotes", json=lote, headers={"Authorization": f"Bearer {_token(clave)}"})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (http.HTTPStatus.UNPROCESSABLE_ENTITY, "dispositivo_invalido")
    ultima = telemetria.get(
        "/v1/telemetria/rutas/ruta-a/ultima-posicion", headers={"Authorization": f"Bearer {_token(clave, Rol.OPERADOR)}"}
    )
    assert ultima.status_code == http.HTTPStatus.NOT_FOUND


def test_una_posicion_sin_uuid_v4_se_rechaza_con_identificador_invalido(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    _evento(telemetria, clave)
    lote = _lote(2)
    lote["posiciones"][1]["id"] = "p-1"
    respuesta = telemetria.post("/v1/telemetria/lotes", json=lote, headers={"Authorization": f"Bearer {_token(clave)}"})
    assert respuesta.status_code == http.HTTPStatus.OK, respuesta.text
    assert respuesta.json() == {
        "aceptadas": [_id_de_posicion(0)],
        "duplicadas": [],
        "rechazadas": [{"id": "p-1", "motivo": "identificador_invalido", "reintentable": False}],
    }


@pytest.mark.parametrize(
    ("encabezado", "estado", "codigo"),
    [
        (None, http.HTTPStatus.UNAUTHORIZED, "no_autenticado"),
        ("Bearer no-es-un-token", http.HTTPStatus.UNAUTHORIZED, "no_autenticado"),
    ],
)
def test_sin_token_valido_es_401(telemetria: TestClient, encabezado, estado, codigo) -> None:  # type: ignore[no-untyped-def]
    respuesta = telemetria.post("/v1/telemetria/lotes", json=_lote(), headers={} if encabezado is None else {"Authorization": encabezado})
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo)


def test_rol_o_consentimiento_faltante_es_403(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    de_operador = telemetria.post("/v1/telemetria/lotes", json=_lote(), headers={"Authorization": f"Bearer {_token(clave, Rol.OPERADOR)}"})
    assert (de_operador.status_code, de_operador.json()["codigo"]) == (http.HTTPStatus.FORBIDDEN, "rol_no_autorizado")
    sin_geo = telemetria.post(
        "/v1/telemetria/lotes", json=_lote(), headers={"Authorization": f"Bearer {_token(clave, consentimientos=('transferencia_internacional',))}"}
    )
    assert (sin_geo.status_code, sin_geo.json()["codigo"]) == (http.HTTPStatus.FORBIDDEN, "terminos_no_aceptados")
    assert sin_geo.json()["detalle"]["consentimientos_faltantes"] == ["geolocalizacion"]


def test_los_eventos_internos_exigen_token_de_servicio(telemetria: TestClient, clave) -> None:  # type: ignore[no-untyped-def]
    con_token_de_sesion = telemetria.post(
        "/interno/eventos",
        json={"evento_id": "e", "tipo": "RutaDespachada", "agregado_id": "r", "secuencia_del_agregado": 1, "cuerpo": {}},
        headers={"Authorization": f"Bearer {_token(clave)}"},
    )
    assert con_token_de_sesion.status_code == http.HTTPStatus.UNAUTHORIZED


@pytest.fixture(scope="module")
def grafo() -> GrafoVialAMBA:
    servicio = crear_cartografia(GRAFO_PREDETERMINADO)
    servicio.construir()
    return servicio


def test_el_despacho_escribe_la_notificacion_y_la_entrega_llega_una_sola_vez(grafo: GrafoVialAMBA) -> None:
    """Salida transaccional de punta a punta, en proceso: el entregador manda
    la notificacion del despacho a la app de telemetria, y entregar otra vez
    no la duplica."""
    clave = clave_efimera()
    contexto = construir_contexto(
        base_de_datos=None,
        cartografia=grafo,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave,
    )
    operador = cliente_autenticado(contexto)
    assert operador.post("/v1/planificaciones", json={}).status_code == http.HTTPStatus.CREATED
    ruta = operador.get("/v1/rutas").json()[0]["id"]
    assert operador.post(f"/v1/rutas/{ruta}/despacho").status_code == http.HTTPStatus.OK
    assert contexto.planificacion.notificaciones_pendientes() == 1
    assert operador.get("/estado").json()["notificaciones_pendientes"] == 1

    seguimiento = crear_seguimiento()
    telemetria = TestClient(crear_app_de_telemetria(seguimiento, clave.public_key()))
    recibidos = []
    enviadas = []

    def enviar(n) -> None:  # type: ignore[no-untyped-def]
        enviadas.append(n)
        token = emitir_token_de_servicio(clave, AUDIENCIA_TELEMETRIA, dt.datetime.now(dt.timezone.utc))
        respuesta = telemetria.post(
            "/interno/eventos",
            json={"evento_id": n.evento_id, "tipo": n.tipo, "agregado_id": n.agregado_id, "secuencia_del_agregado": n.secuencia_del_agregado, "cuerpo": n.cuerpo},
            headers={"Authorization": f"Bearer {token}"},
        )
        respuesta.raise_for_status()
        recibidos.append(respuesta.json()["nuevo"])

    entregador = contexto.planificacion.entregador(enviar)
    assert entregador.entregar_pendientes() == 1
    assert entregador.entregar_pendientes() == 0
    assert contexto.planificacion.notificaciones_pendientes() == 0
    assert recibidos == [True]
    # Reenviar la misma notificacion (al menos una vez) no la duplica.
    enviar(enviadas[0])
    assert recibidos == [True, False]


def test_el_proceso_de_telemetria_no_carga_el_nucleo() -> None:
    """Frontera de procesos (seccion 1.2): el ensamblado de telemetria no
    carga Planificacion de Rutas, Cartografia, Gestion de Ordenes ni
    Vigilancia de SLA."""
    codigo = (
        "import sys, tools.servir_telemetria as t\n"
        "prohibidos = ['hoppit.planificacion_rutas', 'hoppit.cartografia', 'hoppit.gestion_ordenes', 'hoppit.vigilancia_sla']\n"
        "cargados = [m for m in prohibidos if any(n == m or n.startswith(m + '.') for n in sys.modules)]\n"
        "print(','.join(cargados))\n"
    )
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, check=True)
    assert salida.stdout.strip() == ""


def test_el_contrato_de_telemetria_versionado_es_el_que_genera_la_aplicacion() -> None:
    import json
    import pathlib

    from openapi_spec_validator import validate

    from tools.exportar_contrato_telemetria import DESTINO, esquema

    generado = esquema()
    validate(generado)
    assert json.loads(pathlib.Path(DESTINO).read_text(encoding="utf-8")) == generado
