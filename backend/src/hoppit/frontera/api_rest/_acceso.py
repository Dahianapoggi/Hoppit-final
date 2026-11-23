"""Acceso a la API: ingreso, terminos y autorizacion de cada operacion
(ARQ-55, ARQ-56; seccion 4 del diseno de Chat A; D-C3).

Toda operacion de `/v1` exige el encabezado `Authorization: Bearer`, salvo
`POST /v1/sesiones`, `POST /v1/registros` y `/v1/estado`; `/estado` sigue
sin versionar y publico. `GET /v1/terminos/vigentes` y `POST /v1/terminos/aceptaciones`
exigen token aunque falten aceptaciones: son el camino para completarlas.
El resto exige ademas los terminos aceptados y el rol `operador`. El
transportista no tiene ninguna de las operaciones actuales: su vinculo con
la Ruta llega con el transportista en el despacho (D-C2).

La identidad la resuelve `frontera/identidad` por su puerto; aca solo se
traduce a HTTP.
"""
from __future__ import annotations

import datetime as dt
import http
from collections.abc import Callable
from typing import Annotated

import fastapi
import pydantic
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hoppit.frontera.identidad.puerto import (
    LONGITUD_MINIMA_DE_CONTRASENA,
    AceptacionInvalida,
    AceptacionPedida,
    ContrasenaInvalida,
    CredencialesInvalidas,
    DocumentoVigente,
    InvitacionNoValida,
    Medio,
    NoAutenticado,
    Rol,
    ServicioDeIdentidad,
    SesionEmitida,
    SesionVencida,
    UsuarioAutenticado,
    UsuarioBloqueado,
    UsuarioExistente,
    credenciales_ocultas,
)
from hoppit.shared.errores import ErrorDeDominio

from ._limite_de_intentos import (
    MAX_REGISTROS_FALLIDOS_POR_ORIGEN,
    VENTANA_DE_REGISTROS_S,
    DemasiadosIntentos,
    LimiteDeIntentos,
)
from ._errores import (
    CodigoDeError,
    DetalleDocumentosRechazados,
    DetalleRolNoAutorizado,
    DetalleTerminosNoAceptados,
    DetalleUsuarioBloqueado,
    DocumentoRechazadoPublicado,
    ErrorDeContrato,
    respuestas_de_error,
)

NOMBRE_DEL_ESQUEMA_DE_SEGURIDAD = "bearer"
TIPO_DE_TOKEN = "Bearer"
ENCABEZADO_WWW_AUTHENTICATE = "WWW-Authenticate"
ENCABEZADO_RETRY_AFTER = "Retry-After"

_ESQUEMA_BEARER = HTTPBearer(
    scheme_name=NOMBRE_DEL_ESQUEMA_DE_SEGURIDAD,
    description=(
        "Token de sesion de `POST /v1/sesiones` (JWT firmado con EdDSA), en el "
        "encabezado `Authorization: Bearer <token>`. Vence a las 10 h para el "
        "operador y a las 16 h para el transportista; al vencer se vuelve a "
        "ingresar."
    ),
    auto_error=False,
)

Credenciales = Annotated[HTTPAuthorizationCredentials | None, fastapi.Security(_ESQUEMA_BEARER)]

# --- Esquemas -------------------------------------------------------------


# Largos maximos del registro [supuesto]: acotan lo que un cliente puede
# escribir en la base sin cuenta. El ingreso usa los mismos para el usuario y
# la contrasena (2026-10-28C, H6): es la operacion sin autenticacion que llega
# a Argon2, y un cuerpo mas largo se rechaza con 422 antes de verificarlo.
LARGO_MAXIMO_DEL_USUARIO = 64
LARGO_MAXIMO_DEL_NOMBRE_VISIBLE = 100
LARGO_MAXIMO_DE_LA_CONTRASENA = 256
LARGO_MAXIMO_DEL_CODIGO = 64
ORIGEN_DESCONOCIDO = "desconocido"


class SesionSolicitada(pydantic.BaseModel):
    """Usuario y contrasena. El cuerpo de esta operacion no llega a los
    registros (seccion 4.2)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    usuario: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DEL_USUARIO)
    contrasena: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DE_LA_CONTRASENA)


class RegistroSolicitado(pydantic.BaseModel):
    """Alta con un codigo de invitacion (B-246, B-248). El rol no se pide:
    es el de la invitacion. El cuerpo de esta operacion no llega a los
    registros, como el del ingreso."""

    model_config = pydantic.ConfigDict(extra="forbid")

    codigo: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DEL_CODIGO)
    usuario: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DEL_USUARIO)
    nombre_visible: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DEL_NOMBRE_VISIBLE)
    contrasena: str = pydantic.Field(min_length=1, max_length=LARGO_MAXIMO_DE_LA_CONTRASENA)
    """Al menos 12 caracteres, sin reglas de composicion (ARQ-55). La
    longitud la verifica el dominio y responde `contrasena_corta`."""


class UsuarioRegistrado(pydantic.BaseModel):
    """La cuenta creada. El registro no inicia sesion: se ingresa despues,
    con `POST /v1/sesiones`, y en el primer ingreso se aceptan los terminos."""

    usuario: str
    nombre_visible: str
    rol: Rol


class DocumentoPendiente(pydantic.BaseModel):
    """Un documento que el usuario todavia no acepto en su version vigente."""

    documento: str
    version: int
    sha256: str
    titulo: str

    @classmethod
    def desde(cls, vigente: DocumentoVigente) -> DocumentoPendiente:
        return cls(
            documento=vigente.documento.value,
            version=vigente.version,
            sha256=vigente.sha256,
            titulo=vigente.titulo,
        )


class SesionPublicada(pydantic.BaseModel):
    """Lo que recibe quien ingresa o acepta terminos."""

    token: str
    """JWT firmado con EdDSA. Va en `Authorization: Bearer` en cada pedido.
    En la interfaz se guarda en `sessionStorage` (seccion 4.3)."""

    tipo: str = TIPO_DE_TOKEN
    vence_en: dt.datetime
    """Instante de vencimiento, en UTC. Sin token de renovacion: al vencer,
    se vuelve a ingresar."""

    rol: Rol
    nombre_visible: str
    pendientes: tuple[DocumentoPendiente, ...]
    """Documentos que faltan aceptar. Mientras haya alguno, toda operacion
    salvo las de sesion y terminos responde 403 `terminos_no_aceptados`."""

    @classmethod
    def desde(cls, sesion: SesionEmitida) -> SesionPublicada:
        return cls(
            token=sesion.token,
            vence_en=sesion.vence_en,
            rol=sesion.usuario.rol,
            nombre_visible=sesion.usuario.nombre_visible,
            pendientes=tuple(DocumentoPendiente.desde(p) for p in sesion.pendientes),
        )


class DocumentoDeTerminosPublicado(pydantic.BaseModel):
    """Un documento vigente con su texto completo (ARQ-56)."""

    documento: str
    version: int
    sha256: str
    """SHA-256 del texto en UTF-8 con fin de linea `\\n`. La aceptacion lo
    repite, de modo que quede registrado que texto se acepto."""

    titulo: str
    texto: str
    """Markdown. El texto lo redacta el equipo, sin revision juridica."""

    aceptado: bool
    """Si el usuario ya lo acepto en esta version."""


class TerminosVigentes(pydantic.BaseModel):
    """Los documentos que corresponden al rol del usuario."""

    rol: Rol
    documentos: tuple[DocumentoDeTerminosPublicado, ...]


class AceptacionSolicitada(pydantic.BaseModel):
    """Un documento aceptado, con la version y el SHA-256 del texto leido."""

    model_config = pydantic.ConfigDict(extra="forbid")

    documento: str
    version: int
    sha256: str


class AceptacionesSolicitadas(pydantic.BaseModel):
    """Una o mas aceptaciones, cada una un item separado (ley 25.326, art. 5)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    aceptaciones: tuple[AceptacionSolicitada, ...] = pydantic.Field(min_length=1)
    medio: Medio
    dispositivo_id: str | None = None
    """Identificador del dispositivo, opcional (la aplicacion del
    transportista)."""


# --- Traduccion de errores ----------------------------------------------------


def _no_autenticado(error: NoAutenticado) -> ErrorDeContrato:
    codigo = (
        CodigoDeError.SESION_VENCIDA
        if isinstance(error, SesionVencida)
        else CodigoDeError.NO_AUTENTICADO
    )
    return ErrorDeContrato(
        codigo=codigo,
        mensaje=str(error),
        estado=http.HTTPStatus.UNAUTHORIZED,
        encabezados={ENCABEZADO_WWW_AUTHENTICATE: TIPO_DE_TOKEN},
    )


def _autenticar(identidad: ServicioDeIdentidad, credenciales: HTTPAuthorizationCredentials | None) -> UsuarioAutenticado:
    token = credenciales.credentials if credenciales is not None else None
    try:
        return identidad.autenticar(token)
    except NoAutenticado as error:
        raise _no_autenticado(error) from error


def dependencia_de_sesion(identidad: ServicioDeIdentidad) -> Callable[..., UsuarioAutenticado]:
    """Exige un token valido de un usuario activo, falten o no
    aceptaciones."""

    def con_sesion(credenciales: Credenciales) -> UsuarioAutenticado:
        return _autenticar(identidad, credenciales)

    return con_sesion


def dependencia_de_operador(identidad: ServicioDeIdentidad) -> Callable[..., UsuarioAutenticado]:
    """Exige sesion, los terminos aceptados y el rol `operador`."""
    return dependencia_de_rol(identidad, Rol.OPERADOR)


def dependencia_de_transportista(identidad: ServicioDeIdentidad) -> Callable[..., UsuarioAutenticado]:
    """Exige sesion, los terminos aceptados y el rol `transportista`
    (2026-10-27B, T: `GET /v1/yo/ruta-en-ejecucion`)."""
    return dependencia_de_rol(identidad, Rol.TRANSPORTISTA)


def dependencia_de_cliente_comercial(identidad: ServicioDeIdentidad) -> Callable[..., UsuarioAutenticado]:
    """Exige sesion, los terminos aceptados y el rol `cliente_comercial`
    (CU-04 por su integracion; 2026-11-22A)."""
    return dependencia_de_rol(identidad, Rol.CLIENTE_COMERCIAL)


def dependencia_de_rol(identidad: ServicioDeIdentidad, rol: Rol) -> Callable[..., UsuarioAutenticado]:
    """Exige sesion, los terminos aceptados y el rol pedido."""

    def de_operador(credenciales: Credenciales) -> UsuarioAutenticado:
        autenticado = _autenticar(identidad, credenciales)
        if autenticado.pendientes:
            raise ErrorDeContrato(
                codigo=CodigoDeError.TERMINOS_NO_ACEPTADOS,
                mensaje=(
                    "Faltan aceptar terminos o consentimientos vigentes: "
                    + ", ".join(p.documento.value for p in autenticado.pendientes)
                ),
                estado=http.HTTPStatus.FORBIDDEN,
                detalle=DetalleTerminosNoAceptados(
                    pendientes=tuple(p.documento.value for p in autenticado.pendientes)
                ),
            )
        if autenticado.usuario.rol is not rol:
            raise ErrorDeContrato(
                codigo=CodigoDeError.ROL_NO_AUTORIZADO,
                mensaje=(
                    f"El rol {autenticado.usuario.rol.value} no puede usar esta operacion: "
                    f"es del rol {rol.value}"
                ),
                estado=http.HTTPStatus.FORBIDDEN,
                detalle=DetalleRolNoAutorizado(
                    rol=autenticado.usuario.rol.value, roles_admitidos=(rol.value,)
                ),
            )
        return autenticado

    return de_operador


DESCRIPCION_401 = (
    "`no_autenticado` si falta el token, no se puede verificar o el usuario "
    "ya no esta activo; `sesion_vencida` si el token vencio. Hay que volver "
    "a ingresar con `POST /v1/sesiones`."
)
DESCRIPCION_403 = (
    "`terminos_no_aceptados` si faltan aceptar terminos o consentimientos "
    "vigentes (se aceptan con `POST /v1/terminos/aceptaciones`); "
    "`rol_no_autorizado` si el rol del usuario no tiene esta operacion: las "
    "de `/v1/yo` son del rol `transportista` y todas las demas, del rol `operador`."
)


def respuestas_de_acceso() -> dict[int | str, dict[str, object]]:
    """Los errores de acceso de toda operacion protegida, declarados de
    forma explicita (B-102)."""
    return respuestas_de_error(
        (http.HTTPStatus.UNAUTHORIZED, DESCRIPCION_401),
        (http.HTTPStatus.FORBIDDEN, DESCRIPCION_403),
    )


# --- Operaciones -------------------------------------------------------------


def agregar_operaciones_de_acceso(
    publico: fastapi.APIRouter, identidad: ServicioDeIdentidad
) -> None:
    """`/v1/sesiones`, `/v1/registros` y `/v1/terminos/*`. `publico` es el
    router de `/v1` sin la dependencia de operador."""
    con_sesion = dependencia_de_sesion(identidad)
    limite_de_registros = LimiteDeIntentos()

    @publico.post(
        "/registros",
        response_model=UsuarioRegistrado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acceso"],
        summary="Crear una cuenta con un codigo de invitacion",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.CONFLICT,
                "`usuario_existente`: el nombre de usuario ya esta tomado. Solo "
                "se informa con un codigo de invitacion valido.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`invitacion_no_valida`: el codigo no existe, vencio o ya se uso "
                "(una sola respuesta para los tres); `contrasena_corta`: menos de "
                f"{LONGITUD_MINIMA_DE_CONTRASENA} caracteres; "
                "`datos_de_usuario_invalidos`: usuario o nombre visible en "
                "blanco; `peticion_mal_formada` si el cuerpo no cumple el esquema.",
            ),
            (
                http.HTTPStatus.TOO_MANY_REQUESTS,
                f"`demasiados_intentos`: {MAX_REGISTROS_FALLIDOS_POR_ORIGEN} "
                f"registros fallidos desde la misma direccion en "
                f"{VENTANA_DE_REGISTROS_S} s. El detalle y el encabezado "
                "`Retry-After` dicen cuantos segundos faltan. El limite vive en "
                "la memoria del servidor.",
            ),
        ),
    )
    def registrar(solicitud: RegistroSolicitado, pedido: fastapi.Request) -> UsuarioRegistrado:
        """Crea el usuario con el rol de la invitacion y la marca como usada
        en la misma transaccion (B-246, B-248). No inicia sesion. Sin
        verificacion por correo: la cuenta no guarda correo (ARQ-55)."""
        origen = pedido.client.host if pedido.client is not None else ORIGEN_DESCONOCIDO
        with credenciales_ocultas(solicitud.contrasena, solicitud.codigo):
            try:
                limite_de_registros.verificar(origen)
            except DemasiadosIntentos as error:
                raise ErrorDeContrato(
                    codigo=CodigoDeError.DEMASIADOS_INTENTOS,
                    mensaje=str(error),
                    estado=http.HTTPStatus.TOO_MANY_REQUESTS,
                    detalle=DetalleUsuarioBloqueado(segundos_restantes=error.segundos_restantes),
                    encabezados={ENCABEZADO_RETRY_AFTER: str(error.segundos_restantes)},
                ) from error
            try:
                nuevo = identidad.registrar(
                    solicitud.codigo, solicitud.usuario, solicitud.nombre_visible, solicitud.contrasena
                )
            except InvitacionNoValida as error:
                limite_de_registros.registrar_fallo(origen)
                raise ErrorDeContrato(
                    codigo=CodigoDeError.INVITACION_NO_VALIDA,
                    mensaje=str(error),
                    estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
                ) from error
            except UsuarioExistente as error:
                limite_de_registros.registrar_fallo(origen)
                raise ErrorDeContrato(
                    codigo=CodigoDeError.USUARIO_EXISTENTE,
                    mensaje=f"El usuario {solicitud.usuario.strip()} ya existe: hay que elegir otro nombre",
                    estado=http.HTTPStatus.CONFLICT,
                ) from error
            except ContrasenaInvalida as error:
                limite_de_registros.registrar_fallo(origen)
                raise ErrorDeContrato(
                    codigo=CodigoDeError.CONTRASENA_CORTA,
                    mensaje=str(error),
                    estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
                ) from error
            except ErrorDeDominio as error:
                limite_de_registros.registrar_fallo(origen)
                raise ErrorDeContrato(
                    codigo=CodigoDeError.DATOS_DE_USUARIO_INVALIDOS,
                    mensaje=str(error),
                    estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
                ) from error
        return UsuarioRegistrado(usuario=nuevo.usuario, nombre_visible=nuevo.nombre_visible, rol=nuevo.rol)

    @publico.post(
        "/sesiones",
        response_model=SesionPublicada,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acceso"],
        summary="Ingresar con usuario y contrasena",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNAUTHORIZED,
                "`credenciales_invalidas`: usuario o contrasena incorrectos. El "
                "mensaje no distingue un usuario inexistente de una contrasena "
                "erronea.",
            ),
            (
                http.HTTPStatus.LOCKED,
                "`usuario_bloqueado`: despues de 5 intentos fallidos seguidos el "
                "usuario queda bloqueado 900 s, aun con la contrasena correcta. "
                "El detalle y el encabezado `Retry-After` dicen cuantos segundos "
                "faltan.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema.",
            ),
        ),
    )
    def ingresar(solicitud: SesionSolicitada) -> SesionPublicada:
        """Emite el token de sesion. La respuesta lista los documentos que
        faltan aceptar: hasta aceptarlos, las demas operaciones responden
        403 `terminos_no_aceptados`."""
        with credenciales_ocultas(solicitud.contrasena):
            try:
                sesion = identidad.ingresar(solicitud.usuario, solicitud.contrasena)
            except CredencialesInvalidas as error:
                raise ErrorDeContrato(
                    codigo=CodigoDeError.CREDENCIALES_INVALIDAS,
                    mensaje=str(error),
                    estado=http.HTTPStatus.UNAUTHORIZED,
                    encabezados={ENCABEZADO_WWW_AUTHENTICATE: TIPO_DE_TOKEN},
                ) from error
            except UsuarioBloqueado as error:
                raise ErrorDeContrato(
                    codigo=CodigoDeError.USUARIO_BLOQUEADO,
                    mensaje=str(error),
                    estado=http.HTTPStatus.LOCKED,
                    detalle=DetalleUsuarioBloqueado(segundos_restantes=error.segundos_restantes),
                    encabezados={ENCABEZADO_RETRY_AFTER: str(error.segundos_restantes)},
                ) from error
        return SesionPublicada.desde(sesion)

    @publico.get(
        "/terminos/vigentes",
        response_model=TerminosVigentes,
        tags=["Acceso"],
        summary="Consultar los terminos y consentimientos vigentes del rol",
        responses=respuestas_de_error((http.HTTPStatus.UNAUTHORIZED, DESCRIPCION_401)),
    )
    def terminos_vigentes(
        autenticado: UsuarioAutenticado = fastapi.Depends(con_sesion),
    ) -> TerminosVigentes:
        """Cada documento con su texto, su version y su SHA-256, y si el
        usuario ya lo acepto. Responde aunque falten aceptaciones."""
        faltan = {p.documento for p in autenticado.pendientes}
        return TerminosVigentes(
            rol=autenticado.usuario.rol,
            documentos=tuple(
                DocumentoDeTerminosPublicado(
                    documento=d.documento.value,
                    version=d.version,
                    sha256=d.sha256,
                    titulo=d.titulo,
                    texto=d.texto,
                    aceptado=d.documento not in faltan,
                )
                for d in identidad.documentos_del_rol(autenticado.usuario.rol)
            ),
        )

    @publico.post(
        "/terminos/aceptaciones",
        response_model=SesionPublicada,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acceso"],
        summary="Aceptar terminos o consentimientos y recibir un token actualizado",
        responses=respuestas_de_error(
            (http.HTTPStatus.UNAUTHORIZED, DESCRIPCION_401),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`aceptacion_invalida` si algun documento no corresponde al rol, o "
                "no es la version vigente o su SHA-256; no se registra ninguno. "
                "`peticion_mal_formada` si el cuerpo no cumple el esquema.",
            ),
        ),
    )
    def aceptar_terminos(
        solicitud: AceptacionesSolicitadas,
        autenticado: UsuarioAutenticado = fastapi.Depends(con_sesion),
    ) -> SesionPublicada:
        """Registra una fila por documento en `aceptaciones`, que es de solo
        insercion, y responde un token nuevo con los reclamos `terminos` y
        `consentimientos` actualizados y el mismo vencimiento que el
        presentado. Que esta operacion responda un token es una
        interpretacion: el diseno pone los terminos en el token y no dice
        como se actualizan."""
        try:
            sesion = identidad.aceptar(
                autenticado,
                [AceptacionPedida(a.documento, a.version, a.sha256) for a in solicitud.aceptaciones],
                solicitud.medio,
                solicitud.dispositivo_id,
            )
        except AceptacionInvalida as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.ACEPTACION_INVALIDA,
                mensaje=str(error),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
                detalle=DetalleDocumentosRechazados(
                    documentos=tuple(
                        DocumentoRechazadoPublicado(documento=r.documento, motivo=r.motivo)
                        for r in error.rechazados
                    )
                ),
            ) from error
        return SesionPublicada.desde(sesion)
