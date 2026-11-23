"""Identidad y acceso, a nivel de servicio (ARQ-55, ARQ-56; sesion
2026-10-19B, bloque I).

Las contrasenas se generan dentro de cada prueba y no se escriben en ningun
archivo.
"""
from __future__ import annotations

import datetime as dt
import secrets
import sqlite3

import jwt
import pytest

from hoppit.frontera.identidad import puerto as identidad
from hoppit.frontera.identidad.puerto import (
    ALGORITMO_DE_FIRMA,
    AUDIENCIA,
    BLOQUEO_S,
    EMISOR,
    MAX_INTENTOS_FALLIDOS,
    AceptacionInvalida,
    AceptacionPedida,
    ContrasenaInvalida,
    CredencialesInvalidas,
    DocumentoDeTerminos,
    FirmanteDeSesiones,
    Medio,
    NoAutenticado,
    Rol,
    SesionVencida,
    TokenInvalido,
    TokenVencido,
    UsuarioBloqueado,
    crear_repositorio,
    crear_servicio,
    generar_clave_privada,
    verificar_token,
)

class Reloj:
    """Reloj que la prueba adelanta a mano. Arranca en el instante real,
    truncado al segundo: la verificacion del token usa el reloj real, y un
    instante fijo haria vencer los tokens de las pruebas con el paso de los
    dias."""

    def __init__(self) -> None:
        self.inicio = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        self.ahora = self.inicio

    def __call__(self) -> dt.datetime:
        return self.ahora


def _contrasena() -> str:
    return secrets.token_urlsafe(24)


@pytest.fixture
def reloj() -> Reloj:
    return Reloj()


@pytest.fixture
def servicio(tmp_path, reloj):  # type: ignore[no-untyped-def]
    return crear_servicio(crear_repositorio(tmp_path / "identidad.sqlite3"), generar_clave_privada(), reloj=reloj)


def _todas(pendientes):  # type: ignore[no-untyped-def]
    return [AceptacionPedida(p.documento.value, p.version, p.sha256) for p in pendientes]


def test_emision_y_verificacion_del_token() -> None:
    clave = generar_clave_privada()
    firmante = FirmanteDeSesiones(clave)
    ahora = dt.datetime.now(dt.timezone.utc)
    token = firmante.emitir(
        usuario_id="usuario-1",
        rol=Rol.OPERADOR,
        terminos=1,
        consentimientos=["transferencia_internacional"],
        emitido_en=ahora,
        vence_en=ahora + dt.timedelta(hours=10),
    )
    reclamos = verificar_token(token, clave.public_key())
    assert reclamos.usuario_id == "usuario-1"
    assert reclamos.rol is Rol.OPERADOR
    assert reclamos.terminos == 1
    assert reclamos.consentimientos == ("transferencia_internacional",)
    encabezado = jwt.get_unverified_header(token)
    assert encabezado["alg"] == ALGORITMO_DE_FIRMA


def _token(clave, **cambios):  # type: ignore[no-untyped-def]
    ahora = dt.datetime.now(dt.timezone.utc)
    return FirmanteDeSesiones(clave).emitir(
        usuario_id="usuario-1",
        rol=Rol.OPERADOR,
        terminos=1,
        consentimientos=[],
        emitido_en=cambios.get("emitido_en", ahora),
        vence_en=cambios.get("vence_en", ahora + dt.timedelta(hours=1)),
    )


def test_rechaza_un_token_alterado() -> None:
    clave = generar_clave_privada()
    token = _token(clave)
    encabezado, cuerpo, firma = token.split(".")
    otro = jwt.utils.base64url_encode(b'{"sub":"usuario-2"}').decode()
    with pytest.raises(TokenInvalido):
        verificar_token(f"{encabezado}.{otro}.{firma}", clave.public_key())
    with pytest.raises(TokenInvalido):
        verificar_token(token, generar_clave_privada().public_key())


def test_rechaza_otro_algoritmo() -> None:
    """Un token simetrico o sin firma se rechaza aunque su encabezado lo
    declare: el algoritmo lo fija la verificacion."""
    clave = generar_clave_privada()
    reclamos = jwt.decode(_token(clave), options={"verify_signature": False})
    simetrico = jwt.encode(reclamos, secrets.token_bytes(32), algorithm="HS256")
    sin_firma = jwt.encode(reclamos, None, algorithm="none")
    for token in (simetrico, sin_firma):
        with pytest.raises(TokenInvalido):
            verificar_token(token, clave.public_key())


def test_rechaza_un_token_vencido_fuera_de_la_tolerancia() -> None:
    clave = generar_clave_privada()
    ahora = dt.datetime.now(dt.timezone.utc)
    vencido = _token(clave, emitido_en=ahora - dt.timedelta(hours=11), vence_en=ahora - dt.timedelta(seconds=120))
    with pytest.raises(TokenVencido):
        verificar_token(vencido, clave.public_key())
    dentro = _token(clave, emitido_en=ahora - dt.timedelta(hours=1), vence_en=ahora - dt.timedelta(seconds=10))
    assert verificar_token(dentro, clave.public_key()).usuario_id == "usuario-1"


def test_reclamos_obligatorios(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    datos = jwt.decode(sesion.token, servicio.clave_publica, algorithms=[ALGORITMO_DE_FIRMA], audience=AUDIENCIA)
    assert datos["iss"] == EMISOR
    assert set(datos) >= {"iss", "aud", "sub", "rol", "iat", "exp", "jti", "terminos", "consentimientos"}
    assert datos["exp"] - datos["iat"] == 10 * 3600


def test_el_transportista_vence_a_las_16_horas(servicio, reloj) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("tr", "Transportista", Rol.TRANSPORTISTA, contrasena)
    sesion = servicio.ingresar("tr", contrasena)
    assert sesion.vence_en - reloj.inicio == dt.timedelta(hours=16)


def test_contrasena_de_menos_de_12_caracteres(servicio) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ContrasenaInvalida):
        servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, "corta")


def test_mensaje_generico_ante_un_ingreso_fallido(servicio) -> None:  # type: ignore[no-untyped-def]
    """Usuario inexistente y contrasena erronea dan el mismo error y el
    mismo mensaje."""
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, _contrasena())
    with pytest.raises(CredencialesInvalidas) as inexistente:
        servicio.ingresar("nadie", _contrasena())
    with pytest.raises(CredencialesInvalidas) as erronea:
        servicio.ingresar("op", _contrasena())
    assert str(inexistente.value) == str(erronea.value)


def test_bloqueo_tras_cinco_fallos_seguidos(servicio, reloj) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        with pytest.raises(CredencialesInvalidas):
            servicio.ingresar("op", _contrasena())
    with pytest.raises(UsuarioBloqueado) as quinto:
        servicio.ingresar("op", _contrasena())
    assert quinto.value.segundos_restantes == BLOQUEO_S

    # Bloqueado, ni la contrasena correcta entra, y el tiempo restante baja.
    reloj.ahora = reloj.inicio + dt.timedelta(seconds=300)
    with pytest.raises(UsuarioBloqueado) as bloqueado:
        servicio.ingresar("op", contrasena)
    assert bloqueado.value.segundos_restantes == BLOQUEO_S - 300

    # Pasado el bloqueo, entra.
    reloj.ahora = reloj.inicio + dt.timedelta(seconds=BLOQUEO_S + 1)
    assert servicio.ingresar("op", contrasena).usuario.usuario == "op"


def test_sin_bloqueo_por_intentos_se_puede_seguir_probando(tmp_path, reloj) -> None:  # type: ignore[no-untyped-def]
    """Etapa de prueba (2026-11-02): sin bloqueo, ni los fallos seguidos ni un
    bloqueo ya guardado impiden entrar con la contrasena correcta."""
    repositorio = crear_repositorio(tmp_path / "identidad.sqlite3")
    con_bloqueo = crear_servicio(repositorio, generar_clave_privada(), reloj=reloj)
    contrasena = _contrasena()
    con_bloqueo.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        with pytest.raises(CredencialesInvalidas):
            con_bloqueo.ingresar("op", _contrasena())
    with pytest.raises(UsuarioBloqueado):
        con_bloqueo.ingresar("op", _contrasena())

    sin_bloqueo = crear_servicio(repositorio, generar_clave_privada(), reloj=reloj, bloqueo_por_intentos=False)
    for _ in range(MAX_INTENTOS_FALLIDOS * 2):
        with pytest.raises(CredencialesInvalidas):
            sin_bloqueo.ingresar("op", _contrasena())
    assert sin_bloqueo.ingresar("op", contrasena).usuario.usuario == "op"


def test_un_ingreso_correcto_reinicia_la_cuenta_de_fallos(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        with pytest.raises(CredencialesInvalidas):
            servicio.ingresar("op", _contrasena())
    servicio.ingresar("op", contrasena)
    with pytest.raises(CredencialesInvalidas):
        servicio.ingresar("op", _contrasena())


def test_documentos_por_rol_y_aceptacion(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("tr", "Transportista", Rol.TRANSPORTISTA, contrasena)
    sesion = servicio.ingresar("tr", contrasena)
    assert {p.documento for p in sesion.pendientes} == set(DocumentoDeTerminos)

    autenticado = servicio.autenticar(sesion.token)
    nueva = servicio.aceptar(autenticado, _todas(sesion.pendientes), Medio.APLICACION, "dispositivo-de-prueba")
    assert nueva.pendientes == ()
    assert nueva.vence_en == sesion.vence_en
    reclamos = verificar_token(nueva.token, servicio.clave_publica)
    assert reclamos.terminos == 2
    assert set(reclamos.consentimientos) == {"transferencia_internacional", "geolocalizacion"}


def test_el_operador_no_acepta_geolocalizacion(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    assert DocumentoDeTerminos.CONSENTIMIENTO_GEOLOCALIZACION not in {p.documento for p in sesion.pendientes}
    geolocalizacion = next(
        d for d in servicio.documentos_del_rol(Rol.TRANSPORTISTA)
        if d.documento is DocumentoDeTerminos.CONSENTIMIENTO_GEOLOCALIZACION
    )
    with pytest.raises(AceptacionInvalida):
        servicio.aceptar(
            servicio.autenticar(sesion.token),
            [AceptacionPedida(geolocalizacion.documento.value, geolocalizacion.version, geolocalizacion.sha256)],
            Medio.WEB,
        )


def test_la_aceptacion_exige_version_y_sha256_vigentes(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    terminos = sesion.pendientes[0]
    autenticado = servicio.autenticar(sesion.token)
    with pytest.raises(AceptacionInvalida):
        servicio.aceptar(autenticado, [AceptacionPedida(terminos.documento.value, terminos.version + 1, terminos.sha256)], Medio.WEB)
    with pytest.raises(AceptacionInvalida):
        servicio.aceptar(autenticado, [AceptacionPedida(terminos.documento.value, terminos.version, "0" * 64)], Medio.WEB)
    assert len(servicio.pendientes(autenticado.usuario)) == len(sesion.pendientes)


def test_aceptaciones_de_solo_insercion(tmp_path, reloj) -> None:  # type: ignore[no-untyped-def]
    base = tmp_path / "identidad.sqlite3"
    servicio = crear_servicio(crear_repositorio(base), generar_clave_privada(), reloj=reloj)
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    servicio.aceptar(servicio.autenticar(sesion.token), _todas(sesion.pendientes), Medio.WEB)

    conexion = sqlite3.connect(base)
    try:
        assert conexion.execute("SELECT COUNT(*) FROM aceptaciones").fetchone()[0] == len(sesion.pendientes)
        with pytest.raises(sqlite3.IntegrityError):
            conexion.execute("UPDATE aceptaciones SET revocado = 1")
        with pytest.raises(sqlite3.IntegrityError):
            conexion.execute("DELETE FROM aceptaciones")
    finally:
        conexion.close()


def test_un_usuario_desactivado_se_rechaza_en_el_pedido_siguiente(servicio) -> None:  # type: ignore[no-untyped-def]
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    servicio.autenticar(sesion.token)
    servicio.administracion.desactivar_usuario("op")
    with pytest.raises(NoAutenticado):
        servicio.autenticar(sesion.token)
    with pytest.raises(CredencialesInvalidas):
        servicio.ingresar("op", contrasena)


def test_sesion_vencida(servicio, reloj) -> None:  # type: ignore[no-untyped-def]
    """El servicio emite y verifica con el mismo reloj: pasadas las 10 h del
    operador y la tolerancia de 60 s, la sesion vencio."""
    contrasena = _contrasena()
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, contrasena)
    sesion = servicio.ingresar("op", contrasena)
    reloj.ahora = reloj.inicio + dt.timedelta(hours=10, seconds=59)
    servicio.autenticar(sesion.token)
    reloj.ahora = reloj.inicio + dt.timedelta(hours=10, seconds=61)
    with pytest.raises(SesionVencida):
        servicio.autenticar(sesion.token)


def test_restablecer_contrasena_levanta_el_bloqueo(servicio) -> None:  # type: ignore[no-untyped-def]
    servicio.administracion.crear_usuario("op", "Operador", Rol.OPERADOR, _contrasena())
    for _ in range(MAX_INTENTOS_FALLIDOS - 1):
        with pytest.raises(CredencialesInvalidas):
            servicio.ingresar("op", _contrasena())
    with pytest.raises(UsuarioBloqueado):
        servicio.ingresar("op", _contrasena())
    nueva = _contrasena()
    servicio.administracion.restablecer_contrasena("op", nueva)
    assert servicio.ingresar("op", nueva).usuario.usuario == "op"


def test_la_verificacion_del_puerto_no_abre_la_base(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """`verificar_token` es una funcion pura: verifica con la clave publica
    sin abrir ninguna base (seccion 4.3)."""
    clave = generar_clave_privada()
    token = _token(clave)

    def prohibido(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("la verificacion abrio una base")

    monkeypatch.setattr(sqlite3, "connect", prohibido)
    assert identidad.verificar_token(token, clave.public_key()).usuario_id == "usuario-1"


def test_los_terminos_verifican_su_sha256(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Un texto cambiado sin cambiar la version no se carga."""
    import shutil

    copia = tmp_path / "terminos"
    shutil.copytree(identidad.DIRECTORIO_DE_TERMINOS, copia)
    archivo = next(copia.glob("terminos_de_uso_*.md"))
    archivo.write_text(archivo.read_text(encoding="utf-8") + "\nAgregado.\n", encoding="utf-8")
    with pytest.raises(identidad.TerminosAlterados):
        identidad.leer_documentos_vigentes(copia)


def test_los_terminos_dicen_que_no_tuvieron_revision_juridica() -> None:
    """Cada documento lleva su rotulo y no le queda nada sin completar."""
    for documento in identidad.leer_documentos_vigentes().values():
        assert "no fue revisado jurídicamente." in documento.texto
        assert "[a completar" not in documento.texto
