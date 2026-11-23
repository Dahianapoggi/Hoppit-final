"""Registro de usuarios con invitacion (B-246, B-248; sesion 2026-10-20C,
bloque R): codigo de un solo uso, con vencimiento y rol fijo, guardado solo
como SHA-256, y alta e invitacion usada en la misma transaccion.

Las contrasenas y los codigos se generan dentro de cada prueba y no se
escriben en ningun archivo.
"""
from __future__ import annotations

import datetime as dt
import logging
import pathlib
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

from hoppit.bootstrap import GRAFO_PREDETERMINADO
from hoppit.frontera.api_rest.puerto import crear_app
from hoppit.frontera.identidad.puerto import (
    Invitacion,
    RepositorioIdentidadSQLite,
    Rol,
    VencimientoInvalido,
    normalizar_codigo,
    sha256_del_codigo,
)
from hoppit.shared.almacen import conectar
from tools import crear_invitacion
from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, construir_contexto


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    return tmp_path_factory.mktemp("registro") / "registro.sqlite3"


@pytest.fixture(scope="module")
def contexto(base: pathlib.Path):  # type: ignore[no-untyped-def]
    return construir_contexto(
        con_escenario=False,
        base_de_datos=base,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
    )


@pytest.fixture
def anonimo(contexto) -> TestClient:  # type: ignore[no-untyped-def]
    """Una aplicacion nueva por prueba: el limite de intentos vive en ella."""
    return TestClient(crear_app(contexto))


def _invitacion(contexto, rol: Rol = Rol.OPERADOR) -> str:  # type: ignore[no-untyped-def]
    codigo, _ = contexto.identidad.administracion.crear_invitacion(rol)
    return str(codigo)


def _cuerpo(codigo: str, contrasena: str | None = None) -> dict[str, str]:
    return {
        "codigo": codigo,
        "usuario": "registro-" + secrets.token_hex(6),
        "nombre_visible": "Cuenta de prueba",
        "contrasena": contrasena if contrasena is not None else secrets.token_urlsafe(24),
    }


def test_registro_correcto_crea_la_cuenta_sin_iniciar_sesion(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    cuerpo = _cuerpo(_invitacion(contexto))

    respuesta = anonimo.post("/v1/registros", json=cuerpo)

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json() == {"usuario": cuerpo["usuario"], "nombre_visible": "Cuenta de prueba", "rol": "operador"}
    assert "token" not in respuesta.json()
    # Con esa cuenta se ingresa, y faltan aceptar los terminos.
    sesion = anonimo.post("/v1/sesiones", json={"usuario": cuerpo["usuario"], "contrasena": cuerpo["contrasena"]})
    assert sesion.status_code == 201
    assert sesion.json()["pendientes"]


def test_el_rol_es_el_de_la_invitacion_y_no_se_acepta_en_el_cuerpo(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    con_rol = {**_cuerpo(_invitacion(contexto, Rol.TRANSPORTISTA)), "rol": "operador"}
    rechazado = anonimo.post("/v1/registros", json=con_rol)
    assert rechazado.status_code == 422
    assert rechazado.json()["codigo"] == "peticion_mal_formada"

    sin_rol = {k: v for k, v in con_rol.items() if k != "rol"}
    respuesta = anonimo.post("/v1/registros", json=sin_rol)
    assert respuesta.status_code == 201
    assert respuesta.json()["rol"] == "transportista"


def test_invitacion_usada_vencida_o_inexistente_tienen_la_misma_respuesta(
    anonimo: TestClient, contexto, base: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    usada = _invitacion(contexto)
    assert anonimo.post("/v1/registros", json=_cuerpo(usada)).status_code == 201
    vencida = "VENC-IDAA-AAAA-AAAA"
    ahora = dt.datetime.now(dt.timezone.utc)
    RepositorioIdentidadSQLite(conectar(base)).agregar_invitacion(
        Invitacion(
            sha256=sha256_del_codigo(vencida),
            rol=Rol.OPERADOR,
            creada_en=ahora - dt.timedelta(hours=73),
            vence_en=ahora - dt.timedelta(hours=1),
        )
    )

    respuestas = [
        anonimo.post("/v1/registros", json=_cuerpo(codigo))
        for codigo in (usada, vencida, "NOEX-ISTE-NOEX-ISTE")
    ]

    assert {r.status_code for r in respuestas} == {422}
    assert len({(r.json()["codigo"], r.json()["mensaje"]) for r in respuestas}) == 1
    assert respuestas[0].json()["codigo"] == "invitacion_no_valida"


def test_sin_invitacion_valida_no_se_sabe_si_el_usuario_existe(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    cuerpo = _cuerpo(_invitacion(contexto))
    assert anonimo.post("/v1/registros", json=cuerpo).status_code == 201

    mismo_nombre_sin_codigo = anonimo.post("/v1/registros", json={**cuerpo, "codigo": "NOEX-ISTE-NOEX-ISTE"})
    mismo_nombre_con_codigo = anonimo.post("/v1/registros", json={**cuerpo, "codigo": _invitacion(contexto)})

    assert mismo_nombre_sin_codigo.json()["codigo"] == "invitacion_no_valida"
    assert mismo_nombre_con_codigo.status_code == 409
    assert mismo_nombre_con_codigo.json()["codigo"] == "usuario_existente"


def test_contrasena_corta_no_consume_la_invitacion(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    codigo = _invitacion(contexto)

    corta = anonimo.post("/v1/registros", json=_cuerpo(codigo, contrasena="corta"))

    assert corta.status_code == 422
    assert corta.json()["codigo"] == "contrasena_corta"
    assert anonimo.post("/v1/registros", json=_cuerpo(codigo)).status_code == 201


def test_el_codigo_se_acepta_sin_guiones_y_en_minusculas(anonimo: TestClient, contexto) -> None:  # type: ignore[no-untyped-def]
    codigo = _invitacion(contexto)
    assert anonimo.post("/v1/registros", json=_cuerpo(normalizar_codigo(codigo).lower())).status_code == 201


def test_ni_la_contrasena_ni_el_codigo_llegan_a_los_registros(anonimo: TestClient, contexto, caplog) -> None:  # type: ignore[no-untyped-def]
    codigo = _invitacion(contexto)
    contrasena = secrets.token_urlsafe(24)
    with caplog.at_level(logging.DEBUG):
        anonimo.post("/v1/registros", json=_cuerpo(codigo, contrasena="corta-" + contrasena[:3]))
        anonimo.post("/v1/registros", json=_cuerpo(codigo, contrasena=contrasena))
        anonimo.post("/v1/registros", json=_cuerpo(codigo, contrasena=contrasena))
    assert contrasena not in caplog.text
    assert codigo not in caplog.text
    assert normalizar_codigo(codigo) not in caplog.text


def test_el_codigo_no_queda_en_la_base(contexto, base: pathlib.Path) -> None:  # type: ignore[no-untyped-def]
    codigo = _invitacion(contexto)
    conexion = sqlite3.connect(base)
    try:
        conexion.execute("PRAGMA wal_checkpoint(FULL)")
        filas = conexion.execute("SELECT * FROM invitaciones").fetchall()
    finally:
        conexion.close()
    volcado = repr(filas)
    crudo = b"".join(p.read_bytes() for p in base.parent.glob(base.name + "*"))
    assert sha256_del_codigo(codigo) in volcado
    for forma in (codigo, normalizar_codigo(codigo)):
        assert forma not in volcado
        assert forma.encode() not in crudo


def test_demasiados_registros_fallidos_desde_una_direccion_responden_429(anonimo: TestClient) -> None:
    from hoppit.frontera.api_rest._limite_de_intentos import MAX_REGISTROS_FALLIDOS_POR_ORIGEN

    for _ in range(MAX_REGISTROS_FALLIDOS_POR_ORIGEN):
        assert anonimo.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE")).status_code == 422

    respuesta = anonimo.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE"))

    assert respuesta.status_code == 429
    assert respuesta.json()["codigo"] == "demasiados_intentos"
    assert int(respuesta.headers["Retry-After"]) == respuesta.json()["detalle"]["segundos_restantes"] > 0


# Detras del borde (2026-10-28C, H1): el pedido llega desde el contenedor de
# Caddy, que pone la direccion del cliente en X-Forwarded-For. Las direcciones
# son de los bloques de documentacion (RFC 5737) y de la subred del Compose.
SUBRED_DEL_COMPOSE = "172.28.0.0/24"
DIRECCION_DEL_BORDE = ("172.28.0.2", 40000)
DE_AFUERA = ("203.0.113.9", 40000)
CLIENTE_A = "198.51.100.1"
CLIENTE_B = "198.51.100.2"


def _servida_por_uvicorn(contexto, entorno: dict[str, str], desde: tuple[str, int], monkeypatch) -> TestClient:  # type: ignore[no-untyped-def]
    """La aplicacion con los encabezados reenviados tal como los resuelve
    uvicorn con lo que `servir_api.py` le pasa en `forwarded_allow_ips`."""
    import uvicorn
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from tools.proxies_del_borde import proxies_confiables

    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    app = crear_app(contexto)
    confiables = uvicorn.Config(app, forwarded_allow_ips=proxies_confiables(entorno)).forwarded_allow_ips
    return TestClient(ProxyHeadersMiddleware(app, trusted_hosts=confiables), client=desde)  # type: ignore[arg-type]


def _fallar_registros(cliente: TestClient, origen: str) -> None:
    from hoppit.frontera.api_rest._limite_de_intentos import MAX_REGISTROS_FALLIDOS_POR_ORIGEN

    for _ in range(MAX_REGISTROS_FALLIDOS_POR_ORIGEN):
        respuesta = cliente.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE"), headers={"X-Forwarded-For": origen})
        assert respuesta.status_code == 422


def test_detras_del_borde_el_limite_de_registros_es_por_cliente(contexto, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tools.proxies_del_borde import VARIABLE_PROXIES_CONFIABLES

    cliente = _servida_por_uvicorn(contexto, {VARIABLE_PROXIES_CONFIABLES: SUBRED_DEL_COMPOSE}, DIRECCION_DEL_BORDE, monkeypatch)
    _fallar_registros(cliente, CLIENTE_A)

    bloqueado = cliente.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE"), headers={"X-Forwarded-For": CLIENTE_A})
    otro = cliente.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE"), headers={"X-Forwarded-For": CLIENTE_B})

    assert bloqueado.status_code == 429
    assert otro.status_code == 422
    assert otro.json()["codigo"] == "invitacion_no_valida"


def test_fuera_del_compose_no_se_confia_en_x_forwarded_for(contexto, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    cliente = _servida_por_uvicorn(contexto, {}, DE_AFUERA, monkeypatch)
    _fallar_registros(cliente, CLIENTE_A)

    # Cambiar el encabezado no cambia el origen: sigue siendo la direccion
    # de la conexion, y esa ya agoto sus intentos.
    otro = cliente.post("/v1/registros", json=_cuerpo("NOEX-ISTE-NOEX-ISTE"), headers={"X-Forwarded-For": CLIENTE_B})

    assert otro.status_code == 429


def test_sin_la_variable_rige_el_valor_por_omision_de_uvicorn() -> None:
    from tools.proxies_del_borde import VARIABLE_PROXIES_CONFIABLES, proxies_confiables

    assert proxies_confiables({}) is None
    assert proxies_confiables({VARIABLE_PROXIES_CONFIABLES: "  "}) is None
    assert proxies_confiables({VARIABLE_PROXIES_CONFIABLES: SUBRED_DEL_COMPOSE}) == SUBRED_DEL_COMPOSE


def test_el_limite_libera_cuando_el_fallo_mas_viejo_sale_de_la_ventana() -> None:
    from hoppit.frontera.api_rest._limite_de_intentos import DemasiadosIntentos, LimiteDeIntentos

    ahora = [0.0]
    limite = LimiteDeIntentos(maximo=2, ventana_s=60, reloj=lambda: ahora[0])
    limite.registrar_fallo("a")
    ahora[0] = 10
    limite.registrar_fallo("a")
    with pytest.raises(DemasiadosIntentos) as error:
        limite.verificar("a")
    assert error.value.segundos_restantes == 50
    limite.verificar("b")  # otra direccion no se ve afectada
    ahora[0] = 61
    limite.verificar("a")


def test_crear_invitacion_muestra_el_codigo_una_vez_y_guarda_el_resumen(tmp_path: pathlib.Path, caplog) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "herramienta.sqlite3"
    with caplog.at_level(logging.INFO):
        assert crear_invitacion.main(["--rol", "transportista", "--vence-en-horas", "24", "--base-de-datos", str(base)]) == 0
    lineas = [r.getMessage() for r in caplog.records if r.name == "hoppit.tools.crear_invitacion"]
    codigo = next(l for l in lineas if l.startswith("Codigo")).rsplit(" ", 1)[-1]
    assert sum(codigo in l for l in lineas) == 1
    invitacion = RepositorioIdentidadSQLite(conectar(base)).invitacion(sha256_del_codigo(codigo))
    assert invitacion is not None
    assert invitacion.rol is Rol.TRANSPORTISTA
    assert invitacion.vence_en - invitacion.creada_en == dt.timedelta(hours=24)


def test_el_vencimiento_de_una_invitacion_tiene_limites(contexto) -> None:  # type: ignore[no-untyped-def]
    for horas in (0, -1, 24 * 31):
        with pytest.raises(VencimientoInvalido):
            contexto.identidad.administracion.crear_invitacion(Rol.OPERADOR, horas)
