"""API del servicio de telemetria y seguimiento (ARQ-52, ARQ-53, ARQ-59;
secciones 3.2 a 3.5 y 6.3 de ARQ_Pedido2; 2026-10-27B, T).

Corre en su propio proceso, con su propia base, y no carga el grafo ni las
capacidades del nucleo: verifica los tokens con la clave publica, sin abrir
la base del nucleo (seccion 4.3). Sus operaciones:

- `POST /v1/telemetria/lotes`: las posiciones del dispositivo del
  transportista, clasificadas una por una;
- `GET /v1/telemetria/rutas/{id}/ultima-posicion`: para la interfaz del
  operador, con la antiguedad contada desde la captura;
- `POST /interno/eventos`: los eventos del nucleo, con token de servicio.
  El borde la cierra: solo se alcanza desde la red interna;
- `/estado`: la version y si la base responde.

Los errores van en el mismo formato que el nucleo, `{codigo, mensaje,
detalle}`, pero este adaptador no importa el del nucleo: son servicios
distintos.
"""
from __future__ import annotations

import datetime as dt
import http
import importlib.metadata
import json
import logging
import sqlite3
from collections.abc import Callable
from typing import Any

import fastapi
import pydantic
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from hoppit.frontera.identidad.puerto import (
    AUDIENCIA_TELEMETRIA,
    DocumentoDeTerminos,
    Rol,
    TokenInvalido,
    TokenVencido,
    leer_documentos_vigentes,
    verificar_token,
    verificar_token_de_servicio,
)
from hoppit.seguimiento_flota.puerto import (
    MAX_BYTES_DEL_LOTE,
    MAX_POSICIONES_POR_LOTE,
    DispositivoInvalido,
    EventoDesconocido,
    PosicionRecibida,
    ResultadoDelLote,
    SecuenciaFueraDeOrden,
    ServicioDeSeguimiento,
    SinPosiciones,
)
from hoppit.shared.tiempo import instante_actual

_LOG = logging.getLogger(__name__)

NOMBRE_DEL_SERVICIO = "HOPPIT · telemetria y seguimiento"
VERSION = importlib.metadata.version("hoppit")
PREFIJO = "/v1/telemetria"
# Los consentimientos que un lote exige en el token (seccion 4.4).
CONSENTIMIENTOS_DEL_LOTE = ("geolocalizacion", "transferencia_internacional")
ORIGENES_PERMITIDOS = ["*"]
ESQUEMA_BEARER = "bearer"


class ErrorDeTelemetria(Exception):
    def __init__(self, estado: http.HTTPStatus, codigo: str, mensaje: str, detalle: Any = None) -> None:
        super().__init__(mensaje)
        self.estado = estado
        self.codigo = codigo
        self.mensaje = mensaje
        self.detalle = detalle


class ErrorPublicado(pydantic.BaseModel):
    codigo: str
    mensaje: str
    detalle: Any = None


def _respuestas(*casos: tuple[http.HTTPStatus, str]) -> dict[int | str, dict[str, Any]]:
    return {int(estado): {"model": ErrorPublicado, "description": texto} for estado, texto in casos}


# --- Esquemas --------------------------------------------------------------


class PosicionSolicitada(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    id: str = pydantic.Field(min_length=1, max_length=64)
    """Generado en el dispositivo al capturar; no cambia en los reintentos. UUID
    version 4 en forma canonica (minusculas): si no lo es, la posicion se
    rechaza con `identificador_invalido`, definitivo."""
    capturada_en: dt.datetime
    latitud: float
    longitud: float
    precision_m: float = pydantic.Field(ge=0)
    velocidad_mps: float | None = None
    rumbo_grados: float | None = None


class LoteSolicitado(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    ruta_id: str = pydantic.Field(min_length=1)
    dispositivo_id: str = pydantic.Field(min_length=1, max_length=64)
    """UUID version 4 en forma canonica: si no lo es, el lote entero se rechaza
    con 422 `dispositivo_invalido`."""
    posiciones: list[PosicionSolicitada]
    descartadas_por_cola_llena: int = pydantic.Field(default=0, ge=0)
    """Cuantas posiciones descarto el dispositivo por cola llena desde el
    lote anterior (seccion 3.4). Se registra; no cambia la clasificacion."""


class RechazoPublicado(pydantic.BaseModel):
    id: str
    motivo: str
    """`identificador_invalido`, `ejecucion_desconocida` (el unico reintentable),
    `ruta_no_asignada`, `fuera_de_ejecucion`, `marca_temporal_invalida` o
    `coordenadas_invalidas`."""
    reintentable: bool


class LoteClasificado(pydantic.BaseModel):
    aceptadas: tuple[str, ...]
    duplicadas: tuple[str, ...]
    rechazadas: tuple[RechazoPublicado, ...]


class PosicionRecientePublicada(pydantic.BaseModel):
    """Una posicion reciente de la Ruta vigente, tal como la informo el
    dispositivo (D18 del encargo 2026-11-22A)."""

    latitud: float
    longitud: float
    capturada_en: dt.datetime


class UltimaPosicionPublicada(pydantic.BaseModel):
    """La ultima posicion de una Ruta (FR-17; ARQ-53). Es la que informo el
    dispositivo, sin ajustarla al grafo."""

    ruta_id: str
    latitud: float
    longitud: float
    precision_m: float
    capturada_en: dt.datetime
    recibida_en: dt.datetime
    antiguedad_s: float
    """Segundos desde la captura hasta esta respuesta, con el reloj del servidor."""
    antiguedad_sobre_la_recepcion: bool
    """Verdadero si la captura figuraba despues de la recepcion: la antiguedad se
    cuenta entonces desde la recepcion."""
    estado_de_ejecucion: str
    """`en_ejecucion`, o `desconocida` si el servicio no tiene la ejecucion."""


class EventoSolicitado(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid")

    evento_id: str
    tipo: str
    agregado_id: str
    secuencia_del_agregado: int = pydantic.Field(ge=1)
    cuerpo: dict[str, Any]


class EventoRegistrado(pydantic.BaseModel):
    nuevo: bool
    """Falso si el evento ya se habia recibido: se descarto."""


class EstadoDeTelemetria(pydantic.BaseModel):
    producto: str
    version: str
    base: str
    """`disponible` o `no_responde`."""


# --- Aplicacion -------------------------------------------------------------


def crear_app_de_telemetria(
    seguimiento: ServicioDeSeguimiento,
    clave_publica: Ed25519PublicKey,
    base_disponible: Callable[[], bool] = lambda: True,
    reloj: Callable[[], dt.datetime] = instante_actual,
) -> fastapi.FastAPI:
    app = fastapi.FastAPI(
        title=NOMBRE_DEL_SERVICIO,
        version=VERSION,
        description=(
            "Servicio de telemetria y seguimiento de HOPPIT (ARQ-52, ARQ-53, ARQ-59). "
            "Recibe las posiciones de las Rutas en ejecucion, publica la ultima con su antiguedad "
            "y recibe los eventos del nucleo por la salida transaccional, en una ruta que el "
            "borde no expone."
        ),
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=ORIGENES_PERMITIDOS, allow_methods=["*"], allow_headers=["*"]
    )
    vigentes = leer_documentos_vigentes()
    version_de_los_terminos = vigentes[DocumentoDeTerminos.TERMINOS_DE_USO].version

    @app.exception_handler(ErrorDeTelemetria)
    def _error(_: fastapi.Request, error: ErrorDeTelemetria) -> JSONResponse:
        return JSONResponse(
            status_code=int(error.estado),
            content=ErrorPublicado(codigo=error.codigo, mensaje=error.mensaje, detalle=error.detalle).model_dump(),
        )

    @app.exception_handler(sqlite3.OperationalError)
    def _base(_: fastapi.Request, error: sqlite3.OperationalError) -> JSONResponse:
        _LOG.error("La base de telemetria no respondio: %s", error)
        return JSONResponse(
            status_code=int(http.HTTPStatus.SERVICE_UNAVAILABLE),
            content=ErrorPublicado(
                codigo="base_no_disponible", mensaje="La base no respondio: reintentar."
            ).model_dump(),
        )

    def _token(pedido: fastapi.Request) -> str:
        encabezado = pedido.headers.get("authorization", "")
        esquema, _, token = encabezado.partition(" ")
        if esquema.lower() != ESQUEMA_BEARER or not token:
            raise ErrorDeTelemetria(http.HTTPStatus.UNAUTHORIZED, "no_autenticado", "Falta el token de sesion")
        return token

    def _reclamos(pedido: fastapi.Request, rol: Rol, consentimientos: tuple[str, ...]) -> str:
        """Verifica el token sin abrir ninguna base (seccion 4.3) y devuelve el usuario."""
        try:
            reclamos = verificar_token(_token(pedido), clave_publica, ahora=reloj())
        except TokenVencido as error:
            raise ErrorDeTelemetria(http.HTTPStatus.UNAUTHORIZED, "sesion_vencida", "La sesion vencio") from error
        except TokenInvalido as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNAUTHORIZED, "no_autenticado", "El token de sesion no es valido"
            ) from error
        if reclamos.rol is not rol:
            raise ErrorDeTelemetria(
                http.HTTPStatus.FORBIDDEN,
                "rol_no_autorizado",
                f"El rol {reclamos.rol.value} no puede usar esta operacion: es del rol {rol.value}",
                {"rol": reclamos.rol.value, "roles_admitidos": [rol.value]},
            )
        faltan = [c for c in consentimientos if c not in reclamos.consentimientos]
        if reclamos.terminos != version_de_los_terminos or faltan:
            raise ErrorDeTelemetria(
                http.HTTPStatus.FORBIDDEN,
                "terminos_no_aceptados",
                "Faltan aceptar terminos o consentimientos vigentes: hay que aceptarlos y volver a ingresar.",
                {"consentimientos_faltantes": faltan, "terminos_vigentes": version_de_los_terminos},
            )
        return reclamos.usuario_id

    acceso = (
        (http.HTTPStatus.UNAUTHORIZED, "`no_autenticado` o `sesion_vencida`."),
        (http.HTTPStatus.FORBIDDEN, "`rol_no_autorizado` o `terminos_no_aceptados`."),
    )

    @app.get("/estado", response_model=EstadoDeTelemetria, tags=["Servicio"], summary="Version y salud")
    def estado() -> EstadoDeTelemetria:
        return EstadoDeTelemetria(
            producto=NOMBRE_DEL_SERVICIO,
            version=VERSION,
            base="disponible" if base_disponible() else "no_responde",
        )

    @app.post(
        f"{PREFIJO}/lotes",
        response_model=LoteClasificado,
        tags=["Telemetria"],
        summary="Recibir un lote de posiciones del dispositivo (seccion 3.2)",
        responses=_respuestas(
            *acceso,
            (
                http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"`lote_demasiado_grande`: mas de {MAX_POSICIONES_POR_LOTE} posiciones o mas de "
                f"{MAX_BYTES_DEL_LOTE} bytes.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema, o `dispositivo_invalido` si el "
                "`dispositivo_id` no es un UUID version 4 en forma canonica.",
            ),
            (http.HTTPStatus.SERVICE_UNAVAILABLE, "`base_no_disponible`: la aplicacion reintenta."),
        ),
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LoteSolicitado"}}},
            }
        },
    )
    async def recibir_lote(pedido: fastapi.Request) -> LoteClasificado:
        """Token de transportista, terminos vigentes y consentimiento de
        geolocalizacion. Cada posicion se acepta, se descarta por repetida o
        se rechaza con su motivo, y se acepta por instante de captura, no de
        llegada. Los motivos de rechazo son `identificador_invalido` (el `id`
        no es un UUID version 4 en forma canonica), `ejecucion_desconocida`
        (el unico reintentable), `ruta_no_asignada`, `fuera_de_ejecucion`,
        `marca_temporal_invalida` y `coordenadas_invalidas`."""
        transportista = _reclamos(pedido, Rol.TRANSPORTISTA, CONSENTIMIENTOS_DEL_LOTE)
        crudo = await pedido.body()
        if len(crudo) > MAX_BYTES_DEL_LOTE:
            raise ErrorDeTelemetria(
                http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "lote_demasiado_grande",
                f"El lote mide {len(crudo)} bytes; el maximo es {MAX_BYTES_DEL_LOTE}.",
            )
        try:
            lote = LoteSolicitado.model_validate_json(crudo)
        except pydantic.ValidationError as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "peticion_mal_formada",
                "El lote no cumple el esquema.",
                json.loads(error.json(include_url=False)),
            ) from error
        if len(lote.posiciones) > MAX_POSICIONES_POR_LOTE:
            raise ErrorDeTelemetria(
                http.HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "lote_demasiado_grande",
                f"El lote trae {len(lote.posiciones)} posiciones; el maximo es {MAX_POSICIONES_POR_LOTE}.",
            )
        if lote.descartadas_por_cola_llena:
            _LOG.warning(
                "El dispositivo %s descarto %d posiciones por cola llena",
                lote.dispositivo_id,
                lote.descartadas_por_cola_llena,
            )
        try:
            resultado = _recibir(transportista, lote)
        except DispositivoInvalido as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "dispositivo_invalido",
                "El dispositivo_id no es un UUID version 4 en forma canonica: el lote no se guardo.",
                {"dispositivo_id": lote.dispositivo_id},
            ) from error
        return LoteClasificado(
            aceptadas=resultado.aceptadas,
            duplicadas=resultado.duplicadas,
            rechazadas=tuple(
                RechazoPublicado(id=r.id, motivo=r.motivo.value, reintentable=r.reintentable)
                for r in resultado.rechazadas
            ),
        )

    def _recibir(transportista: str, lote: LoteSolicitado) -> ResultadoDelLote:
        return seguimiento.recibir_lote(
            transportista,
            lote.ruta_id,
            lote.dispositivo_id,
            [
                PosicionRecibida(
                    id=p.id,
                    capturada_en=p.capturada_en,
                    latitud=p.latitud,
                    longitud=p.longitud,
                    precision_m=p.precision_m,
                    velocidad_mps=p.velocidad_mps,
                    rumbo_grados=p.rumbo_grados,
                )
                for p in lote.posiciones
            ],
        )

    @app.get(
        f"{PREFIJO}/rutas/{{ruta_id}}/ultima-posicion",
        response_model=UltimaPosicionPublicada,
        tags=["Telemetria"],
        summary="Ultima posicion de una Ruta y su antiguedad (FR-17)",
        responses=_respuestas(
            *acceso,
            (http.HTTPStatus.NOT_FOUND, "`sin_posiciones`: la Ruta no tiene ninguna posicion informada."),
        ),
    )
    def ultima_posicion(ruta_id: str, pedido: fastapi.Request) -> UltimaPosicionPublicada:
        """Para la interfaz del operador, que la superpone sobre la Ruta que
        obtiene del nucleo."""
        _reclamos(pedido, Rol.OPERADOR, ("transferencia_internacional",))
        try:
            ultima = seguimiento.ultima_posicion(ruta_id)
        except SinPosiciones as error:
            raise ErrorDeTelemetria(http.HTTPStatus.NOT_FOUND, "sin_posiciones", str(error)) from error
        p = ultima.posicion
        return UltimaPosicionPublicada(
            ruta_id=ruta_id,
            latitud=p.latitud,
            longitud=p.longitud,
            precision_m=p.precision_m,
            capturada_en=p.capturada_en,
            recibida_en=p.recibida_en,
            antiguedad_s=ultima.antiguedad_s,
            antiguedad_sobre_la_recepcion=ultima.antiguedad_sobre_la_recepcion,
            estado_de_ejecucion="en_ejecucion" if ultima.en_ejecucion else "desconocida",
        )

    @app.get(
        "/interno/rutas/{ruta_id}/ultima-posicion",
        response_model=UltimaPosicionPublicada,
        tags=["Interno"],
        summary="Ultima posicion de una Ruta para el nucleo (CU-07, paso 3; CU-09)",
        responses=_respuestas(
            (http.HTTPStatus.UNAUTHORIZED, "`no_autenticado`: falta el token de servicio o no es valido."),
            (http.HTTPStatus.NOT_FOUND, "`sin_posiciones`: la Ruta no tiene ninguna posicion informada."),
        ),
    )
    def ultima_posicion_interna(ruta_id: str, pedido: fastapi.Request) -> UltimaPosicionPublicada:
        """La misma respuesta que la del operador, con token de servicio del
        nucleo: es como el nucleo lee la posicion para replanificar (17B, C.1,
        camino a). Una Ruta reemplazada responde con la de la vigente."""
        try:
            verificar_token_de_servicio(_token(pedido), clave_publica, AUDIENCIA_TELEMETRIA, ahora=reloj())
        except TokenInvalido as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNAUTHORIZED, "no_autenticado", "Token de servicio invalido"
            ) from error
        return _publicar_ultima_posicion(ruta_id)

    @app.get(
        "/interno/rutas/{ruta_id}/posiciones-recientes",
        response_model=list[PosicionRecientePublicada],
        tags=["Interno"],
        summary="Posiciones recientes de la Ruta vigente para el nucleo (reruteo por desvio)",
        responses=_respuestas(
            (http.HTTPStatus.UNAUTHORIZED, "`no_autenticado`: falta el token de servicio o no es valido."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si `segundos` no es positivo."),
        ),
    )
    def posiciones_recientes_internas(
        ruta_id: str, pedido: fastapi.Request, segundos: float = fastapi.Query(default=120.0, gt=0, le=3600)
    ) -> list[PosicionRecientePublicada]:
        """Las capturadas en los ultimos `segundos`, en orden de captura (a lo
        sumo 20); una Ruta reemplazada responde con las de la vigente. Lista
        vacia si no hay."""
        try:
            verificar_token_de_servicio(_token(pedido), clave_publica, AUDIENCIA_TELEMETRIA, ahora=reloj())
        except TokenInvalido as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNAUTHORIZED, "no_autenticado", "Token de servicio invalido"
            ) from error
        return [PosicionRecientePublicada(latitud=p.latitud, longitud=p.longitud, capturada_en=p.capturada_en)
                for p in seguimiento.posiciones_recientes(ruta_id, segundos)]

    def _publicar_ultima_posicion(ruta_id: str) -> UltimaPosicionPublicada:
        try:
            ultima = seguimiento.ultima_posicion(ruta_id)
        except SinPosiciones as error:
            raise ErrorDeTelemetria(http.HTTPStatus.NOT_FOUND, "sin_posiciones", str(error)) from error
        p = ultima.posicion
        return UltimaPosicionPublicada(
            ruta_id=p.ruta_id,
            latitud=p.latitud,
            longitud=p.longitud,
            precision_m=p.precision_m,
            capturada_en=p.capturada_en,
            recibida_en=p.recibida_en,
            antiguedad_s=ultima.antiguedad_s,
            antiguedad_sobre_la_recepcion=ultima.antiguedad_sobre_la_recepcion,
            estado_de_ejecucion="en_ejecucion" if ultima.en_ejecucion else "desconocida",
        )

    @app.post(
        "/interno/eventos",
        response_model=EventoRegistrado,
        tags=["Interno"],
        summary="Eventos del nucleo por la salida transaccional (ARQ-59)",
        responses=_respuestas(
            (http.HTTPStatus.UNAUTHORIZED, "`no_autenticado`: falta el token de servicio o no es valido."),
            (http.HTTPStatus.CONFLICT, "`secuencia_fuera_de_orden`: el nucleo reintenta en orden."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`evento_desconocido` o `peticion_mal_formada`."),
        ),
    )
    def recibir_evento(evento: EventoSolicitado, pedido: fastapi.Request) -> EventoRegistrado:
        """Con token de servicio del nucleo. Un evento repetido se descarta."""
        try:
            verificar_token_de_servicio(_token(pedido), clave_publica, AUDIENCIA_TELEMETRIA, ahora=reloj())
        except TokenInvalido as error:
            raise ErrorDeTelemetria(
                http.HTTPStatus.UNAUTHORIZED, "no_autenticado", "Token de servicio invalido"
            ) from error
        try:
            nuevo = seguimiento.recibir_evento(
                evento.evento_id, evento.tipo, evento.agregado_id, evento.secuencia_del_agregado, evento.cuerpo
            )
        except SecuenciaFueraDeOrden as error:
            raise ErrorDeTelemetria(http.HTTPStatus.CONFLICT, "secuencia_fuera_de_orden", str(error)) from error
        except EventoDesconocido as error:
            raise ErrorDeTelemetria(http.HTTPStatus.UNPROCESSABLE_ENTITY, "evento_desconocido", str(error)) from error
        return EventoRegistrado(nuevo=nuevo)

    # El cuerpo del lote se lee crudo para medir sus bytes antes de
    # validarlo; el esquema se declara igual en el contrato.
    def _esquema() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        esquema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        componentes = esquema.setdefault("components", {}).setdefault("schemas", {})
        componentes["LoteSolicitado"] = LoteSolicitado.model_json_schema(ref_template="#/components/schemas/{model}")
        for nombre, definicion in componentes["LoteSolicitado"].pop("$defs", {}).items():
            componentes.setdefault(nombre, definicion)
        app.openapi_schema = esquema
        return esquema

    app.openapi = _esquema  # type: ignore[method-assign]
    return app
