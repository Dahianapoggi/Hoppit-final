"""Entidad Orden y su maquina de estados -- FR-03.

Fuente de verdad del ciclo de vida: HOPPIT_Glosario_Modelo_Dominio.md.
Maquina unica para ambas modalidades (entrega | recoleccion). Se ejercitan
Creada -> Planificada, por planificacion (ver ARQ-P1); Planificada ->
Creada, por descarte de la Ruta que contiene la Orden (B-91, B-109);
Planificada -> En transito, por despacho de la Ruta (FR-16), y En transito ->
Completada o Fallida, por el resultado declarado de su Parada (CU-06), desde
la sesion 2026-10-13F. Reprogramada, Devuelta y Cancelada no tienen todavia
quien las dispare. Las alertas
proactivas de FR-07 no son estados -- son reglas derivadas sobre (estado,
marca temporal) -- y por lo tanto no aparecen aca.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum

from hoppit.shared.errores import ErrorDeDominio, TransicionInvalida
from hoppit.shared.eventos import Declarante
from hoppit.shared.tiempo import instante_actual
from hoppit.shared.ids import OrdenId, RutaId


class EstadoOrden(enum.Enum):
    CREADA = "creada"
    PLANIFICADA = "planificada"
    EN_TRANSITO = "en_transito"
    COMPLETADA = "completada"
    FALLIDA = "fallida"
    REPROGRAMADA = "reprogramada"
    DEVUELTA = "devuelta"
    CANCELADA = "cancelada"


class Modalidad(enum.Enum):
    """Glosario, entidad 1: de entrega o de recoleccion (CU-01, flujo 3a;
    2026-11-22A). Las dos recorren el mismo ciclo de vida y se planifican
    igual: una Parada donde se entrega o se retira."""

    ENTREGA = "entrega"
    RECOLECCION = "recoleccion"


_TRANSICIONES_VALIDAS: dict[EstadoOrden, frozenset[EstadoOrden]] = {
    EstadoOrden.CREADA: frozenset({EstadoOrden.PLANIFICADA, EstadoOrden.CANCELADA}),
    # El retorno a Creada es la desasignacion que el glosario define en el
    # ciclo de vida: "admite el retorno a Creada ante una desasignacion,
    # mecanismo que soporta la reasignacion de proveedor y la
    # replanificacion".
    EstadoOrden.PLANIFICADA: frozenset(
        {EstadoOrden.EN_TRANSITO, EstadoOrden.CANCELADA, EstadoOrden.CREADA}
    ),
    EstadoOrden.EN_TRANSITO: frozenset({EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA}),
    EstadoOrden.FALLIDA: frozenset({EstadoOrden.REPROGRAMADA, EstadoOrden.DEVUELTA}),
    EstadoOrden.REPROGRAMADA: frozenset({EstadoOrden.PLANIFICADA, EstadoOrden.CANCELADA}),
    EstadoOrden.COMPLETADA: frozenset(),
    EstadoOrden.DEVUELTA: frozenset(),
    EstadoOrden.CANCELADA: frozenset(),
}

# El nombre de cada estado en el glosario, para los mensajes que lee una
# persona: "En transito" y no `en_transito` (2026-10-20A, P24; 2026-10-20C,
# C9). El valor crudo sigue siendo el del contrato.
ETIQUETA_DEL_GLOSARIO: dict[EstadoOrden, str] = {
    EstadoOrden.CREADA: "Creada",
    EstadoOrden.PLANIFICADA: "Planificada",
    EstadoOrden.EN_TRANSITO: "En tránsito",
    EstadoOrden.COMPLETADA: "Completada",
    EstadoOrden.FALLIDA: "Fallida",
    EstadoOrden.REPROGRAMADA: "Reprogramada",
    EstadoOrden.DEVUELTA: "Devuelta",
    EstadoOrden.CANCELADA: "Cancelada",
}

ESTADOS_TERMINALES = frozenset(
    {EstadoOrden.COMPLETADA, EstadoOrden.DEVUELTA, EstadoOrden.CANCELADA}
)


@dataclasses.dataclass(frozen=True)
class ReasignacionDeRuta:
    """De que Ruta y Parada a cuales se mudo una Orden en ejecucion, cuando su
    Ruta se reemplazo (CU-07, paso 6) o se activo una contingencia (CU-11,
    paso 6). La Orden no cambia de estado: el asiento deja la reasignacion en
    el historial (decision D9 del encargo 2026-11-20A)."""

    ruta_anterior: RutaId
    secuencia_anterior: int | None
    ruta_nueva: RutaId
    secuencia_nueva: int
    motivo: str
    """`incidente` (CU-07) o `contingencia` (CU-11)."""


@dataclasses.dataclass
class TransicionRegistrada:
    """Un asiento del historial de transiciones de una Orden (FR-03)."""

    estado_anterior: EstadoOrden | None
    estado_nuevo: EstadoOrden
    marca_temporal: dt.datetime
    declarante: Declarante | None = None
    """Quien declaro el resultado de Parada que produjo esta transicion
    (ARQ-65). None en las demas transiciones y en los resultados declarados
    antes de que existiera el dato."""

    reasignacion: ReasignacionDeRuta | None = None
    """Solo en los asientos de reasignacion de Ruta, en los que el estado
    anterior y el nuevo son el mismo (En transito): no es una transicion del
    ciclo de vida sino el registro de que la Orden cambio de Ruta."""

    campo: AsientoDeCampo | None = None
    """Solo en los asientos que deja un resultado declarado desde la
    aplicacion con su hora (CU-06, 4a; encargo 2026-11-23B, D29). Con el
    campo, `marca_temporal` es la hora del hecho; la de recepcion va aca."""


TOLERANCIA_DE_LA_HORA_DEL_DISPOSITIVO = dt.timedelta(minutes=2)
"""CU-06, flujo 4a (D29): la hora que envia el dispositivo se acepta como hora
del hecho si esta entre el despacho de la Ruta y la recepcion mas esta
tolerancia (relojes de telefono algo adelantados)."""


@dataclasses.dataclass(frozen=True)
class DeclaracionDeCampo:
    """Lo que acompana un resultado de Parada declarado desde la aplicacion
    con su hora (CU-06, flujo 4a; D29): la hora del dispositivo, la de
    recepcion en el servidor, si se declaro sin conexion y sobre que Ruta y
    Parada lo declaro el transportista."""

    declarado_en: dt.datetime
    recibido_en: dt.datetime
    sin_conexion: bool
    ruta_declarada: RutaId
    secuencia_declarada: int


@dataclasses.dataclass(frozen=True)
class AsientoDeCampo:
    """La parte de campo de un asiento del historial (D29): las dos horas, la
    marca sin conexion y la nota que dice que se hizo con la declaracion."""

    declarado_en: dt.datetime
    """La hora que envio el dispositivo, tal como llego."""

    recibido_en: dt.datetime
    sin_conexion: bool
    hora_del_dispositivo_descartada: bool
    """True si la hora del dispositivo quedo fuera de rango y el asiento lleva
    la de recepcion como instante del hecho."""

    aplicada: bool
    nota: str
    ruta_declarada: RutaId
    secuencia_declarada: int
    efecto: str = "aplicada"
    """`aplicada`, `prevalece` o `no_aplicada` (EfectoDeLaDeclaracion)."""

    sobre_otra_ruta: bool = False
    """True si se aplico sobre la Ruta vigente que reemplazo a la declarada (o
    sobre la de contingencia)."""


class EfectoDeLaDeclaracion(enum.Enum):
    """Que le hace una declaracion de campo a una Orden (D29)."""

    APLICADA = "aplicada"
    """La Orden estaba En transito: toma el resultado."""

    PREVALECE = "prevalece"
    """A la hora del hecho la Orden estaba En transito y despues otro
    declarante le asento un resultado distinto que sigue siendo su ultimo
    cambio de estado: la declaracion del transportista prevalece."""

    YA_APLICADA = "ya_aplicada"
    """La Orden ya esta en el estado declarado, o esta misma declaracion ya se
    asento: no cambia nada ni se agrega ningun asiento."""

    NO_APLICADA = "no_aplicada"
    """El ciclo de vida no la admite: el estado no cambia y queda el asiento
    de declaracion de campo no aplicada, con su motivo."""


@dataclasses.dataclass(frozen=True)
class EvaluacionDeCampo:
    efecto: EfectoDeLaDeclaracion
    hecho_en: dt.datetime
    hora_del_dispositivo_descartada: bool
    motivo: str
    sobre_otra_ruta: bool


class DemandaInvalida(ErrorDeDominio):
    """La demanda declarada no es una cantidad de bultos utilizable."""


class AsignacionInvalida(ErrorDeDominio):
    """La Parada que se pretende asignar no es una posicion valida de Ruta."""


class PrioridadInvalida(ErrorDeDominio):
    """La prioridad declarada no es una de las admitidas."""


class PlazoInvalido(ErrorDeDominio):
    """El plazo comprometido no es una fecha."""


class VentanaInvalida(ErrorDeDominio):
    """La ventana horaria no es un intervalo: el inicio no es anterior al fin."""


@dataclasses.dataclass(frozen=True)
class VentanaHoraria:
    """Intervalo admisible para cumplir la Orden (glosario, entidad 9: un par
    de atributos de la Orden, no una entidad). CU-01, paso 2. Se registra y se
    publica; el motor no la usa: la instancia de demostracion es un CVRP sin
    ventanas (B-12)."""

    desde: dt.time
    hasta: dt.time

    def __post_init__(self) -> None:
        if not isinstance(self.desde, dt.time) or not isinstance(self.hasta, dt.time):
            raise VentanaInvalida("La ventana horaria se declara con dos horas del dia")
        if self.desde >= self.hasta:
            raise VentanaInvalida(
                f"La ventana horaria empieza a las {self.desde.isoformat('minutes')} y termina a las "
                f"{self.hasta.isoformat('minutes')}: el inicio tiene que ser anterior al fin"
            )


# La secuencia de Parada arranca en 1 en cada Ruta (ARQ-05).
PRIMERA_SECUENCIA_DE_PARADA = 1


# Una Orden de entrega mueve al menos un bulto: con cero no hay trabajo
# logistico que planificar, y una demanda nula ocuparia una Parada sin
# consumir capacidad, que es una instancia de CVRP mal formada.
DEMANDA_MINIMA_BULTOS = 1


# Prioridad declarada de la Orden, atributo minimo segun el glosario (entidad
# 1): baja, media o alta (B-220, punto 2). Una Orden dada de alta sin
# prioridad, y las de la instancia canonica, que no la tenian, quedan en
# media.
PRIORIDAD_BAJA = 1
PRIORIDAD_MEDIA = 2
PRIORIDAD_ALTA = 3
PRIORIDADES_ADMITIDAS = (PRIORIDAD_BAJA, PRIORIDAD_MEDIA, PRIORIDAD_ALTA)
PRIORIDAD_POR_OMISION = PRIORIDAD_MEDIA


@dataclasses.dataclass(frozen=True)
class PuntoDelAlta:
    """La coordenada con que se dio de alta la Orden: el punto marcado en el
    mapa o el que devolvio la busqueda por direccion (B-259, punto 6). El
    destino es el nodo del grafo al que esa coordenada quedo anclada; esto es
    lo que el operador eligio, antes del anclaje."""

    lat: float
    lon: float


@dataclasses.dataclass
class Orden:
    id: OrdenId
    demanda: int
    """Cantidad de bultos, entero (B-55). Es la magnitud que consume la
    capacidad del Vehiculo, tambien entera. Ni peso ni volumen."""

    destino: int
    """Nodo del grafo vial donde se entrega. El glosario lista el destino
    entre los atributos minimos de la Orden, y ARQ-05 fija que la ubicacion
    se ancla por referencia a un nodo del grafo."""

    estado: EstadoOrden = EstadoOrden.CREADA
    historial: list[TransicionRegistrada] = dataclasses.field(default_factory=list)

    ruta_id: RutaId | None = None
    """Ruta a la que quedo asignada, si esta Planificada (RB-09).

    Es la direccion directa de una relacion que ya existia al reves: la
    Parada sabe que Ordenes entrega. Sin esta, responder "¿en que Ruta quedo
    esta Orden?" obligaria a recorrer todas las Rutas buscandola."""

    secuencia_parada: int | None = None
    """Numero de Parada dentro de esa Ruta, empezando en 1.

    Junto con `ruta_id` forma `(ruta_id, secuencia)`, que es la identidad de
    la Parada segun ARQ-05. No se guarda un identificador de Parada porque
    la Parada no tiene uno: seria una segunda identidad capaz de discrepar
    de la primera."""

    prioridad: int = PRIORIDAD_POR_OMISION
    """Prioridad declarada: PRIORIDAD_BAJA, PRIORIDAD_MEDIA o PRIORIDAD_ALTA.
    Con el atraso respecto del plazo comprometido da la criticidad, un valor
    derivado que calcula Planificacion de Rutas y que no se guarda aca."""

    plazo_comprometido: dt.date | None = None
    """Jornada en la que se comprometio la entrega, si se comprometio una
    (glosario, entidad 1). Una fecha y no un instante: el atraso se cuenta en
    dias contra la fecha de planificacion (B-220, punto 2)."""

    ventana_horaria: VentanaHoraria | None = None
    """Ventana horaria admisible, si se declaro (CU-01, paso 2; 2026-11-20A)."""

    modalidad: Modalidad = Modalidad.ENTREGA
    """CU-01, flujo 3a (2026-11-22A): de entrega, por omision, o de recoleccion."""

    punto_del_alta: PuntoDelAlta | None = None
    """Coordenada del alta, guardada junto al nodo (B-259, punto 6;
    2026-10-27C, G). None en las Ordenes que no se dieron de alta por
    coordenada, como las de la instancia de demostracion."""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.prioridad, int)
            or isinstance(self.prioridad, bool)
            or self.prioridad not in PRIORIDADES_ADMITIDAS
        ):
            raise PrioridadInvalida(
                f"Orden {self.id}: prioridad {self.prioridad!r}, admitidas "
                f"{list(PRIORIDADES_ADMITIDAS)}"
            )
        if self.plazo_comprometido is not None and (
            not isinstance(self.plazo_comprometido, dt.date)
            or isinstance(self.plazo_comprometido, dt.datetime)
        ):
            raise PlazoInvalido(
                f"Orden {self.id}: el plazo comprometido debe ser una fecha"
            )
        if not isinstance(self.demanda, int) or isinstance(self.demanda, bool):
            raise DemandaInvalida(f"Orden {self.id}: la demanda debe ser un entero de bultos")
        if self.demanda < DEMANDA_MINIMA_BULTOS:
            raise DemandaInvalida(
                f"Orden {self.id}: demanda {self.demanda}, minimo {DEMANDA_MINIMA_BULTOS} bulto"
            )
        if not self.historial:
            # Asiento inicial (RB-06, CU-01 paso 6). El glosario define que la
            # Orden registra el historial completo de sus transiciones, y la
            # postcondicion del caso de uso exige que el historial contenga su
            # transicion inicial: sin este asiento, una Orden recien creada
            # tenia historial vacio y la primera entrada aparecia recien al
            # planificar, de modo que no quedaba registro de cuando se creo.
            #
            # `estado_anterior` es None porque no hay estado previo: la Orden
            # no venia de ningun lado. Es distinto de un estado desconocido.
            #
            # Se asienta aca, en el constructor, y no en quien da de alta,
            # porque toda Orden nace por este camino y ninguna deberia poder
            # existir sin su asiento. La condicion sobre el historial deja
            # pasar la reconstruccion desde el almacen, que trae el suyo.
            self.historial.append(
                TransicionRegistrada(
                    estado_anterior=None,
                    estado_nuevo=self.estado,
                    marca_temporal=instante_actual(),
                )
            )

    def transicionar(
        self,
        nuevo_estado: EstadoOrden,
        *,
        ahora: dt.datetime | None = None,
        declarante: Declarante | None = None,
    ) -> None:
        """Aplica una transicion si es valida; si no, levanta TransicionInvalida.
        Toda transicion pasa por aca y toda transicion queda en el historial.

        Sin `ahora`, la marca sale de `instante_actual`, la misma fuente que
        usa el constructor para el asiento inicial: con dos relojes, fijar
        uno en una prueba dejaba al otro corriendo, y el historial podia
        mezclar instantes de las dos fuentes."""
        permitidas = _TRANSICIONES_VALIDAS[self.estado]
        if nuevo_estado not in permitidas:
            raise TransicionInvalida(
                f"Orden {self.id}: no se puede pasar de {self.estado.value} "
                f"a {nuevo_estado.value}"
            )
        marca = ahora if ahora is not None else instante_actual()
        self.historial.append(TransicionRegistrada(self.estado, nuevo_estado, marca, declarante))
        self.estado = nuevo_estado

    def asignar_a_ruta(
        self, ruta_id: RutaId, secuencia: int, *, ahora: dt.datetime | None = None
    ) -> None:
        """Transiciona a Planificada dejando registrado donde quedo (RB-09).

        La transicion y la asignacion ocurren juntas, en una sola operacion,
        porque el glosario define Planificada como que la Orden quedo
        asignada a una Parada dentro de una Ruta: una Orden Planificada sin
        Ruta seria un estado que el ciclo de vida no contempla. Separarlas en
        dos llamadas abriria la ventana para producirlo.
        """
        if secuencia < PRIMERA_SECUENCIA_DE_PARADA:
            raise AsignacionInvalida(
                f"Orden {self.id}: secuencia de Parada {secuencia}, "
                f"la numeracion arranca en {PRIMERA_SECUENCIA_DE_PARADA}"
            )
        self.transicionar(EstadoOrden.PLANIFICADA, ahora=ahora)
        self.ruta_id = ruta_id
        self.secuencia_parada = secuencia

    def reasignar_a_ruta(
        self,
        ruta_nueva: RutaId,
        secuencia: int,
        motivo: str,
        *,
        ahora: dt.datetime | None = None,
    ) -> None:
        """Muda una Orden En transito a la Ruta que reemplaza a la suya, sin
        cambiar su estado, y lo asienta en el historial (CU-07, paso 6; CU-11,
        paso 6; D9).

        Solo En transito: una Orden Planificada se replanifica por descarte
        (B-91) y una terminada ya no tiene Parada pendiente."""
        if self.estado is not EstadoOrden.EN_TRANSITO:
            raise TransicionInvalida(
                f"Orden {self.id}: solo una Orden En transito se reasigna de Ruta; "
                f"esta en {self.estado.value}"
            )
        if secuencia < PRIMERA_SECUENCIA_DE_PARADA:
            raise AsignacionInvalida(
                f"Orden {self.id}: secuencia de Parada {secuencia}, "
                f"la numeracion arranca en {PRIMERA_SECUENCIA_DE_PARADA}"
            )
        if self.ruta_id is None:
            raise AsignacionInvalida(f"Orden {self.id}: no tiene Ruta de la que mudarse")
        marca = ahora if ahora is not None else instante_actual()
        self.historial.append(
            TransicionRegistrada(
                self.estado,
                self.estado,
                marca,
                reasignacion=ReasignacionDeRuta(
                    self.ruta_id, self.secuencia_parada, ruta_nueva, secuencia, motivo
                ),
            )
        )
        self.ruta_id = ruta_nueva
        self.secuencia_parada = secuencia

    def desasignar(self, *, ahora: dt.datetime | None = None) -> None:
        """Devuelve la Orden a Creada, liberandola para replanificarse (FR-03).

        Es el inverso exacto de `asignar_a_ruta`, y por la misma razon hace
        las dos cosas juntas: si la Orden volviera a Creada conservando su
        Ruta, informaria estar asignada a una Ruta a la que ya no pertenece.
        Creada significa, segun el glosario, que la Orden todavia no ha sido
        asignada a una ruta.

        La transicion queda en el historial como cualquier otra: FR-03 exige
        el historial completo, y una desasignacion que no dejara rastro
        borraria el hecho de que la Orden estuvo planificada.
        """
        self.transicionar(EstadoOrden.CREADA, ahora=ahora)
        self.ruta_id = None
        self.secuencia_parada = None

    def evaluar_declaracion_de_campo(
        self,
        destino: EstadoOrden,
        declaracion: DeclaracionDeCampo,
        declarante: Declarante | None,
    ) -> EvaluacionDeCampo:
        """CU-06, flujo 4a, con trazabilidad (D29), sin cambiar nada.

        Hora del hecho: la del dispositivo si esta entre el despacho (el ultimo
        paso de Planificada a En transito) y la recepcion mas la tolerancia; si
        no, la de recepcion, con la del dispositivo descartada.

        Prioridad del transportista: la admisibilidad se mira a la hora del
        hecho. Si a esa hora la Orden estaba En transito y despues otro
        declarante le asento un resultado distinto que sigue siendo su ultimo
        cambio de estado, En transito -> resultado es la transicion que el
        ciclo de vida admite a esa hora, y la declaracion prevalece. Las
        mudanzas por reemplazo o contingencia no cambian el estado: la Orden
        sigue En transito y el resultado se aplica en la Ruta donde este.
        Cualquier otro caso no se aplica."""
        despacho = next(
            (
                t.marca_temporal
                for t in reversed(self.historial)
                if t.estado_anterior is EstadoOrden.PLANIFICADA and t.estado_nuevo is EstadoOrden.EN_TRANSITO
            ),
            None,
        )
        dispositivo = declaracion.declarado_en
        en_rango = (
            despacho is not None
            and despacho <= dispositivo <= declaracion.recibido_en + TOLERANCIA_DE_LA_HORA_DEL_DISPOSITIVO
        )
        hecho = dispositivo if en_rango else declaracion.recibido_en
        descartada = not en_rango
        otra_ruta = self.ruta_id is not None and self.ruta_id != declaracion.ruta_declarada

        def evaluacion(efecto: EfectoDeLaDeclaracion, motivo: str) -> EvaluacionDeCampo:
            return EvaluacionDeCampo(efecto, hecho, descartada, motivo, otra_ruta)

        usuario = declarante.usuario_id if declarante is not None else None
        for t in self.historial:
            if (
                t.campo is not None
                and t.campo.declarado_en == dispositivo
                and (t.declarante.usuario_id if t.declarante is not None else None) == usuario
                and t.campo.ruta_declarada == declaracion.ruta_declarada
                and t.campo.secuencia_declarada == declaracion.secuencia_declarada
            ):
                return evaluacion(EfectoDeLaDeclaracion.YA_APLICADA, "la misma declaracion ya esta asentada")
        if self.estado is destino:
            return evaluacion(EfectoDeLaDeclaracion.YA_APLICADA, f"la Orden ya esta en {destino.value}")
        if self.estado is EstadoOrden.EN_TRANSITO:
            # La Orden tiene que seguir en la Ruta declarada o en la que la
            # reemplazo o la recibio por contingencia (sus asientos de
            # reasignacion); si no, es otro viaje.
            ruta = declaracion.ruta_declarada
            for t in self.historial:
                if t.reasignacion is not None and t.reasignacion.ruta_anterior == ruta:
                    ruta = t.reasignacion.ruta_nueva
            if ruta != self.ruta_id:
                return evaluacion(
                    EfectoDeLaDeclaracion.NO_APLICADA,
                    "declaracion de campo no aplicada: la Orden ya no esta en la Ruta declarada ni en una que "
                    "la reemplace",
                )
            return evaluacion(
                EfectoDeLaDeclaracion.APLICADA,
                f"aplicada sobre la Ruta vigente {self.ruta_id}" if otra_ruta else "aplicada",
            )
        cambios = [t for t in self.historial if t.estado_anterior is not t.estado_nuevo]
        resultado_posterior = next(
            (
                (posicion, t)
                for posicion, t in enumerate(cambios)
                if t.estado_anterior is EstadoOrden.EN_TRANSITO
                and t.estado_nuevo in (EstadoOrden.COMPLETADA, EstadoOrden.FALLIDA)
                and t.marca_temporal > hecho
            ),
            None,
        )
        actual = ETIQUETA_DEL_GLOSARIO[self.estado]
        declarado = ETIQUETA_DEL_GLOSARIO[destino]
        if descartada:
            return evaluacion(
                EfectoDeLaDeclaracion.NO_APLICADA,
                "declaracion de campo no aplicada: la hora del dispositivo se descarto y al recibirse la "
                f"Orden estaba en {actual}; el ciclo de vida no admite pasar a {declarado}",
            )
        if resultado_posterior is None:
            return evaluacion(
                EfectoDeLaDeclaracion.NO_APLICADA,
                f"declaracion de campo no aplicada: la Orden ya estaba en {actual} a la hora declarada; "
                f"el ciclo de vida no admite pasar a {declarado}",
            )
        posicion, posterior = resultado_posterior
        if posicion != len(cambios) - 1:
            return evaluacion(
                EfectoDeLaDeclaracion.NO_APLICADA,
                "declaracion de campo no aplicada: despues del resultado posterior la Orden paso a "
                f"{actual}; el ciclo de vida no admite pasar de {actual} a {declarado}",
            )
        if self.estado in ESTADOS_TERMINALES:
            # Consejo del 23B (E12): un estado terminal no se abandona, tampoco
            # por la prioridad del transportista (Completada lo es; Fallida no).
            return evaluacion(
                EfectoDeLaDeclaracion.NO_APLICADA,
                f"declaracion de campo no aplicada: la Orden ya esta en {actual}, un estado terminal del ciclo de "
                f"vida; no se pasa de ahi a {declarado}",
            )
        autor = posterior.declarante.usuario_id if posterior.declarante is not None else None
        if autor is not None and autor == usuario:
            return evaluacion(
                EfectoDeLaDeclaracion.NO_APLICADA,
                "declaracion de campo no aplicada: el mismo transportista declaro despues otro resultado",
            )
        rol = posterior.declarante.rol if posterior.declarante is not None else "sistema"
        return evaluacion(
            EfectoDeLaDeclaracion.PREVALECE,
            f"prevalece la declaracion de campo sobre el resultado {posterior.estado_nuevo.value} asentado "
            f"despues ({posterior.marca_temporal.isoformat()}, {rol})",
        )

    def aplicar_declaracion_de_campo(
        self,
        destino: EstadoOrden,
        declaracion: DeclaracionDeCampo,
        declarante: Declarante | None,
    ) -> EvaluacionDeCampo:
        """Aplica lo que `evaluar_declaracion_de_campo` decide y lo asienta con
        las dos horas (D29). Ya aplicada no agrega nada."""
        ev = self.evaluar_declaracion_de_campo(destino, declaracion, declarante)
        if ev.efecto is EfectoDeLaDeclaracion.YA_APLICADA:
            return ev
        asiento = AsientoDeCampo(
            declarado_en=declaracion.declarado_en,
            recibido_en=declaracion.recibido_en,
            sin_conexion=declaracion.sin_conexion,
            hora_del_dispositivo_descartada=ev.hora_del_dispositivo_descartada,
            aplicada=ev.efecto is not EfectoDeLaDeclaracion.NO_APLICADA,
            nota=ev.motivo,
            ruta_declarada=declaracion.ruta_declarada,
            secuencia_declarada=declaracion.secuencia_declarada,
            efecto=ev.efecto.value,
            sobre_otra_ruta=ev.sobre_otra_ruta and ev.efecto is not EfectoDeLaDeclaracion.NO_APLICADA,
        )
        if ev.efecto is EfectoDeLaDeclaracion.APLICADA:
            self.transicionar(destino, ahora=ev.hecho_en, declarante=declarante)
            self.historial[-1].campo = asiento
        elif ev.efecto is EfectoDeLaDeclaracion.PREVALECE:
            # A la hora del hecho la Orden estaba En transito: el asiento lo dice
            # (estado anterior En transito) y la nota nombra el resultado que
            # queda sin efecto, que sigue en el historial.
            self.historial.append(
                TransicionRegistrada(EstadoOrden.EN_TRANSITO, destino, ev.hecho_en, declarante, campo=asiento)
            )
            self.estado = destino
        else:
            self.historial.append(
                TransicionRegistrada(self.estado, self.estado, ev.hecho_en, declarante, campo=asiento)
            )
        return ev

    @property
    def esta_asignada(self) -> bool:
        """Si la Orden puede informar su Ruta y su Parada (CU-04, pasos 2 y 4)."""
        return self.ruta_id is not None and self.secuencia_parada is not None

    def reprogramar(self, plazo_comprometido: dt.date | None = None, *, ahora: dt.datetime | None = None) -> None:
        """CU-06, flujo 1a (2026-11-20A): una Orden Fallida aguarda una nueva
        fecha (glosario, estado 6). Deja de estar asignada a la Ruta en que
        fallo y vuelve a ser planificable; con `plazo_comprometido`, ese es el
        plazo nuevo. Al planificarse pasa a Planificada (glosario)."""
        if plazo_comprometido is not None and (
            not isinstance(plazo_comprometido, dt.date) or isinstance(plazo_comprometido, dt.datetime)
        ):
            raise PlazoInvalido(f"Orden {self.id}: el plazo comprometido debe ser una fecha")
        self.transicionar(EstadoOrden.REPROGRAMADA, ahora=ahora)
        self.ruta_id = None
        self.secuencia_parada = None
        if plazo_comprometido is not None:
            self.plazo_comprometido = plazo_comprometido

    def devolver(self, *, ahora: dt.datetime | None = None) -> None:
        """CU-06, flujo 1a: agotados los intentos, la Orden Fallida retorna al
        Deposito (glosario, estado 7, terminal). Cuantos intentos se admiten
        no esta definido: lo decide el operador."""
        self.transicionar(EstadoOrden.DEVUELTA, ahora=ahora)

    def es_planificable(self) -> bool:
        """Creada o, desde 2026-11-20A, Reprogramada (glosario: retorna a
        Planificada una vez replanificada)."""
        return self.estado in (EstadoOrden.CREADA, EstadoOrden.REPROGRAMADA)
