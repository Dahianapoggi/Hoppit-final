"""Puerto publico de Planificacion de Rutas.

Unica superficie que otros containers y los adaptadores de frontera
pueden importar de este paquete; reforzado por contrato de import-linter.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from typing import Callable

from hoppit.cartografia.puerto import ProveedorGrafoVial
from hoppit.gestion_ordenes.puerto import (
    ETIQUETA_DEL_GLOSARIO,
    DeclaracionDeCampo,
    EfectoDeLaDeclaracion,
    EstadoOrden,
    ServicioGestionOrdenes,
)
from hoppit.shared.bus import Bus
from hoppit.shared.errores import EntidadNoEncontrada, ErrorDeDominio
from hoppit.shared.eventos import Declarante, ResultadoDeParada
from hoppit.shared.ids import OrdenId, PlanId, RutaId, VehiculoId

from ._adaptador_contexto import AdaptadorDatosContexto
from hoppit.shared.tiempo import instante_actual

from ._ejecucion import (
    TIPO_RUTA_DESPACHADA,
    NotificacionSaliente,
    RepositorioDeEjecucion,
    RepositorioDeEjecucionEnMemoria,
    RepositorioDeEjecucionSQLite,
    notificacion_de_despacho,
)
from ._entregador import EntregadorDeNotificaciones
from ._criticidad import (
    ATRASO_MAXIMO_COMPUTADO_DIAS,
    MARGEN_DE_LA_OPERACION,
    SIN_MARGEN,
    atraso_en_dias,
    capacidad_efectiva,
    criticidad,
)
from ._ensamblador_instancia import (
    EnsambladorInstancia,
    MotivoDeIncomunicacion,
    ParadaIncomunicada,
    ParadasIncomunicadas,
)
from ._flota import (
    CAPACIDAD_MAXIMA,
    CapacidadInvalida,
    PatenteInvalida,
    RepositorioDeFlota,
    RepositorioDeFlotaEnMemoria,
    RepositorioDeFlotaSQLite,
    VehiculoExistente,
    VehiculoInexistente,
    VehiculoRegistrado,
    normalizar_patente,
    validar_patente,
)
from ._capa_dinamica import (
    A_PARADA,
    AL_DEPOSITO,
    CARGANDO,
    EN_DEPOSITO,
    UMBRAL_DE_FRESCURA_S,
    VARIABLE_MODO_SOMBRA,
    VARIABLE_PESOS,
    Decision,
    DecisionEnModoSombra,
    DecisionPorInsercionMasBarata,
    EstadoDeLaFlota,
    OrdenParaDecidir,
    ParadaPendiente,
    PosicionReportada,
    PuertoDeDecision,
    RegistroDeDecision,
    RegistroDeSombra,
    RegistroDeSombraEnMemoria,
    VehiculoEnLaJornada,
    nodo_de_partida,
    observacion_etapa_2,
    puerto_de_decision_desde_configuracion,
)
from ._plan import PlanDeDistribucion
from ._reemplazo import (
    ANTIGUEDAD_MAXIMA_S,
    MOTIVO_CONTINGENCIA,
    MOTIVO_DESVIO,
    MOTIVO_INCIDENTE,
    MOTIVO_RIESGO_VIAL,
    TIPO_RUTA_REEMPLAZADA,
    AvanceDeRuta,
    CompromisoDeRiesgoVial,
    UmbralDeRiesgoVial,
    EstadoDePropuesta,
    FuenteDelOrigen,
    OrigenDelRecalculo,
    PosicionDelVehiculo,
    PropuestaDeReemplazo,
    PropuestaNoEncontrada,
    PropuestaVencida,
    PropuestaYaResuelta,
    ReemplazoDeRuta,
    RepositorioDeReemplazos,
    RepositorioDeReemplazosEnMemoria,
    RepositorioDeReemplazosSQLite,
    RutaNoEnEjecucion,
    RutaYaReemplazada,
    ServicioDeReemplazo,
    SinAlternativaFactible,
    ZonaContieneAlVehiculo,
)
from ._contingencias import (
    PRIORIDAD_MAXIMA,
    Contingencia,
    ContingenciaInfactible,
    ContingenciaNoEncontrada,
    EstadoDeContingencia,
    EvaluacionDeContingencia,
    RepositorioDeContingencias,
    RepositorioDeContingenciasEnMemoria,
    RepositorioDeContingenciasSQLite,
    ServicioDeContingencias,
    SinContingenciaAplicable,
)
from ._desvio import (
    INTERVALO_DE_VIGILANCIA_S,
    REPORTES_CONSECUTIVOS,
    UMBRAL_DE_DESVIO_M,
    DesvioDetectado,
    ParametrosDeDesvio,
    ReporteDePosicion,
    VigilanteDeDesvio,
    distancia_a_polilinea_m,
)
from ._riesgo_en_ruta import MU_DE_LA_PROPUESTA_M, VigilanteDeRiesgoVial
from ._pedido_en_la_jornada import (
    AsignacionEnLaJornada,
    PedidoNoAsignable,
    ServicioDePedidoEnLaJornada,
    SinVehiculoParaElPedido,
)
from ._replanificacion import MAXIMO_EXACTO, SecuenciaRecalculada, secuenciar_pendientes
from ._brecha import BrechaDeOptimalidad, brecha_de_optimalidad
from ._publicador import PublicadorRuta, fecha_operativa_de_hoy
from ._receptor import (
    ReceptorSolicitudes,
    SolicitudInvalida,
    SolicitudPlanificacion,
    TipoSolicitud,
)
from ._repositorio import (
    RepositorioRutas,
    RepositorioRutasEnMemoria,
    RepositorioRutasSQLite,
    RutaPersistida,
)
from .motor._solver_vrp import (
    LIMITE_DE_RAMAS,
    LIMITE_DE_SOLUCIONES,
    SolverVRPDeterminista,
    parametros_de_busqueda,
)
from .motor.base import (
    CRITICIDAD_MAXIMA,
    CRITICIDAD_MINIMA,
    CRITICIDAD_POR_OMISION,
    DemandaExcedeCapacidad,
    InstanciaInvalida,
    InstanciaProblema,
    MotivoNoPlanificada,
    MotivoSinSolucion,
    MotorDeRuteo,
    OrdenNoPlanificada,
    ParadaDeInstancia,
    ParadaPlaneada,
    Ruta,
    SinSolucion,
    SinSolucionConTodosLosVehiculos,
    SolucionRuteo,
)


_LOG = logging.getLogger(__name__)


class OrdenNoAsignada(ErrorDeDominio):
    """Se pidio desasignar una Orden que no esta asignada a ninguna Ruta."""


class OrdenesFueraDeEstado(ErrorDeDominio):
    """Base de los rechazos por el estado de las Ordenes de una Ruta: lleva
    cada Orden con su estado vigente, para que la causa sea concreta."""

    def __init__(self, mensaje: str, estados: dict[OrdenId, EstadoOrden]) -> None:
        detalle = ", ".join(
            f"{orden} en {ETIQUETA_DEL_GLOSARIO[estado]}" for orden, estado in sorted(estados.items())
        )
        super().__init__(f"{mensaje} Estados: {detalle}.")
        self.estados = dict(estados)


class RutaYaDespachada(OrdenesFueraDeEstado):
    """Se pidio despachar una Ruta cuyas Ordenes ya no estan en Planificada
    (FR-16): una Ruta se despacha una sola vez."""


class RutaEnEjecucion(OrdenesFueraDeEstado):
    """Se pidio descartar o invalidar una Ruta ya despachada.

    Descartarla devolveria sus Ordenes a Creada, y el ciclo de vida solo
    admite ese retorno desde Planificada. Que hacer con una Ruta en
    ejecucion (recalculo, contingencia) es FR-05, CU-07 y FR-11, no
    implementados."""


class ResultadoNoAdmitido(OrdenesFueraDeEstado):
    """El resultado declarado para una Parada no es una transicion que el
    ciclo de vida admita para sus Ordenes: solo pasan a Completada o a
    Fallida desde En transito (CU-06; glosario, ciclo de vida)."""


class DeclaracionDeCampoNoAplicada(OrdenesFueraDeEstado):
    """CU-06, 4a (encargo 2026-11-23B, D29): la declaracion del transportista
    llego con su hora pero el ciclo de vida no la admite para ninguna Orden de
    la Parada. El estado no cambia; queda asentada como declaracion de campo
    no aplicada, con su motivo, y el operador la ve en Alertas."""


class ParadaNoEncontrada(EntidadNoEncontrada):
    """La Ruta no tiene una Parada con esa secuencia."""


class VehiculoEnUso(ErrorDeDominio):
    """El Vehiculo tiene una Ruta en un Plan de la jornada: no se le cambia la
    capacidad ni se lo da de baja hasta que esa Ruta se descarte. Cambiar la
    capacidad de un Vehiculo con Ruta dejaria su ocupacion calculada contra
    una capacidad que ya no tiene; darlo de baja, una Ruta sin Vehiculo."""

    def __init__(self, vehiculo: VehiculoId, plan: PlanId, fecha: dt.date) -> None:
        self.vehiculo = vehiculo
        self.plan = plan
        self.fecha = fecha
        super().__init__(
            f"El Vehiculo {vehiculo} tiene una Ruta en el Plan {plan} del {fecha.isoformat()}: "
            f"no se le cambia la capacidad ni se lo da de baja mientras la tenga."
        )


class VehiculosComprometidos(ErrorDeDominio):
    """Algun Vehiculo seleccionado ya participa de un Plan de esa jornada.

    El glosario fija que un Vehiculo participa de un unico Plan de
    Distribucion por jornada (entidad 13). Un Vehiculo en dos Planes del
    mismo dia tendria que recorrer dos Rutas a la vez, que es fisicamente
    imposible.

    Lleva que Vehiculos estan comprometidos y en que Plan, para que la
    correccion sea concreta: liberar ese Plan o elegir otros Vehiculos.
    """

    def __init__(self, ocupados: dict[VehiculoId, PlanId], fecha: dt.date) -> None:
        detalle = ", ".join(
            f"{vehiculo} en {plan}" for vehiculo, plan in sorted(ocupados.items())
        )
        super().__init__(
            f"{len(ocupados)} Vehiculo(s) ya participan de un Plan del "
            f"{fecha.isoformat()}: {detalle}. Un Vehiculo participa de un unico "
            f"Plan por jornada; hay que liberar ese Plan o elegir otros "
            f"Vehiculos."
        )
        self.ocupados = dict(ocupados)
        self.fecha = fecha


class VehiculosFueraDeLaFlota(ErrorDeDominio):
    """Algun Vehiculo seleccionado no pertenece a la Flota registrada.

    La Flota es el conjunto de Vehiculos que gestiona el operador (glosario,
    entidad 5). Un identificador ajeno a ella no designa ningun Vehiculo: no
    tiene capacidad nominal contra la que medir la carga, y con nombres
    libres un mismo Vehiculo podria entrar con otro nombre en un segundo Plan
    de la jornada, escapando de la regla de un Plan por jornada (B-141).

    Lleva todos los identificadores rechazados y no solo el primero, para que
    la correccion se haga de una vez.
    """

    def __init__(self, ajenos: Sequence[VehiculoId]) -> None:
        self.vehiculos = tuple(sorted(ajenos))
        super().__init__(
            f"{len(self.vehiculos)} Vehiculo(s) no pertenecen a la Flota: "
            f"{', '.join(self.vehiculos)}. Solo se planifica con Vehiculos de "
            f"la Flota registrada."
        )


@dataclasses.dataclass(frozen=True)
class CapacidadDiscrepante:
    """Un Vehiculo pedido con una capacidad distinta de la nominal."""

    vehiculo: VehiculoId
    pedida: int
    nominal: int


class CapacidadDistintaDeLaNominal(ErrorDeDominio):
    """Algun Vehiculo se pidio con una capacidad distinta de la registrada.

    La capacidad es un atributo del Vehiculo, su capacidad nominal (glosario,
    entidad 4), y no un parametro de cada corrida. Aceptar otra permitiria
    resolver con una carga que el Vehiculo no admite, o planificar como si
    tuviera menos lugar del que tiene (B-141).
    """

    def __init__(self, discrepancias: Sequence[CapacidadDiscrepante]) -> None:
        self.discrepancias = tuple(sorted(discrepancias, key=lambda d: d.vehiculo))
        detalle = ", ".join(
            f"{d.vehiculo} pedido con {d.pedida} y nominal {d.nominal}"
            for d in self.discrepancias
        )
        super().__init__(
            f"{len(self.discrepancias)} Vehiculo(s) con una capacidad distinta "
            f"de la nominal: {detalle}. La capacidad de cada Vehiculo es la "
            f"registrada en la Flota."
        )


class ServicioPlanificacionRutas:
    def __init__(
        self,
        motor: MotorDeRuteo,
        ensamblador: EnsambladorInstancia,
        publicador: PublicadorRuta,
        receptor: ReceptorSolicitudes,
        repositorio: RepositorioRutas,
        gestion_ordenes: ServicioGestionOrdenes,
        reloj: Callable[[], dt.date] = fecha_operativa_de_hoy,
        flota: Mapping[VehiculoId, int] | None = None,
        ejecucion: RepositorioDeEjecucion | None = None,
        reloj_de_eventos: Callable[[], dt.datetime] = instante_actual,
        repositorio_de_flota: RepositorioDeFlota | None = None,
        reemplazo: ServicioDeReemplazo | None = None,
    ) -> None:
        self._motor = motor
        # CU-07 desde el paso 2 (2026-11-20A): el reemplazo de una Ruta en
        # ejecucion. None solo si quien ensambla no lo arma.
        self.reemplazo = reemplazo
        # CU-11 (2026-11-20A): lo asigna `crear_servicio`, que lo arma sobre
        # este mismo servicio (Flota, margen y ocupacion de los Vehiculos).
        self.contingencias: ServicioDeContingencias | None = None
        # Reruteo por desvio (2026-11-22A, D17 a D19); lo asigna `crear_servicio`.
        self.desvio: VigilanteDeDesvio | None = None
        # Propuesta de reruteo por riesgo vial (2026-11-23E, D31); lo asigna `crear_servicio`.
        self.riesgo_vial: VigilanteDeRiesgoVial | None = None
        # Pedido nuevo durante la jornada (2026-11-22A, bloque 3).
        self.pedido_en_la_jornada: ServicioDePedidoEnLaJornada | None = None
        # Quien ejecuta cada Ruta despachada y la salida transaccional hacia
        # telemetria (D-C2, ARQ-59; 2026-10-27B, T).
        self._ejecucion = ejecucion if ejecucion is not None else RepositorioDeEjecucionEnMemoria()
        self._reloj_de_eventos = reloj_de_eventos
        self._ensamblador = ensamblador
        self._publicador = publicador
        self._receptor = receptor
        self._repositorio = repositorio
        # Se consulta para resolver a que Ruta pertenece una Orden. Es
        # lectura por el puerto publico de Gestion de Ordenes, no escritura:
        # el estado de la Orden lo cambia su propio servicio, al consumir el
        # evento (ARQ-09, ARQ-31).
        self._ordenes = gestion_ordenes
        # Unico reloj del container. La jornada se lee una sola vez por
        # corrida y viaja: a la instancia, a la verificacion de ocupacion de
        # los Vehiculos y al Publicador. Tenerlo tambien en el Publicador
        # significaba dos lecturas de la hora por corrida, y desde que la
        # jornada entra en el resumen del que se derivan los identificadores,
        # dos lecturas a ambos lados de la medianoche local darian un Plan
        # cuyo identificador declara un dia y cuya fecha registrada es otro.
        self._reloj = reloj
        # La Flota registrada: cada Vehiculo con su capacidad nominal
        # (glosario, entidades 4 y 5). Con Flota registrada, una planificacion
        # solo elige entre estos Vehiculos y con su capacidad nominal. Sin
        # ella la seleccion no se restringe: es el caso de las pruebas del
        # motor y de las mediciones, que componen instancias propias. El
        # proceso que sirve la API la registra al ensamblarse.
        #
        # Desde 2026-11-02 la Flota vive en un registro que el operador
        # administra (altas, capacidad, bajas). `flota` es la Flota inicial:
        # siembra el registro solo si esta vacio, de modo que una base que ya
        # tiene Vehiculos conserva los suyos.
        if repositorio_de_flota is None and flota is not None:
            repositorio_de_flota = RepositorioDeFlotaEnMemoria()
        if repositorio_de_flota is not None and flota is not None:
            repositorio_de_flota.sembrar(dict(flota))
        self._flota = repositorio_de_flota

    def ruta_persistida(self, ruta_id: RutaId) -> RutaPersistida:
        """Lectura de una Ruta ya publicada (FR-02, paso 19 de ARQ-P1).

        El almacen es propio del container (ARQ-09) y no se expone: lo que
        sale es la Ruta, no el repositorio. El identificador es el de una Ruta
        --- la de un Vehiculo ---, no el de una corrida de planificacion."""
        return self._repositorio.obtener(ruta_id)

    def rutas_persistidas(
        self, fecha: dt.date | None = None
    ) -> tuple[RutaPersistida, ...]:
        """Rutas ya publicadas, opcionalmente las de un solo dia (RB-01).

        El filtro es por atributo de la Ruta, no por corrida: dos
        planificaciones del mismo dia devuelven sus Rutas juntas, porque lo
        que se pregunta es que se planifico ese dia y no cual fue la enesima
        corrida.
        """
        return tuple(self._repositorio.listar(fecha))

    def fechas_planificadas(self) -> tuple[dt.date, ...]:
        """Dias con al menos una Ruta, para poblar el filtro de lectura."""
        return self._repositorio.fechas_con_rutas()

    def plan(self, plan_id: PlanId) -> PlanDeDistribucion:
        """Un Plan de Distribucion por su identificador."""
        return self._repositorio.obtener_plan(plan_id)

    def planes(self, fecha: dt.date | None = None) -> tuple[PlanDeDistribucion, ...]:
        """Planes publicados, opcionalmente los de una jornada (RB-01)."""
        return tuple(self._repositorio.listar_planes(fecha))

    def rutas_del_plan(self, plan_id: PlanId) -> tuple[RutaPersistida, ...]:
        """Las Rutas que componen un Plan, en el orden de su identificador.

        Se resuelve preguntando por las Rutas y no leyendo una lista guardada
        en el Plan: la Ruta ya referencia su Plan, y dos listas de lo mismo
        pueden discrepar.
        """
        self._repositorio.obtener_plan(plan_id)
        return tuple(
            persistida
            for persistida in self._repositorio.listar()
            if persistida.plan_id == plan_id
        )

    def fechas_con_planificacion(self) -> tuple[dt.date, ...]:
        """Jornadas que tienen al menos un Plan (RB-11).

        Se enumeran las que tienen **Plan** y no las que tienen Ruta, aunque
        hoy den lo mismo: el Plan es la unidad de lectura para el operador
        (glosario, entidad 13), y una jornada tiene contenido cuando hubo una
        corrida.

        Que den lo mismo ya no es una coincidencia que haya que vigilar: un
        Plan que se queda sin Rutas se elimina, de modo que no puede quedar
        una jornada listada como planificada y sin nada que mostrar.
        """
        return self._repositorio.fechas_con_planes()

    def despachar_ruta(
        self, ruta_id: RutaId, transportista_id: str | None = None
    ) -> tuple[OrdenId, ...]:
        """FR-16: despacha una Ruta planificada y devuelve sus Ordenes, que
        pasan a En transito (glosario, ciclo de vida).

        La Ruta no tiene estados (B-96, abierta) y no se le agrega ninguno:
        el inicio de la ejecucion queda registrado como la transicion de cada
        Orden, con su marca temporal, en su historial (FR-03). Por la misma
        razon, que la Ruta ya este despachada se lee en sus Ordenes: si
        alguna no esta en Planificada, el despacho se rechaza. Una Ruta de un
        Plan descartado no existe: `EntidadNoEncontrada`.
        """
        persistida = self._repositorio.obtener(ruta_id)
        fuera = self._fuera_de(persistida.ruta.ordenes, EstadoOrden.PLANIFICADA, ruta_id)
        if fuera:
            raise RutaYaDespachada(
                f"La Ruta {ruta_id} ya fue despachada: sus Ordenes no estan en Planificada.",
                fuera,
            )
        despachadas = self._publicador.despachar(persistida.ruta)
        # El transportista (opcional, D-C2) y la notificacion para telemetria,
        # en una sola transaccion del componente (ARQ-59).
        self._ejecucion.registrar_despacho(
            ruta_id,
            transportista_id,
            notificacion_de_despacho(
                ruta_id, str(persistida.ruta.vehiculo_id), transportista_id, self._reloj_de_eventos()
            ),
        )
        _LOG.info(
            "Ruta despachada | %s | ordenes=%d | transportista=%s",
            ruta_id,
            len(despachadas),
            transportista_id or "sin asignar",
        )
        return despachadas

    def transportista_de(self, ruta_id: RutaId) -> str | None:
        """El transportista asignado al despachar la Ruta, si se asigno."""
        return self._ejecucion.transportista_de(ruta_id)

    def rutas_del_transportista(self, transportista_id: str) -> tuple[RutaId, ...]:
        """Las Rutas despachadas a un transportista, en cualquier jornada."""
        return self._ejecucion.rutas_del_transportista(transportista_id)

    def entregador(
        self, enviar: Callable[[NotificacionSaliente], None]
    ) -> EntregadorDeNotificaciones:
        """El entregador de la salida transaccional, con el transporte que
        decida el ensamblado."""
        return EntregadorDeNotificaciones(self._ejecucion, enviar)

    def notificaciones_pendientes(self) -> int:
        """Verificacion de salud para `/estado` (ARQ-13: no es una metrica)."""
        return len(self._ejecucion.sin_entregar())

    def declarar_resultado_de_parada(
        self,
        ruta_id: RutaId,
        secuencia: int,
        resultado: ResultadoDeParada,
        declarante: Declarante | None = None,
        declarado_en: dt.datetime | None = None,
        sin_conexion: bool = False,
    ) -> tuple[OrdenId, ...]:
        """CU-06 sin telemetria: el resultado de una Parada lleva cada una de
        sus Ordenes de En transito a Completada (cumplida) o a Fallida
        (fallida), con la transicion y su declarante en el historial (ARQ-65).

        Se admite fuera del orden planificado: la operacion real saltea
        Paradas, y exigir el orden obligaria a declarar resultados falsos
        para seguir. La Ruta no cambia.

        Solo se aceptan transiciones que el ciclo de vida admite: si alguna
        Orden de la Parada no esta En transito --la Ruta no se despacho, o la
        Parada ya tiene resultado-- se rechaza y ninguna cambia. La posicion
        del Vehiculo, la reprogramacion y la devolucion no estan implementadas.
        """
        persistida = self._repositorio.obtener(ruta_id)
        paradas = persistida.ruta.paradas
        if not 1 <= secuencia <= len(paradas):
            raise ParadaNoEncontrada(
                f"La Ruta {ruta_id} no tiene Parada {secuencia}: tiene {len(paradas)}, "
                f"numeradas desde 1"
            )
        ordenes = paradas[secuencia - 1].ordenes
        if declarado_en is not None:
            return self._declarar_resultado_de_campo(
                persistida.ruta, secuencia, resultado, declarante, declarado_en, sin_conexion
            )
        fuera = self._fuera_de(ordenes, EstadoOrden.EN_TRANSITO, ruta_id)
        if fuera:
            raise ResultadoNoAdmitido(
                f"La Parada {secuencia} de la Ruta {ruta_id} no admite el resultado "
                f"{resultado.value}: sus Ordenes tienen que estar En transito.",
                fuera,
            )
        declaradas = self._publicador.declarar_resultado(
            persistida.ruta, secuencia, resultado, declarante
        )
        _LOG.info(
            "Resultado de Parada | %s Parada %d | %s | ordenes=%d",
            ruta_id,
            secuencia,
            resultado.value,
            len(declaradas),
        )
        return declaradas

    def _declarar_resultado_de_campo(
        self,
        ruta: Ruta,
        secuencia: int,
        resultado: ResultadoDeParada,
        declarante: Declarante | None,
        declarado_en: dt.datetime,
        sin_conexion: bool,
    ) -> tuple[OrdenId, ...]:
        """CU-06, flujo 4a con trazabilidad (encargo 2026-11-23B, D29).

        La declaracion llega con la hora del dispositivo. Cada Orden de la
        Parada decide por su historial (Gestion de Ordenes): la hora del hecho,
        si se aplica, si prevalece sobre un resultado posterior o si no se
        aplica. Si la Ruta se reemplazo mientras tanto, las Ordenes de la
        Parada ya estan en la vigente (o en la de contingencia), y el
        resultado se aplica ahi. Un reenvio de la misma declaracion no cambia
        nada y da `ResultadoNoAdmitido`, como antes."""
        ordenes = ruta.paradas[secuencia - 1].ordenes
        destino = EstadoOrden.COMPLETADA if resultado is ResultadoDeParada.CUMPLIDA else EstadoOrden.FALLIDA
        declaracion = DeclaracionDeCampo(
            declarado_en=declarado_en,
            recibido_en=self._reloj_de_eventos(),
            sin_conexion=sin_conexion,
            ruta_declarada=ruta.id,
            secuencia_declarada=secuencia,
        )
        evaluaciones = {
            id_orden: self._ordenes.consultar(id_orden).evaluar_declaracion_de_campo(destino, declaracion, declarante)
            for id_orden in ordenes
        }
        estados = {id_orden: self._ordenes.consultar(id_orden).estado for id_orden in ordenes}
        if all(e.efecto is EfectoDeLaDeclaracion.YA_APLICADA for e in evaluaciones.values()):
            raise ResultadoNoAdmitido(
                f"La Parada {secuencia} de la Ruta {ruta.id} ya tiene ese resultado: la declaracion no cambia nada.",
                estados,
            )
        self._publicador.declarar_resultado(
            ruta, secuencia, resultado, declarante, declaracion.declarado_en, declaracion.recibido_en, sin_conexion
        )
        aplicadas = tuple(
            o for o, e in evaluaciones.items()
            if e.efecto in (EfectoDeLaDeclaracion.APLICADA, EfectoDeLaDeclaracion.PREVALECE)
        )
        _LOG.info(
            "Resultado de Parada de campo | %s Parada %d | %s | %s",
            ruta.id,
            secuencia,
            resultado.value,
            ", ".join(f"{o}={e.efecto.value}" for o, e in evaluaciones.items()),
        )
        if not aplicadas:
            motivos = sorted({e.motivo for e in evaluaciones.values()
                              if e.efecto is EfectoDeLaDeclaracion.NO_APLICADA})
            raise DeclaracionDeCampoNoAplicada(
                f"La declaracion de la Parada {secuencia} de la Ruta {ruta.id} no se aplico y quedo en el "
                f"historial: {'; '.join(motivos)}",
                {o: self._ordenes.consultar(o).estado for o in ordenes},
            )
        return aplicadas

    def _fuera_de(
        self, ordenes: Sequence[OrdenId], estado: EstadoOrden, ruta_id: RutaId
    ) -> dict[OrdenId, EstadoOrden]:
        """Las Ordenes que no estan en `estado` o que no apuntan a la Ruta."""
        fuera: dict[OrdenId, EstadoOrden] = {}
        for id_orden in ordenes:
            orden = self._ordenes.consultar(id_orden)
            if orden.estado is not estado or orden.ruta_id != ruta_id:
                fuera[id_orden] = orden.estado
        return fuera

    def _verificar_sin_despachar(self, persistida: RutaPersistida) -> None:
        fuera = self._fuera_de(persistida.ruta.ordenes, EstadoOrden.PLANIFICADA, persistida.ruta.id)
        if fuera:
            raise RutaEnEjecucion(
                f"La Ruta {persistida.ruta.id} ya fue despachada: no se puede descartar "
                f"ni invalidar.",
                fuera,
            )

    def descartar_plan(self, plan_id: PlanId) -> tuple[OrdenId, ...]:
        """Elimina un Plan entero: sus Rutas, y con ellas sus Ordenes (FR-03).

        Es la operacion que corresponde a la entidad: descartar Ruta por Ruta
        dejaba el Plan sin Rutas pero existente, y sus Vehiculos ocupados para
        la jornada sin que ninguna Ruta lo justificara.

        El Plan se elimina al final, despues de sus Rutas: al reves, las
        Rutas quedarian referenciando un Plan inexistente si algo fallara en
        el medio. Devuelve las Ordenes liberadas, que quedan planificables.
        """
        plan = self._repositorio.obtener_plan(plan_id)
        rutas = self.rutas_del_plan(plan_id)
        # Todas antes de eliminar ninguna: un Plan con una Ruta despachada no
        # se descarta a medias.
        for persistida in rutas:
            self._verificar_sin_despachar(persistida)
        liberadas: list[OrdenId] = []
        for persistida in rutas:
            liberadas.extend(self._publicador.invalidar_ruta(persistida.ruta))
        self._repositorio.eliminar_plan(plan_id)
        _LOG.info(
            "Plan descartado | %s | rutas=%d ordenes_liberadas=%d vehiculos=%s",
            plan_id,
            plan.cantidad_rutas,
            len(liberadas),
            list(plan.vehiculos),
        )
        return tuple(liberadas)

    def invalidar_ruta(self, ruta_id: RutaId) -> tuple[OrdenId, ...]:
        """Elimina una Ruta y devuelve sus Ordenes a Creada (FR-03 inverso).

        Devuelve las Ordenes liberadas, que quedan planificables otra vez.

        **Por que la Ruta se elimina entera y no se le quita la Orden.** El
        glosario define la Ruta como la secuencia ordenada de Paradas
        asignada a un Vehiculo y como la salida primaria del motor de
        optimizacion. Su distancia, su carga y su orden de visita son
        resultado de haber resuelto un CVRP sobre un conjunto de Ordenes:
        sacarle una y conservar el resto produce un objeto que ya no es
        solucion de ningun problema, con una distancia que no corresponde a
        su recorrido o una carga que no corresponde a sus Ordenes. A eso se
        suma que el identificador de la Ruta se deriva por resumen canonico
        de la instancia y del recorrido, de modo que una Ruta mutada
        arrastraria un identificador que ya no describe su contenido.

        **Por que se elimina en vez de conservarse marcada como invalida.**
        Marcarla exigiria un estado de Ruta, y el glosario no define ninguno:
        la Ruta no tiene ciclo de vida propio, solo lo tiene la Orden.
        Introducir "invalidada" seria agregar vocabulario de dominio por
        conveniencia de implementacion. Si mas adelante hace falta conservar
        el historial de lo que se planifico y se descarto, eso es una entidad
        y una decision que hoy no estan tomadas.

        **Por que se liberan todas sus Ordenes y no solo una.** Planificada
        significa, segun el glosario, que la Orden quedo asignada a una
        Parada dentro de una Ruta. Desaparecida la Ruta, ninguna de sus
        Ordenes sigue estandolo: dejar alguna en Planificada la haria
        apuntar a una Ruta inexistente.

        **Por que el Plan tambien desaparece si era su ultima Ruta.** Mismo
        argumento, un nivel arriba: un Plan de Distribucion es el conjunto de
        Rutas de una corrida (glosario, entidad 13), y un conjunto sin
        elementos no describe ninguna corrida. Las alternativas se descartaron
        por lo que exigirian: marcarlo como vaciado seria un estado, y el
        glosario no define ciclo de vida para el Plan; recalcular sus totales
        contradice a la entidad, que los define como totales de la corrida y
        no como estado presente. Ademas, un Plan sin Rutas ya no ocupa ningun
        Vehiculo --la ocupacion se deriva de las Rutas vigentes-- de modo que
        conservarlo solo dejaba una jornada listada como planificada sin nada
        que mostrar.
        """
        persistida = self._repositorio.obtener(ruta_id)
        self._verificar_sin_despachar(persistida)
        plan_id = persistida.plan_id
        liberadas = tuple(persistida.ruta.ordenes)
        self._publicador.invalidar_ruta(persistida.ruta)
        self._eliminar_plan_si_quedo_sin_rutas(plan_id)
        return liberadas

    def _eliminar_plan_si_quedo_sin_rutas(self, plan_id: PlanId) -> None:
        """El Plan que se quedo sin Rutas deja de ser plan y se elimina."""
        try:
            self._repositorio.obtener_plan(plan_id)
        except EntidadNoEncontrada:
            # La Ruta referenciaba un Plan que ya no esta. No es un error:
            # descartar el Plan entero elimina sus Rutas por el camino del
            # Publicador, no por este.
            return
        if self.rutas_del_plan(plan_id):
            return
        self._repositorio.eliminar_plan(plan_id)
        _LOG.info(
            "Plan eliminado por quedarse sin Rutas | %s | se descarto su ultima Ruta",
            plan_id,
        )

    def desasignar_orden(self, orden_id: OrdenId) -> tuple[RutaId, tuple[OrdenId, ...]]:
        """Desasigna una Orden, con el efecto que eso tiene sobre su Ruta.

        Devuelve la Ruta que quedo invalidada y todas las Ordenes liberadas
        --incluida la pedida--, para que quien lo dispare no tenga que
        deducir el alcance de lo que ocurrio.
        """
        orden = self._ordenes.consultar(orden_id)
        if orden.ruta_id is None:
            raise OrdenNoAsignada(
                f"La Orden {orden_id} no esta asignada a ninguna Ruta: "
                f"esta en estado {orden.estado.value}"
            )
        ruta_id = orden.ruta_id
        return ruta_id, self.invalidar_ruta(ruta_id)

    def ensamblar_instancia(
        self,
        deposito: int,
        capacidad_por_vehiculo: dict[VehiculoId, int],
        ordenes: tuple[OrdenId, ...] | None = None,
        fecha: dt.date | None = None,
        depositos_por_vehiculo: dict[VehiculoId, int] | None = None,
    ) -> InstanciaProblema:
        """Pasos 7-13: la instancia, sin resolverla ni publicar nada.

        Existe porque el motor es intercambiable y puro (ARQ-30): poder
        obtener la instancia sin ejecutar el resto del escenario es lo que
        permite medirla, compararla entre motores y verificar NFR-07 por
        ejecucion repetida. No muta nada.

        `fecha` omitida toma la jornada en curso. Se admite explicita para
        que quien mide o compara instancias pueda fijarla: desde que la
        jornada forma parte de la instancia, dejarla al reloj haria que dos
        corridas de la misma medicion a ambos lados de la medianoche local
        resultaran instancias distintas.
        """
        solicitud = self._receptor.normalizar(
            tipo=TipoSolicitud.NUEVA_PLANIFICACION,
            deposito=deposito,
            capacidad_por_vehiculo=capacidad_por_vehiculo,
            fecha_planificacion=fecha if fecha is not None else self._reloj(),
            ordenes=ordenes,
            depositos_por_vehiculo=depositos_por_vehiculo,
        )
        return self._ensamblador.ensamblar(solicitud)

    def planificar(
        self,
        deposito: int,
        capacidad_por_vehiculo: dict[VehiculoId, int],
        ordenes: tuple[OrdenId, ...] | None = None,
    ) -> SolucionRuteo:
        """Escenario completo de ARQ-P1, pasos 7-17, disparado por FR-01.

        Devuelve el plan de flota: una Ruta por Vehiculo, cada una con su
        identificador. El Deposito es parametro de cada planificacion (B-59),
        no constante del codigo: quien dispara la planificacion lo aporta.

        `ordenes` acota la corrida a un subconjunto; omitido, entran todas
        las planificables.
        """
        fecha = self._reloj()
        instancia = self.ensamblar_instancia(
            deposito, capacidad_por_vehiculo, ordenes, fecha
        )
        self._verificar_seleccion_en_la_flota(capacidad_por_vehiculo)
        self._verificar_vehiculos_libres(instancia.capacidad_por_vehiculo, fecha)
        solucion = self._motor.resolver(instancia)
        self._publicador.publicar(solucion, deposito, fecha)
        return solucion

    def planificar_plan(
        self,
        deposito: int,
        capacidad_por_vehiculo: dict[VehiculoId, int],
        ordenes: tuple[OrdenId, ...] | None = None,
        depositos_por_vehiculo: dict[VehiculoId, int] | None = None,
    ) -> PlanDeDistribucion:
        """Igual que `planificar`, devolviendo el Plan de Distribucion.

        Es lo que necesita quien dispara la corrida desde la interfaz: el
        Plan es la unidad de lectura por encima de la Ruta (glosario, entidad
        13) y trae los totales ya calculados, de modo que no haya que sumar
        el listado de Rutas --suma que deja de ser correcta en cuanto conviven
        varios Planes en una jornada--.

        `planificar` se conserva porque devuelve la salida del motor, que es
        lo que consumen las mediciones y las pruebas del solver.
        """
        fecha = self._reloj()
        instancia = self.ensamblar_instancia(
            deposito, capacidad_por_vehiculo, ordenes, fecha, depositos_por_vehiculo
        )
        self._verificar_seleccion_en_la_flota(capacidad_por_vehiculo)
        self._verificar_vehiculos_libres(instancia.capacidad_por_vehiculo, fecha)
        solucion = self._motor.resolver(instancia)
        return self._publicador.publicar(solucion, deposito, fecha)

    @property
    def margen_de_capacidad(self) -> float:
        """El margen de la operacion con que se planifica (B-220, punto 2).

        Se expone para que quien publique la Flota informe la capacidad
        efectiva con el mismo margen que usa el calculo, en vez de repetirlo
        como constante en la frontera (B-242, punto 5, propuesta 3)."""
        return self._ensamblador.margen_de_capacidad

    # --- La Flota (2026-11-02) -------------------------------------------

    def flota_vigente(self) -> dict[VehiculoId, int] | None:
        """Los Vehiculos activos con su capacidad nominal: los que pueden
        entrar en una planificacion. None si el servicio no tiene Flota
        registrada (las pruebas del motor). Un Vehiculo dado de baja no esta:
        pedirlo en una planificacion es pedir un Vehiculo ajeno."""
        if self._flota is None:
            return None
        return {v.id: v.capacidad for v in self._flota.listar() if v.activo}

    def vehiculos_registrados(self) -> tuple[VehiculoRegistrado, ...]:
        """Toda la Flota, tambien los dados de baja, por patente."""
        return () if self._flota is None else self._flota.listar()

    def dar_de_alta_vehiculo(self, patente: str, capacidad: int) -> VehiculoRegistrado:
        """Suma un Vehiculo a la Flota, activo. La patente se normaliza
        (mayusculas, sin espacios ni guiones) y tiene que ser una matricula
        argentina; la capacidad, un entero de bultos que deje al menos uno de
        capacidad efectiva con el margen de la operacion."""
        registro = self._registro_de_flota()
        vehiculo = validar_patente(patente)
        self._validar_capacidad(capacidad)
        existente = registro.obtener(vehiculo)
        if existente is not None:
            raise VehiculoExistente(vehiculo, existente.activo)
        nuevo = VehiculoRegistrado(vehiculo, capacidad, activo=True)
        registro.guardar(nuevo)
        return nuevo

    def modificar_vehiculo(
        self, patente: str, *, capacidad: int | None = None, activo: bool | None = None
    ) -> VehiculoRegistrado:
        """Cambia la capacidad, da de baja (`activo` en False) o reactiva.

        Con una Ruta en un Plan de la jornada operativa, el Vehiculo no se
        da de baja ni cambia de capacidad (`VehiculoEnUso`): la Ruta se
        calculo contra la capacidad que tenia. Reactivar no tiene esa
        restriccion."""
        registro = self._registro_de_flota()
        vehiculo = normalizar_patente(patente)
        actual = registro.obtener(vehiculo)
        if actual is None:
            raise VehiculoInexistente(patente)
        if capacidad is not None:
            self._validar_capacidad(capacidad)
        cambia_capacidad = capacidad is not None and capacidad != actual.capacidad
        da_de_baja = activo is False and actual.activo
        if cambia_capacidad or da_de_baja:
            fecha = self._reloj()
            ocupado = self._repositorio.planes_que_ocupan((vehiculo,), fecha)
            if vehiculo in ocupado:
                raise VehiculoEnUso(vehiculo, ocupado[vehiculo], fecha)
        nuevo = dataclasses.replace(
            actual,
            capacidad=capacidad if capacidad is not None else actual.capacidad,
            activo=activo if activo is not None else actual.activo,
        )
        registro.guardar(nuevo)
        return nuevo

    def _registro_de_flota(self) -> RepositorioDeFlota:
        if self._flota is None:
            raise ErrorDeDominio("Este servicio no tiene Flota registrada.")
        return self._flota

    def _validar_capacidad(self, capacidad: int) -> None:
        if isinstance(capacidad, bool) or not isinstance(capacidad, int):
            raise CapacidadInvalida(capacidad, "tiene que ser un numero entero de bultos")
        if capacidad < 1 or capacidad > CAPACIDAD_MAXIMA:
            raise CapacidadInvalida(capacidad, f"tiene que estar entre 1 y {CAPACIDAD_MAXIMA} bultos")
        if capacidad_efectiva(capacidad, self.margen_de_capacidad) < 1:
            raise CapacidadInvalida(
                capacidad, "con el margen de la operacion no le queda ni un bulto de capacidad efectiva"
            )

    def jornada_operativa(self) -> dt.date:
        """La jornada en la que una corrida disparada ahora quedaria registrada.

        Se expone porque el reloj del container es el unico que decide en que
        jornada cae una planificacion, y quien responda "que Vehiculos hay
        disponibles hoy" tiene que preguntarselo a el. Leer la hora por
        separado en la frontera daria una respuesta que puede no coincidir con
        la jornada que el Plan termine llevando --sobre la medianoche local, o
        con el reloj fijado por una prueba o por el exportador del contrato--.
        """
        return self._reloj()

    def vehiculos_comprometidos(
        self, vehiculos: Sequence[VehiculoId], fecha: dt.date
    ) -> dict[VehiculoId, PlanId]:
        """Que Plan de esa jornada ocupa cada Vehiculo pedido, si alguno.

        Es la misma consulta que `planificar` hace antes de resolver, expuesta
        para poder responderla sin disparar nada. Existe porque la regla --un
        Vehiculo participa de un unico Plan por jornada-- es de dominio, y
        quien componga una planificacion tiene que poder anticiparla sin
        reimplementarla: derivarla afuera recorriendo Rutas seria una segunda
        copia de la regla, capaz de discrepar de esta.

        No reemplaza la verificacion de `planificar`: esto informa, aquello
        decide.
        """
        return self._repositorio.planes_que_ocupan(tuple(vehiculos), fecha)

    def _verificar_seleccion_en_la_flota(
        self, capacidad_por_vehiculo: Mapping[VehiculoId, int]
    ) -> None:
        """Cada Vehiculo pedido es de la Flota y viene con su capacidad nominal.

        Se verifica sobre la capacidad pedida y no sobre la de la instancia,
        que es la efectiva: con un margen de capacidad, las dos difieren
        (B-220, punto 2).

        Corre antes de resolver y antes que la verificacion de ocupacion, con
        el mismo criterio que ella: la instancia se declara inadmisible
        entera, sin planificar con los Vehiculos validos. Primero la
        pertenencia y despues la capacidad, porque un Vehiculo ajeno no tiene
        capacidad nominal contra la que comparar.
        """
        vigente = self.flota_vigente()
        if vigente is None:
            return
        ajenos = [v for v in capacidad_por_vehiculo if v not in vigente]
        if ajenos:
            raise VehiculosFueraDeLaFlota(ajenos)
        discrepancias = [
            CapacidadDiscrepante(vehiculo=v, pedida=c, nominal=vigente[v])
            for v, c in capacidad_por_vehiculo.items()
            if c != vigente[v]
        ]
        if discrepancias:
            raise CapacidadDistintaDeLaNominal(discrepancias)

    def _verificar_vehiculos_libres(
        self, capacidad_por_vehiculo: dict[VehiculoId, int], fecha: dt.date
    ) -> None:
        """Ningun Vehiculo de la seleccion puede estar en otro Plan del dia.

        Se verifica antes de resolver: descubrirlo despues significaria haber
        gastado el calculo para descartarlo. Y la instancia se declara
        inadmisible entera, sin planificar con los Vehiculos libres, con el
        mismo criterio que rige para la demanda que excede la capacidad --un
        plan parcial que nadie pidio es peor que un rechazo con causa--.

        La jornada llega como parametro y no se relee del reloj: tiene que ser
        exactamente aquella con la que se ensamblo la instancia y en la que el
        Plan va a quedar registrado.
        """
        ocupados = self._repositorio.planes_que_ocupan(
            tuple(capacidad_por_vehiculo), fecha
        )
        if ocupados:
            raise VehiculosComprometidos(ocupados, fecha)


def crear_servicio(
    bus: Bus,
    cartografia: ProveedorGrafoVial,
    gestion_ordenes: ServicioGestionOrdenes,
    motor: MotorDeRuteo | None = None,
    reloj: Callable[[], dt.date] = fecha_operativa_de_hoy,
    repositorio: RepositorioRutas | None = None,
    flota: Mapping[VehiculoId, int] | None = None,
    margen_de_capacidad: float = SIN_MARGEN,
    ejecucion: RepositorioDeEjecucion | None = None,
    repositorio_de_flota: RepositorioDeFlota | None = None,
    reemplazos: RepositorioDeReemplazos | None = None,
    contingencias: RepositorioDeContingencias | None = None,
    parametros_de_desvio: ParametrosDeDesvio | None = None,
) -> ServicioPlanificacionRutas:
    """Punto de ensamblado del container -- llamado unicamente desde
    hoppit.bootstrap. Cartografia entra por su Protocol y no por la clase
    concreta: lo que este container necesita es la superficie de consulta
    del grafo, no la implementacion que la carga desde disco. El motor se
    inyecta (B-51): sin argumento se usa el
    Solver VRP determinista; el reemplazo se pasa desde bootstrap.py como
    parametro explicito, no editando esta funcion (ARQ-29, ARQ-30).

    `flota` registra los Vehiculos con su capacidad nominal; con ella, toda
    planificacion se restringe a esos Vehiculos y a esas capacidades. Con
    `repositorio_de_flota` (la base SQLite) la Flota persiste y el operador
    la administra; `flota` la siembra solo si el registro esta vacio.

    `margen_de_capacidad` es el margen de la operacion (B-220, punto 2,
    criterio d): el motor planifica con la capacidad efectiva,
    floor(nominal x (1 - margen)). Sin margen, la efectiva es la nominal."""
    contexto = AdaptadorDatosContexto(cartografia)
    almacen = repositorio if repositorio is not None else RepositorioRutasEnMemoria()
    registro_de_ejecucion = ejecucion if ejecucion is not None else RepositorioDeEjecucionEnMemoria()
    # CU-07 (2026-11-20A): sin repositorio propio, el de memoria, como el resto.
    reemplazo = ServicioDeReemplazo(
        almacen, gestion_ordenes, contexto, bus, registro_de_ejecucion,
        reemplazos if reemplazos is not None else RepositorioDeReemplazosEnMemoria(),
        reloj=instante_actual,
    )
    servicio = ServicioPlanificacionRutas(
        motor=motor if motor is not None else SolverVRPDeterminista(),
        ensamblador=EnsambladorInstancia(gestion_ordenes, contexto, margen_de_capacidad),
        publicador=PublicadorRuta(almacen, bus),
        receptor=ReceptorSolicitudes(),
        repositorio=almacen,
        gestion_ordenes=gestion_ordenes,
        reloj=reloj,
        flota=flota,
        ejecucion=registro_de_ejecucion,
        repositorio_de_flota=repositorio_de_flota,
        reemplazo=reemplazo,
    )
    # CU-11 (2026-11-20A): las reglas de Flota y ocupacion son las del servicio.
    servicio.contingencias = ServicioDeContingencias(
        almacen, gestion_ordenes, contexto, bus, registro_de_ejecucion,
        contingencias if contingencias is not None else RepositorioDeContingenciasEnMemoria(),
        flota_vigente=servicio.flota_vigente,
        margen_de_capacidad=lambda: servicio.margen_de_capacidad,
        vehiculos_comprometidos=servicio.vehiculos_comprometidos,
        ruta_vigente=reemplazo.vigente,
        reloj=instante_actual,
    )
    # D36 (2026-11-23F): el mu de cada Ruta, entre reemplazos y contingencias.
    servicio.contingencias.mu_de = reemplazo.mu_del_trazado
    reemplazo.mu_de_otras = servicio.contingencias.mu_de_la_ruta_de_contingencia
    servicio.desvio = VigilanteDeDesvio(almacen, reemplazo, contexto, reloj=instante_actual,
                                        parametros=parametros_de_desvio or ParametrosDeDesvio())
    # D31 (2026-11-23E): con el mismo candado que el desvio.
    servicio.riesgo_vial = VigilanteDeRiesgoVial(reemplazo, reloj=instante_actual, candado=servicio.desvio.candado)
    servicio.pedido_en_la_jornada = ServicioDePedidoEnLaJornada(
        almacen, gestion_ordenes, contexto, bus, rutas_de_la_jornada=servicio.rutas_persistidas,
        ruta_vigente=reemplazo.vigente, invalidar=servicio.invalidar_ruta, reloj=instante_actual,
    )
    return servicio


__all__ = [
    # D33 (2026-11-23E).
    "ZonaContieneAlVehiculo",
    # Propuesta de reruteo por riesgo vial (2026-11-23E, D31).
    "MOTIVO_RIESGO_VIAL",
    "CompromisoDeRiesgoVial",
    "UmbralDeRiesgoVial",
    "MU_DE_LA_PROPUESTA_M",
    "VigilanteDeRiesgoVial",
    "BrechaDeOptimalidad",
    "brecha_de_optimalidad",
    # CU-07 desde el paso 2 (2026-11-20A).
    "ANTIGUEDAD_MAXIMA_S",
    "MAXIMO_EXACTO",
    "MOTIVO_CONTINGENCIA",
    "MOTIVO_INCIDENTE",
    "TIPO_RUTA_REEMPLAZADA",
    "AvanceDeRuta",
    "EstadoDePropuesta",
    "FuenteDelOrigen",
    "OrigenDelRecalculo",
    "PosicionDelVehiculo",
    "PropuestaDeReemplazo",
    "PropuestaNoEncontrada",
    "PropuestaVencida",
    "PropuestaYaResuelta",
    "ReemplazoDeRuta",
    "RepositorioDeReemplazos",
    "RepositorioDeReemplazosEnMemoria",
    "RepositorioDeReemplazosSQLite",
    "RutaNoEnEjecucion",
    "RutaYaReemplazada",
    "SecuenciaRecalculada",
    "ServicioDeReemplazo",
    # Reruteo por desvio (2026-11-22A).
    "MOTIVO_DESVIO",
    "AsignacionEnLaJornada",
    "PedidoNoAsignable",
    "ServicioDePedidoEnLaJornada",
    "SinVehiculoParaElPedido",
    "INTERVALO_DE_VIGILANCIA_S",
    "REPORTES_CONSECUTIVOS",
    "UMBRAL_DE_DESVIO_M",
    "DesvioDetectado",
    "ParametrosDeDesvio",
    "ReporteDePosicion",
    "VigilanteDeDesvio",
    "distancia_a_polilinea_m",
    # CU-11 (2026-11-20A).
    "PRIORIDAD_MAXIMA",
    "Contingencia",
    "ContingenciaInfactible",
    "ContingenciaNoEncontrada",
    "EstadoDeContingencia",
    "EvaluacionDeContingencia",
    "RepositorioDeContingencias",
    "RepositorioDeContingenciasEnMemoria",
    "RepositorioDeContingenciasSQLite",
    "ServicioDeContingencias",
    "SinContingenciaAplicable",
    "SinAlternativaFactible",
    "secuenciar_pendientes",
    "CAPACIDAD_MAXIMA",
    "CapacidadInvalida",
    "PatenteInvalida",
    "RepositorioDeFlota",
    "RepositorioDeFlotaEnMemoria",
    "RepositorioDeFlotaSQLite",
    "VehiculoEnUso",
    "VehiculoExistente",
    "VehiculoInexistente",
    "VehiculoRegistrado",
    "TIPO_RUTA_DESPACHADA",
    "NotificacionSaliente",
    "RepositorioDeEjecucion",
    "RepositorioDeEjecucionEnMemoria",
    "RepositorioDeEjecucionSQLite",
    "EntregadorDeNotificaciones",
    "crear_servicio",
    "ServicioPlanificacionRutas",
    "InstanciaProblema",
    "SolucionRuteo",
    "Ruta",
    "ParadaDeInstancia",
    "ParadaPlaneada",
    "TipoSolicitud",
    "SolicitudPlanificacion",
    "RutaPersistida",
    "SolicitudInvalida",
    "InstanciaInvalida",
    "SinSolucion",
    "SinSolucionConTodosLosVehiculos",
    # El motor por omision y su limite de soluciones, exportados para usarlos
    # sobre instancias externas y en las herramientas de comparacion (B-211,
    # punto 4). `crear_servicio` lo sigue usando sin opciones.
    "SolverVRPDeterminista",
    "LIMITE_DE_SOLUCIONES",
    # Garantia de terminacion y planificacion por prioridad (B-220, punto 2).
    "LIMITE_DE_RAMAS",
    "MotivoSinSolucion",
    "MotivoNoPlanificada",
    "OrdenNoPlanificada",
    "CRITICIDAD_MINIMA",
    "CRITICIDAD_MAXIMA",
    "CRITICIDAD_POR_OMISION",
    "ATRASO_MAXIMO_COMPUTADO_DIAS",
    "atraso_en_dias",
    "criticidad",
    "capacidad_efectiva",
    "SIN_MARGEN",
    "MARGEN_DE_LA_OPERACION",
    # La configuracion de la busqueda, para inspeccionarla (NFR-07) y para
    # las replicas del modelo en tools/; cada llamada devuelve un objeto
    # nuevo (B-220, punto 4).
    "parametros_de_busqueda",
    # Flujos de excepcion de CU-02 (RB-05), distinguibles entre si.
    "ParadasIncomunicadas",
    "ParadaIncomunicada",
    "MotivoDeIncomunicacion",
    "DemandaExcedeCapacidad",
    "OrdenNoAsignada",
    # Despacho y resultado de Parada (FR-16, CU-06).
    "OrdenesFueraDeEstado",
    "RutaYaDespachada",
    "RutaEnEjecucion",
    "ResultadoNoAdmitido",
    "DeclaracionDeCampoNoAplicada",
    "ParadaNoEncontrada",
    "ResultadoDeParada",
    "VehiculosComprometidos",
    "VehiculosFueraDeLaFlota",
    "CapacidadDiscrepante",
    "CapacidadDistintaDeLaNominal",
    "PlanDeDistribucion",
    "RepositorioRutas",
    "RepositorioRutasEnMemoria",
    "RepositorioRutasSQLite",
]
