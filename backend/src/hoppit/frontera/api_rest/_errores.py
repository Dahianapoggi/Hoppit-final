"""Errores del contrato REST (NFR-06, RB-05).

Cada error de dominio se traduce a un codigo estable y a una forma de
respuesta uniforme. El codigo --y no el texto del mensaje-- es lo que la
interfaz debe mirar para decidir que mostrar: el texto puede cambiar sin
aviso, el codigo es parte del contrato.

Los dos flujos de excepcion de CU-02 tienen codigo propio y detalle propio,
que es la condicion que RB-05 pide: la interfaz tiene que poder mostrar
causa y accion correctiva distintas para cada uno.
"""
from __future__ import annotations

import datetime as dt
import enum
import http
from collections.abc import Mapping, Sequence
from typing import Any

import pydantic

from hoppit.cartografia.puerto import RotuloNodo
from hoppit.gestion_ordenes.puerto import EstadoOrden
from hoppit.planificacion_rutas.puerto import (
    CapacidadDistintaDeLaNominal,
    DemandaExcedeCapacidad,
    MotivoDeIncomunicacion,
    OrdenesFueraDeEstado,
    ParadasIncomunicadas,
    VehiculosComprometidos,
    VehiculosFueraDeLaFlota,
)
from hoppit.vigilancia_sla.puerto import RutasAfectadasInexistentes


class CodigoDeError(enum.Enum):
    """Catalogo cerrado de errores del contrato.

    Cerrado a proposito: quien construye la interfaz tiene que poder
    enumerar los casos que debe contemplar y saber que no van a aparecer
    otros sin que el contrato cambie.
    """

    DEMANDA_INVALIDA = "demanda_invalida"
    PETICION_MAL_FORMADA = "peticion_mal_formada"
    DESTINO_ES_EL_DEPOSITO = "destino_es_el_deposito"
    ORDEN_NO_ENCONTRADA = "orden_no_encontrada"
    ORDEN_NO_ASIGNADA = "orden_no_asignada"
    RUTA_NO_ENCONTRADA = "ruta_no_encontrada"
    PLAN_NO_ENCONTRADO = "plan_no_encontrado"
    VEHICULOS_COMPROMETIDOS = "vehiculos_comprometidos"
    VEHICULOS_FUERA_DE_LA_FLOTA = "vehiculos_fuera_de_la_flota"
    CAPACIDAD_DISTINTA_DE_LA_NOMINAL = "capacidad_distinta_de_la_nominal"
    SOLICITUD_INVALIDA = "solicitud_invalida"
    SIN_ORDENES_PLANIFICABLES = "sin_ordenes_planificables"
    PARADAS_INCOMUNICADAS = "paradas_incomunicadas"
    DEMANDA_EXCEDE_CAPACIDAD = "demanda_excede_capacidad"
    SIN_SOLUCION = "sin_solucion"
    PARADA_NO_ENCONTRADA = "parada_no_encontrada"
    RUTA_YA_DESPACHADA = "ruta_ya_despachada"
    RUTA_EN_EJECUCION = "ruta_en_ejecucion"
    RESULTADO_NO_ADMITIDO = "resultado_no_admitido"
    DECLARACION_DE_CAMPO_NO_APLICADA = "declaracion_de_campo_no_aplicada"
    TIPO_DE_INCIDENTE_INVALIDO = "tipo_de_incidente_invalido"
    ALCANCE_INVALIDO = "alcance_invalido"
    RUTAS_INEXISTENTES = "rutas_inexistentes"
    INCIDENTE_NO_ENCONTRADO = "incidente_no_encontrado"
    # Replanificacion ante un Incidente (CU-07; 2026-11-20A).
    RUTA_NO_AFECTADA = "ruta_no_afectada"
    RUTA_NO_EN_EJECUCION = "ruta_no_en_ejecucion"
    RUTA_REEMPLAZADA = "ruta_reemplazada"
    SIN_ALTERNATIVA_FACTIBLE = "sin_alternativa_factible"
    ZONA_CONTIENE_AL_VEHICULO = "zona_contiene_al_vehiculo"
    PROPUESTA_NO_ENCONTRADA = "propuesta_no_encontrada"
    PROPUESTA_YA_RESUELTA = "propuesta_ya_resuelta"
    PROPUESTA_VENCIDA = "propuesta_vencida"
    # Brechas de CU-01 y CU-09 (2026-11-20A, bloque 3).
    DESTINO_NO_LOCALIZABLE = "destino_no_localizable"
    VENTANA_INVALIDA = "ventana_invalida"
    PARADA_CUMPLIDA = "parada_cumplida"
    TRANSICION_NO_ADMITIDA = "transicion_no_admitida"
    # Rutas de contingencia (CU-11; 2026-11-20A, bloque 4).
    CONTINGENCIA_INFACTIBLE = "contingencia_infactible"
    CONTINGENCIA_NO_ENCONTRADA = "contingencia_no_encontrada"
    SIN_CONTINGENCIA_APLICABLE = "sin_contingencia_aplicable"
    # Acuerdos de nivel de servicio (CU-10; 2026-11-20A, bloque 5).
    CLIENTE_COMERCIAL_NO_ENCONTRADO = "cliente_comercial_no_encontrado"
    PROVEEDOR_NO_ENCONTRADO = "proveedor_no_encontrado"
    ACUERDO_NO_ENCONTRADO = "acuerdo_no_encontrado"
    REGISTRO_DE_SLA_INVALIDO = "registro_de_sla_invalido"
    # Ingestion del estado de un Proveedor de ultima milla (CU-12; 2026-11-20A, bloque 6).
    SIN_MAPA_DE_ESTADOS = "sin_mapa_de_estados"
    ORDEN_DE_OTRO_PROVEEDOR = "orden_de_otro_proveedor"
    CODIGO_NO_MAPEADO = "codigo_no_mapeado"
    # Pedido nuevo durante la jornada (2026-11-22A, bloque 3).
    PEDIDO_NO_ASIGNABLE = "pedido_no_asignable"
    SIN_VEHICULO_PARA_EL_PEDIDO = "sin_vehiculo_para_el_pedido"
    # Despacho con transportista (D-C2; 2026-10-27B, T).
    TRANSPORTISTA_INVALIDO = "transportista_invalido"
    # Busqueda de direcciones (RB-10; 2026-10-27C, G).
    GEOREF_NO_RESPONDE = "georef_no_responde"
    LIMITE_DE_GEOREF = "limite_de_georef"
    GEOCODIFICADOR_NO_DISPONIBLE = "geocodificador_no_disponible"
    # Acceso (ARQ-55, ARQ-56), desde la version 0.13.0.
    NO_AUTENTICADO = "no_autenticado"
    SESION_VENCIDA = "sesion_vencida"
    CREDENCIALES_INVALIDAS = "credenciales_invalidas"
    USUARIO_BLOQUEADO = "usuario_bloqueado"
    ROL_NO_AUTORIZADO = "rol_no_autorizado"
    TERMINOS_NO_ACEPTADOS = "terminos_no_aceptados"
    ACEPTACION_INVALIDA = "aceptacion_invalida"
    # Registro con invitacion (B-246, B-248), desde la version 0.15.0.
    INVITACION_NO_VALIDA = "invitacion_no_valida"
    USUARIO_EXISTENTE = "usuario_existente"
    CONTRASENA_CORTA = "contrasena_corta"
    DATOS_DE_USUARIO_INVALIDOS = "datos_de_usuario_invalidos"
    DEMASIADOS_INTENTOS = "demasiados_intentos"
    # Administracion de la Flota (2026-11-02).
    PATENTE_INVALIDA = "patente_invalida"
    CAPACIDAD_INVALIDA = "capacidad_invalida"
    VEHICULO_EXISTENTE = "vehiculo_existente"
    VEHICULO_NO_ENCONTRADO = "vehiculo_no_encontrado"
    VEHICULO_EN_USO = "vehiculo_en_uso"


class ParadaIncomunicadaPublicada(pydantic.BaseModel):
    """Una Parada con los pares que su aislamiento produce (RB-05, 3a)."""

    nodo: int
    pares_afectados: int
    """Cuantos pares de la matriz involucran a esta Parada. Cero cuando el
    diagnostico fue por pertenencia a la componente y no se calculo ningun
    camino: informar pares que no se midieron seria inventarlos."""

    rotulo: str | None
    """Como se nombra la Parada, para poder senalarla en la pantalla. None si
    el nodo no esta en el grafo y no se puede rotular."""


class DetalleParadasIncomunicadas(pydantic.BaseModel):
    """Flujo 3a de CU-02: no hay camino entre pares de Paradas.

    Lleva la totalidad de los pares afectados y ademas el agrupamiento por
    Parada. Los dos, y no uno u otro: la totalidad porque el caso de uso lo
    exige, y el agrupamiento porque una Parada aislada entre veinte produce
    decenas de pares con una sola causa y una sola correccion.
    """

    motivo: MotivoDeIncomunicacion
    paradas: tuple[ParadaIncomunicadaPublicada, ...]
    pares: tuple[tuple[int, int], ...]
    """Pares (origen, destino) sin camino. Vacio cuando el motivo es
    `fuera_de_la_componente`, donde no se calculo ninguno."""


class DetalleDemandaExcedeCapacidad(pydantic.BaseModel):
    """Flujo 4a de CU-02: la Flota seleccionada no alcanza."""

    demanda_total: int
    capacidad_total: int
    faltante: int
    """Bultos de capacidad que hay que sumar para que la instancia sea
    factible. Es la accion correctiva, expresada como numero."""


class CampoRechazado(pydantic.BaseModel):
    """Un campo concreto de la peticion que no paso la validacion.

    Conserva lo que la validacion de esquema sabe y el mensaje suelto pierde:
    cual es el campo y por que se rechazo. Sin esto, una interfaz que quiera
    marcar el campo en el formulario tendria que deducirlo del texto.
    """

    campo: str
    """Ruta del campo dentro del cuerpo, con puntos: `vehiculos.AE472LP`.
    Cadena vacia cuando el rechazo es del cuerpo entero y no de un campo."""

    motivo: str
    tipo: str
    """Clase de violacion segun la validacion de esquema, por ejemplo
    `greater_than_equal` o `missing`. Es estable y sirve para decidir; el
    motivo es texto para mostrar."""


class DetalleCamposRechazados(pydantic.BaseModel):
    """Detalle de un rechazo por validacion de esquema."""

    campos: tuple[CampoRechazado, ...]


class VehiculoComprometido(pydantic.BaseModel):
    """Un Vehiculo que ya participa de un Plan de la jornada."""

    vehiculo_id: str
    plan_id: str
    """El Plan que lo ocupa. Liberarlo es una de las dos correcciones
    posibles; la otra es elegir otros Vehiculos."""


class DetalleVehiculosComprometidos(pydantic.BaseModel):
    """Un Vehiculo participa de un unico Plan por jornada (glosario, entidad
    13): en dos Planes del mismo dia tendria que recorrer dos Rutas a la vez.
    """

    fecha: dt.date
    vehiculos: tuple[VehiculoComprometido, ...]


class DetalleVehiculosFueraDeLaFlota(pydantic.BaseModel):
    """Vehiculos pedidos que no pertenecen a la Flota registrada (glosario,
    entidad 5)."""

    vehiculos: tuple[str, ...]
    """Cada identificador rechazado, todos y no solo el primero."""


class CapacidadRechazada(pydantic.BaseModel):
    """Un Vehiculo pedido con una capacidad que no es la suya."""

    vehiculo_id: str
    capacidad_pedida: int
    capacidad_nominal: int
    """La registrada en la Flota: es la que hay que enviar."""


class DetalleCapacidadDistintaDeLaNominal(pydantic.BaseModel):
    """La capacidad es un atributo del Vehiculo (glosario, entidad 4), no un
    parametro de cada corrida."""

    vehiculos: tuple[CapacidadRechazada, ...]


class EstadoDeOrdenPublicado(pydantic.BaseModel):
    """Una Orden con su estado vigente, la causa de un rechazo por estado."""

    orden: str
    estado: EstadoOrden


class DetalleEstadosDeOrdenes(pydantic.BaseModel):
    """Las Ordenes cuyo estado impide la operacion, con su estado vigente
    (FR-16, CU-06; glosario, ciclo de vida)."""

    ordenes: tuple[EstadoDeOrdenPublicado, ...]


class DetalleRutasInexistentes(pydantic.BaseModel):
    """Las Rutas declaradas como afectadas por un Incidente que no existen
    (FR-18). Todas, no solo la primera."""

    rutas: tuple[str, ...]


class DetalleUsuarioBloqueado(pydantic.BaseModel):
    """Cuanto falta para poder volver a intentar: el ingreso de un usuario
    bloqueado (`usuario_bloqueado`) o el registro desde una direccion con
    demasiados fallos (`demasiados_intentos`)."""

    segundos_restantes: int


class DetalleTerminosNoAceptados(pydantic.BaseModel):
    """Los documentos que faltan aceptar en su version vigente."""

    pendientes: tuple[str, ...]


class DetalleRolNoAutorizado(pydantic.BaseModel):
    """El rol del usuario y los que admite la operacion."""

    rol: str
    roles_admitidos: tuple[str, ...]


class DocumentoRechazadoPublicado(pydantic.BaseModel):
    documento: str
    motivo: str


class DetalleDocumentosRechazados(pydantic.BaseModel):
    """Las aceptaciones que no corresponden a un documento vigente del rol:
    todas, no solo la primera."""

    documentos: tuple[DocumentoRechazadoPublicado, ...]


class DetalleLimiteDeGeoref(pydantic.BaseModel):
    """Cuanto esperar antes de volver a buscar una direccion (2026-10-27C, G)."""

    segundos_de_espera: int


Detalle = (
    DetalleParadasIncomunicadas
    | DetalleDemandaExcedeCapacidad
    | DetalleCamposRechazados
    | DetalleVehiculosComprometidos
    | DetalleVehiculosFueraDeLaFlota
    | DetalleCapacidadDistintaDeLaNominal
    | DetalleEstadosDeOrdenes
    | DetalleRutasInexistentes
    | DetalleUsuarioBloqueado
    | DetalleTerminosNoAceptados
    | DetalleRolNoAutorizado
    | DetalleDocumentosRechazados
    | DetalleLimiteDeGeoref
)


class ErrorPublicado(pydantic.BaseModel):
    """Forma unica de toda respuesta de error del contrato.

    Toda respuesta de error tiene esta forma, venga de una validacion de
    esquema o de una regla de negocio. Que la validacion automatica de
    FastAPI responda con una forma y el dominio con otra obligaria a escribir
    dos manejadores para el mismo endpoint.
    """

    codigo: CodigoDeError
    mensaje: str
    detalle: Detalle | None = None


class ErrorDeContrato(Exception):
    """Error ya traducido al contrato, listo para responderse.

    Existe para que los adaptadores no armen respuestas a mano en cada
    endpoint: se levanta con codigo y detalle, y un unico manejador la
    convierte en respuesta.
    """

    def __init__(
        self,
        codigo: CodigoDeError,
        mensaje: str,
        estado: http.HTTPStatus,
        detalle: Detalle | None = None,
        encabezados: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.estado = estado
        self.detalle = detalle
        # Encabezados que el estado HTTP pide junto con el cuerpo:
        # `WWW-Authenticate` en un 401 y `Retry-After` en el bloqueo.
        self.encabezados = dict(encabezados or {})

    def publicar(self) -> ErrorPublicado:
        return ErrorPublicado(
            codigo=self.codigo, mensaje=self.mensaje, detalle=self.detalle
        )


def desde_paradas_incomunicadas(
    error: ParadasIncomunicadas, rotulos: dict[int, RotuloNodo]
) -> ErrorDeContrato:
    """Flujo 3a al contrato, con las Paradas rotuladas.

    Los rotulos llegan resueltos por el adaptador y no se buscan aca: este
    modulo traduce, no consulta. Un nodo sin rotulo disponible queda en None
    en vez de omitirse, para que la lista de Paradas siga siendo completa.
    """
    return ErrorDeContrato(
        codigo=CodigoDeError.PARADAS_INCOMUNICADAS,
        mensaje=str(error),
        estado=http.HTTPStatus.CONFLICT,
        detalle=DetalleParadasIncomunicadas(
            motivo=error.motivo,
            paradas=tuple(
                ParadaIncomunicadaPublicada(
                    nodo=parada.nodo,
                    pares_afectados=parada.pares_afectados,
                    rotulo=(
                        rotulos[parada.nodo].texto if parada.nodo in rotulos else None
                    ),
                )
                for parada in error.paradas
            ),
            pares=error.pares,
        ),
    )


def desde_ordenes_fuera_de_estado(
    error: OrdenesFueraDeEstado, codigo: CodigoDeError
) -> ErrorDeContrato:
    """Un rechazo por el estado de las Ordenes al contrato: 409, porque
    depende del estado del sistema, con cada Orden y su estado."""
    return ErrorDeContrato(
        codigo=codigo,
        mensaje=str(error),
        estado=http.HTTPStatus.CONFLICT,
        detalle=DetalleEstadosDeOrdenes(
            ordenes=tuple(
                EstadoDeOrdenPublicado(orden=str(orden), estado=estado)
                for orden, estado in sorted(error.estados.items())
            )
        ),
    )


def desde_demanda_excede_capacidad(
    error: DemandaExcedeCapacidad,
) -> ErrorDeContrato:
    """Flujo 4a al contrato."""
    return ErrorDeContrato(
        codigo=CodigoDeError.DEMANDA_EXCEDE_CAPACIDAD,
        mensaje=str(error),
        estado=http.HTTPStatus.CONFLICT,
        detalle=DetalleDemandaExcedeCapacidad(
            demanda_total=error.demanda_total,
            capacidad_total=error.capacidad_total,
            faltante=error.faltante,
        ),
    )


# Cuando el cuerpo entero es invalido --no es JSON, o falta-- la validacion no
# nombra ningun campo.
_SIN_CAMPO = ""

# La ubicacion que informa Pydantic arranca por el origen del dato (`body`,
# `query`, `path`), que no es parte del nombre del campo.
_ORIGENES_DE_DATO = frozenset({"body", "query", "path", "header", "cookie"})

_SEPARADOR_DE_CAMPO = "."

_CLAVE_UBICACION = "loc"
_CLAVE_MENSAJE = "msg"
_CLAVE_TIPO = "type"


def _nombre_del_campo(ubicacion: tuple[object, ...]) -> str:
    """Ubicacion de Pydantic a un nombre de campo legible."""
    partes = [str(parte) for parte in ubicacion]
    if partes and partes[0] in _ORIGENES_DE_DATO:
        partes = partes[1:]
    return _SEPARADOR_DE_CAMPO.join(partes) if partes else _SIN_CAMPO


def desde_validacion_de_esquema(errores: Sequence[Mapping[str, Any]]) -> ErrorDeContrato:
    """Rechazo de validacion al formato unico del contrato.

    Lo que la validacion de esquema sabe --que campo fallo, con que motivo y
    de que clase es la violacion-- se conserva dentro de `detalle`. Aplanarlo
    a un mensaje suelto perderia justamente lo que permite marcar el campo en
    el formulario.
    """
    campos = tuple(
        CampoRechazado(
            campo=_nombre_del_campo(tuple(error.get(_CLAVE_UBICACION, ()))),
            motivo=str(error.get(_CLAVE_MENSAJE, "")),
            tipo=str(error.get(_CLAVE_TIPO, "")),
        )
        for error in errores
    )
    nombrados = ", ".join(campo.campo for campo in campos if campo.campo)
    mensaje = (
        f"La peticion no cumple el contrato en: {nombrados}"
        if nombrados
        else "La peticion no cumple el contrato"
    )
    return ErrorDeContrato(
        codigo=CodigoDeError.PETICION_MAL_FORMADA,
        mensaje=mensaje,
        estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        detalle=DetalleCamposRechazados(campos=campos),
    )


def respuestas_de_error(
    *casos: tuple[http.HTTPStatus, str],
) -> dict[int | str, dict[str, Any]]:
    """Declara en el esquema los errores que un endpoint puede devolver.

    Hace falta declararlos explicitamente por dos razones. La primera es que
    FastAPI no puede inferirlos: los errores de dominio se levantan dentro
    del cuerpo del endpoint y no aparecen en su firma, asi que un contrato
    sin esto no dice que existen y quien lo consuma no sabe que
    contemplarlos. La segunda es que FastAPI declara por su cuenta un 422 con
    la forma de `HTTPValidationError`, que dejo de ser la forma real cuando
    los rechazos de validacion pasaron a responder como cualquier otro error
    del contrato: sin sobrescribirlo, el esquema afirmaria una forma que la
    API no devuelve.

    La descripcion de cada caso nombra los codigos posibles, porque es contra
    el codigo --y no contra el estado HTTP-- que hay que decidir: dos casos
    distintos pueden compartir estado.
    """
    respuestas: dict[int | str, dict[str, Any]] = {
        int(estado): {"model": ErrorPublicado, "description": descripcion}
        for estado, descripcion in casos
    }
    return respuestas


def desde_vehiculos_comprometidos(
    error: VehiculosComprometidos,
) -> ErrorDeContrato:
    """La regla de un Vehiculo por jornada, al contrato.

    El detalle nombra cada Vehiculo con el Plan que lo ocupa: sin eso, la
    correccion --liberar ese Plan o elegir otros Vehiculos-- exigiria buscar
    a mano cual es cual.
    """
    return ErrorDeContrato(
        codigo=CodigoDeError.VEHICULOS_COMPROMETIDOS,
        mensaje=str(error),
        estado=http.HTTPStatus.CONFLICT,
        detalle=DetalleVehiculosComprometidos(
            fecha=error.fecha,
            vehiculos=tuple(
                VehiculoComprometido(vehiculo_id=str(vehiculo), plan_id=str(plan))
                for vehiculo, plan in sorted(error.ocupados.items())
            ),
        ),
    )


# Los dos rechazos de la seleccion contra la Flota responden 422 y no 409,
# como `solicitud_invalida`: no chocan con el estado del sistema --ningun
# Plan, ninguna Orden los provoca-- sino que la peticion misma no describe
# una seleccion posible, y repetirla sin cambiarla fallaria siempre. El 409
# queda para lo que depende del estado vigente: `vehiculos_comprometidos` se
# resuelve descartando un Plan, `sin_ordenes_planificables` registrando
# Ordenes.


def desde_vehiculos_fuera_de_la_flota(
    error: VehiculosFueraDeLaFlota,
) -> ErrorDeContrato:
    """Vehiculos ajenos a la Flota, al contrato, con cada uno nombrado."""
    return ErrorDeContrato(
        codigo=CodigoDeError.VEHICULOS_FUERA_DE_LA_FLOTA,
        mensaje=str(error),
        estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        detalle=DetalleVehiculosFueraDeLaFlota(
            vehiculos=tuple(str(vehiculo) for vehiculo in error.vehiculos)
        ),
    )


def desde_capacidad_distinta_de_la_nominal(
    error: CapacidadDistintaDeLaNominal,
) -> ErrorDeContrato:
    """Capacidades que no son las nominales, al contrato.

    Cada Vehiculo rechazado lleva la capacidad pedida y la nominal: la
    correccion es enviar la nominal, y asi no hay que ir a buscarla.
    """
    return ErrorDeContrato(
        codigo=CodigoDeError.CAPACIDAD_DISTINTA_DE_LA_NOMINAL,
        mensaje=str(error),
        estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        detalle=DetalleCapacidadDistintaDeLaNominal(
            vehiculos=tuple(
                CapacidadRechazada(
                    vehiculo_id=str(d.vehiculo),
                    capacidad_pedida=d.pedida,
                    capacidad_nominal=d.nominal,
                )
                for d in error.discrepancias
            )
        ),
    )


def desde_rutas_afectadas_inexistentes(error: RutasAfectadasInexistentes) -> ErrorDeContrato:
    """Rutas afectadas que no existen, al contrato: 422, como la Flota
    ajena, porque la peticion nombra algo que no hay y repetirla sin
    cambiarla fallaria siempre. El detalle lleva cada Ruta."""
    return ErrorDeContrato(
        codigo=CodigoDeError.RUTAS_INEXISTENTES,
        mensaje=str(error),
        estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        detalle=DetalleRutasInexistentes(rutas=tuple(str(ruta) for ruta in error.rutas)),
    )
