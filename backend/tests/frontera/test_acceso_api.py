"""El acceso por la API: token exigido, terminos, roles y registros (ARQ-55,
ARQ-56; sesion 2026-10-19B, bloque I).

Las contrasenas se generan dentro de cada prueba y no se escriben en ningun
archivo.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import crear_app
from hoppit.frontera.identidad.puerto import (
    MARCADOR_DE_CREDENCIAL,
    MAX_INTENTOS_FALLIDOS,
    FirmanteDeSesiones,
    Rol,
    credenciales_ocultas,
    generar_clave_privada,
)
from tools.acceso_en_proceso import cliente_autenticado
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


# Clave efimera conocida por la prueba, para poder emitir un token vencido
# firmado por el mismo nucleo.
_CLAVE = generar_clave_privada()


@pytest.fixture(scope="module")
def contexto():  # type: ignore[no-untyped-def]
    return construir_contexto(
        con_escenario=False,
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=_CLAVE,
    )


@pytest.fixture
def anonimo(contexto) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(crear_app(contexto))


def _usuario(contexto, rol: Rol = Rol.OPERADOR) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    nombre = "prueba-" + secrets.token_hex(6)
    contrasena = secrets.token_urlsafe(24)
    contexto.identidad.administracion.crear_usuario(nombre, "Usuario de prueba", rol, contrasena)
    return nombre, contrasena


def _ingresar(cliente: TestClient, usuario: str, contrasena: str) -> dict:
    respuesta = cliente.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena})
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


def _con(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_las_operaciones_publicas_responden_sin_token(anonimo: TestClient) -> None:
    assert anonimo.get("/estado").status_code == 200
    assert anonimo.get("/v1/estado").status_code == 200


def test_sin_token_responde_401_no_autenticado(anonimo: TestClient) -> None:
    respuesta = anonimo.get("/v1/ordenes")
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "no_autenticado"
    assert respuesta.headers["WWW-Authenticate"] == "Bearer"
    assert anonimo.get("/v1/terminos/vigentes").json()["codigo"] == "no_autenticado"


def test_token_invalido_responde_401(anonimo: TestClient) -> None:
    respuesta = anonimo.get("/v1/ordenes", headers=_con("no.es.un-token"))
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "no_autenticado"


def test_ingreso_fallido_con_mensaje_generico(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    usuario, _ = _usuario(contexto)
    inexistente = anonimo.post("/v1/sesiones", json={"usuario": "nadie-" + secrets.token_hex(4), "contrasena": secrets.token_urlsafe(16)})
    erronea = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": secrets.token_urlsafe(16)})
    assert inexistente.status_code == erronea.status_code == 401
    assert inexistente.json() == erronea.json()
    assert erronea.json()["codigo"] == "credenciales_invalidas"


def test_bloqueo_responde_423_con_el_tiempo_restante(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    usuario, contrasena = _usuario(contexto)
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": secrets.token_urlsafe(16)})
    quinto = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": secrets.token_urlsafe(16)})
    assert quinto.status_code == 423
    assert quinto.json()["codigo"] == "usuario_bloqueado"
    correcta = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena})
    assert correcta.status_code == 423
    segundos = correcta.json()["detalle"]["segundos_restantes"]
    assert 0 < segundos <= 900
    assert int(correcta.headers["Retry-After"]) == segundos


def test_sin_bloqueo_por_intentos_el_ingreso_no_se_traba() -> None:
    """Como arranca `servir_api.py` sin `--con-bloqueo-por-intentos`
    (2026-11-02): los fallos seguidos responden 401 y despues la contrasena
    correcta entra."""
    contexto = construir_contexto(
        con_escenario=False,
        base_de_datos=None,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=_CLAVE,
        bloqueo_por_intentos=False,
    )
    anonimo = TestClient(crear_app(contexto))
    usuario, contrasena = _usuario(contexto)
    for _ in range(MAX_INTENTOS_FALLIDOS * 2):
        fallido = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": secrets.token_urlsafe(16)})
        assert (fallido.status_code, fallido.json()["codigo"]) == (401, "credenciales_invalidas")
    correcta = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena})
    assert correcta.status_code == 201, correcta.text


def test_el_ingreso_acepta_los_topes_del_registro(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    """Con el largo justo en los topes, el pedido llega a la verificacion
    (2026-10-28C, H6)."""
    from hoppit.frontera.api_rest._acceso import LARGO_MAXIMO_DE_LA_CONTRASENA, LARGO_MAXIMO_DEL_USUARIO

    usuario, _ = _usuario(contexto)
    en_el_tope = anonimo.post("/v1/sesiones", json={"usuario": "u" * LARGO_MAXIMO_DEL_USUARIO, "contrasena": "x"})
    contrasena_en_el_tope = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": "x" * LARGO_MAXIMO_DE_LA_CONTRASENA})
    assert en_el_tope.status_code == contrasena_en_el_tope.status_code == 401
    assert contrasena_en_el_tope.json()["codigo"] == "credenciales_invalidas"


def test_el_ingreso_rechaza_lo_que_supera_los_topes_antes_de_verificar(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    """Un caracter mas que el tope responde 422 en el formato unificado y no
    llega a Argon2: no cuenta como intento fallido (2026-10-28C, H6)."""
    from hoppit.frontera.api_rest._acceso import LARGO_MAXIMO_DE_LA_CONTRASENA, LARGO_MAXIMO_DEL_USUARIO

    usuario, contrasena = _usuario(contexto)
    largo = anonimo.post("/v1/sesiones", json={"usuario": "u" * (LARGO_MAXIMO_DEL_USUARIO + 1), "contrasena": "x"})
    assert largo.status_code == 422
    assert largo.json()["codigo"] == "peticion_mal_formada"
    assert set(largo.json()) == {"codigo", "mensaje", "detalle"}
    for _ in range(MAX_INTENTOS_FALLIDOS):
        larga = anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": "x" * (LARGO_MAXIMO_DE_LA_CONTRASENA + 1)})
        assert larga.status_code == 422
        assert larga.json()["codigo"] == "peticion_mal_formada"
    # Si hubieran llegado a la verificacion, el usuario estaria bloqueado.
    assert anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena}).status_code == 201


def test_terminos_pendientes_responden_403(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    usuario, contrasena = _usuario(contexto)
    sesion = _ingresar(anonimo, usuario, contrasena)
    assert {p["documento"] for p in sesion["pendientes"]} == {
        "terminos_de_uso",
        "consentimiento_transferencia_internacional",
    }
    rechazo = anonimo.get("/v1/ordenes", headers=_con(sesion["token"]))
    assert rechazo.status_code == 403
    assert rechazo.json()["codigo"] == "terminos_no_aceptados"

    # Los terminos se leen y se aceptan con el mismo token.
    vigentes = anonimo.get("/v1/terminos/vigentes", headers=_con(sesion["token"]))
    assert vigentes.status_code == 200
    documentos = vigentes.json()["documentos"]
    assert all(not d["aceptado"] for d in documentos)
    aceptada = anonimo.post(
        "/v1/terminos/aceptaciones",
        headers=_con(sesion["token"]),
        json={
            "medio": "web",
            "aceptaciones": [
                {"documento": d["documento"], "version": d["version"], "sha256": d["sha256"]}
                for d in documentos
            ],
        },
    )
    assert aceptada.status_code == 201, aceptada.text
    nueva = aceptada.json()
    assert nueva["pendientes"] == []
    assert nueva["vence_en"] == sesion["vence_en"]
    assert anonimo.get("/v1/ordenes", headers=_con(nueva["token"])).status_code == 200
    # El token anterior tambien sirve: el nucleo consulta las aceptaciones en
    # la base, no en el token.
    assert anonimo.get("/v1/ordenes", headers=_con(sesion["token"])).status_code == 200


def test_aceptacion_invalida_responde_422(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    usuario, contrasena = _usuario(contexto)
    sesion = _ingresar(anonimo, usuario, contrasena)
    respuesta = anonimo.post(
        "/v1/terminos/aceptaciones",
        headers=_con(sesion["token"]),
        json={"medio": "web", "aceptaciones": [{"documento": "terminos_de_uso", "version": 99, "sha256": "0" * 64}]},
    )
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "aceptacion_invalida"
    assert respuesta.json()["detalle"]["documentos"][0]["documento"] == "terminos_de_uso"


def test_el_transportista_no_tiene_operaciones_del_operador(contexto) -> None:  # type: ignore[no-untyped-def]
    cliente = cliente_autenticado(contexto, Rol.TRANSPORTISTA)
    respuesta = cliente.get("/v1/ordenes")
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "rol_no_autorizado"
    assert respuesta.json()["detalle"] == {"rol": "transportista", "roles_admitidos": ["operador"]}
    assert cliente.get("/v1/terminos/vigentes").status_code == 200


def test_el_operador_opera(contexto) -> None:  # type: ignore[no-untyped-def]
    cliente = cliente_autenticado(contexto)
    assert cliente.get("/v1/flota").status_code == 200
    assert cliente.get("/v1/incidentes").status_code == 200


def test_usuario_desactivado_se_rechaza_en_el_pedido_siguiente(contexto) -> None:  # type: ignore[no-untyped-def]
    cliente = TestClient(crear_app(contexto))
    usuario, contrasena = _usuario(contexto)
    sesion = _ingresar(cliente, usuario, contrasena)
    token = sesion["token"]
    assert cliente.get("/v1/terminos/vigentes", headers=_con(token)).status_code == 200
    contexto.identidad.administracion.desactivar_usuario(usuario)
    rechazo = cliente.get("/v1/terminos/vigentes", headers=_con(token))
    assert rechazo.status_code == 401
    assert rechazo.json()["codigo"] == "no_autenticado"


def test_sesion_vencida_responde_401_sesion_vencida(contexto) -> None:  # type: ignore[no-untyped-def]
    """Un token de vencimiento corto, emitido en la prueba con la clave del
    servicio: firmado por el nucleo pero vencido."""
    cliente = TestClient(crear_app(contexto))
    usuario, contrasena = _usuario(contexto)
    sesion = _ingresar(cliente, usuario, contrasena)
    autenticado = contexto.identidad.autenticar(sesion["token"])
    firmante = FirmanteDeSesiones(_CLAVE)
    ahora = dt.datetime.now(dt.timezone.utc)
    vencido = firmante.emitir(
        usuario_id=autenticado.usuario.id,
        rol=autenticado.usuario.rol,
        terminos=None,
        consentimientos=[],
        emitido_en=ahora - dt.timedelta(minutes=10),
        vence_en=ahora - dt.timedelta(minutes=5),
    )
    respuesta = cliente.get("/v1/terminos/vigentes", headers=_con(vencido))
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "sesion_vencida"


def test_la_contrasena_no_llega_a_los_registros(anonimo: TestClient, contexto, caplog) -> None:  # type: ignore[no-untyped-def]
    usuario, contrasena = _usuario(contexto)
    erronea = secrets.token_urlsafe(24)
    with caplog.at_level(logging.DEBUG):
        anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": erronea})
        anonimo.post("/v1/sesiones", json={"usuario": usuario, "contrasena": contrasena})
        anonimo.post("/v1/sesiones", json={"usuario": usuario})
        # Un registro que, dentro del ingreso, nombrara la contrasena sale con
        # el marcador.
        with credenciales_ocultas(contrasena):
            logging.getLogger("hoppit.prueba").warning("contrasena recibida: %s", contrasena)
    assert contrasena not in caplog.text
    assert erronea not in caplog.text
    assert MARCADOR_DE_CREDENCIAL in caplog.text


def test_el_esquema_declara_la_seguridad_y_los_errores_de_acceso(anonimo: TestClient) -> None:
    esquema = anonimo.get("/openapi.json").json()
    assert esquema["components"]["securitySchemes"]["bearer"]["scheme"] == "bearer"
    publicas = {("get", "/estado"), ("get", "/v1/estado"), ("post", "/v1/sesiones"), ("post", "/v1/registros")}
    for ruta, metodos in esquema["paths"].items():
        for metodo, operacion in metodos.items():
            if (metodo, ruta) in publicas:
                assert "security" not in operacion, (metodo, ruta)
                continue
            assert operacion.get("security") == [{"bearer": []}], (metodo, ruta)
            assert "401" in operacion["responses"], (metodo, ruta)
            if not ruta.startswith("/v1/terminos/"):
                assert "403" in operacion["responses"], (metodo, ruta)
