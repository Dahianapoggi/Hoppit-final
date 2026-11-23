"""Adaptador REST sobre FastAPI (FR-10, NFR-06, NFR-10).

Es un adaptador y no una capa de negocio: traduce HTTP a llamadas de puerto y
entidades a payloads, sin decidir nada del dominio. Toda regla que aparezca
aca --que una demanda sea positiva, que un destino no sea el Deposito-- se
verifica en el borde para poder responder con un mensaje util, pero la fuente
de verdad sigue siendo el container que la posee.

Desde la version 0.13.0 toda operacion de `/v1` exige un token de sesion,
salvo `POST /v1/sesiones` y `/v1/estado`; `/estado` sigue sin versionar y
publico (D-C3: la autenticacion entra en `/v1` sin cambiar la version del
contrato). La identidad la resuelve `frontera/identidad` por su puerto
(ARQ-16, regla 3 de los limites de import); la traduccion a HTTP esta en
`_acceso.py`.
"""
from __future__ import annotations

import dataclasses
import functools
import datetime as dt
import http
import logging
from collections.abc import Callable, Mapping
from typing import Annotated, Sequence, cast

import fastapi
from fastapi.exceptions import RequestValidationError

import hoppit
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from hoppit.cartografia.puerto import (
    ZonaCircular,
    ATRIBUCION_DE_GEOREF,
    LARGO_MAXIMO_DEL_TEXTO,
    ArtefactoDelGrafo,
    GeocodificadorDeDirecciones,
    GeocodificadorNoResponde,
    LimiteDeConsultasAlcanzado,
    ProveedorGrafoVial,
    RotuloNodo,
    Trazado,
)
from hoppit.gestion_ordenes.puerto import (
    Modalidad,
    ESTADOS_REPORTABLES_POR_PROVEEDOR,
    VentanaHoraria,
    VentanaInvalida,
    ESTADOS_TERMINALES,
    DemandaInvalida,
    EstadoOrden,
    Orden,
    PuntoDelAlta,
    ServicioGestionOrdenes,
)
from hoppit.planificacion_rutas.puerto import (
    brecha_de_optimalidad,
    PedidoNoAsignable,
    SinVehiculoParaElPedido,
    ParadaPlaneada,
    Ruta,
    ReporteDePosicion,
    Contingencia,
    ContingenciaInfactible,
    ContingenciaNoEncontrada,
    EstadoDePropuesta,
    SinContingenciaAplicable,
    CapacidadDistintaDeLaNominal,
    capacidad_efectiva,
    DemandaExcedeCapacidad,
    InstanciaInvalida,
    OrdenNoAsignada,
    ParadaNoEncontrada,
    ResultadoDeParada,
    ResultadoNoAdmitido,
    DeclaracionDeCampoNoAplicada,
    RutaEnEjecucion,
    RutaYaDespachada,
    VehiculosComprometidos,
    VehiculosFueraDeLaFlota,
    CapacidadInvalida,
    PatenteInvalida,
    VehiculoEnUso,
    VehiculoExistente,
    VehiculoInexistente,
    VehiculoRegistrado,
    ParadasIncomunicadas,
    ANTIGUEDAD_MAXIMA_S,
    MOTIVO_INCIDENTE,
    ZonaContieneAlVehiculo,
    PosicionDelVehiculo,
    PropuestaDeReemplazo,
    PropuestaNoEncontrada,
    PropuestaVencida,
    PropuestaYaResuelta,
    ReemplazoDeRuta,
    RutaNoEnEjecucion,
    RutaYaReemplazada,
    SinAlternativaFactible,
    RutaPersistida,
    ServicioPlanificacionRutas,
    SinSolucion,
    SolicitudInvalida,
)
from ._esquemas import (
    AsignacionEnLaJornadaPublicada,
    UsuarioDeIntegracionSolicitado,
    MapaDeEstadosPublicado,
    MapaDeEstadosSolicitado,
    ReporteDeProveedorSolicitado,
    ReporteRechazadoPublicado,
    AcuerdoPublicado,
    AcuerdoSolicitado,
    ClienteComercialPublicado,
    ClienteComercialSolicitado,
    EvaluacionDeCumplimientoPublicada,
    OrdenesDelClienteSolicitadas,
    ProveedorPublicado,
    ProveedorSolicitado,
    ActivacionSolicitada,
    ContingenciaPublicada,
    ContingenciaSolicitada,
    EvaluacionDeContingenciaPublicada,
)
from hoppit.prediccion_eta.puerto import (
    ERRORES_DE_ESTIMACION,
    ParadaARecorrer,
    ServicioPrediccionEta,
)
from hoppit.shared.errores import EntidadNoEncontrada, TransicionInvalida
from hoppit.shared.tiempo import ZONA_OPERATIVA, fecha_operativa, instante_actual
from hoppit.vigilancia_sla.puerto import (
    DeclaracionDeCampoObservada,
    DesvioObservado,
    RiesgoVialObservado,
    Incidente,
    ServicioDeReportesDeProveedor,
    TipoDeRechazo,
    TAMANO_MINIMO_REPRESENTATIVO,
    AcuerdoDeNivelDeServicio,
    AcuerdoNoEncontrado,
    ClienteComercialNoEncontrado,
    OrdenEjecutada,
    ProveedorNoEncontrado,
    RegistroInvalido,
    ServicioDeAcuerdos,
    evaluar_cumplimiento,
    OrdenObservada,
    RutaObservada,
    evaluar_alertas,
    AlcanceDelIncidente,
    AlcanceDeIncidenteInvalido,
    RutasAfectadasInexistentes,
    ServicioIncidentes,
    TipoDeIncidenteInvalido,
    ZonaAfectada,
    ZonaAfectadaInvalida,
)
from hoppit.shared.ids import OrdenId, PlanId, RutaId, VehiculoId
from hoppit.shared.intervalo_por_celda import MINUTOS_POR_HORA
from hoppit.frontera.identidad.puerto import (
    ServicioDeIdentidad,
    UsuarioAutenticado,
    instalar_filtro_de_credenciales,
)
from hoppit.shared.eventos import Declarante, MedioDeDeclaracion

from ._acceso import (
    agregar_operaciones_de_acceso,
    dependencia_de_operador,
    dependencia_de_transportista,
    dependencia_de_cliente_comercial,
    respuestas_de_acceso,
)
from ._errores import (
    DetalleLimiteDeGeoref,
    CodigoDeError,
    ErrorDeContrato,
    desde_capacidad_distinta_de_la_nominal,
    desde_vehiculos_comprometidos,
    desde_vehiculos_fuera_de_la_flota,
    respuestas_de_error,
    desde_demanda_excede_capacidad,
    desde_ordenes_fuera_de_estado,
    desde_paradas_incomunicadas,
    desde_rutas_afectadas_inexistentes,
    desde_validacion_de_esquema,
)
from ._memoria_de_eta import CAPACIDAD_DE_MEMORIA_DE_ETA, MemoriaDeEta
from ._esquemas import (
    BrechaPublicada,
    ExposicionAlRiesgoVialPublicada,
    ReprogramacionSolicitada,
    EtaDeParadaPublicada,
    AvancePublicado,
    OrigenPublicado,
    CompromisoDeRiesgoVialPublicado,
    ParadaComparada,
    PropuestaPublicada,
    PropuestaSolicitada,
    ReemplazoPublicado,
    AltaDeOrden,
    AnclajePublicado,
    CandidatoDeDireccionPublicado,
    Coordenada,
    DireccionesEncontradas,
    DepositoPublicado,
    DepositosPublicados,
    DesasignacionAplicada,
    DeclarantePublicado,
    DespachoAplicado,
    ResultadoDeParadaAplicado,
    ResultadoDeParadaSolicitado,
    EstadoDelServicio,
    DespachoSolicitado,
    RutaEnEjecucionPublicada,
    TransportistaPublicado,
    AlertaPublicada,
    AlertasPublicadas,
    GrafoActivoPublicado,
    EtaPublicado,
    FlotaPublicada,
    IncidenteDelTransportistaSolicitado,
    CambioDeVehiculoSolicitado,
    VehiculoRegistradoPublicado,
    VehiculoSolicitado,
    IncidentePublicado,
    IncidenteSolicitado,
    ZonaAfectadaSolicitada,
    JornadasPublicadas,
    PlanConRutas,
    PlanEnListado,
    VehiculoPublicado,
    OrdenPublicada,
    ParadaPublicada,
    Punto,
    PlanPublicado,
    Latitud,
    Longitud,
    PlanificacionSolicitada,
    OrdenNoPlanificadaPublicada,
    RutaEnListado,
    RutaPublicada,
    TrazadoPublicado,
)

_LOG = logging.getLogger(__name__)

TITULO = "HOPPIT -- API del nucleo"

SEGUNDOS_POR_MINUTO = 60

# `info.version` del esquema OpenAPI: la version del producto, no la del
# contrato. La del contrato viaja en la ruta, donde un cliente la ve sin
# tener que leer el esquema.
VERSION = hoppit.__version__

NOMBRE_DEL_PRODUCTO = "HOPPIT"

# Servidor declarado en el esquema. Sin esto, un cliente de API que importe
# el OpenAPI arma las peticiones sin host y hay que escribir la URL base a
# mano en cada una. Se declara con variables para que el host y el puerto
# lleguen editables al cliente en vez de quedar fijos en una cadena.
HOST_LOCAL = "127.0.0.1"
PUERTO_LOCAL = "8000"
DESCRIPCION_DEL_SERVIDOR = (
    "Backend corriendo en local con tools/servir_api.py. En el despliegue "
    "(infra/compose.yaml) la API responde detras del borde, por HTTPS y en el "
    "mismo origen que la interfaz; su dominio no se fija en el contrato."
)
DESCRIPCION = (
    "Contrato de integracion del nucleo de HOPPIT (FR-10, NFR-06). Las "
    "operaciones se agrupan por etiqueta, y el resumen de cada una nombra el "
    "caso de uso o el requerimiento que implementa, cuando lo hay. La "
    "telemetria de los dispositivos tiene su propio contrato, el del servicio "
    "de telemetria y seguimiento. "
    "Toda operacion exige el encabezado `Authorization: Bearer` con el token "
    "de `POST /v1/sesiones`, salvo esa misma operacion, el registro con "
    "invitacion y el estado del servicio. Mientras falte aceptar terminos o "
    "consentimientos vigentes, las operaciones responden 403 "
    "`terminos_no_aceptados`, salvo las de terminos."
)

# CORS abierto: en local el frontend corre en otro origen. Detras del borde la
# interfaz y la API comparten origen y no lo necesitan. Desde 0.13.0 la API
# exige un token, pero viaja en el encabezado `Authorization` y no en una
# cookie: el navegador no lo agrega solo a un pedido de otro sitio, asi que
# abrir el origen no expone la sesion.
ORIGENES_PERMITIDOS = ["*"]

# Version del contrato, como prefijo de toda ruta. Sin ella, el primer cambio
# incompatible dejaria a quien consume la API sin forma de saber contra que
# version construyo: las dos versiones responderian en la misma URL y la
# unica senal del cambio seria que algo dejo de funcionar. Con el prefijo, una
# version nueva vive en rutas nuevas y la anterior puede seguir respondiendo
# mientras haga falta.
PREFIJO_DE_VERSION = "/v1"

# Unica ruta fuera del prefijo. Preguntar que version de contrato corre no
# puede exigir conocerla de antemano: eso seria circular. Los endpoints de
# dominio llevan prefijo; el de descubrimiento, no.
RUTA_DE_ESTADO = "/estado"
# Caracteres del SHA-256 del grafo que publica `/estado`: alcanzan para
# distinguir un artefacto de otro a simple vista (2026-10-27B, K).
LARGO_DEL_SHA256_ABREVIADO = 12

# Estados en que una Orden puede quedar en riesgo de plazo: tiene Ruta y no
# fue entregada todavia (2026-10-27B, A).
ESTADOS_EN_RIESGO_DE_PLAZO = frozenset({EstadoOrden.PLANIFICADA, EstadoOrden.EN_TRANSITO})


class ContextoDeLaApi:
    """Lo que los endpoints necesitan, resuelto una sola vez.

    El Deposito y la Flota entran como parametros y no se leen de disco aca:
    los adaptadores de frontera no pueden importar el plano offline (regla 2
    de los limites de import), y el Deposito es parametro de cada
    planificacion por decision de dominio (B-59).

    `prediccion_eta`, `hora_de_salida` y `velocidad_km_h` van juntos o no
    van: el ETA de una Parada se cuenta desde la salida de su Ruta (B-193,
    punto 3, criterios b y c) y suma un viaje por la red a esa velocidad, un
    supuesto declarado (B-196). Sin servicio, el ETA se publica ausente.

    `incidentes` es el registro de Incidentes (FR-18). Sin el, la API no
    publica las operaciones de Incidentes: un contexto armado a mano para
    probar otra cosa no las necesita, y responder un error que el esquema no
    declara seria peor que no ofrecerlas. `construir_contexto` lo arma
    siempre.

    `identidad` es obligatoria: no hay forma de armar la API sin exigir
    sesion. Las pruebas y las herramientas que la ejercitan ingresan con un
    usuario creado en su proceso (`tools/acceso_en_proceso.py`).
    """

    def __init__(
        self,
        ordenes: ServicioGestionOrdenes,
        planificacion: ServicioPlanificacionRutas,
        cartografia: ProveedorGrafoVial,
        deposito: int,
        flota: dict[VehiculoId, int],
        prediccion_eta: ServicioPrediccionEta | None = None,
        hora_de_salida: dt.time | None = None,
        velocidad_km_h: float | None = None,
        incidentes: ServicioIncidentes | None = None,
        *,
        identidad: ServicioDeIdentidad,
        grafo_activo: ArtefactoDelGrafo | None = None,
        reloj_de_alertas: Callable[[], dt.datetime] = instante_actual,
        capacidad_de_memoria_de_eta: int = CAPACIDAD_DE_MEMORIA_DE_ETA,
        geocodificador: GeocodificadorDeDirecciones | None = None,
        lector_de_posicion: Callable[[str], PosicionDelVehiculo | None] | None = None,
        acuerdos: ServicioDeAcuerdos | None = None,
        lector_de_posiciones_recientes: Callable[[str], Sequence[ReporteDePosicion]] | None = None,
        reportes_de_proveedor: ServicioDeReportesDeProveedor | None = None,
    ) -> None:
        # CU-12 (2026-11-20A): mapas de estados y reportes rechazados; requiere
        # los acuerdos (los Proveedores y sus Vehiculos).
        self.reportes_de_proveedor = reportes_de_proveedor
        # Acuerdos de nivel de servicio (CU-10; 2026-11-20A). Sin ellos, sus
        # operaciones no se publican y las alertas usan solo el plazo de la Orden.
        self.acuerdos = acuerdos
        # Reruteo por desvio (2026-11-22A, D17 y D18): las posiciones
        # recientes de una Ruta en telemetria. Sin el, no se vigila el desvio.
        self.lector_de_posiciones_recientes = lector_de_posiciones_recientes
        # La ultima posicion de telemetria de una Ruta, para el origen del
        # recalculo (CU-07, paso 3; 2026-11-20A). Sin lector, el recalculo
        # parte de la Parada en curso.
        self.lector_de_posicion = lector_de_posicion
        dados = {prediccion_eta is None, hora_de_salida is None, velocidad_km_h is None}
        if len(dados) != 1:
            raise ValueError(
                "El servicio de Prediccion de ETA, la hora de salida y la velocidad se "
                "dan juntos: el ETA se cuenta desde la salida de la Ruta y suma un viaje "
                "por la red a esa velocidad"
            )
        self.ordenes = ordenes
        self.planificacion = planificacion
        self.cartografia = cartografia
        self.deposito = deposito
        # La Flota con que se armo el contexto. La vigente se lee del
        # registro de Planificacion (propiedad `flota`), que el operador
        # administra desde 2026-11-02; esta queda para un servicio armado sin
        # registro.
        self._flota_inicial = dict(flota)
        self.prediccion_eta = prediccion_eta
        self.hora_de_salida = hora_de_salida
        self.velocidad_km_h = velocidad_km_h
        self.incidentes = incidentes
        self.identidad = identidad
        # El artefacto del grafo cargado, que `/estado` publica (2026-10-27B,
        # K). Un contexto armado a mano para probar otra cosa puede no darlo.
        self.grafo_activo = grafo_activo
        # El instante con que se evaluan las alertas (2026-10-27B, A); las
        # pruebas lo fijan.
        self.reloj_de_alertas = reloj_de_alertas
        # El ETA de cada Ruta, guardado por su identificador (2026-10-27C,
        # F3): con este contexto, depende solo de la Ruta.
        self.memoria_de_eta: MemoriaDeEta[tuple[EtaPublicado | None, ...]] = MemoriaDeEta(
            capacidad_de_memoria_de_eta
        )
        # La busqueda por direccion (RB-10; 2026-10-27C, G). Sin el, la
        # operacion responde 503 `geocodificador_no_disponible`: las pruebas y
        # el exportador del contrato no salen a la red.
        self.geocodificador = geocodificador

    @property
    def flota(self) -> dict[VehiculoId, int]:
        """Los Vehiculos activos con su capacidad nominal, leidos en cada
        consulta: un Vehiculo dado de alta entra en la proxima planificacion
        sin reiniciar el proceso, y uno dado de baja deja de ofrecerse."""
        vigente = self.planificacion.flota_vigente()
        return dict(self._flota_inicial) if vigente is None else vigente

    def rotulo(self, nodo: int) -> RotuloNodo:
        return self.cartografia.rotulo(nodo)

    def rotulos(self, nodos: Sequence[int]) -> dict[int, RotuloNodo]:
        """Rotulos de varios nodos, sin repetir consultas por nodo repetido."""
        resueltos: dict[int, RotuloNodo] = {}
        for nodo in nodos:
            if nodo in resueltos:
                continue
            try:
                resueltos[nodo] = self.cartografia.rotulo(nodo)
            except EntidadNoEncontrada:
                # Un nodo que no esta en el grafo no se rotula. No se
                # interrumpe la respuesta por eso: el resto sigue siendo util.
                _LOG.warning("Nodo %d sin rotulo: no pertenece al grafo", nodo)
        return resueltos


# CU-01, flujo 4a (2026-11-20A): mas alla de esta distancia de la calle mas
# cercana del grafo, un punto no se localiza sobre la red y el alta se rechaza.
# Entre DISTANCIA_DE_ADVERTENCIA_M de la interfaz (300 m, B-249) y este
# umbral, la interfaz advierte y pide confirmacion [supuesto de esta sesion].
DISTANCIA_MAXIMA_DE_ANCLAJE_M = 1_000.0


def _publicar_orden(contexto: ContextoDeLaApi, orden: Orden) -> OrdenPublicada:
    return OrdenPublicada.desde(
        orden, contexto.rotulo(orden.destino), contexto.identidad.nombre_visible
    )


def _mu_del_trazado(contexto: ContextoDeLaApi, ruta_id: RutaId) -> float | None:
    """El mu de una Ruta nacida de una propuesta por riesgo vial aprobada
    (D31, 2026-11-23E); None en las demas."""
    servicio = contexto.planificacion.reemplazo
    return None if servicio is None else servicio.mu_del_trazado(ruta_id)


def _trazado_de_la_ruta(contexto: ContextoDeLaApi, ruta_id: RutaId, nodos: Sequence[int]) -> Trazado:
    """Lo que se dibuja es lo que se ruteo: una Ruta nacida de una propuesta
    por riesgo vial se traza con los caminos de su mu (D31, 2026-11-23E); las
    demas, con los del proceso."""
    mu = _mu_del_trazado(contexto, ruta_id)
    trazar = getattr(contexto.cartografia, "trazado_con_riesgo", None)
    if mu is None or trazar is None:
        return contexto.cartografia.trazado(nodos)
    return cast(Trazado, trazar(nodos, mu))


def _publicar_ruta(contexto: ContextoDeLaApi, persistida: RutaPersistida) -> RutaPublicada:
    """Una Ruta completa, con Paradas rotuladas y geometria dibujable.

    El recorrido que se traza es Deposito -> Paradas en orden -> Deposito: la
    Ruta parte del Deposito y vuelve a el (glosario 3), y sin los extremos
    faltarian el primer y el ultimo tramo.
    """
    ruta = persistida.ruta
    reemplazada_por, reemplaza_a = _reemplazos_de(contexto, ruta.id)
    # Una Ruta nacida de un reemplazo empieza en el origen del recalculo, no en
    # el Deposito (CU-07, paso 3); vuelve al Deposito como toda Ruta.
    inicio = persistida.deposito if reemplaza_a is None else reemplaza_a.origen_nodo
    # Las Ordenes del sistema, indexadas: la Ruta guarda identificadores y
    # la respuesta publica ademas su demanda y su estado vigente. Se leen una
    # sola vez para toda la Ruta, no una por Parada.
    ordenes_del_sistema = {
        str(orden.id): orden for orden in contexto.ordenes.listar()
    }
    # Solo lo que hoy es de esta Ruta (2026-11-22A, bloque 1): una Orden mudada
    # por un reemplazo o una contingencia se publica en la suya y no tambien
    # aca; la Reemplazada queda con lo cumplido (glosario, estado 5).
    paradas = _paradas_propias(ruta, ordenes_del_sistema)
    nodos_del_recorrido = (
        (inicio,)
        + tuple(parada.nodo for parada in paradas)
        + (persistida.deposito,)
    )
    rotulos = contexto.rotulos(nodos_del_recorrido)
    eta_por_secuencia = dict(zip((p.secuencia for p in ruta.paradas), _eta_por_parada(contexto, persistida)))
    de_contingencia, activadas = _contingencias_de(contexto, ruta.id)
    hora_de_salida = (
        contexto.hora_de_salida
        if reemplaza_a is None or contexto.hora_de_salida is None
        else _hora_local(reemplaza_a)
    )
    if de_contingencia is not None and de_contingencia.activada_en is not None and contexto.hora_de_salida is not None:
        hora_de_salida = _hora_local_de(de_contingencia.activada_en)
    return RutaPublicada(
        id=str(ruta.id),
        plan_id=str(persistida.plan_id),
        vehiculo_id=str(ruta.vehiculo_id),
        fecha_planificacion=persistida.fecha_planificacion,
        hora_de_salida=hora_de_salida,
        distancia_m=ruta.distancia_m,
        carga=ruta.carga,
        capacidad_vehiculo=ruta.capacidad_vehiculo,
        deposito=_punto(rotulos, persistida.deposito),
        paradas=tuple(
            ParadaPublicada.desde(
                parada, rotulos[parada.nodo], ordenes_del_sistema, eta
            )
            for parada in paradas
            for eta in [eta_por_secuencia.get(parada.secuencia)]
        ),
        trazado=TrazadoPublicado.desde(_trazado_de_la_ruta(contexto, ruta.id, nodos_del_recorrido)),
        reemplazada_por=None if reemplazada_por is None else _reemplazo_publicado(contexto, reemplazada_por),
        reemplaza_a=None if reemplaza_a is None else _reemplazo_publicado(contexto, reemplaza_a),
        de_contingencia=None if de_contingencia is None else de_contingencia.id,
        contingencias_activadas=tuple(c.id for c in activadas) or None,
        mu_de_los_caminos_m=_mu_del_trazado(contexto, ruta.id),
    )


def _recorrido_de_la_ruta(contexto: ContextoDeLaApi, persistida: RutaPersistida) -> tuple[int, list[int], int]:
    """(inicio, nodos de las Paradas propias en orden, Deposito): el mismo
    recorrido que se traza al publicar la Ruta."""
    _, reemplaza_a = _reemplazos_de(contexto, persistida.ruta.id)
    inicio = persistida.deposito if reemplaza_a is None else reemplaza_a.origen_nodo
    ordenes_del_sistema = {str(orden.id): orden for orden in contexto.ordenes.listar()}
    paradas = _paradas_propias(persistida.ruta, ordenes_del_sistema)
    return int(inicio), [int(p.nodo) for p in paradas], int(persistida.deposito)


def _objetivo_del_ruteo(contexto: ContextoDeLaApi) -> str:
    capa = getattr(contexto.cartografia, "capa_de_riesgo_vial", lambda: None)()
    if capa is not None and capa.mu > 0:
        return f"distancia por la red mas mu x riesgo vial (mu = {capa.mu:g} m por hecho anual esperado)"
    return "distancia por la red"


def _paradas_propias(ruta: Ruta, ordenes_del_sistema: Mapping[str, Orden]) -> tuple[ParadaPlaneada, ...]:
    """Las Paradas con las Ordenes que hoy pertenecen a la Ruta (las que no
    estan en el sistema, como en el exportador del contrato, se dejan). Una
    Parada sin ninguna no se publica. Sin reemplazos ni contingencias es la
    Ruta tal como se planifico (NFR-07)."""
    propias = []
    for parada in ruta.paradas:
        suyas = tuple(o for o in parada.ordenes
                      if str(o) not in ordenes_del_sistema or ordenes_del_sistema[str(o)].ruta_id == ruta.id)
        if suyas == parada.ordenes:
            propias.append(parada)
        elif suyas:
            propias.append(dataclasses.replace(parada, ordenes=suyas))
    return tuple(propias)


def _hora_local(reemplazo: ReemplazoDeRuta) -> dt.time:
    """La hora local del reemplazo: la de salida de la Ruta nueva."""
    return _hora_local_de(reemplazo.instante)


def _hora_local_de(instante: dt.datetime) -> dt.time:
    return instante.astimezone(ZONA_OPERATIVA).time().replace(microsecond=0, tzinfo=None)


def _contingencias_de(
    contexto: ContextoDeLaApi, ruta_id: RutaId
) -> tuple[Contingencia | None, tuple[Contingencia, ...]]:
    """(la contingencia de la que nacio la Ruta, las activadas sobre ella)."""
    servicio = contexto.planificacion.contingencias
    if servicio is None:
        return None, ()
    return servicio.de_la_ruta_de_contingencia(ruta_id), servicio.activadas_desde(ruta_id)


def _reemplazos_de(
    contexto: ContextoDeLaApi, ruta_id: RutaId
) -> tuple[ReemplazoDeRuta | None, ReemplazoDeRuta | None]:
    """(el que la reemplazo, al que reemplaza), o None en cada lado."""
    servicio = contexto.planificacion.reemplazo
    if servicio is None:
        return None, None
    return servicio.reemplazo_de_la_reemplazada(ruta_id), servicio.reemplazo_de_la_nueva(ruta_id)


def _reemplazo_publicado(contexto: ContextoDeLaApi, r: ReemplazoDeRuta) -> ReemplazoPublicado:
    return ReemplazoPublicado(
        ruta_reemplazada=str(r.ruta_reemplazada), ruta_nueva=str(r.ruta_nueva), motivo=r.motivo,
        referencia=r.referencia, propuesta_id=r.propuesta_id, instante=r.instante,
        origen=Punto.desde(contexto.rotulo(r.origen_nodo)), fuente_del_origen=r.fuente_del_origen.value,
        paradas_cumplidas=r.paradas_cumplidas, mu_m=r.mu_m,
    )


def _en_listado(contexto: ContextoDeLaApi, persistida: RutaPersistida) -> RutaEnListado:
    """La Ruta del listado con sus enlaces de reemplazo, si los tiene (D11:
    ausentes en las demas)."""
    fila = RutaEnListado.desde(persistida)
    reemplazada_por, reemplaza_a = _reemplazos_de(contexto, persistida.ruta.id)
    if reemplazada_por is None and reemplaza_a is None and not _contingencias_de(contexto, persistida.ruta.id)[1]:
        return fila
    # Con Ordenes mudadas, las cantidades son las de lo que hoy es suyo (bloque 1).
    paradas = _paradas_propias(persistida.ruta, {str(o.id): o for o in contexto.ordenes.listar()})
    fila = fila.model_copy(update={"cantidad_paradas": len(paradas),
                                   "cantidad_ordenes": sum(len(p.ordenes) for p in paradas)})
    if reemplazada_por is None and reemplaza_a is None:
        return fila
    return fila.model_copy(update={
        "reemplazada_por": None if reemplazada_por is None else str(reemplazada_por.ruta_nueva),
        "reemplaza_a": None if reemplaza_a is None else str(reemplaza_a.ruta_reemplazada),
    })


def _eta_por_parada(
    contexto: ContextoDeLaApi, persistida: RutaPersistida
) -> tuple[EtaPublicado | None, ...]:
    """El ETA de cada Parada, una vez por Ruta y proceso: el calculo es
    determinista y la identidad de la Ruta sale de su contenido (2026-10-27C,
    F3; `_memoria_de_eta`)."""
    return contexto.memoria_de_eta.obtener(
        str(persistida.ruta.id), lambda: _calcular_eta_por_parada(contexto, persistida)
    )


def _calcular_eta_por_parada(
    contexto: ContextoDeLaApi, persistida: RutaPersistida
) -> tuple[EtaPublicado | None, ...]:
    """El ETA de cada Parada, calculado al responder y sin persistir (B-193,
    punto 3, criterio d).

    Se invoca el puerto de Prediccion de ETA con la distancia por la red de
    llegada a cada Parada, en el orden de la Ruta y empezando por el Deposito,
    y la cantidad de Ordenes que la Ruta le asigna (B-196, punto 4). Las
    distancias salen de la matriz de costos de Cartografia, la misma que
    optimiza el solver. Sin servicio, o si el estimador rechaza la Ruta por
    una franja o una celda que la tabla no cubre, todas sus Paradas van sin
    ETA: no hay valor de reemplazo (criterio f), y el rechazo queda en el log.
    """
    ruta = persistida.ruta
    sin_eta: tuple[EtaPublicado | None, ...] = (None,) * len(ruta.paradas)
    if (
        contexto.prediccion_eta is None
        or contexto.hora_de_salida is None
        or contexto.velocidad_km_h is None
    ):
        return sin_eta
    salida = contexto.hora_de_salida
    inicio = persistida.deposito
    _, reemplaza_a = _reemplazos_de(contexto, ruta.id)
    if reemplaza_a is not None:
        # CU-07: la Ruta nueva sale del origen del recalculo en el instante del
        # reemplazo (D12).
        salida = _hora_local(reemplaza_a)
        inicio = reemplaza_a.origen_nodo
    de_contingencia, _ = _contingencias_de(contexto, ruta.id)
    if de_contingencia is not None and de_contingencia.activada_en is not None:
        # CU-11: la Ruta de contingencia sale del Deposito al activarse.
        salida = _hora_local_de(de_contingencia.activada_en)
    salida_min = (
        salida.hour * MINUTOS_POR_HORA + salida.minute + salida.second / SEGUNDOS_POR_MINUTO
    )
    nodos = [inicio] + [parada.nodo for parada in ruta.paradas]
    # D36 (2026-11-23F): sobre los caminos con que se dibujo la Ruta.
    mu = _mu_del_trazado(contexto, ruta.id)
    con_riesgo = getattr(contexto.cartografia, "matriz_de_costos_con_riesgo", None)
    matriz = (contexto.cartografia.matriz_de_costos(nodos) if mu is None or con_riesgo is None
              else con_riesgo(nodos, mu))
    paradas = [
        ParadaARecorrer(
            distancia_por_la_red_m=matriz.distancia_m(origen, parada.nodo),
            ordenes=len(parada.ordenes),
        )
        for origen, parada in zip(nodos, ruta.paradas)
    ]
    try:
        estimadas = contexto.prediccion_eta.estimar(
            paradas, salida_min, contexto.velocidad_km_h
        )
    except ERRORES_DE_ESTIMACION as error:
        _LOG.warning("ETA ausente | ruta=%s | %s", ruta.id, error)
        return sin_eta

    def segundos_desde_la_salida(hora_min: float) -> float:
        return float(round((hora_min - salida_min) * SEGUNDOS_POR_MINUTO))

    return tuple(
        EtaPublicado(
            mediana_s=segundos_desde_la_salida(eta.mediana_min),
            p10_s=segundos_desde_la_salida(eta.p10_min),
            p90_s=segundos_desde_la_salida(eta.p90_min),
        )
        for eta in estimadas
    )



def _punto(rotulos: dict[int, RotuloNodo], nodo: int) -> Punto:
    return Punto.desde(rotulos[nodo])


def crear_app(contexto: ContextoDeLaApi) -> fastapi.FastAPI:
    """Arma la aplicacion sobre servicios ya ensamblados.

    Recibe el contexto en vez de construirlo: quien ensambla el sistema es
    `bootstrap`, y un adaptador que armara sus propias dependencias dejaria
    de ser reemplazable (ARQ-30).
    """
    app = fastapi.FastAPI(
        title=TITULO,
        version=VERSION,
        description=DESCRIPCION,
        servers=[
            {
                "url": "http://{host}:{puerto}",
                "description": DESCRIPCION_DEL_SERVIDOR,
                "variables": {
                    "host": {"default": HOST_LOCAL},
                    "puerto": {"default": PUERTO_LOCAL},
                },
            }
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ORIGENES_PERMITIDOS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Ninguna contrasena llega a los registros (seccion 4.2 del diseno).
    instalar_filtro_de_credenciales()

    # Dos routers bajo `/v1`. El publico lleva el estado, el ingreso y los
    # terminos, cada uno con su propia exigencia. El de operacion exige en
    # todas sus operaciones sesion, terminos aceptados y rol `operador`, y
    # declara los 401 y 403 en cada una (B-102).
    # La misma dependencia en el enrutador y en las operaciones que necesitan
    # al usuario: FastAPI la resuelve una sola vez por pedido.
    de_operador = dependencia_de_operador(contexto.identidad)
    publico = fastapi.APIRouter(prefix=PREFIJO_DE_VERSION)
    router = fastapi.APIRouter(
        prefix=PREFIJO_DE_VERSION,
        dependencies=[fastapi.Depends(de_operador)],
        responses=respuestas_de_acceso(),
    )

    def _plan_o_error(id_plan: str):
        """Resuelve el Plan o levanta el error del contrato."""
        try:
            return contexto.planificacion.plan(PlanId(id_plan))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.PLAN_NO_ENCONTRADO,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error

    def _responder(error: ErrorDeContrato) -> JSONResponse:
        _LOG.info("Error de contrato | codigo=%s %s", error.codigo.value, error.mensaje)
        return JSONResponse(
            status_code=int(error.estado),
            content=error.publicar().model_dump(mode="json"),
            headers=error.encabezados or None,
        )

    @app.exception_handler(ErrorDeContrato)
    async def _manejar_error_de_contrato(
        peticion: fastapi.Request, error: ErrorDeContrato
    ) -> JSONResponse:
        """Errores de negocio."""
        return _responder(error)

    @app.exception_handler(RequestValidationError)
    async def _manejar_validacion(
        peticion: fastapi.Request, error: RequestValidationError
    ) -> JSONResponse:
        """Rechazos de validacion de esquema, con la misma forma que los de
        negocio.

        FastAPI responderia con su propio formato --una lista bajo `detail`--
        que no se parece al de los errores de dominio. Quien consume la API
        tendria que escribir dos manejadores para el mismo endpoint. Se
        traduce al formato unico conservando que campo fallo y por que.
        """
        return _responder(desde_validacion_de_esquema(error.errors()))

    # --- Estado del servicio ---------------------------------------------

    @publico.get(
        "/estado",
        response_model=EstadoDelServicio,
        tags=["Servicio"],
        summary="Version del producto y del contrato",
    )
    def estado() -> EstadoDelServicio:
        """Contra que esta corriendo quien consume la API.

        Las dos versiones se publican por separado a proposito. La del
        producto avanza con cada entrega; la del contrato --la que esta en la
        ruta-- solo cambia cuando se rompe compatibilidad.
        """
        activo = contexto.grafo_activo
        return EstadoDelServicio(
            producto=NOMBRE_DEL_PRODUCTO,
            version=VERSION,
            contrato=PREFIJO_DE_VERSION.lstrip("/"),
            grafo=None
            if activo is None
            else GrafoActivoPublicado(
                archivo=activo.archivo,
                ambito=activo.ambito,
                sha256=activo.sha256[:LARGO_DEL_SHA256_ABREVIADO],
                verificado=activo.verificado,
            ),
            notificaciones_pendientes=contexto.planificacion.notificaciones_pendientes(),
        )

    # El mismo estado, fuera del prefijo de version. Es el unico endpoint sin
    # versionar y es deliberado: preguntar que version de contrato corre no
    # puede exigir conocerla de antemano. Los endpoints de dominio llevan
    # prefijo; el de descubrimiento, no. Se declara con la misma funcion para
    # que las dos rutas no puedan responder cosas distintas.
    app.add_api_route(
        RUTA_DE_ESTADO,
        estado,
        methods=["GET"],
        response_model=EstadoDelServicio,
        tags=["Servicio"],
        summary="Version del producto y del contrato, sin versionar la ruta",
    )

    # --- Flota: administracion de los Vehiculos (2026-11-02) ---------------

    def publicar_vehiculo(vehiculo: VehiculoRegistrado, ocupados: dict) -> VehiculoRegistradoPublicado:
        margen = contexto.planificacion.margen_de_capacidad
        return VehiculoRegistradoPublicado(
            id=str(vehiculo.id),
            capacidad=vehiculo.capacidad,
            capacidad_efectiva=capacidad_efectiva(vehiculo.capacidad, margen),
            disponible=vehiculo.activo and vehiculo.id not in ocupados,
            comprometido_en=str(ocupados[vehiculo.id]) if vehiculo.id in ocupados else None,
            activo=vehiculo.activo,
        )

    def ocupados_hoy(vehiculos: Sequence[VehiculoId]) -> dict:
        return contexto.planificacion.vehiculos_comprometidos(
            tuple(vehiculos), contexto.planificacion.jornada_operativa()
        )

    @router.get(
        "/vehiculos",
        response_model=list[VehiculoRegistradoPublicado],
        tags=["Flota"],
        summary="Listar la Flota registrada, tambien los Vehiculos dados de baja",
    )
    def listar_vehiculos() -> list[VehiculoRegistradoPublicado]:
        """Cada Vehiculo con su capacidad, si esta activo y si tiene Ruta en la
        jornada operativa en curso. Por patente."""
        registrados = contexto.planificacion.vehiculos_registrados()
        ocupados = ocupados_hoy([v.id for v in registrados])
        return [publicar_vehiculo(v, ocupados) for v in registrados]

    @router.post(
        "/vehiculos",
        response_model=VehiculoRegistradoPublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Flota"],
        summary="Dar de alta un Vehiculo en la Flota",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.CONFLICT,
                "`vehiculo_existente` si la patente ya esta en la Flota, activa o dada "
                "de baja (una dada de baja se reactiva, no se da de alta).",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`patente_invalida` si no es Mercosur ni la anterior; "
                "`capacidad_invalida` si no esta entre 1 y 999 bultos o no deja "
                "capacidad efectiva; `peticion_mal_formada` si el cuerpo no cumple "
                "el esquema.",
            ),
        ),
    )
    def dar_de_alta_vehiculo(solicitud: VehiculoSolicitado) -> VehiculoRegistradoPublicado:
        """El Vehiculo entra activo y libre: se puede elegir en la proxima
        planificacion, sin reiniciar nada."""
        try:
            vehiculo = contexto.planificacion.dar_de_alta_vehiculo(solicitud.id, solicitud.capacidad)
        except (PatenteInvalida, CapacidadInvalida, VehiculoExistente) as error:
            raise _error_de_flota(error) from error
        return publicar_vehiculo(vehiculo, {})

    @router.patch(
        "/vehiculos/{id_vehiculo}",
        response_model=VehiculoRegistradoPublicado,
        tags=["Flota"],
        summary="Cambiar la capacidad de un Vehiculo, darlo de baja o reactivarlo",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`vehiculo_no_encontrado` si la patente no esta en la Flota."),
            (
                http.HTTPStatus.CONFLICT,
                "`vehiculo_en_uso` si se le cambia la capacidad o se lo da de baja "
                "teniendo una Ruta en un Plan de la jornada en curso.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`capacidad_invalida` si la capacidad no esta entre 1 y 999 bultos o "
                "no deja capacidad efectiva; `peticion_mal_formada` si el cuerpo no "
                "cumple el esquema.",
            ),
        ),
    )
    def modificar_vehiculo(id_vehiculo: str, solicitud: CambioDeVehiculoSolicitado) -> VehiculoRegistradoPublicado:
        """`activo` en false lo da de baja (no se borra: las Rutas que ya
        recorrio lo nombran); en true, lo reactiva. Lo que no viene en el
        cuerpo no cambia."""
        try:
            vehiculo = contexto.planificacion.modificar_vehiculo(
                id_vehiculo, capacidad=solicitud.capacidad, activo=solicitud.activo
            )
        except (CapacidadInvalida, VehiculoInexistente, VehiculoEnUso) as error:
            raise _error_de_flota(error) from error
        return publicar_vehiculo(vehiculo, ocupados_hoy([vehiculo.id]))

    # --- Instancia: lo que existe antes de que haya plan -----------------

    @router.get(
        "/flota",
        response_model=FlotaPublicada,
        tags=["Instancia"],
        summary="Consultar la Flota vigente y su disponibilidad por jornada",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si la fecha no es una fecha ISO.",
            ),
        ),
    )
    def consultar_flota(fecha: dt.date | None = None) -> FlotaPublicada:
        """Los Vehiculos disponibles, legibles antes de planificar (FR-15).

        Componer una planificacion --elegir Vehiculos y comparar la demanda
        seleccionada contra la capacidad seleccionada-- exige conocer la
        Flota, y hasta ahora los Vehiculos solo aparecian dentro del detalle
        de una Ruta ya publicada. El contrato los aceptaba en la solicitud y
        no los devolvia.

        Cada Vehiculo informa ademas si esta **disponible para la jornada
        consultada** y, si no lo esta, que Plan lo ocupa. Un Vehiculo
        participa de un unico Plan por jornada (glosario, entidad 13), y sin
        este dato la unica forma de anticipar la regla era recorrer las Rutas
        del dia y reconstruirla afuera: una segunda copia de una regla de
        dominio, capaz de discrepar de la que se aplica al planificar. Quien
        compone la instancia puede ahora ofrecer solo lo elegible, y el
        rechazo del backend queda como lo que debe ser --la ultima palabra, no
        la primera noticia--.

        `fecha` omitida se resuelve con la jornada operativa en curso, que es
        aquella en la que una planificacion disparada ahora quedaria
        registrada. La jornada evaluada viaja en la respuesta: una
        disponibilidad sin la fecha a la que corresponde no significa nada.

        La Flota es una coleccion, no una entidad con identidad propia
        (glosario, entidad 5). Esto publica los Vehiculos activos; desde
        2026-11-02 el operador la administra en `/v1/vehiculos` (altas,
        capacidad, bajas y reactivaciones).
        """
        jornada = (
            fecha
            if fecha is not None
            else contexto.planificacion.jornada_operativa()
        )
        ocupados = contexto.planificacion.vehiculos_comprometidos(
            tuple(contexto.flota), jornada
        )
        # La efectiva sale del mismo margen que usa el calculo (F1.4a).
        margen = contexto.planificacion.margen_de_capacidad
        vehiculos = tuple(
            VehiculoPublicado(
                id=str(vehiculo),
                capacidad=capacidad,
                capacidad_efectiva=capacidad_efectiva(capacidad, margen),
                disponible=vehiculo not in ocupados,
                comprometido_en=(
                    str(ocupados[vehiculo]) if vehiculo in ocupados else None
                ),
            )
            for vehiculo, capacidad in sorted(contexto.flota.items())
        )
        return FlotaPublicada(
            vehiculos=vehiculos,
            capacidad_total=sum(v.capacidad for v in vehiculos),
            fecha=jornada,
            capacidad_disponible=sum(v.capacidad for v in vehiculos if v.disponible),
            margen_de_capacidad=margen,
            capacidad_efectiva_total=sum(v.capacidad_efectiva for v in vehiculos),
            capacidad_efectiva_disponible=sum(v.capacidad_efectiva for v in vehiculos if v.disponible),
        )

    @router.get(
        "/depositos",
        response_model=DepositosPublicados,
        tags=["Instancia"],
        summary="Consultar los Depositos de la operacion",
    )
    def consultar_depositos() -> DepositosPublicados:
        """El origen y retorno de toda Ruta, exista o no un plan (FR-14).

        Es una precondicion de la planificacion, asi que tiene que poder
        leerse antes de que haya Rutas: hasta ahora solo se obtenia dentro
        del detalle de una, de modo que un sistema sin planificar no podia
        ni ubicarlo en el mapa.

        Coleccion de un elemento: fase 1 opera con un Deposito unico (B-12),
        pero el glosario admite varios y FR-12 los contempla. Publicarlo como
        objeto unico obligaria a un cambio incompatible el dia que haya mas
        de uno.
        """
        return DepositosPublicados(
            depositos=(
                DepositoPublicado(punto=Punto.desde(contexto.rotulo(contexto.deposito))),
            )
        )

    @router.get(
        "/anclajes",
        response_model=AnclajePublicado,
        tags=["Instancia"],
        summary="Resolver una coordenada al nodo del grafo, sin registrar nada",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si falta alguna coordenada o esta "
                "fuera de rango, con el detalle por campo.",
            ),
        ),
    )
    def resolver_anclaje(lat: Latitud, lon: Longitud) -> AnclajePublicado:
        """A que nodo engancharia una coordenada, y a que distancia.

        Consulta sin efecto: no crea ninguna Orden ni modifica nada. Existe
        porque el anclaje ocurria dentro del alta, de modo que se veia el
        resultado despues de haber registrado. La distancia es lo que permite
        juzgarlo: un enganche a 18 m es correcto, uno a 300 m es un dato para
        revisar **antes** de registrar.

        El nodo se busca dentro de la mayor componente fuertemente conexa,
        que es la misma restriccion que aplica el alta: asi lo que se
        previsualiza es lo que va a ocurrir.
        """
        utilizables = contexto.cartografia.nodos_de_mayor_componente_fuerte()
        anclaje = contexto.cartografia.anclar(lat, lon, restringido_a=utilizables)
        return AnclajePublicado.desde(
            anclaje,
            contexto.rotulo(anclaje.nodo.id_osm),
            es_el_deposito=anclaje.nodo.id_osm == contexto.deposito,
        )

    @router.get(
        "/direcciones",
        response_model=DireccionesEncontradas,
        tags=["Instancia"],
        summary="Buscar una direccion con Georef y anclarla al grafo, sin registrar nada (RB-10)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si falta el texto, esta vacio o pasa de "
                f"{LARGO_MAXIMO_DEL_TEXTO} caracteres.",
            ),
            (
                http.HTTPStatus.TOO_MANY_REQUESTS,
                "`limite_de_georef`: la busqueda pasaria uno de los limites de uso de Georef "
                "(10 por segundo, 40 por minuto, 2.000 por hora y 10.000 por dia, B-247). El "
                "detalle trae `segundos_de_espera`.",
            ),
            (
                http.HTTPStatus.SERVICE_UNAVAILABLE,
                "`georef_no_responde` si Georef no contesto a tiempo o contesto con un error "
                "suyo; `geocodificador_no_disponible` si este servidor arranco sin busqueda "
                "por direccion.",
            ),
        ),
    )
    def buscar_direccion(
        texto: Annotated[str, fastapi.Query(min_length=1, max_length=LARGO_MAXIMO_DEL_TEXTO)],
    ) -> DireccionesEncontradas:
        """Busca la direccion en Georef y ancla cada candidato al nodo mas
        cercano de la componente mayor del grafo activo, como `/anclajes`.

        Consulta sin efecto: no crea ninguna Orden. El alta se hace despues con
        la coordenada del candidato elegido, y la Orden guarda esa coordenada y
        el nodo (B-259, punto 6). Una busqueda repetida sale de la cache del
        servidor y no consume cuota de Georef.
        """
        if contexto.geocodificador is None:
            raise ErrorDeContrato(
                codigo=CodigoDeError.GEOCODIFICADOR_NO_DISPONIBLE,
                mensaje="Este servidor arranco sin busqueda por direccion: marca el destino en el mapa.",
                estado=http.HTTPStatus.SERVICE_UNAVAILABLE,
            )
        if not texto.strip():
            raise ErrorDeContrato(
                codigo=CodigoDeError.PETICION_MAL_FORMADA,
                mensaje="El texto de la direccion esta vacio.",
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        try:
            encontrados = contexto.geocodificador.buscar(texto)
        except LimiteDeConsultasAlcanzado as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.LIMITE_DE_GEOREF,
                mensaje=f"{error} Probá de nuevo en un rato.",
                estado=http.HTTPStatus.TOO_MANY_REQUESTS,
                detalle=DetalleLimiteDeGeoref(segundos_de_espera=error.segundos_de_espera),
            ) from error
        except GeocodificadorNoResponde as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.GEOREF_NO_RESPONDE,
                mensaje=f"{error}. Marcá el destino en el mapa o probá de nuevo.",
                estado=http.HTTPStatus.SERVICE_UNAVAILABLE,
            ) from error
        utilizables = contexto.cartografia.nodos_de_mayor_componente_fuerte()
        candidatos = []
        for encontrado in encontrados:
            anclaje = contexto.cartografia.anclar(encontrado.lat, encontrado.lon, restringido_a=utilizables)
            candidatos.append(
                CandidatoDeDireccionPublicado(
                    direccion=encontrado.direccion,
                    consultado=Coordenada(lat=encontrado.lat, lon=encontrado.lon),
                    nodo=Punto.desde(contexto.rotulo(anclaje.nodo.id_osm)),
                    distancia_m=anclaje.distancia_m,
                    es_el_deposito=anclaje.nodo.id_osm == contexto.deposito,
                )
            )
        return DireccionesEncontradas(texto=texto, candidatos=tuple(candidatos), atribucion=ATRIBUCION_DE_GEOREF)

    # --- Ordenes ---------------------------------------------------------

    @router.post(
        "/ordenes",
        response_model=OrdenPublicada,
        status_code=http.HTTPStatus.CREATED,
        tags=["Ordenes"],
        summary="Dar de alta una Orden de entrega (CU-01)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema, con "
                "el detalle por campo. `destino_es_el_deposito` si la "
                "coordenada ancla al nodo del Deposito (CU-01, flujo 4b). "
                "`destino_no_localizable` si el punto queda a mas de 1.000 m de "
                "la calle mas cercana (CU-01, flujo 4a). `ventana_invalida` si "
                "la ventana horaria no empieza antes de terminar. "
                "`demanda_invalida` si la demanda no es una cantidad de bultos "
                "utilizable.",
            ),
        ),
    )
    def alta_de_orden(alta: AltaDeOrden) -> OrdenPublicada:
        """Registra una Orden anclada al nodo de la red vial mas cercano.

        El anclaje se restringe a la mayor componente fuertemente conexa: un
        destino fuera de ella dejaria pares sin camino y volveria infactible
        toda planificacion que lo incluya. Es decir que el destino siempre
        resulta localizable, y lo que la respuesta informa es a que punto de
        la calle quedo anclado.
        """
        utilizables = contexto.cartografia.nodos_de_mayor_componente_fuerte()
        anclaje = contexto.cartografia.anclar(alta.lat, alta.lon, restringido_a=utilizables)
        nodo = anclaje.nodo
        if anclaje.distancia_m > DISTANCIA_MAXIMA_DE_ANCLAJE_M:
            # Flujo 4a de CU-01 (2026-11-20A): mas lejos que esto de toda calle
            # del grafo, el punto no se localiza sobre la red. Entre 300 m y
            # este umbral la interfaz advierte y pide confirmacion (B-249).
            raise ErrorDeContrato(
                codigo=CodigoDeError.DESTINO_NO_LOCALIZABLE,
                mensaje=(
                    f"El punto queda a {anclaje.distancia_m:.0f} m de la calle mas cercana del grafo vial, "
                    f"mas que los {DISTANCIA_MAXIMA_DE_ANCLAJE_M:.0f} m admitidos: no se localiza sobre la red"
                ),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        if nodo.id_osm == contexto.deposito:
            # Flujo 4b de CU-01: el Deposito es el origen de toda Ruta, no un
            # destino de entrega.
            raise ErrorDeContrato(
                codigo=CodigoDeError.DESTINO_ES_EL_DEPOSITO,
                mensaje=(
                    f"El destino se ancla al nodo {nodo.id_osm}, que es el "
                    f"Deposito: una Orden no se entrega en el punto del que "
                    f"parte la Ruta"
                ),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        try:
            ventana = (
                None
                if alta.ventana_horaria is None
                else VentanaHoraria(alta.ventana_horaria.desde, alta.ventana_horaria.hasta)
            )
        except VentanaInvalida as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.VENTANA_INVALIDA,
                mensaje=str(error),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        try:
            orden = contexto.ordenes.registrar(
                alta.demanda,
                nodo.id_osm,
                alta.prioridad,
                alta.plazo_comprometido,
                punto_del_alta=PuntoDelAlta(alta.lat, alta.lon),
                ventana_horaria=ventana,
                modalidad=Modalidad(alta.modalidad),
            )
        except DemandaInvalida as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.DEMANDA_INVALIDA,
                mensaje=str(error),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        _LOG.info(
            "Orden dada de alta | id=%s demanda=%d destino=%d",
            orden.id,
            orden.demanda,
            orden.destino,
        )
        return _publicar_orden(contexto, orden)

    @router.get(
        "/ordenes",
        response_model=list[OrdenPublicada],
        tags=["Ordenes"],
        summary="Consultar Ordenes, filtrables por estado (CU-04)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el estado pedido no pertenece al "
                "catalogo canonico del glosario.",
            ),
        ),
    )
    def listar_ordenes(estado: EstadoOrden | None = None) -> list[OrdenPublicada]:
        """Las planificadas informan su Ruta y su numero de Parada (RB-09)."""
        return [
            _publicar_orden(contexto, orden)
            for orden in contexto.ordenes.listar(estado)
        ]

    @router.get(
        "/ordenes/{id_orden}",
        response_model=OrdenPublicada,
        tags=["Ordenes"],
        summary="Consultar una Orden por su identificador (CU-04)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`orden_no_encontrada`: el identificador no corresponde a "
                "ninguna Orden registrada (CU-04, flujo 1a).",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no cumple "
                "el esquema. Se declara por completitud: FastAPI agrega este "
                "estado a toda operacion con parametros, y sin declararlo el "
                "esquema afirmaria una forma que la API no devuelve.",
            ),
        ),
    )
    def consultar_orden(id_orden: str) -> OrdenPublicada:
        """Estado vigente, historial completo y asignacion de una Orden.

        El estado pertenece siempre al catalogo canonico del glosario. El
        historial trae cada transicion con su marca temporal, en UTC. Si la
        Orden todavia no fue asignada a ninguna Parada, `asignacion` viene en
        null y se consigna de forma expresa (CU-04, flujo 4a).
        """
        try:
            orden = contexto.ordenes.consultar(OrdenId(id_orden))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.ORDEN_NO_ENCONTRADA,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        return _publicar_orden(contexto, orden)

    @router.post(
        "/ordenes/{id_orden}/desasignacion",
        response_model=DesasignacionAplicada,
        tags=["Ordenes"],
        summary="Devolver una Orden a Creada para replanificarla (FR-03)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`orden_no_encontrada`: el identificador no corresponde a "
                "ninguna Orden registrada.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`orden_no_asignada`: la Orden no esta asignada a ninguna "
                "Ruta, asi que no hay nada que desasignar. "
                "`ruta_en_ejecucion`: la Ruta de la Orden ya fue despachada, y "
                "el ciclo de vida solo admite volver a Creada desde Planificada.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no cumple "
                "el esquema. Se declara por completitud: FastAPI agrega este "
                "estado a toda operacion con parametros, y sin declararlo el "
                "esquema afirmaria una forma que la API no devuelve.",
            ),
        ),
    )
    def desasignar_orden(id_orden: str) -> DesasignacionAplicada:
        """Libera la Orden y, con ella, la Ruta que la contenia.

        El glosario admite el retorno de Planificada a Creada como el
        mecanismo que soporta la replanificacion. La Ruta se elimina entera
        --no se le quita la Parada-- porque es la salida de un calculo sobre
        un conjunto: sin una de sus Ordenes, su distancia y su carga dejarian
        de corresponder a su recorrido. Por eso todas sus Ordenes quedan
        libres, no solo la pedida, y la respuesta lo informa.
        """
        try:
            ruta_id, liberadas = contexto.planificacion.desasignar_orden(
                OrdenId(id_orden)
            )
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.ORDEN_NO_ENCONTRADA,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        except OrdenNoAsignada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.ORDEN_NO_ASIGNADA,
                mensaje=str(error),
                estado=http.HTTPStatus.CONFLICT,
            ) from error
        except RutaEnEjecucion as error:
            raise desde_ordenes_fuera_de_estado(error, CodigoDeError.RUTA_EN_EJECUCION) from error
        _LOG.info(
            "Desasignacion | orden=%s ruta_invalidada=%s liberadas=%d",
            id_orden,
            ruta_id,
            len(liberadas),
        )
        return DesasignacionAplicada(
            ruta_invalidada=str(ruta_id),
            ordenes_liberadas=tuple(str(orden) for orden in liberadas),
        )

    # --- Planificacion ---------------------------------------------------

    @router.post(
        "/planificaciones",
        response_model=PlanPublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Planificacion"],
        summary="Disparar una planificacion (CU-02)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.CONFLICT,
                "Los dos flujos de excepcion de CU-02, distinguibles por "
                "codigo porque la correccion es distinta: "
                "`paradas_incomunicadas` (flujo 3a) cuando no hay camino entre "
                "pares de Paradas, con los pares agrupados bajo la Parada que "
                "los causa; `demanda_excede_capacidad` (flujo 4a) cuando la "
                "Flota seleccionada no alcanza y no entra ninguna Orden, con "
                "los bultos faltantes; si entra al menos una, la respuesta es "
                "201 con un Plan parcial y `ordenes_no_planificadas`. "
                "Ademas `vehiculos_comprometidos` si algun Vehiculo "
                "seleccionado ya participa de un Plan de la jornada (glosario, "
                "entidad 13), con cada Vehiculo y el Plan que lo ocupa; "
                "`sin_ordenes_planificables` si no hay Ordenes en Creada, y "
                "`sin_solucion` si el solver no encuentra una asignacion, "
                "ni siquiera parcial: por infactibilidad o porque alcanzo el "
                "limite de busqueda, que el mensaje distingue. Ninguno de "
                "estos produce plan.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema. "
                "`solicitud_invalida` si la seleccion no describe un problema "
                "planteable, por ejemplo una lista de Ordenes vacia. "
                "`vehiculos_fuera_de_la_flota` si algun Vehiculo no pertenece "
                "a la Flota registrada, con cada identificador rechazado; "
                "`capacidad_distinta_de_la_nominal` si algun Vehiculo se pide "
                "con una capacidad distinta de la nominal, con la pedida y la "
                "nominal de cada uno. En ambos se rechaza la seleccion entera.",
            ),
        ),
    )
    def planificar(solicitud: PlanificacionSolicitada | None = None) -> PlanPublicado:
        """Resuelve el CVRP y publica una Ruta por Vehiculo.

        Los tres parametros son opcionales: sin cuerpo, planifica todas las
        Ordenes planificables con la Flota y el Deposito vigentes.

        Las dos condiciones de error de CU-02 salen con codigo propio --
        `paradas_incomunicadas` y `demanda_excede_capacidad`-- porque la causa
        y la accion correctiva son distintas (RB-05).

        Si la demanda excede la capacidad efectiva de la Flota, o el motor no
        encuentra una solucion con todas las Ordenes dentro de su limite de
        busqueda, el Plan es parcial: deja afuera primero las Ordenes de menor
        criticidad y las declara en `ordenes_no_planificadas`, que siguen en
        Creada (B-220, punto 2). `demanda_excede_capacidad` queda solo para
        cuando no entra ninguna.

        Los Vehiculos se eligen dentro de la Flota que publica /v1/flota y
        con su capacidad nominal; otro identificador u otra capacidad
        rechazan la seleccion entera (B-141).
        """
        pedido = solicitud or PlanificacionSolicitada()
        deposito = pedido.deposito if pedido.deposito is not None else contexto.deposito
        flota = (
            {VehiculoId(v): c for v, c in pedido.vehiculos.items()}
            if pedido.vehiculos is not None
            else contexto.flota
        )
        ordenes = (
            tuple(OrdenId(o) for o in pedido.ordenes)
            if pedido.ordenes is not None
            else None
        )
        try:
            plan = contexto.planificacion.planificar_plan(
                deposito, flota, ordenes,
                None if pedido.depositos_por_vehiculo is None
                else {VehiculoId(v): n for v, n in pedido.depositos_por_vehiculo.items()},
            )
        except VehiculosFueraDeLaFlota as error:
            raise desde_vehiculos_fuera_de_la_flota(error) from error
        except CapacidadDistintaDeLaNominal as error:
            raise desde_capacidad_distinta_de_la_nominal(error) from error
        except VehiculosComprometidos as error:
            raise desde_vehiculos_comprometidos(error) from error
        except ParadasIncomunicadas as error:
            raise desde_paradas_incomunicadas(
                error, contexto.rotulos([p.nodo for p in error.paradas])
            ) from error
        except DemandaExcedeCapacidad as error:
            raise desde_demanda_excede_capacidad(error) from error
        except SolicitudInvalida as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.SOLICITUD_INVALIDA,
                mensaje=str(error),
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            ) from error
        except InstanciaInvalida as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.SIN_ORDENES_PLANIFICABLES,
                mensaje=str(error),
                estado=http.HTTPStatus.CONFLICT,
            ) from error
        except SinSolucion as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.SIN_SOLUCION,
                mensaje=str(error),
                estado=http.HTTPStatus.CONFLICT,
            ) from error

        persistidas = [
            contexto.planificacion.ruta_persistida(ruta_id) for ruta_id in plan.rutas
        ]
        return PlanPublicado(
            plan=PlanEnListado.desde(plan),
            fecha_planificacion=plan.fecha_planificacion,
            distancia_total_m=plan.distancia_total_m,
            demanda_total=plan.demanda_total,
            capacidad_total=sum(flota.values()),
            rutas=tuple(_en_listado(contexto, p) for p in persistidas),
            ordenes_no_planificadas=tuple(
                OrdenNoPlanificadaPublicada.desde(fuera)
                for fuera in plan.ordenes_no_planificadas
            ),
        )

    # --- Plan de Distribucion (glosario, entidad 13) ---------------------

    @router.get(
        "/planes",
        response_model=list[PlanEnListado],
        tags=["Planes"],
        summary="Consultar los Planes de Distribucion, filtrables por fecha",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si la fecha no es una fecha ISO. Una "
                "jornada sin Planes no es un error: devuelve la lista vacia.",
            ),
        ),
    )
    def listar_planes(fecha: dt.date | None = None) -> list[PlanEnListado]:
        """Las corridas de planificacion con sus totales.

        El Plan es la unidad de lectura por encima de la Ruta: sus totales
        vienen calculados, de modo que no haya que sumar el listado de Rutas
        --suma que deja de ser correcta en cuanto conviven varios Planes en
        una jornada--.
        """
        return [
            PlanEnListado.desde(plan)
            for plan in contexto.planificacion.planes(fecha)
        ]

    @router.get(
        "/planes/{id_plan}",
        response_model=PlanConRutas,
        tags=["Planes"],
        summary="Consultar un Plan de Distribucion con sus Rutas",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`plan_no_encontrado`: el identificador no corresponde a "
                "ningun Plan vigente, o el Plan fue descartado.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no "
                "cumple el esquema.",
            ),
        ),
    )
    def consultar_plan(id_plan: str) -> PlanConRutas:
        """Un Plan con las Rutas que lo componen, sin geometria.

        El trazado son cientos de coordenadas por Ruta; quien dibuja pide el
        detalle de la Ruta que va a mostrar.
        """
        plan = _plan_o_error(id_plan)
        rutas = contexto.planificacion.rutas_del_plan(plan.id)
        return PlanConRutas(
            plan=PlanEnListado.desde(plan),
            deposito=Punto.desde(contexto.rotulo(plan.deposito)),
            rutas=tuple(_en_listado(contexto, p) for p in rutas),
        )

    @router.delete(
        "/planes/{id_plan}",
        response_model=DesasignacionAplicada,
        tags=["Planes"],
        summary="Descartar un Plan entero y liberar sus Ordenes y Vehiculos",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`plan_no_encontrado`: el identificador no corresponde a "
                "ningun Plan vigente.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`ruta_en_ejecucion`: alguna Ruta del Plan ya fue despachada. "
                "No se descarta ninguna.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no "
                "cumple el esquema.",
            ),
        ),
    )
    def descartar_plan(id_plan: str) -> DesasignacionAplicada:
        """Elimina el Plan, sus Rutas, y libera lo que ocupaba.

        Las Ordenes vuelven a Creada y quedan planificables; los Vehiculos
        quedan disponibles para otra corrida de la misma jornada. Es la
        operacion que corresponde a la entidad: descartar Ruta por Ruta
        dejaba el Plan existente y sin Rutas, con sus Vehiculos ocupados sin
        nada que lo justificara.
        """
        plan = _plan_o_error(id_plan)
        try:
            liberadas = contexto.planificacion.descartar_plan(plan.id)
        except RutaEnEjecucion as error:
            raise desde_ordenes_fuera_de_estado(error, CodigoDeError.RUTA_EN_EJECUCION) from error
        _LOG.info(
            "Plan descartado | %s | liberadas=%d", id_plan, len(liberadas)
        )
        return DesasignacionAplicada(
            ruta_invalidada=id_plan,
            ordenes_liberadas=tuple(str(orden) for orden in liberadas),
        )

    @router.get(
        "/jornadas",
        response_model=JornadasPublicadas,
        tags=["Planes"],
        summary="Consultar las jornadas que tienen Plan (RB-11)",
    )
    def listar_jornadas() -> JornadasPublicadas:
        """Las fechas con al menos un Plan, en orden.

        Es lo que permite ofrecer navegacion entre jornadas sin adivinar:
        sin esto, la unica forma de saber si un dia tiene contenido era pedir
        sus Rutas y ver si volvia vacio.

        Se enumeran jornadas con **Plan** y no con Ruta. Hoy dan lo mismo,
        pero el Plan es la unidad de lectura para el operador y es lo que
        registra que hubo una corrida; contar Rutas obligaria ademas a
        decidir que informar de una jornada cuyas Rutas se descartaron una
        por una.
        """
        return JornadasPublicadas(
            fechas=contexto.planificacion.fechas_con_planificacion()
        )

    # --- Rutas -----------------------------------------------------------

    @router.get(
        "/rutas",
        response_model=list[RutaEnListado],
        tags=["Rutas"],
        summary="Consultar Rutas, filtrables por fecha (CU-03)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si la fecha no es una fecha ISO. Una "
                "fecha sin Rutas no es un error: devuelve la lista vacia "
                "(CU-03, flujo 1a).",
            ),
        ),
    )
    def listar_rutas(fecha: dt.date | None = None) -> list[RutaEnListado]:
        """Sin geometria: el trazado son cientos de coordenadas por Ruta.

        Quien dibuja pide el detalle de la Ruta que va a mostrar.
        """
        return [
            _en_listado(contexto, persistida)
            for persistida in contexto.planificacion.rutas_persistidas(fecha)
        ]

    @router.get(
        "/rutas/{id_ruta}",
        response_model=RutaPublicada,
        tags=["Rutas"],
        summary="Consultar una Ruta con su geometria dibujable (CU-03)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`ruta_no_encontrada`: el identificador no corresponde a "
                "ninguna Ruta publicada, o la Ruta fue invalidada.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no cumple "
                "el esquema. Se declara por completitud: FastAPI agrega este "
                "estado a toda operacion con parametros, y sin declararlo el "
                "esquema afirmaria una forma que la API no devuelve.",
            ),
        ),
    )
    def consultar_ruta(id_ruta: str) -> RutaPublicada:
        """Incluye el trazado: la polilinea completa del recorrido real.

        Son pares (latitud, longitud) en el orden del recorrido, siguiendo la
        traza de cada calle. Dibujar rectas entre Paradas mostraria segmentos
        que no existen en la red vial (NFR-08).
        """
        try:
            persistida = contexto.planificacion.ruta_persistida(RutaId(id_ruta))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.RUTA_NO_ENCONTRADA,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        return _publicar_ruta(contexto, persistida)

    _ruta_no_encontrada = (
        (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada`."),
        (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
    )

    def _persistida(id_ruta: str) -> RutaPersistida:
        try:
            return contexto.planificacion.ruta_persistida(RutaId(id_ruta))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.RUTA_NO_ENCONTRADA, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error

    @router.get(
        "/rutas/{id_ruta}/brecha",
        response_model=BrechaPublicada,
        tags=["Rutas"],
        summary="Brecha de la Ruta respecto del orden optimo de sus Paradas (D23)",
        responses=respuestas_de_error(*_ruta_no_encontrada),
    )
    def brecha_de_ruta(id_ruta: str) -> BrechaPublicada:
        """Optimo exacto (Held-Karp) del mismo recorrido y el mismo objetivo con
        hasta 12 Paradas distintas; con mas, `calculada` es falso y `motivo`
        lo dice. Operacion aparte: la Ruta publicada no cambia (NFR-07)."""
        inicio, nodos, deposito = _recorrido_de_la_ruta(contexto, _persistida(id_ruta))
        matriz = contexto.cartografia.matriz_de_costos([inicio, *nodos, deposito])
        b = brecha_de_optimalidad(inicio, nodos, deposito,
                                  lambda a, c: 0.0 if a == c else matriz.costo_m(a, c))
        return BrechaPublicada(ruta_id=id_ruta, objetivo=_objetivo_del_ruteo(contexto), paradas=b.paradas,
                               calculada=b.calculada, costo_m=b.costo_m, optimo_m=b.optimo_m, brecha=b.brecha,
                               orden_optimo=b.orden_optimo, motivo=b.motivo)

    @router.get(
        "/rutas/{id_ruta}/exposicion-al-riesgo-vial",
        response_model=ExposicionAlRiesgoVialPublicada,
        tags=["Rutas"],
        summary="Exposicion de la Ruta al riesgo vial de sus tramos (D25)",
        responses=respuestas_de_error(*_ruta_no_encontrada),
    )
    def exposicion_de_ruta(id_ruta: str) -> ExposicionAlRiesgoVialPublicada:
        """Suma del riesgo esperado de los arcos del trazado de la Ruta. Sin
        tabla de riesgo para el grafo cargado, `calculada` es falso."""
        inicio, nodos, deposito = _recorrido_de_la_ruta(contexto, _persistida(id_ruta))
        calcular = getattr(contexto.cartografia, "exposicion_al_riesgo_vial", None)
        # D31 (2026-11-23E): una Ruta nacida de una propuesta por riesgo vial
        # se mide con los caminos de su mu, los que dibuja.
        mu = _mu_del_trazado(contexto, RutaId(id_ruta))
        e = (None if calcular is None
             else calcular([inicio, *nodos, deposito]) if mu is None
             else calcular([inicio, *nodos, deposito], mu=mu))
        if e is None:
            return ExposicionAlRiesgoVialPublicada(
                ruta_id=id_ruta, calculada=False, exposicion_hechos_por_anio=None, arcos=None, mu=None, tabla=None,
                tabla_sha256=None, motivo="no hay tabla de riesgo vial para el grafo cargado")
        return ExposicionAlRiesgoVialPublicada(
            ruta_id=id_ruta, calculada=True, exposicion_hechos_por_anio=e.hechos_por_anio, arcos=e.arcos, mu=e.mu,
            tabla=e.archivo, tabla_sha256=e.sha256, motivo=None)

    @router.delete(
        "/rutas/{id_ruta}",
        response_model=DesasignacionAplicada,
        tags=["Rutas"],
        summary="Invalidar una Ruta y liberar sus Ordenes (FR-03)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`ruta_no_encontrada`: el identificador no corresponde a "
                "ninguna Ruta vigente.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`ruta_en_ejecucion`: la Ruta ya fue despachada, y el ciclo de "
                "vida solo admite volver a Creada desde Planificada.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador de la ruta no cumple "
                "el esquema. Se declara por completitud: FastAPI agrega este "
                "estado a toda operacion con parametros, y sin declararlo el "
                "esquema afirmaria una forma que la API no devuelve.",
            ),
        ),
    )
    def invalidar_ruta(id_ruta: str) -> DesasignacionAplicada:
        """La misma operacion que la desasignacion, entrando por la Ruta.

        Es la via natural para replanificar una corrida entera: se invalida
        la Ruta y todas sus Ordenes vuelven a estar disponibles.
        """
        try:
            liberadas = contexto.planificacion.invalidar_ruta(RutaId(id_ruta))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.RUTA_NO_ENCONTRADA,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        except RutaEnEjecucion as error:
            raise desde_ordenes_fuera_de_estado(error, CodigoDeError.RUTA_EN_EJECUCION) from error
        _LOG.info(
            "Ruta invalidada | ruta=%s liberadas=%d", id_ruta, len(liberadas)
        )
        return DesasignacionAplicada(
            ruta_invalidada=id_ruta,
            ordenes_liberadas=tuple(str(orden) for orden in liberadas),
        )

    # --- Ejecucion (FR-16, CU-06) ------------------------------------------

    @router.post(
        "/rutas/{id_ruta}/despacho",
        response_model=DespachoAplicado,
        # Sin transportista, la respuesta es la de siempre, sin el campo.
        response_model_exclude_none=True,
        tags=["Ejecucion"],
        summary="Despachar una Ruta planificada (FR-16)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`ruta_no_encontrada`: el identificador no corresponde a "
                "ninguna Ruta vigente; una Ruta de un Plan descartado no existe.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`ruta_ya_despachada`: las Ordenes de la Ruta ya no estan en "
                "Planificada. Una Ruta se despacha una sola vez. El detalle "
                "lleva cada Orden con su estado.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`transportista_invalido` si el transportista indicado no es un "
                "usuario activo con rol transportista; `peticion_mal_formada` si "
                "el identificador o el cuerpo no cumplen el esquema.",
            ),
        ),
    )
    def despachar_ruta(
        id_ruta: str, solicitud: DespachoSolicitado | None = fastapi.Body(default=None)
    ) -> DespachoAplicado:
        """Registra el inicio de la ejecucion de la Ruta: sus Ordenes pasan de
        Planificada a En transito, con la transicion en su historial.

        La Ruta no tiene estados (B-96): el despacho queda registrado en sus
        Ordenes, y que la Ruta ya este despachada se lee en ellas.

        El cuerpo es opcional (D-C2): con `transportista`, la Ruta queda
        asignada a ese usuario, que la ve en su aplicacion. Sin el, el
        despacho funciona igual.
        """
        transportista = solicitud.transportista if solicitud is not None else None
        if transportista is not None and not contexto.identidad.es_transportista_activo(transportista):
            raise ErrorDeContrato(
                codigo=CodigoDeError.TRANSPORTISTA_INVALIDO,
                mensaje=f"'{transportista}' no es un usuario activo con rol transportista.",
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        try:
            ordenes = contexto.planificacion.despachar_ruta(RutaId(id_ruta), transportista)
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.RUTA_NO_ENCONTRADA,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        except RutaYaDespachada as error:
            raise desde_ordenes_fuera_de_estado(error, CodigoDeError.RUTA_YA_DESPACHADA) from error
        return DespachoAplicado(
            ruta_id=id_ruta,
            ordenes=tuple(str(orden) for orden in ordenes),
            estado=EstadoOrden.EN_TRANSITO,
            transportista=transportista,
        )

    @router.get(
        "/transportistas",
        response_model=list[TransportistaPublicado],
        tags=["Ejecucion"],
        summary="Transportistas que se pueden asignar al despachar (D-C2)",
    )
    def listar_transportistas() -> list[TransportistaPublicado]:
        """Los usuarios activos con rol transportista, por nombre de usuario."""
        return [
            TransportistaPublicado(id=u.id, nombre_visible=u.nombre_visible)
            for u in contexto.identidad.transportistas_activos()
        ]

    @router.post(
        "/rutas/{id_ruta}/paradas/{secuencia}/resultado",
        response_model=ResultadoDeParadaAplicado,
        tags=["Ejecucion"],
        summary="Declarar el resultado de una Parada (CU-06)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`ruta_no_encontrada` si la Ruta no existe; "
                "`parada_no_encontrada` si la Ruta no tiene esa secuencia.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`resultado_no_admitido`: las Ordenes de la Parada no estan En "
                "transito --la Ruta no se despacho, o la Parada ya tiene "
                "resultado-- y el ciclo de vida no admite la transicion. El "
                "detalle lleva cada Orden con su estado.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema.",
            ),
        ),
    )
    def declarar_resultado_de_parada(
        id_ruta: str,
        secuencia: int,
        solicitud: ResultadoDeParadaSolicitado,
        autenticado: UsuarioAutenticado = fastapi.Depends(de_operador),
    ) -> ResultadoDeParadaAplicado:
        """CU-06 sin telemetria: `cumplida` lleva las Ordenes de la Parada de
        En transito a Completada, y `fallida`, a Fallida, con la transicion
        en su historial. Queda registrado quien declaro, con su rol y el medio
        `web`, tomado de la sesion (ARQ-65). Se admite fuera del orden
        planificado. La posicion del Vehiculo, la reprogramacion y la
        devolucion no estan implementadas.
        """
        declarante = Declarante(
            usuario_id=autenticado.usuario.id,
            rol=autenticado.usuario.rol.value,
            medio=MedioDeDeclaracion.WEB,
        )
        if solicitud.declarado_en is not None or solicitud.sin_conexion is not None:
            # D29: la hora del dispositivo y la marca sin conexion son de la
            # aplicacion del transportista (encargo 2026-11-23B).
            raise ErrorDeContrato(
                codigo=CodigoDeError.PETICION_MAL_FORMADA,
                mensaje="`declarado_en` y `sin_conexion` solo se aceptan desde la aplicacion del transportista.",
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
            )
        return _declarar_resultado(contexto, id_ruta, secuencia, solicitud.resultado, declarante)

    if contexto.incidentes is not None:
        _agregar_incidentes(router, contexto.incidentes, contexto)
        _agregar_replanificacion(router, contexto, contexto.incidentes)
    _agregar_eta_de_parada(router, contexto)
    _agregar_tras_la_falla(router, contexto)
    _agregar_contingencias(router, contexto)
    _agregar_acuerdos(router, contexto)
    _agregar_reportes_de_proveedor(router, contexto)
    _agregar_pedido_en_la_jornada(router, contexto)

    _agregar_alertas(router, contexto)
    del_transportista = _agregar_ruta_del_transportista(contexto)
    del_cliente_comercial = _agregar_del_cliente_comercial(contexto)

    agregar_operaciones_de_acceso(publico, contexto.identidad)
    app.include_router(publico)
    app.include_router(router)
    app.include_router(del_transportista)
    if del_cliente_comercial is not None:
        app.include_router(del_cliente_comercial)
    return app


def _error_de_flota(error: Exception) -> ErrorDeContrato:
    """Cada error de la administracion de la Flota con su codigo y su estado."""
    codigo, estado = {
        PatenteInvalida: (CodigoDeError.PATENTE_INVALIDA, http.HTTPStatus.UNPROCESSABLE_ENTITY),
        CapacidadInvalida: (CodigoDeError.CAPACIDAD_INVALIDA, http.HTTPStatus.UNPROCESSABLE_ENTITY),
        VehiculoExistente: (CodigoDeError.VEHICULO_EXISTENTE, http.HTTPStatus.CONFLICT),
        VehiculoInexistente: (CodigoDeError.VEHICULO_NO_ENCONTRADO, http.HTTPStatus.NOT_FOUND),
        VehiculoEnUso: (CodigoDeError.VEHICULO_EN_USO, http.HTTPStatus.CONFLICT),
    }[type(error)]
    return ErrorDeContrato(codigo=codigo, mensaje=str(error), estado=estado)


def _declarar_resultado(
    contexto: ContextoDeLaApi,
    id_ruta: str,
    secuencia: int,
    resultado: ResultadoDeParada,
    declarante: Declarante,
    declarado_en: dt.datetime | None = None,
    sin_conexion: bool = False,
) -> ResultadoDeParadaAplicado:
    """CU-06: la misma regla y los mismos errores para quien declare, el
    operador desde la web o el transportista desde su aplicacion. Lo unico
    que cambia es el declarante que queda en el historial (ARQ-65). Con
    `declarado_en` (solo la aplicacion; encargo 2026-11-23B, D29), la hora del
    hecho, la prioridad del transportista y la Ruta vigente."""
    try:
        ordenes = contexto.planificacion.declarar_resultado_de_parada(
            RutaId(id_ruta), secuencia, resultado, declarante, declarado_en, sin_conexion
        )
    except DeclaracionDeCampoNoAplicada as error:
        raise desde_ordenes_fuera_de_estado(error, CodigoDeError.DECLARACION_DE_CAMPO_NO_APLICADA) from error
    except ParadaNoEncontrada as error:
        raise ErrorDeContrato(
            codigo=CodigoDeError.PARADA_NO_ENCONTRADA,
            mensaje=str(error),
            estado=http.HTTPStatus.NOT_FOUND,
        ) from error
    except EntidadNoEncontrada as error:
        raise ErrorDeContrato(
            codigo=CodigoDeError.RUTA_NO_ENCONTRADA,
            mensaje=str(error),
            estado=http.HTTPStatus.NOT_FOUND,
        ) from error
    except ResultadoNoAdmitido as error:
        raise desde_ordenes_fuera_de_estado(error, CodigoDeError.RESULTADO_NO_ADMITIDO) from error
    return ResultadoDeParadaAplicado(
        ruta_id=id_ruta,
        secuencia=secuencia,
        resultado=resultado,
        ordenes=tuple(str(orden) for orden in ordenes),
        estado=EstadoOrden.COMPLETADA if resultado is ResultadoDeParada.CUMPLIDA else EstadoOrden.FALLIDA,
        declarante=DeclarantePublicado.desde(declarante, contexto.identidad.nombre_visible),
    )


def _exigir_ruta_propia(contexto: ContextoDeLaApi, autenticado: UsuarioAutenticado, id_ruta: str) -> None:
    """Un transportista solo opera sobre una Ruta que le despacharon. Una ajena
    se responde como inexistente (`ruta_no_encontrada`): no se le confirma que
    exista una Ruta que no es suya."""
    propias = {str(r) for r in contexto.planificacion.rutas_del_transportista(autenticado.usuario.id)}
    if id_ruta not in propias:
        raise ErrorDeContrato(
            codigo=CodigoDeError.RUTA_NO_ENCONTRADA,
            mensaje=f"La Ruta '{id_ruta}' no esta despachada a este transportista.",
            estado=http.HTTPStatus.NOT_FOUND,
        )


def _observar_declaraciones_de_campo(contexto: ContextoDeLaApi) -> tuple[DeclaracionDeCampoObservada, ...]:
    """Los asientos de campo del historial de las Ordenes (CU-06, 4a; encargo
    2026-11-23B, D29), para las alertas de la jornada en que se recibieron."""
    return tuple(
        DeclaracionDeCampoObservada(
            orden=str(orden.id),
            ruta_declarada=str(t.campo.ruta_declarada),
            secuencia_declarada=t.campo.secuencia_declarada,
            ruta_actual=None if orden.ruta_id is None else str(orden.ruta_id),
            hecho_en=t.marca_temporal,
            recibido_en=t.campo.recibido_en,
            efecto=t.campo.efecto,
            sobre_otra_ruta=t.campo.sobre_otra_ruta,
            nota=t.campo.nota,
        )
        for orden in contexto.ordenes.listar()
        for t in orden.historial
        if t.campo is not None
    )


def _observar_para_alertas(
    contexto: ContextoDeLaApi, jornada: dt.date
) -> tuple[tuple[RutaObservada, ...], tuple[OrdenObservada, ...]]:
    """Lo que las reglas de alerta necesitan, leido por los puertos de Ordenes,
    de Rutas y de Prediccion de ETA (Vigilancia de SLA es una hoja y no los
    importa). El ETA se calcula solo para las Rutas de la jornada que tienen
    alguna Orden con plazo comprometido, planificada o en transito."""
    ordenes = {str(orden.id): orden for orden in contexto.ordenes.listar()}
    rutas = contexto.planificacion.rutas_persistidas(jornada)
    etas: dict[str, tuple[dt.datetime, dt.datetime]] = {}
    rutas_observadas = []
    for persistida in rutas:
        # Solo las Ordenes que hoy son de esta Ruta (bloque 1): una mudada por
        # un reemplazo cuenta en la vigente.
        ids = [str(o) for parada in persistida.ruta.paradas for o in parada.ordenes
               if str(o) in ordenes and ordenes[str(o)].ruta_id == persistida.ruta.id]
        rutas_observadas.append(
            RutaObservada(
                id=str(persistida.ruta.id),
                ordenes_no_terminales=tuple(
                    o for o in ids if o in ordenes and ordenes[o].estado not in ESTADOS_TERMINALES
                ),
            )
        )
        necesita_eta = any(
            ordenes[o].plazo_comprometido is not None and ordenes[o].estado in ESTADOS_EN_RIESGO_DE_PLAZO
            for o in ids
            if o in ordenes
        )
        if not necesita_eta or contexto.hora_de_salida is None:
            continue
        salida = dt.datetime.combine(jornada, contexto.hora_de_salida, tzinfo=ZONA_OPERATIVA)
        for parada, eta in zip(persistida.ruta.paradas, _eta_por_parada(contexto, persistida)):
            if eta is None:
                continue
            for orden in parada.ordenes:
                if str(orden) not in ids:
                    continue
                etas[str(orden)] = (
                    salida + dt.timedelta(seconds=eta.mediana_s),
                    salida + dt.timedelta(seconds=eta.p90_s),
                )
    ordenes_observadas = tuple(
        OrdenObservada(
            id=id_orden,
            en_creada=orden.estado is EstadoOrden.CREADA,
            planificada_o_en_transito=orden.estado in ESTADOS_EN_RIESGO_DE_PLAZO,
            ultima_transicion_en=orden.historial[-1].marca_temporal if orden.historial else None,
            plazo_comprometido=_plazo_efectivo(contexto, orden),
            eta_p50=etas[id_orden][0] if id_orden in etas else None,
            eta_p90=etas[id_orden][1] if id_orden in etas else None,
        )
        for id_orden, orden in ordenes.items()
    )
    return tuple(rutas_observadas), ordenes_observadas


def _agregar_ruta_del_transportista(contexto: ContextoDeLaApi) -> fastapi.APIRouter:
    """Lo que consulta la aplicacion del transportista (2026-10-27B, T), en un
    enrutador propio que exige el rol transportista."""
    de_transportista = dependencia_de_transportista(contexto.identidad)
    enrutador = fastapi.APIRouter(prefix=PREFIJO_DE_VERSION, responses=respuestas_de_acceso())

    @enrutador.get(
        "/yo/ruta-en-ejecucion",
        response_model=RutaEnEjecucionPublicada,
        tags=["Transportista"],
        summary="La Ruta en ejecucion del transportista que consulta",
    )
    def ruta_en_ejecucion(
        autenticado: UsuarioAutenticado = fastapi.Depends(de_transportista),
    ) -> RutaEnEjecucionPublicada:
        """La Ruta despachada a este transportista que todavia tiene alguna
        Orden sin terminar, con sus Paradas en orden, el estado de sus
        Ordenes, el ETA si existe y los Incidentes declarados en su jornada
        que la afectan. Sin Ruta, responde 200 con `ruta` en null: la
        aplicacion deja de capturar posiciones. Si tuviera mas de una, la de
        la jornada mas reciente."""
        ordenes = {str(o.id): o for o in contexto.ordenes.listar()}
        candidatas = []
        for id_ruta in contexto.planificacion.rutas_del_transportista(autenticado.usuario.id):
            try:
                persistida = contexto.planificacion.ruta_persistida(id_ruta)
            except EntidadNoEncontrada:
                continue
            ids = [str(o) for o in persistida.ruta.ordenes]
            # Solo las Ordenes que siguen en esta Ruta: las de una Ruta
            # reemplazada se mudaron a la vigente (CU-07, D10).
            if any(
                o in ordenes
                and ordenes[o].estado not in ESTADOS_TERMINALES
                and ordenes[o].ruta_id == persistida.ruta.id
                for o in ids
            ):
                candidatas.append(persistida)
        if not candidatas:
            return RutaEnEjecucionPublicada(ruta=None)
        elegida = max(candidatas, key=lambda p: (p.fecha_planificacion, str(p.ruta.id)))
        incidentes = (
            tuple(
                IncidentePublicado.desde(i)
                for i in contexto.incidentes.listar()
                if (
                    elegida.ruta.id in i.rutas_afectadas
                    or _alguna_anterior(contexto, elegida.ruta.id, i.rutas_afectadas)
                )
                and i.declarado_en.astimezone(ZONA_OPERATIVA).date() == elegida.fecha_planificacion
            )
            if contexto.incidentes is not None
            else ()
        )
        return RutaEnEjecucionPublicada(ruta=_publicar_ruta(contexto, elegida), incidentes=incidentes)

    @enrutador.post(
        "/yo/rutas/{id_ruta}/paradas/{secuencia}/resultado",
        response_model=ResultadoDeParadaAplicado,
        tags=["Transportista"],
        summary="El transportista declara el resultado de una Parada de su Ruta (CU-06)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`ruta_no_encontrada` si la Ruta no existe o no esta despachada a "
                "este transportista; `parada_no_encontrada` si la Ruta no tiene "
                "esa secuencia.",
            ),
            (
                http.HTTPStatus.CONFLICT,
                "`resultado_no_admitido`: las Ordenes de la Parada no estan En "
                "transito --la Parada ya tiene resultado-- y el ciclo de vida no "
                "admite la transicion; con `declarado_en`, tambien un reenvio de "
                "la misma declaracion. `declaracion_de_campo_no_aplicada` (con "
                "`declarado_en`): el ciclo de vida no la admite; el estado no "
                "cambia y queda asentada en el historial con su motivo.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el cuerpo no cumple el esquema.",
            ),
        ),
    )
    def declarar_resultado_propio(
        id_ruta: str,
        secuencia: int,
        solicitud: ResultadoDeParadaSolicitado,
        autenticado: UsuarioAutenticado = fastapi.Depends(de_transportista),
    ) -> ResultadoDeParadaAplicado:
        """La misma declaracion que hace el operador desde la web, hecha por el
        transportista desde su aplicacion y solo sobre una Ruta que le
        despacharon: "Entregue" es `cumplida` y "No pude entregar", `fallida`.
        Queda registrado con el medio `aplicacion` (ARQ-65)."""
        _exigir_ruta_propia(contexto, autenticado, id_ruta)
        declarante = Declarante(
            usuario_id=autenticado.usuario.id,
            rol=autenticado.usuario.rol.value,
            medio=MedioDeDeclaracion.APLICACION,
        )
        return _declarar_resultado(
            contexto, id_ruta, secuencia, solicitud.resultado, declarante,
            solicitud.declarado_en, bool(solicitud.sin_conexion),
        )

    if contexto.incidentes is not None:
        incidentes_del_registro = contexto.incidentes

        @enrutador.post(
            "/yo/incidentes",
            response_model=IncidentePublicado,
            status_code=http.HTTPStatus.CREATED,
            tags=["Transportista"],
            summary="El transportista declara un Incidente sobre su Ruta (FR-18)",
            responses=respuestas_de_error(
                (
                    http.HTTPStatus.NOT_FOUND,
                    "`ruta_no_encontrada` si la Ruta no existe o no esta despachada "
                    "a este transportista.",
                ),
                (
                    http.HTTPStatus.UNPROCESSABLE_ENTITY,
                    "`tipo_de_incidente_invalido` si el tipo no es uno del catalogo "
                    "`TipoDeIncidente`; `peticion_mal_formada` si el cuerpo no "
                    "cumple el esquema.",
                ),
            ),
        )
        def declarar_incidente_propio(
            solicitud: IncidenteDelTransportistaSolicitado,
            autenticado: UsuarioAutenticado = fastapi.Depends(de_transportista),
        ) -> IncidentePublicado:
            """Un problema que el transportista encuentra en la calle: afecta
            su Ruta y solo su Ruta, con alcance parcial. Como el que declara el
            operador, no recalcula nada; lo ve el operador en Incidentes y, si
            la Ruta tiene entregas pendientes, en Alertas."""
            _exigir_ruta_propia(contexto, autenticado, solicitud.ruta)
            return _declarar_incidente(
                incidentes_del_registro,
                solicitud.tipo,
                AlcanceDelIncidente.PARCIAL.value,
                solicitud.descripcion,
                (solicitud.ruta,),
                solicitud.zona_afectada,
                functools.partial(_vencer_por_incidente, contexto),
            )

    return enrutador


def _agregar_alertas(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """Alertas por reglas (CU-08; 2026-10-27B, A), derivadas al consultar."""

    @router.get(
        "/alertas",
        response_model=AlertasPublicadas,
        tags=["Alertas"],
        summary="Alertas vigentes de una jornada, derivadas por reglas (CU-08)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si la jornada no es una fecha AAAA-MM-DD.",
            ),
        ),
    )
    def listar_alertas(jornada: dt.date | None = None) -> AlertasPublicadas:
        """Cuatro reglas, tres propuestas del chat central (B-259, punto 3) y
        la del desvio (2026-11-22A, D17):

        - `incidente_en_ruta`: un Incidente declarado en la jornada sobre una
          Ruta con Ordenes sin terminar; como los Incidentes no tienen cierre,
          rige durante la jornada en que se declaro;
        - `permanencia_en_creada`: una Orden en Creada desde hace mas de 48 h;
        - `riesgo_de_plazo`: una Orden planificada o en transito cuyo p90 de la
          primera entrega estimada queda despues del dia de su plazo;
        - `desvio_de_ruta` (2026-11-22A): los dos ultimos reportes de
          telemetria de a lo sumo 120 s de una Ruta en ejecucion, anclados al
          grafo, a mas de 300 m del trazado del tramo en curso; al detectarse,
          se genera una propuesta de reruteo desde la posicion (CU-07, sin la
          Parada en curso fija), que el operador aprueba o rechaza;
        - `riesgo_vial_en_ruta` (2026-11-23E, D31): con tabla de riesgo vial
          para el grafo cargado, una Ruta en ejecucion con una alternativa que
          baja al menos un 20 % la frecuencia anual predicha de hechos viales de
          sus tramos pendientes con a lo sumo un 5 % mas de distancia; la
          propuesta (motivo `riesgo_vial`) se genera una vez por Ruta y el
          operador la aprueba o la rechaza.

        Se calculan al consultar y no se persisten: una condicion vigente es una
        sola alerta. Consultar no cambia ninguna Orden ni ninguna Ruta; lo unico
        que puede registrar es la propuesta de reruteo de un desvio, una por
        desvio, o la de riesgo vial, una por Ruta, que no tocan nada vigente
        hasta que el operador las aprueba. Sin
        `jornada`, la del reloj del servidor."""
        ahora = contexto.reloj_de_alertas()
        dia = jornada if jornada is not None else fecha_operativa(ahora)
        rutas, ordenes = _observar_para_alertas(contexto, dia)
        incidentes = _incidentes_sobre_las_vigentes(
            contexto, contexto.incidentes.listar() if contexto.incidentes is not None else ())
        desvios = _observar_desvios(contexto, rutas, ahora)
        alertas = evaluar_alertas(dia, ahora, ZONA_OPERATIVA, incidentes, rutas, ordenes, desvios,
                                  _observar_declaraciones_de_campo(contexto),
                                  _observar_riesgo_vial(contexto, rutas, ahora))
        return AlertasPublicadas(
            jornada=dia,
            evaluadas_en=ahora,
            alertas=tuple(AlertaPublicada.desde_alerta(alerta) for alerta in alertas),
        )


def _declarar_incidente(
    incidentes: ServicioIncidentes,
    tipo: str,
    alcance: str,
    descripcion: str,
    rutas_afectadas: tuple[str, ...],
    zona: ZonaAfectadaSolicitada | None = None,
    al_declarar: Callable[[Incidente], None] | None = None,
) -> IncidentePublicado:
    """FR-18: la misma regla y los mismos errores para el operador y para el
    transportista. La zona afectada es opcional (bloque K, 2026-11-23B).
    `al_declarar` vence las propuestas por riesgo vial que el Incidente deja
    sin sentido (D35, 2026-11-23F)."""
    try:
        incidente = incidentes.declarar(
            tipo, alcance, descripcion, rutas_afectadas,
            None if zona is None else ZonaAfectada(lat=zona.lat, lon=zona.lon, radio_m=zona.radio_m),
        )
    except ZonaAfectadaInvalida as error:
        raise ErrorDeContrato(
            codigo=CodigoDeError.PETICION_MAL_FORMADA,
            mensaje=str(error),
            estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error
    except TipoDeIncidenteInvalido as error:
        raise ErrorDeContrato(
            codigo=CodigoDeError.TIPO_DE_INCIDENTE_INVALIDO,
            mensaje=str(error),
            estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error
    except AlcanceDeIncidenteInvalido as error:
        raise ErrorDeContrato(
            codigo=CodigoDeError.ALCANCE_INVALIDO,
            mensaje=str(error),
            estado=http.HTTPStatus.UNPROCESSABLE_ENTITY,
        ) from error
    except RutasAfectadasInexistentes as error:
        raise desde_rutas_afectadas_inexistentes(error) from error
    if al_declarar is not None:
        try:
            al_declarar(incidente)
        except Exception as error:  # noqa: BLE001 - el Incidente ya quedo registrado
            _LOG.warning("Sin vencimiento de propuestas por riesgo vial para %s: %s", incidente.id, error)
    return IncidentePublicado.desde(incidente)


def _zona_del_incidente(incidente: Incidente) -> ZonaCircular | None:
    return None if incidente.zona is None else ZonaCircular(incidente.zona.lat, incidente.zona.lon, incidente.zona.radio_m)


def _vencer_por_incidente(contexto: ContextoDeLaApi, incidente: Incidente) -> None:
    """D35 (2026-11-23F): un Incidente vence la propuesta por riesgo vial
    pendiente de cada Ruta que afecta (con zona o sin ella), y con zona, la de
    cualquier Ruta de la jornada cuyo camino propuesto la cruce."""
    servicio = contexto.planificacion.reemplazo
    if servicio is None:
        return
    for ruta in incidente.rutas_afectadas:
        servicio.vencer_las_de_riesgo(servicio.vigente(RutaId(str(ruta))), f"Incidente {incidente.id} sobre la Ruta")
    zona = _zona_del_incidente(incidente)
    if zona is not None:
        for persistida in contexto.planificacion.rutas_persistidas(contexto.planificacion.jornada_operativa()):
            servicio.vencer_si_cruza_zonas(persistida.ruta.id, [(str(incidente.id), zona)])


def _agregar_incidentes(router: fastapi.APIRouter, incidentes: ServicioIncidentes,
                        contexto: ContextoDeLaApi | None = None) -> None:
    """Registro y consulta de Incidentes (FR-18; CU-07, paso 1), sin
    recalculo: registrar un Incidente no cambia ninguna Ruta ni ninguna
    Orden."""

    @router.post(
        "/incidentes",
        response_model=IncidentePublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Incidentes"],
        summary="Declarar un Incidente y las Rutas que afecta (FR-18; CU-07, paso 1)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`tipo_de_incidente_invalido` si el tipo no es uno de los del "
                "catalogo `TipoDeIncidente`; `alcance_invalido` si no es total "
                "ni parcial; `rutas_inexistentes` si alguna Ruta afectada no "
                "existe, con todas en el detalle; `peticion_mal_formada` si el "
                "cuerpo no cumple el esquema.",
            ),
        ),
    )
    def declarar_incidente(solicitud: IncidenteSolicitado) -> IncidentePublicado:
        """Registra el Incidente con el instante de la declaracion y publica
        `IncidenteDeclarado`, todavia sin consumidores. Cero Rutas afectadas
        es valido (glosario, relacion 6). No recalcula nada: las Rutas y sus
        Ordenes quedan como estaban."""
        return _declarar_incidente(
            incidentes, solicitud.tipo, solicitud.alcance, solicitud.descripcion, solicitud.rutas_afectadas,
            solicitud.zona_afectada,
            None if contexto is None else functools.partial(_vencer_por_incidente, contexto),
        )

    @router.get(
        "/incidentes",
        response_model=list[IncidentePublicado],
        tags=["Incidentes"],
        summary="Listar los Incidentes registrados (FR-18)",
    )
    def listar_incidentes() -> list[IncidentePublicado]:
        """Por instante de declaracion y, a igual instante, por
        identificador."""
        return [IncidentePublicado.desde(incidente) for incidente in incidentes.listar()]

    @router.get(
        "/incidentes/{id_incidente}",
        response_model=IncidentePublicado,
        tags=["Incidentes"],
        summary="Consultar un Incidente por su identificador (FR-18)",
        responses=respuestas_de_error(
            (
                http.HTTPStatus.NOT_FOUND,
                "`incidente_no_encontrado`: el identificador no corresponde a "
                "ningun Incidente registrado.",
            ),
            (
                http.HTTPStatus.UNPROCESSABLE_ENTITY,
                "`peticion_mal_formada` si el identificador no cumple el esquema.",
            ),
        ),
    )
    def consultar_incidente(id_incidente: str) -> IncidentePublicado:
        """Tipo, alcance, descripcion, Rutas afectadas e instante de la
        declaracion."""
        try:
            incidente = incidentes.obtener(id_incidente)
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(
                codigo=CodigoDeError.INCIDENTE_NO_ENCONTRADO,
                mensaje=str(error),
                estado=http.HTTPStatus.NOT_FOUND,
            ) from error
        return IncidentePublicado.desde(incidente)


def _alguna_anterior(contexto: ContextoDeLaApi, ruta_id: RutaId, afectadas: Sequence[RutaId]) -> bool:
    """Si alguna de las Rutas que `ruta_id` reemplazo esta en `afectadas`: el
    Incidente que motivo un reemplazo sigue siendo de la Ruta vigente."""
    servicio = contexto.planificacion.reemplazo
    actual = ruta_id
    while servicio is not None and (r := servicio.reemplazo_de_la_nueva(actual)) is not None:
        actual = r.ruta_reemplazada
        if actual in afectadas:
            return True
    return False


def _propuesta_publicada(contexto: ContextoDeLaApi, p: PropuestaDeReemplazo) -> PropuestaPublicada:
    persistida = contexto.planificacion.ruta_persistida(p.ruta_id)
    nodo = {parada.secuencia: parada.nodo for parada in persistida.ruta.paradas}
    rotulos = contexto.rotulos([p.origen.nodo, *nodo.values()])

    def plan(secuencias: Sequence[int]) -> tuple[ParadaComparada, ...]:
        return tuple(ParadaComparada(secuencia_vigente=s, punto=_punto(rotulos, nodo[s])) for s in secuencias)

    return PropuestaPublicada(
        id=p.id, ruta_id=str(p.ruta_id), motivo=p.motivo, incidente_id=p.referencia, creada_en=p.creada_en,
        estado=p.estado.value,
        origen=OrigenPublicado(punto=_punto(rotulos, p.origen.nodo), fuente=p.origen.fuente.value,
                               antiguedad_s=p.origen.antiguedad_s,
                               distancia_del_anclaje_m=p.origen.distancia_del_anclaje_m),
        paradas_cumplidas=p.cumplidas, parada_en_curso=p.en_curso,
        plan_vigente=plan(p.secuencia_vigente), plan_propuesto=plan(p.secuencia_propuesta),
        paradas_que_cambian_de_lugar=tuple(
            s for s, t in zip(p.secuencia_vigente, p.secuencia_propuesta) if s != t),
        distancia_vigente_m=p.distancia_vigente_m, distancia_propuesta_m=p.distancia_propuesta_m,
        diferencia_m=p.diferencia_m, metodo=p.metodo, segundos_de_calculo=p.segundos_de_calculo,
        resuelta_en=p.resuelta_en, ruta_nueva=None if p.ruta_nueva is None else str(p.ruta_nueva),
        fija_en_curso=p.fija_en_curso, orden_sin_cambios=p.orden_sin_cambios,
        contingencias_aplicables=_aplicables_para(contexto, p),
        riesgo_vial=None if p.riesgo_vial is None else CompromisoDeRiesgoVialPublicado(
            **dataclasses.asdict(p.riesgo_vial), baja_de_exposicion=p.riesgo_vial.baja_de_exposicion,
            suba_de_distancia=p.riesgo_vial.suba_de_distancia),
        motivo_del_vencimiento=p.motivo_del_vencimiento,
    )


def _aplicables_para(contexto: ContextoDeLaApi, p: PropuestaDeReemplazo) -> tuple[str, ...] | None:
    """CU-07, flujo 5b: las contingencias aplicables mientras la propuesta
    esta pendiente; None si no hay (y el campo no se publica)."""
    servicio = contexto.planificacion.contingencias
    if servicio is None or p.estado is not EstadoDePropuesta.PENDIENTE:
        return None
    return tuple(c.id for c in servicio.aplicables(p.ruta_id)) or None


def _error_de_reemplazo(error: Exception) -> ErrorDeContrato:
    """Los flujos de excepcion de CU-07 como errores del contrato."""
    if isinstance(error, PropuestaNoEncontrada):
        return ErrorDeContrato(codigo=CodigoDeError.PROPUESTA_NO_ENCONTRADA, mensaje=str(error),
                               estado=http.HTTPStatus.NOT_FOUND)
    if isinstance(error, EntidadNoEncontrada):
        return ErrorDeContrato(codigo=CodigoDeError.RUTA_NO_ENCONTRADA, mensaje=str(error),
                               estado=http.HTTPStatus.NOT_FOUND)
    if isinstance(error, RutaYaReemplazada):
        return ErrorDeContrato(codigo=CodigoDeError.RUTA_REEMPLAZADA, mensaje=str(error),
                               estado=http.HTTPStatus.CONFLICT)
    if isinstance(error, ZonaContieneAlVehiculo):
        # D33 (2026-11-23E): el pedido declara mal el punto; no es 3a.
        return ErrorDeContrato(codigo=CodigoDeError.ZONA_CONTIENE_AL_VEHICULO, mensaje=str(error),
                               estado=http.HTTPStatus.UNPROCESSABLE_ENTITY)
    codigos = {RutaNoEnEjecucion: CodigoDeError.RUTA_NO_EN_EJECUCION,
               SinAlternativaFactible: CodigoDeError.SIN_ALTERNATIVA_FACTIBLE,
               PropuestaYaResuelta: CodigoDeError.PROPUESTA_YA_RESUELTA,
               PropuestaVencida: CodigoDeError.PROPUESTA_VENCIDA}
    for tipo, codigo in codigos.items():
        if isinstance(error, tipo):
            return ErrorDeContrato(codigo=codigo, mensaje=str(error), estado=http.HTTPStatus.CONFLICT)
    raise error


_ERRORES_DE_REEMPLAZO = (EntidadNoEncontrada, RutaYaReemplazada, RutaNoEnEjecucion, SinAlternativaFactible,
                         ZonaContieneAlVehiculo,
                         PropuestaYaResuelta, PropuestaVencida)


def _agregar_replanificacion(
    router: fastapi.APIRouter, contexto: ContextoDeLaApi, incidentes: ServicioIncidentes
) -> None:
    """CU-07 desde el paso 2 (2026-11-20A): avance de una Ruta en ejecucion,
    propuesta de recalculo por un Incidente que la afecta, aprobacion (que
    reemplaza la Ruta, paso 6) y rechazo (flujo 5a)."""
    servicio = contexto.planificacion.reemplazo
    if servicio is None:
        return

    @router.get(
        "/rutas/{id_ruta}/avance",
        response_model=AvancePublicado,
        tags=["Replanificacion"],
        summary="Avance vigente de una Ruta en ejecucion (CU-07, paso 2)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`ruta_no_en_ejecucion`: no se despacho o no le queda nada pendiente; "
                                       "`ruta_reemplazada`: el mensaje nombra la vigente."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def avance_de_ruta(id_ruta: str) -> AvancePublicado:
        """Paradas cumplidas, la Parada en curso (la pendiente de menor
        secuencia) y las demas pendientes."""
        try:
            a = servicio.avance(RutaId(id_ruta))
        except _ERRORES_DE_REEMPLAZO as error:
            raise _error_de_reemplazo(error) from error
        return AvancePublicado(ruta_id=id_ruta, paradas_cumplidas=a.cumplidas, parada_en_curso=a.en_curso,
                               paradas_pendientes=a.pendientes)

    @router.post(
        "/incidentes/{id_incidente}/propuestas",
        response_model=PropuestaPublicada,
        status_code=http.HTTPStatus.CREATED,
        tags=["Replanificacion"],
        summary="Recalcular una Ruta afectada por el Incidente y proponer el plan (CU-07, pasos 2 a 4)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`incidente_no_encontrado` o `ruta_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`ruta_no_afectada`: el Incidente no declara esa Ruta; "
                                       "`ruta_no_en_ejecucion`; `ruta_reemplazada`; `sin_alternativa_factible` "
                                       "(flujo 3a): no hay camino para lo pendiente y el plan vigente se conserva."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el cuerpo no cumple el esquema; "
                                                   "`zona_contiene_al_vehiculo` (2026-11-23E, D33): la zona afectada "
                                                   "del Incidente contiene el origen del recalculo (la posicion del "
                                                   "Vehiculo): hay que declarar el punto del corte."),
        ),
    )
    def proponer_reemplazo(id_incidente: str, solicitud: PropuestaSolicitada) -> PropuestaPublicada:
        """Recalcula la secuencia de lo pendiente desde la posicion del
        Vehiculo (de a lo sumo 120 s, anclada al grafo; si no, la Parada en
        curso), con la Parada en curso fija y primera y el Deposito como
        retorno, y devuelve el plan resultante con su diferencia contra el
        vigente. No cambia nada vigente: el operador lo aprueba o lo
        rechaza."""
        try:
            incidente = incidentes.obtener(id_incidente)
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.INCIDENTE_NO_ENCONTRADO, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error
        ruta_id = RutaId(solicitud.ruta_id)
        if ruta_id not in incidente.rutas_afectadas:
            raise ErrorDeContrato(
                codigo=CodigoDeError.RUTA_NO_AFECTADA,
                mensaje=f"El Incidente {id_incidente} no declara a la Ruta {ruta_id} entre las afectadas",
                estado=http.HTTPStatus.CONFLICT)
        posicion = None
        if contexto.lector_de_posicion is not None:
            try:
                posicion = contexto.lector_de_posicion(str(ruta_id))
            except Exception as error:  # noqa: BLE001 - sin posicion se recalcula desde la Parada en curso
                _LOG.warning("Sin posicion de telemetria para %s: %s", ruta_id, error)
        try:
            # Bloque K (2026-11-23B): con zona afectada, la propuesta evita sus tramos.
            evitando = (None if incidente.zona is None
                        else ZonaCircular(incidente.zona.lat, incidente.zona.lon, incidente.zona.radio_m))
            propuesta = servicio.proponer(ruta_id, MOTIVO_INCIDENTE, str(incidente.id), posicion, evitando=evitando)
        except _ERRORES_DE_REEMPLAZO as error:
            raise _error_de_reemplazo(error) from error
        _LOG.info("Propuesta de reemplazo | %s | ruta=%s incidente=%s diferencia_m=%.1f %s %.3f s", propuesta.id,
                  ruta_id, id_incidente, propuesta.diferencia_m, propuesta.metodo, propuesta.segundos_de_calculo)
        return _propuesta_publicada(contexto, propuesta)

    @router.get(
        "/propuestas/{id_propuesta}",
        response_model=PropuestaPublicada,
        tags=["Replanificacion"],
        summary="Consultar una propuesta de recalculo (CU-07, paso 4)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`propuesta_no_encontrada`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def consultar_propuesta(id_propuesta: str) -> PropuestaPublicada:
        """La propuesta tal como se calculo, con su estado: pendiente,
        aprobada (con la Ruta nueva) o rechazada."""
        try:
            return _propuesta_publicada(contexto, servicio.propuesta(id_propuesta))
        except _ERRORES_DE_REEMPLAZO as error:
            raise _error_de_reemplazo(error) from error

    @router.post(
        "/propuestas/{id_propuesta}/aprobacion",
        response_model=ReemplazoPublicado,
        tags=["Replanificacion"],
        summary="Aprobar la propuesta: reemplaza la Ruta (CU-07, pasos 5 y 6)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`propuesta_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`propuesta_ya_resuelta`; `propuesta_vencida`: el avance cambio desde "
                                       "que se calculo (pida otra)."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def aprobar_propuesta(id_propuesta: str) -> ReemplazoPublicado:
        """La Ruta vigente pasa a Reemplazada y conserva sus Paradas cumplidas;
        la nueva, del mismo Vehiculo y Plan, nace En ejecucion con lo
        pendiente; las Ordenes no cumplidas se mudan sin cambiar de estado,
        con la reasignacion en su historial; telemetria y la aplicacion del
        transportista siguen a la nueva."""
        try:
            reemplazo = servicio.aprobar(id_propuesta)
        except _ERRORES_DE_REEMPLAZO as error:
            raise _error_de_reemplazo(error) from error
        _LOG.info("Ruta reemplazada | %s -> %s | propuesta=%s", reemplazo.ruta_reemplazada, reemplazo.ruta_nueva,
                  id_propuesta)
        return _reemplazo_publicado(contexto, reemplazo)

    @router.post(
        "/propuestas/{id_propuesta}/rechazo",
        response_model=PropuestaPublicada,
        tags=["Replanificacion"],
        summary="Rechazar la propuesta: el plan vigente se conserva (CU-07, flujo 5a)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`propuesta_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`propuesta_ya_resuelta`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def rechazar_propuesta(id_propuesta: str) -> PropuestaPublicada:
        """El plan vigente queda sin modificacion y el Incidente sigue
        registrado, con la propuesta rechazada asentada."""
        try:
            return _propuesta_publicada(contexto, servicio.rechazar(id_propuesta))
        except _ERRORES_DE_REEMPLAZO as error:
            raise _error_de_reemplazo(error) from error


def _a_la_hora(base: dt.datetime, minutos: float) -> dt.datetime:
    return base + dt.timedelta(minutes=minutos)


def _agregar_eta_de_parada(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """CU-09 (2026-11-20A, bloque 3): el ETA de una Parada con el instante en
    que se calculo (paso 3), desde la posicion del Vehiculo y la secuencia
    pendiente cuando la Ruta esta en ejecucion y hay una posicion reciente
    (paso 2), y la ultima estimacion con su antiguedad cuando no la hay (2a).
    Las ultimas estimaciones viven en la memoria del proceso: es la version
    minima, y al reiniciar se vuelve a la del plan."""
    ultimas: dict[tuple[str, int], EtaDeParadaPublicada] = {}

    @router.get(
        "/rutas/{id_ruta}/paradas/{secuencia}/eta",
        response_model=EtaDeParadaPublicada,
        tags=["Rutas"],
        summary="ETA de una Parada y el instante en que se calculo (CU-09)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada` o `parada_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`parada_cumplida`: la Parada ya tiene resultado y no hay arribo que estimar."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si la secuencia no es un entero."),
        ),
    )
    def eta_de_parada(id_ruta: str, secuencia: int) -> EtaDeParadaPublicada:
        """Con la Ruta en ejecucion y una posicion de a lo sumo 120 s, el ETA
        sale de la posicion anclada al grafo, recorriendo lo pendiente en el
        orden vigente hasta esta Parada (`fuente` = `posicion`). Sin posicion
        reciente, la ultima estimacion desde la posicion, con su antiguedad
        (`ultima_estimacion`). Si no hay ninguna, o la Ruta no se despacho, la
        del plan (`plan`), la misma que publica la Ruta."""
        try:
            persistida = contexto.planificacion.ruta_persistida(RutaId(id_ruta))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.RUTA_NO_ENCONTRADA, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error
        paradas = persistida.ruta.paradas
        if not 1 <= secuencia <= len(paradas):
            raise ErrorDeContrato(
                codigo=CodigoDeError.PARADA_NO_ENCONTRADA,
                mensaje=f"La Ruta {id_ruta} no tiene Parada {secuencia}: tiene {len(paradas)}, numeradas desde 1",
                estado=http.HTTPStatus.NOT_FOUND)
        parada = paradas[secuencia - 1]
        ordenes = [contexto.ordenes.consultar(o) for o in parada.ordenes]
        if ordenes and all(
            o.estado in (EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA) and o.ruta_id == persistida.ruta.id
            for o in ordenes
        ):
            raise ErrorDeContrato(codigo=CodigoDeError.PARADA_CUMPLIDA,
                                  mensaje=f"La Parada {secuencia} de la Ruta {id_ruta} ya tiene resultado",
                                  estado=http.HTTPStatus.CONFLICT)
        ahora = contexto.reloj_de_alertas()
        posicion: PosicionDelVehiculo | None = None
        if contexto.lector_de_posicion is not None:
            try:
                posicion = contexto.lector_de_posicion(id_ruta)
            except Exception as error:  # noqa: BLE001 - sin posicion se usa la ultima estimacion o el plan
                _LOG.warning("Sin posicion de telemetria para %s: %s", id_ruta, error)
        antiguedad_de_la_posicion = None if posicion is None else posicion.antiguedad_s
        servicio = contexto.planificacion.reemplazo
        pendientes: list[int] = []
        if servicio is not None:
            try:
                avance = servicio.avance(RutaId(id_ruta))
                pendientes = [s for s in (avance.en_curso, *avance.pendientes) if s is not None]
            except (RutaNoEnEjecucion, RutaYaReemplazada):
                pendientes = []
        clave = (id_ruta, secuencia)
        reciente = posicion is not None and 0.0 <= posicion.antiguedad_s <= ANTIGUEDAD_MAXIMA_S
        if (secuencia in pendientes and reciente and contexto.prediccion_eta is not None
                and contexto.velocidad_km_h is not None and posicion is not None):
            tramo = pendientes[: pendientes.index(secuencia) + 1]
            anclaje = contexto.cartografia.anclar(
                posicion.latitud, posicion.longitud,
                restringido_a=contexto.cartografia.nodos_de_mayor_componente_fuerte())
            nodos = [anclaje.nodo.id_osm, *(paradas[s - 1].nodo for s in tramo)]
            matriz = contexto.cartografia.matriz_de_costos(list(dict.fromkeys(nodos)))
            recorrido = [
                ParadaARecorrer(distancia_por_la_red_m=0.0 if a == b else matriz.distancia_m(a, b),
                                ordenes=len(paradas[s - 1].ordenes))
                for (a, b), s in zip(zip(nodos, nodos[1:]), tramo)
            ]
            local = ahora.astimezone(ZONA_OPERATIVA)
            medianoche = dt.datetime.combine(local.date(), dt.time(0), tzinfo=ZONA_OPERATIVA)
            salida_min = local.hour * MINUTOS_POR_HORA + local.minute + local.second / SEGUNDOS_POR_MINUTO
            try:
                eta = contexto.prediccion_eta.estimar(recorrido, salida_min, contexto.velocidad_km_h)[-1]
            except ERRORES_DE_ESTIMACION as error:
                _LOG.warning("ETA desde la posicion ausente | ruta=%s | %s", id_ruta, error)
            else:
                calculada = EtaDeParadaPublicada(
                    ruta_id=id_ruta, secuencia=secuencia, hora_estimada=_a_la_hora(medianoche, eta.mediana_min),
                    hora_p10=_a_la_hora(medianoche, eta.p10_min), hora_p90=_a_la_hora(medianoche, eta.p90_min),
                    calculado_en=ahora, antiguedad_s=0.0, fuente="posicion",
                    antiguedad_de_la_posicion_s=antiguedad_de_la_posicion, motivo_sin_estimacion=None)
                ultimas[clave] = calculada
                return calculada
        if secuencia in pendientes and clave in ultimas:
            previa = ultimas[clave]
            return previa.model_copy(update={
                "fuente": "ultima_estimacion",
                "antiguedad_s": max(0.0, (ahora - previa.calculado_en).total_seconds()),
                "antiguedad_de_la_posicion_s": antiguedad_de_la_posicion,
            })
        eta_del_plan = _eta_por_parada(contexto, persistida)[secuencia - 1]
        _, reemplaza_a = _reemplazos_de(contexto, persistida.ruta.id)
        hora_de_salida = contexto.hora_de_salida if reemplaza_a is None else _hora_local(reemplaza_a)
        if eta_del_plan is None or hora_de_salida is None:
            return EtaDeParadaPublicada(
                ruta_id=id_ruta, secuencia=secuencia, hora_estimada=None, hora_p10=None, hora_p90=None,
                calculado_en=ahora, antiguedad_s=0.0, fuente="plan",
                antiguedad_de_la_posicion_s=antiguedad_de_la_posicion,
                motivo_sin_estimacion="sin servicio de ETA o la Ruta pide una franja o una celda que la tabla no cubre")
        dia = (persistida.fecha_planificacion if reemplaza_a is None
               else reemplaza_a.instante.astimezone(ZONA_OPERATIVA).date())
        salida = dt.datetime.combine(dia, hora_de_salida, tzinfo=ZONA_OPERATIVA)
        return EtaDeParadaPublicada(
            ruta_id=id_ruta, secuencia=secuencia,
            hora_estimada=salida + dt.timedelta(seconds=eta_del_plan.mediana_s),
            hora_p10=salida + dt.timedelta(seconds=eta_del_plan.p10_s),
            hora_p90=salida + dt.timedelta(seconds=eta_del_plan.p90_s),
            calculado_en=ahora, antiguedad_s=0.0, fuente="plan",
            antiguedad_de_la_posicion_s=antiguedad_de_la_posicion, motivo_sin_estimacion=None)


def _agregar_tras_la_falla(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """CU-06, flujo 1a (2026-11-20A): la Orden Fallida, que no es terminal,
    habilita la reprogramacion o la devolucion (glosario, estados 5 a 7)."""

    def _no_encontrada(error: EntidadNoEncontrada) -> ErrorDeContrato:
        return ErrorDeContrato(codigo=CodigoDeError.ORDEN_NO_ENCONTRADA, mensaje=str(error),
                               estado=http.HTTPStatus.NOT_FOUND)

    def _no_admitida(error: TransicionInvalida) -> ErrorDeContrato:
        return ErrorDeContrato(codigo=CodigoDeError.TRANSICION_NO_ADMITIDA, mensaje=str(error),
                               estado=http.HTTPStatus.CONFLICT)

    @router.post(
        "/ordenes/{id_orden}/reprogramacion",
        response_model=OrdenPublicada,
        tags=["Ordenes"],
        summary="Reprogramar una Orden Fallida (CU-06, flujo 1a)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`orden_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`transicion_no_admitida`: la Orden no esta en Fallida."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el cuerpo no cumple el esquema."),
        ),
    )
    def reprogramar_orden(id_orden: str, solicitud: ReprogramacionSolicitada | None = None) -> OrdenPublicada:
        """Fallida -> Reprogramada, con el plazo nuevo si se confirma uno. Deja
        de estar asignada a la Ruta en que fallo y entra en la proxima
        planificacion, donde pasa a Planificada."""
        plazo = None if solicitud is None else solicitud.plazo_comprometido
        try:
            orden = contexto.ordenes.reprogramar(OrdenId(id_orden), plazo)
        except EntidadNoEncontrada as error:
            raise _no_encontrada(error) from error
        except TransicionInvalida as error:
            raise _no_admitida(error) from error
        return _publicar_orden(contexto, orden)

    @router.post(
        "/ordenes/{id_orden}/devolucion",
        response_model=OrdenPublicada,
        tags=["Ordenes"],
        summary="Devolver al Deposito una Orden Fallida (CU-06, flujo 1a)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`orden_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`transicion_no_admitida`: la Orden no esta en Fallida."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def devolver_orden(id_orden: str) -> OrdenPublicada:
        """Fallida -> Devuelta, estado terminal: la Orden retorna al Deposito.
        Cuantos intentos se admiten antes no esta definido; lo decide el
        operador."""
        try:
            orden = contexto.ordenes.devolver(OrdenId(id_orden))
        except EntidadNoEncontrada as error:
            raise _no_encontrada(error) from error
        except TransicionInvalida as error:
            raise _no_admitida(error) from error
        return _publicar_orden(contexto, orden)


def _contingencia_publicada(c: Contingencia) -> ContingenciaPublicada:
    return ContingenciaPublicada(
        id=c.id, ruta_id=str(c.ruta_id), paradas=c.paradas, vehiculo_reserva=str(c.vehiculo_reserva),
        prioridad=c.prioridad, estado=c.estado.value, demanda=c.demanda, capacidad_efectiva=c.capacidad_efectiva,
        distancia_m=c.distancia_m, metodo=c.metodo, creada_en=c.creada_en, activada_en=c.activada_en,
        ruta_de_contingencia=None if c.ruta_de_contingencia is None else str(c.ruta_de_contingencia),
    )


def _agregar_contingencias(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """CU-11 en su version minima (D14 del encargo 2026-11-20A): declarar,
    priorizar, validar y registrar una contingencia; consultar cuales aplican
    a una Ruta; y activarla a pedido del operador."""
    servicio = contexto.planificacion.contingencias
    if servicio is None:
        return

    def _ruta_no_encontrada(error: EntidadNoEncontrada) -> ErrorDeContrato:
        return ErrorDeContrato(codigo=CodigoDeError.RUTA_NO_ENCONTRADA, mensaje=str(error),
                               estado=http.HTTPStatus.NOT_FOUND)

    @router.post(
        "/contingencias",
        response_model=ContingenciaPublicada,
        status_code=http.HTTPStatus.CREATED,
        tags=["Contingencias"],
        summary="Declarar, priorizar y registrar una Ruta de contingencia (CU-11, pasos 1 a 4)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`contingencia_infactible` (flujo 3a): el mensaje es la causa (capacidad del Vehiculo de reserva, "
             "topologia del grafo, Vehiculo fuera de la Flota o ya en un Plan de la jornada, Paradas que no son "
             "de la Ruta o ya no estan pendientes) y no se registra; `peticion_mal_formada` si el cuerpo no cumple "
             "el esquema."),
        ),
    )
    def declarar_contingencia(solicitud: ContingenciaSolicitada) -> ContingenciaPublicada:
        """Valida la contingencia contra la capacidad efectiva del Vehiculo de
        reserva y el grafo vial, secuencia sus Paradas desde el Deposito (el
        plan preparado) y la registra como disponible."""
        try:
            contingencia = servicio.declarar(RutaId(solicitud.ruta_id), solicitud.paradas,
                                             solicitud.vehiculo_reserva, solicitud.prioridad)
        except EntidadNoEncontrada as error:
            raise _ruta_no_encontrada(error) from error
        except ContingenciaInfactible as error:
            raise ErrorDeContrato(codigo=CodigoDeError.CONTINGENCIA_INFACTIBLE, mensaje=str(error),
                                  estado=http.HTTPStatus.UNPROCESSABLE_ENTITY) from error
        _LOG.info("Contingencia registrada | %s | ruta=%s reserva=%s prioridad=%d", contingencia.id,
                  contingencia.ruta_id, contingencia.vehiculo_reserva, contingencia.prioridad)
        return _contingencia_publicada(contingencia)

    @router.get(
        "/contingencias",
        response_model=list[ContingenciaPublicada],
        tags=["Contingencias"],
        summary="Listar las contingencias declaradas, por prioridad (CU-11)",
        responses=respuestas_de_error(
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el filtro no cumple el esquema."),
        ),
    )
    def listar_contingencias(ruta_id: str | None = None) -> list[ContingenciaPublicada]:
        """Todas, o las de una Ruta; la de prioridad 1 primero y, a igual
        prioridad, la declarada antes."""
        return [_contingencia_publicada(c) for c in servicio.listar(None if ruta_id is None else RutaId(ruta_id))]

    @router.get(
        "/contingencias/{id_contingencia}",
        response_model=ContingenciaPublicada,
        tags=["Contingencias"],
        summary="Consultar una contingencia (CU-11)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`contingencia_no_encontrada`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def consultar_contingencia(id_contingencia: str) -> ContingenciaPublicada:
        """La contingencia con su estado y, si se activo, su Ruta."""
        try:
            return _contingencia_publicada(servicio.obtener(id_contingencia))
        except ContingenciaNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.CONTINGENCIA_NO_ENCONTRADA, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error

    @router.get(
        "/rutas/{id_ruta}/contingencias",
        response_model=list[EvaluacionDeContingenciaPublicada],
        tags=["Contingencias"],
        summary="Contingencias de una Ruta y si son aplicables ahora (CU-11, paso 5; CU-07, flujo 5b)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def contingencias_de_ruta(id_ruta: str) -> list[EvaluacionDeContingenciaPublicada]:
        """Por prioridad; cada una dice si aplica y, si no, la causa."""
        try:
            evaluaciones = servicio.evaluar(RutaId(id_ruta))
        except EntidadNoEncontrada as error:
            raise _ruta_no_encontrada(error) from error
        return [EvaluacionDeContingenciaPublicada(contingencia=_contingencia_publicada(e.contingencia),
                                                  aplicable=e.aplicable, causa=e.causa) for e in evaluaciones]

    @router.post(
        "/rutas/{id_ruta}/contingencias/activacion",
        response_model=ContingenciaPublicada,
        tags=["Contingencias"],
        summary="Activar la contingencia de mayor prioridad aplicable (CU-11, pasos 5 y 6)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`ruta_no_encontrada`."),
            (http.HTTPStatus.CONFLICT, "`ruta_no_en_ejecucion`; `sin_contingencia_aplicable` (flujo 5a): queda el "
                                       "recalculo de CU-07."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el cuerpo no cumple el esquema."),
        ),
    )
    def activar_contingencia(id_ruta: str, solicitud: ActivacionSolicitada | None = None) -> ContingenciaPublicada:
        """La decision es del operador. Crea la Ruta de contingencia del
        Vehiculo de reserva en el mismo Plan, con las Paradas comprometidas en
        el orden preparado; sus Ordenes pasan a ella sin cambiar de estado,
        con la reasignacion en su historial; la Ruta de origen sigue con las
        Paradas que le quedan."""
        transportista = None if solicitud is None else solicitud.transportista_id
        try:
            activada = servicio.activar(RutaId(id_ruta), transportista)
        except EntidadNoEncontrada as error:
            raise _ruta_no_encontrada(error) from error
        except RutaNoEnEjecucion as error:
            raise ErrorDeContrato(codigo=CodigoDeError.RUTA_NO_EN_EJECUCION, mensaje=str(error),
                                  estado=http.HTTPStatus.CONFLICT) from error
        except SinContingenciaAplicable as error:
            raise ErrorDeContrato(codigo=CodigoDeError.SIN_CONTINGENCIA_APLICABLE, mensaje=str(error),
                                  estado=http.HTTPStatus.CONFLICT) from error
        _LOG.info("Contingencia activada | %s | %s -> %s", activada.id, id_ruta, activada.ruta_de_contingencia)
        return _contingencia_publicada(activada)


def _plazo_efectivo(contexto: ContextoDeLaApi, orden: Orden) -> dt.date | None:
    """CU-08, precondicion (2026-11-20A): el plazo propio de la Orden o, si no
    tiene, el que le da el acuerdo vigente de su Cliente comercial (fecha del
    alta mas los dias pactados)."""
    if orden.plazo_comprometido is not None or contexto.acuerdos is None or not orden.historial:
        return orden.plazo_comprometido
    acuerdo = contexto.acuerdos.acuerdo_de_orden(str(orden.id))
    return None if acuerdo is None else acuerdo.plazo_para(orden.historial[0].marca_temporal)


def _ejecutadas_del_cliente(contexto: ContextoDeLaApi, servicio: ServicioDeAcuerdos,
                            acuerdo: AcuerdoDeNivelDeServicio) -> list[OrdenEjecutada]:
    """Las Ordenes del Cliente cuya ejecucion termino, con el Proveedor del
    Vehiculo de la Ruta en que se ejecutaron (si lo tiene) y su plazo efectivo."""
    ejecutadas: list[OrdenEjecutada] = []
    terminales = (EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA)
    for orden_id in servicio.ordenes_del_cliente(acuerdo.cliente_comercial_id):
        try:
            orden = contexto.ordenes.consultar(OrdenId(orden_id))
        except EntidadNoEncontrada:
            continue
        fin = next((t for t in reversed(orden.historial)
                    if t.estado_nuevo in terminales and t.estado_anterior is not t.estado_nuevo), None)
        if fin is None:
            continue
        proveedor = None
        if orden.ruta_id is not None:
            try:
                vehiculo = contexto.planificacion.ruta_persistida(orden.ruta_id).ruta.vehiculo_id
                proveedor = servicio.proveedor_de_vehiculo(str(vehiculo))
            except EntidadNoEncontrada:
                proveedor = None
        plazo = orden.plazo_comprometido or (acuerdo.plazo_para(orden.historial[0].marca_temporal)
                                            if orden.historial else None)
        ejecutadas.append(OrdenEjecutada(orden_id, proveedor, fin.estado_nuevo is EstadoOrden.COMPLETADA,
                                         fin.marca_temporal, plazo))
    return ejecutadas


def _agregar_acuerdos(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """CU-10 en su version minima (D14 del encargo 2026-11-20A): Clientes
    comerciales y sus Ordenes, Proveedores de ultima milla y sus Vehiculos,
    acuerdos y la evaluacion de cumplimiento por periodo y Proveedor."""
    servicio = contexto.acuerdos
    if servicio is None:
        return

    def _invalido(error: Exception) -> ErrorDeContrato:
        return ErrorDeContrato(codigo=CodigoDeError.REGISTRO_DE_SLA_INVALIDO, mensaje=str(error),
                               estado=http.HTTPStatus.UNPROCESSABLE_ENTITY)

    def _no_encontrado(error: EntidadNoEncontrada) -> ErrorDeContrato:
        codigo = (CodigoDeError.CLIENTE_COMERCIAL_NO_ENCONTRADO if isinstance(error, ClienteComercialNoEncontrado)
                  else CodigoDeError.PROVEEDOR_NO_ENCONTRADO if isinstance(error, ProveedorNoEncontrado)
                  else CodigoDeError.ACUERDO_NO_ENCONTRADO if isinstance(error, AcuerdoNoEncontrado)
                  else CodigoDeError.ORDEN_NO_ENCONTRADA)
        return ErrorDeContrato(codigo=codigo, mensaje=str(error), estado=http.HTTPStatus.NOT_FOUND)

    def _acuerdo_publicado(a: AcuerdoDeNivelDeServicio) -> AcuerdoPublicado:
        vigente = servicio.acuerdo_vigente(a.cliente_comercial_id) == a
        return AcuerdoPublicado(
            id=a.id, cliente_comercial_id=a.cliente_comercial_id, umbral_de_cumplimiento=a.umbral_de_cumplimiento,
            plazo_comprometido_dias=a.plazo_comprometido_dias, registrado_en=a.registrado_en, vigente=vigente,
            ordenes=servicio.ordenes_del_cliente(a.cliente_comercial_id) if vigente else ())

    @router.post(
        "/clientes-comerciales",
        response_model=ClienteComercialPublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acuerdos de nivel de servicio"],
        summary="Registrar un Cliente comercial (CU-10, precondicion)",
        responses=respuestas_de_error(
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`registro_de_sla_invalido` o `peticion_mal_formada`."),
        ),
    )
    def registrar_cliente_comercial(solicitud: ClienteComercialSolicitado) -> ClienteComercialPublicado:
        """La organizacion que contrata el servicio y origina Ordenes."""
        try:
            cliente = servicio.registrar_cliente(solicitud.nombre)
        except RegistroInvalido as error:
            raise _invalido(error) from error
        return ClienteComercialPublicado(id=cliente.id, nombre=cliente.nombre, ordenes=())

    @router.post(
        "/clientes-comerciales/{id_cliente}/usuarios",
        response_model=ClienteComercialPublicado,
        tags=["Acuerdos de nivel de servicio"],
        summary="Vincular el usuario de integracion de un Cliente comercial (CU-04)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`cliente_comercial_no_encontrado`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`registro_de_sla_invalido`: el usuario no existe, no tiene el rol `cliente_comercial` o ya actua "
             "por otro Cliente; `peticion_mal_formada`."),
        ),
    )
    def vincular_usuario_de_integracion(id_cliente: str,
                                        solicitud: UsuarioDeIntegracionSolicitado) -> ClienteComercialPublicado:
        """Desde ahi, ese usuario consulta por `/v1/cliente/ordenes` las
        Ordenes que origino el Cliente, y solo esas."""
        try:
            cliente = servicio.cliente(id_cliente)
            if not contexto.identidad.es_cliente_comercial_activo(solicitud.usuario_id):
                raise RegistroInvalido(
                    f"El usuario {solicitud.usuario_id} no existe, no esta activo o no tiene el rol cliente_comercial")
            servicio.vincular_usuario(id_cliente, solicitud.usuario_id)
        except EntidadNoEncontrada as error:
            raise _no_encontrado(error) from error
        except RegistroInvalido as error:
            raise _invalido(error) from error
        return ClienteComercialPublicado(id=cliente.id, nombre=cliente.nombre,
                                         ordenes=servicio.ordenes_del_cliente(cliente.id))

    @router.get(
        "/clientes-comerciales",
        response_model=list[ClienteComercialPublicado],
        tags=["Acuerdos de nivel de servicio"],
        summary="Listar los Clientes comerciales con sus Ordenes (CU-10)",
    )
    def listar_clientes_comerciales() -> list[ClienteComercialPublicado]:
        """En el orden en que se registraron."""
        return [ClienteComercialPublicado(id=c.id, nombre=c.nombre, ordenes=servicio.ordenes_del_cliente(c.id))
                for c in servicio.clientes()]

    @router.post(
        "/clientes-comerciales/{id_cliente}/ordenes",
        response_model=ClienteComercialPublicado,
        tags=["Acuerdos de nivel de servicio"],
        summary="Asentar las Ordenes que origino un Cliente comercial (CU-10, paso 2)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`cliente_comercial_no_encontrado` u `orden_no_encontrada`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`registro_de_sla_invalido`: una Orden ya la origino otro Cliente; `peticion_mal_formada`."),
        ),
    )
    def asociar_ordenes_al_cliente(id_cliente: str,
                                   solicitud: OrdenesDelClienteSolicitadas) -> ClienteComercialPublicado:
        """Desde ahi el acuerdo vigente del Cliente rige esas Ordenes; la que no
        tiene plazo propio toma el del acuerdo, que es el que vigila CU-08."""
        try:
            cliente = servicio.cliente(id_cliente)
            for orden in solicitud.ordenes:
                contexto.ordenes.consultar(OrdenId(orden))
            ordenes = servicio.asociar_ordenes(id_cliente, solicitud.ordenes)
        except EntidadNoEncontrada as error:
            raise _no_encontrado(error) from error
        except RegistroInvalido as error:
            raise _invalido(error) from error
        return ClienteComercialPublicado(id=cliente.id, nombre=cliente.nombre, ordenes=ordenes)

    @router.post(
        "/proveedores",
        response_model=ProveedorPublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acuerdos de nivel de servicio"],
        summary="Registrar un Proveedor de ultima milla y los Vehiculos que aporta (CU-10)",
        responses=respuestas_de_error(
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`registro_de_sla_invalido`: un Vehiculo no esta en la Flota registrada o ya lo aporta otro "
             "Proveedor; `peticion_mal_formada`."),
        ),
    )
    def registrar_proveedor(solicitud: ProveedorSolicitado) -> ProveedorPublicado:
        """El vinculo con los Vehiculos es opcional (glosario, relacion 5)."""
        registrados = {str(v.id) for v in contexto.planificacion.vehiculos_registrados()}
        vehiculos = tuple(v.strip().upper() for v in solicitud.vehiculos)
        ajenos = [v for v in vehiculos if v not in registrados]
        try:
            if ajenos:
                raise RegistroInvalido(f"Los Vehiculos {ajenos} no estan en la Flota registrada")
            proveedor = servicio.registrar_proveedor(solicitud.nombre, vehiculos)
        except RegistroInvalido as error:
            raise _invalido(error) from error
        return ProveedorPublicado(id=proveedor.id, nombre=proveedor.nombre, vehiculos=proveedor.vehiculos)

    @router.get(
        "/proveedores",
        response_model=list[ProveedorPublicado],
        tags=["Acuerdos de nivel de servicio"],
        summary="Listar los Proveedores de ultima milla (CU-10)",
    )
    def listar_proveedores() -> list[ProveedorPublicado]:
        """En el orden en que se registraron."""
        return [ProveedorPublicado(id=p.id, nombre=p.nombre, vehiculos=p.vehiculos) for p in servicio.proveedores()]

    @router.post(
        "/acuerdos",
        response_model=AcuerdoPublicado,
        status_code=http.HTTPStatus.CREATED,
        tags=["Acuerdos de nivel de servicio"],
        summary="Registrar un acuerdo de nivel de servicio (CU-10, pasos 1 y 2)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`cliente_comercial_no_encontrado`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`registro_de_sla_invalido` o `peticion_mal_formada`."),
        ),
    )
    def registrar_acuerdo(solicitud: AcuerdoSolicitado) -> AcuerdoPublicado:
        """Con su umbral y su plazo; pasa a ser el vigente del Cliente y rige
        sus Ordenes."""
        try:
            acuerdo = servicio.registrar_acuerdo(solicitud.cliente_comercial_id, solicitud.umbral_de_cumplimiento,
                                                 solicitud.plazo_comprometido_dias)
        except EntidadNoEncontrada as error:
            raise _no_encontrado(error) from error
        except RegistroInvalido as error:
            raise _invalido(error) from error
        return _acuerdo_publicado(acuerdo)

    @router.get(
        "/acuerdos",
        response_model=list[AcuerdoPublicado],
        tags=["Acuerdos de nivel de servicio"],
        summary="Listar los acuerdos de nivel de servicio (CU-10)",
    )
    def listar_acuerdos() -> list[AcuerdoPublicado]:
        """En el orden en que se registraron, con cual rige a cada Cliente."""
        return [_acuerdo_publicado(a) for a in servicio.acuerdos()]

    @router.get(
        "/acuerdos/{id_acuerdo}/evaluacion",
        response_model=EvaluacionDeCumplimientoPublicada,
        tags=["Acuerdos de nivel de servicio"],
        summary="Evaluar el cumplimiento de un acuerdo por periodo y Proveedor (CU-10, pasos 3 a 5)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`acuerdo_no_encontrado` o `proveedor_no_encontrado`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`registro_de_sla_invalido` si el periodo termina antes de empezar; `peticion_mal_formada`."),
        ),
    )
    def evaluar_acuerdo(id_acuerdo: str, proveedor_id: str, desde: dt.date,
                        hasta: dt.date) -> EvaluacionDeCumplimientoPublicada:
        """Sobre las Ordenes del Cliente cuya ejecucion termino en el periodo
        (fecha local) con un Vehiculo del Proveedor: proporcion de Completadas
        y de Completadas dentro del plazo, contra el umbral. Sin Proveedor
        atribuible en ninguna, no se imputa (4a); con una base chica, se
        informa su tamano (4b). No cambia ninguna Orden."""
        try:
            acuerdo = servicio.acuerdo(id_acuerdo)
            servicio.proveedor(proveedor_id)
            e = evaluar_cumplimiento(acuerdo, proveedor_id, desde, hasta,
                                     _ejecutadas_del_cliente(contexto, servicio, acuerdo))
        except EntidadNoEncontrada as error:
            raise _no_encontrado(error) from error
        except RegistroInvalido as error:
            raise _invalido(error) from error
        if not e.atribuible:
            observacion: str | None = (
                f"Ninguna de las {e.ordenes_del_periodo} Ordenes del periodo tiene un Proveedor de ultima milla "
                f"asociado: el resultado no se puede atribuir y no se imputa (4a)")
        elif not e.base_suficiente:
            observacion = (f"Base de calculo de {e.base} Ordenes, por debajo de {TAMANO_MINIMO_REPRESENTATIVO}: "
                           f"el resultado no es representativo (4b)")
        else:
            observacion = None
        return EvaluacionDeCumplimientoPublicada(
            **{campo.name: getattr(e, campo.name) for campo in dataclasses.fields(e)},
            tamano_minimo_representativo=TAMANO_MINIMO_REPRESENTATIVO, observacion=observacion)


def _agregar_reportes_de_proveedor(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """CU-12 en su version minima (D14 del encargo 2026-11-20A): el mapa de
    traduccion de un Proveedor y la ingestion de sus reportes de estado, con
    los flujos 2a, 3a y 4a asentados para el operador."""
    acuerdos, reportes = contexto.acuerdos, contexto.reportes_de_proveedor
    if acuerdos is None or reportes is None:
        return
    reportables = {e.value: e for e in ESTADOS_REPORTABLES_POR_PROVEEDOR}

    def _proveedor_no_encontrado(error: EntidadNoEncontrada) -> ErrorDeContrato:
        return ErrorDeContrato(codigo=CodigoDeError.PROVEEDOR_NO_ENCONTRADO, mensaje=str(error),
                               estado=http.HTTPStatus.NOT_FOUND)

    @router.put(
        "/proveedores/{id_proveedor}/mapa-de-estados",
        response_model=MapaDeEstadosPublicado,
        tags=["Acuerdos de nivel de servicio"],
        summary="Declarar el mapa de traduccion de estados de un Proveedor (CU-12, precondicion)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`proveedor_no_encontrado`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`registro_de_sla_invalido`: un estado del mapa no es del catalogo o no lo informa un Proveedor; "
             "`peticion_mal_formada`."),
        ),
    )
    def declarar_mapa_de_estados(id_proveedor: str, solicitud: MapaDeEstadosSolicitado) -> MapaDeEstadosPublicado:
        """Reemplaza el mapa del Proveedor; cada codigo va a un estado canonico
        que un ejecutor de la entrega puede informar."""
        try:
            acuerdos.proveedor(id_proveedor)
        except EntidadNoEncontrada as error:
            raise _proveedor_no_encontrado(error) from error
        ajenos = sorted({e for e in solicitud.codigos.values() if e not in reportables})
        if ajenos:
            raise ErrorDeContrato(
                codigo=CodigoDeError.REGISTRO_DE_SLA_INVALIDO,
                mensaje=f"Estados que el mapa no admite: {ajenos}; solo {sorted(reportables)}",
                estado=http.HTTPStatus.UNPROCESSABLE_ENTITY)
        return MapaDeEstadosPublicado(proveedor_id=id_proveedor,
                                      codigos=reportes.declarar_mapa(id_proveedor, solicitud.codigos))

    @router.post(
        "/proveedores/{id_proveedor}/reportes",
        response_model=OrdenPublicada,
        tags=["Acuerdos de nivel de servicio"],
        summary="Ingerir el estado de una Orden reportado por un Proveedor (CU-12, pasos 1 a 4)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`proveedor_no_encontrado`; `orden_no_encontrada` (2a, asentado)."),
            (http.HTTPStatus.CONFLICT,
             "`sin_mapa_de_estados`: el Proveedor no tiene mapa declarado; `orden_de_otro_proveedor`: la Orden no "
             "esta asignada a un Vehiculo del Proveedor (asentado); `transicion_no_admitida` (4a): inconsistencia "
             "con el estado vigente, asentada."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY,
             "`codigo_no_mapeado` (3a): se rechaza y se notifica al operador, sin aproximar un estado; "
             "`peticion_mal_formada`."),
        ),
    )
    def ingerir_reporte(id_proveedor: str, solicitud: ReporteDeProveedorSolicitado) -> OrdenPublicada:
        """Identifica la Orden, traduce el codigo con el mapa del Proveedor y
        asienta la transicion en su historial. Todo rechazo queda en
        `GET /v1/proveedores/{id}/reportes-rechazados`."""
        try:
            proveedor = acuerdos.proveedor(id_proveedor)
        except EntidadNoEncontrada as error:
            raise _proveedor_no_encontrado(error) from error
        mapa = reportes.mapa_de(id_proveedor)
        if mapa is None:
            raise ErrorDeContrato(codigo=CodigoDeError.SIN_MAPA_DE_ESTADOS,
                                  mensaje=f"El Proveedor {id_proveedor} no tiene mapa de traduccion declarado",
                                  estado=http.HTTPStatus.CONFLICT)

        def rechazar(tipo: TipoDeRechazo, causa: str, codigo: CodigoDeError,
                     estado: http.HTTPStatus) -> ErrorDeContrato:
            reportes.rechazar(id_proveedor, solicitud.orden_id, solicitud.codigo, tipo, causa)
            _LOG.warning("Reporte de Proveedor rechazado | %s | %s | %s", id_proveedor, tipo.value, causa)
            return ErrorDeContrato(codigo=codigo, mensaje=causa, estado=estado)

        try:
            orden = contexto.ordenes.consultar(OrdenId(solicitud.orden_id))
        except EntidadNoEncontrada as error:
            raise rechazar(TipoDeRechazo.ORDEN_INEXISTENTE, f"La Orden {solicitud.orden_id} no esta registrada",
                           CodigoDeError.ORDEN_NO_ENCONTRADA, http.HTTPStatus.NOT_FOUND) from error
        vehiculo = None
        if orden.ruta_id is not None:
            try:
                vehiculo = str(contexto.planificacion.ruta_persistida(orden.ruta_id).ruta.vehiculo_id)
            except EntidadNoEncontrada:
                vehiculo = None
        if vehiculo is None or vehiculo not in proveedor.vehiculos:
            raise rechazar(TipoDeRechazo.ORDEN_DE_OTRO_PROVEEDOR,
                           f"La Orden {orden.id} no esta asignada a un Vehiculo del Proveedor {id_proveedor}",
                           CodigoDeError.ORDEN_DE_OTRO_PROVEEDOR, http.HTTPStatus.CONFLICT)
        if solicitud.codigo not in mapa:
            raise rechazar(TipoDeRechazo.CODIGO_NO_MAPEADO,
                           f"El codigo {solicitud.codigo!r} no figura en el mapa del Proveedor {id_proveedor}: "
                           f"no se asigna ningun estado",
                           CodigoDeError.CODIGO_NO_MAPEADO, http.HTTPStatus.UNPROCESSABLE_ENTITY)
        try:
            actualizada = contexto.ordenes.registrar_estado_reportado(orden.id, reportables[mapa[solicitud.codigo]])
        except TransicionInvalida as error:
            raise rechazar(TipoDeRechazo.TRANSICION_INADMISIBLE, str(error), CodigoDeError.TRANSICION_NO_ADMITIDA,
                           http.HTTPStatus.CONFLICT) from error
        return _publicar_orden(contexto, actualizada)

    @router.get(
        "/proveedores/{id_proveedor}/reportes-rechazados",
        response_model=list[ReporteRechazadoPublicado],
        tags=["Acuerdos de nivel de servicio"],
        summary="Reportes rechazados de un Proveedor: codigos no mapeados e inconsistencias (CU-12, 2a, 3a y 4a)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`proveedor_no_encontrado`."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def reportes_rechazados(id_proveedor: str) -> list[ReporteRechazadoPublicado]:
        """En el orden en que llegaron."""
        try:
            acuerdos.proveedor(id_proveedor)
        except EntidadNoEncontrada as error:
            raise _proveedor_no_encontrado(error) from error
        return [ReporteRechazadoPublicado(id=r.id, proveedor_id=r.proveedor_id,
                                          referencia_de_orden=r.referencia_de_orden,
                                          codigo_reportado=r.codigo_reportado, tipo=r.tipo.value, causa=r.causa,
                                          recibido_en=r.recibido_en) for r in reportes.rechazos_de(id_proveedor)]


def _observar_desvios(contexto: ContextoDeLaApi, rutas: Sequence[RutaObservada],
                      ahora: dt.datetime) -> tuple[DesvioObservado, ...]:
    """D17 y D18 (2026-11-22A): para cada Ruta de la jornada con Ordenes sin
    terminar, las posiciones recientes de telemetria y, si hay desvio, la
    propuesta de reruteo (una por desvio). Un error de telemetria o del
    recalculo no tumba las alertas: se registra y esa Ruta queda sin vigilar."""
    vigilante, lector = contexto.planificacion.desvio, contexto.lector_de_posiciones_recientes
    if vigilante is None or lector is None:
        return ()
    activas = [RutaId(ruta.id) for ruta in rutas if ruta.ordenes_no_terminales]
    return tuple(
        DesvioObservado(ruta=str(desvio.ruta_id), vehiculo=desvio.vehiculo_id, distancia_m=desvio.distancia_m,
                        desde=desvio.desde, umbral_m=vigilante.parametros.umbral_m,
                        propuesta=None if propuesta is None else propuesta.id)
        for desvio, propuesta in vigilante.revisar(activas, lector, ahora)
    )


def _observar_riesgo_vial(contexto: ContextoDeLaApi, rutas: Sequence[RutaObservada],
                          ahora: dt.datetime) -> tuple[RiesgoVialObservado, ...]:
    """D31 (2026-11-23E): evalua una vez cada Ruta de la jornada con Ordenes sin
    terminar (si hay tabla de riesgo para el grafo) y devuelve sus propuestas
    por riesgo vial pendientes. Un error no tumba las alertas."""
    vigilante = contexto.planificacion.riesgo_vial
    if vigilante is None:
        return ()
    activas = [RutaId(ruta.id) for ruta in rutas if ruta.ordenes_no_terminales]
    pendientes = vigilante.revisar(activas, contexto.lector_de_posiciones_recientes, ahora)
    # D35 (2026-11-23F): las que cruzan la zona de un Incidente vigente (los de
    # la jornada, como la alerta de Incidente) vencen, aunque sean de otra Ruta.
    servicio = contexto.planificacion.reemplazo
    zonas = [(str(i.id), z) for i in (contexto.incidentes.listar() if contexto.incidentes is not None else ())
             if fecha_operativa(i.declarado_en) == fecha_operativa(ahora)
             for z in [_zona_del_incidente(i)] if z is not None]
    if zonas and servicio is not None and pendientes:
        vencidas = {v.id for r in {p.ruta_id for p in pendientes} for v in servicio.vencer_si_cruza_zonas(r, zonas)}
        pendientes = [p for p in pendientes if p.id not in vencidas]
    observados = []
    for p in pendientes:
        c = p.riesgo_vial
        if c is None:  # pragma: no cover - toda propuesta por riesgo vial lleva su compromiso
            continue
        vehiculo = str(contexto.planificacion.ruta_persistida(p.ruta_id).ruta.vehiculo_id)
        observados.append(RiesgoVialObservado(
            ruta=str(p.ruta_id), vehiculo=vehiculo, propuesta=p.id, desde=p.creada_en,
            distancia_vigente_m=c.distancia_vigente_m, distancia_propuesta_m=c.distancia_propuesta_m,
            minutos_vigentes=c.minutos_vigentes, minutos_propuestos=c.minutos_propuestos,
            exposicion_vigente=c.exposicion_vigente, exposicion_propuesta=c.exposicion_propuesta))
    return tuple(observados)


def _agregar_del_cliente_comercial(contexto: ContextoDeLaApi) -> fastapi.APIRouter | None:
    """CU-04 por su integracion (2026-11-22A): el Cliente comercial, con su
    usuario de rol propio, consulta el estado y el historial de sus Ordenes, y
    solo de las suyas; una Orden de otro responde como inexistente."""
    servicio = contexto.acuerdos
    if servicio is None:
        return None
    de_cliente = dependencia_de_cliente_comercial(contexto.identidad)
    enrutador = fastapi.APIRouter(prefix=PREFIJO_DE_VERSION, responses=respuestas_de_acceso())

    def _suyas(autenticado: UsuarioAutenticado) -> tuple[str, ...]:
        cliente = servicio.cliente_del_usuario(autenticado.usuario.id)
        return () if cliente is None else servicio.ordenes_del_cliente(cliente)

    @enrutador.get(
        "/cliente/ordenes",
        response_model=list[OrdenPublicada],
        tags=["Cliente comercial"],
        summary="Las Ordenes del Cliente comercial, con su estado e historial (CU-04)",
    )
    def ordenes_del_cliente(
        autenticado: UsuarioAutenticado = fastapi.Depends(de_cliente),
    ) -> list[OrdenPublicada]:
        """Las que origino el Cliente del usuario, por identificador; vacia si
        el usuario no esta vinculado a ningun Cliente."""
        ordenes = []
        for orden_id in sorted(_suyas(autenticado)):
            try:
                ordenes.append(_publicar_orden(contexto, contexto.ordenes.consultar(OrdenId(orden_id))))
            except EntidadNoEncontrada:
                continue
        return ordenes

    @enrutador.get(
        "/cliente/ordenes/{id_orden}",
        response_model=OrdenPublicada,
        tags=["Cliente comercial"],
        summary="Una Orden del Cliente comercial, con su estado e historial (CU-04)",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`orden_no_encontrada`: no existe o es de otro Cliente comercial."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def orden_del_cliente(
        id_orden: str, autenticado: UsuarioAutenticado = fastapi.Depends(de_cliente),
    ) -> OrdenPublicada:
        """Una Orden de otro Cliente responde igual que una inexistente: no se
        revela que existe."""
        if id_orden not in _suyas(autenticado):
            raise ErrorDeContrato(codigo=CodigoDeError.ORDEN_NO_ENCONTRADA,
                                  mensaje=f"Orden {id_orden} inexistente", estado=http.HTTPStatus.NOT_FOUND)
        try:
            return _publicar_orden(contexto, contexto.ordenes.consultar(OrdenId(id_orden)))
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.ORDEN_NO_ENCONTRADA, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error

    return enrutador


def _incidentes_sobre_las_vigentes(contexto: ContextoDeLaApi, incidentes: Sequence[Incidente]) -> list[Incidente]:
    """Un Incidente declarado sobre una Ruta que despues se reemplazo afecta a
    la vigente, que es donde estan sus Ordenes sin terminar (bloque 1)."""
    reemplazo = contexto.planificacion.reemplazo
    if reemplazo is None:
        return list(incidentes)
    return [
        dataclasses.replace(i, rutas_afectadas=tuple(dict.fromkeys(
            RutaId(str(reemplazo.vigente(RutaId(str(r))))) for r in i.rutas_afectadas)))
        for i in incidentes
    ]


def _agregar_pedido_en_la_jornada(router: fastapi.APIRouter, contexto: ContextoDeLaApi) -> None:
    """Pedido nuevo durante la jornada (2026-11-22A, bloque 3): la capa
    dinamica del 19A, con (b) y R3, conectada a la API."""
    servicio = contexto.planificacion.pedido_en_la_jornada
    if servicio is None:
        return

    @router.post(
        "/ordenes/{id_orden}/asignacion-en-la-jornada",
        response_model=AsignacionEnLaJornadaPublicada,
        tags=["Planificacion"],
        summary="Asignar una Orden nueva al viaje siguiente de un Vehiculo de la jornada",
        responses=respuestas_de_error(
            (http.HTTPStatus.NOT_FOUND, "`orden_no_encontrada`."),
            (http.HTTPStatus.CONFLICT,
             "`pedido_no_asignable`: la Orden no esta en Creada o no hay Plan vigente en la jornada; "
             "`sin_vehiculo_para_el_pedido`: ningun Vehiculo tiene capacidad comprometida libre (queda en Creada)."),
            (http.HTTPStatus.UNPROCESSABLE_ENTITY, "`peticion_mal_formada` si el identificador no cumple el esquema."),
        ),
    )
    def asignar_en_la_jornada(id_orden: str) -> AsignacionEnLaJornadaPublicada:
        """Con la linea de base (b) y la regla de regreso R3 (la capa dinamica
        del 19A): el Vehiculo y el lugar de menor aumento del recorrido
        previsto. La Orden va al viaje siguiente del Vehiculo (su Ruta
        siguiente, Planificada, en el mismo Plan, que se crea o se rearma) y la
        Ruta en curso no se toca: el Vehiculo no la lleva a bordo."""
        try:
            a = servicio.asignar(OrdenId(id_orden), contexto.planificacion.jornada_operativa())
        except EntidadNoEncontrada as error:
            raise ErrorDeContrato(codigo=CodigoDeError.ORDEN_NO_ENCONTRADA, mensaje=str(error),
                                  estado=http.HTTPStatus.NOT_FOUND) from error
        except PedidoNoAsignable as error:
            raise ErrorDeContrato(codigo=CodigoDeError.PEDIDO_NO_ASIGNABLE, mensaje=str(error),
                                  estado=http.HTTPStatus.CONFLICT) from error
        except SinVehiculoParaElPedido as error:
            raise ErrorDeContrato(codigo=CodigoDeError.SIN_VEHICULO_PARA_EL_PEDIDO, mensaje=str(error),
                                  estado=http.HTTPStatus.CONFLICT) from error
        _LOG.info("Pedido en la jornada | %s -> %s, %s (secuencia %d) | %.1f min", a.orden_id, a.vehiculo_id,
                  a.ruta_siguiente, a.secuencia, a.costo_min)
        return AsignacionEnLaJornadaPublicada(
            orden_id=str(a.orden_id), vehiculo_id=str(a.vehiculo_id), ruta_siguiente=str(a.ruta_siguiente),
            secuencia=a.secuencia, costo_min=a.costo_min, politica=a.politica,
            ruta_en_curso=None if a.ruta_en_curso is None else str(a.ruta_en_curso))
