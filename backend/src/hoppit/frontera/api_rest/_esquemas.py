"""Modelos del contrato REST (NFR-06).

Estos modelos son el contrato: lo que se publica en el esquema OpenAPI y lo
que consume quien construye la interfaz. Son una capa de traduccion, no una
copia del dominio -- traducen entidades a payloads, y por eso viven en
frontera y no adentro de los containers (ARQ-N1, NFR-10).

Dos criterios los ordenan:

  - La ausencia se declara. Un campo que todavia no tiene valor va en None y
    lo dice, en vez de un cero o una cadena vacia que se leerian como dato.
  - Nada se ata a una libreria de mapas. Las coordenadas son pares de
    numeros; elegir GeoJSON o polilinea codificada es decision de quien
    dibuja.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable
from typing import Annotated, Literal

import pydantic

from hoppit.cartografia.puerto import Anclaje, OrigenRotulo, RotuloNodo, Trazado
from hoppit.gestion_ordenes.puerto import (
    Modalidad,
    PRIORIDAD_ALTA,
    PRIORIDAD_BAJA,
    PRIORIDAD_POR_OMISION,
    EstadoOrden,
    Orden,
)
from hoppit.planificacion_rutas.puerto import (
    MotivoNoPlanificada,
    ResultadoDeParada,
    OrdenNoPlanificada,
    ParadaPlaneada,
    PlanDeDistribucion,
    RutaPersistida,
)
from hoppit.prediccion_eta.puerto import SORTEOS_DEL_ETA
from hoppit.shared.eventos import Declarante, MedioDeDeclaracion
from hoppit.vigilancia_sla.puerto import (
    AlcanceDelIncidente,
    Alerta,
    Incidente,
    TipoDeAlerta,
    TipoDeIncidente,
)

# Procedencia del ETA, para las descripciones del esquema (B-193, punto 3,
# criterio g; B-19): de donde sale la estimacion y que no es.
_PROCEDENCIA_DEL_ETA = (
    "Es una estimacion, no una prediccion validada: se obtiene simulando "
    f"{SORTEOS_DEL_ETA} recorridos de la Ruta, con semilla fija (B-196). Cada "
    "tramo suma un viaje por la red, la distancia del camino minimo dividida "
    "por una velocidad supuesta, que es un parametro de operacion del "
    "servidor y no un dato medido en el AMBA, y una componente de parada por "
    "Orden, sorteada de la distribucion del intervalo entre finalizaciones de "
    "entregas de LaDe (Cainiao) en Hangzhou a menos de 100 m, por franja "
    "horaria. Son datos de referencia de otra ciudad: la estimacion no esta "
    "validada para el AMBA y no depende del transito. La banda es la del "
    "modelo, no un intervalo calibrado: en datos de LaDe, con distancia en "
    "linea recta, la banda de p10 a p90 cubrio entre el 64 y el 67 % de las "
    "horas reales hasta la quinta entrega de un recorrido, y menos despues, "
    "para un nominal del 80 %; en el AMBA no esta medida."
)
_AUSENCIA_DEL_ETA = (
    "Null si el servidor no tiene configurado el servicio de estimacion, o si "
    "la Ruta necesita una franja horaria o una celda que la tabla no cubre: "
    "no hay valor de reemplazo."
)
_DESCRIPCION_ETA_ESTIMADO = (
    "Segundos desde `hora_de_salida` de la Ruta hasta la hora estimada en que "
    "se completa la primera entrega de esta Parada: la mediana de los "
    "recorridos simulados. Estima la finalizacion de la primera entrega, no "
    "la llegada: incluye la atencion y queda despues de la llegada real. "
    + _PROCEDENCIA_DEL_ETA
    + " "
    + _AUSENCIA_DEL_ETA
)
_DESCRIPCION_ETA_P10 = (
    "Limite inferior de la banda del ETA: el percentil 10 de la hora simulada "
    "en que se completa la primera entrega de esta Parada, en segundos desde "
    "`hora_de_salida`. Misma estimacion que `eta_estimado_s`. "
    + _PROCEDENCIA_DEL_ETA
    + " "
    + _AUSENCIA_DEL_ETA
)
_DESCRIPCION_ETA_P90 = (
    "Limite superior de la banda del ETA: el percentil 90 de la hora simulada "
    "en que se completa la primera entrega de esta Parada, en segundos desde "
    "`hora_de_salida`. Misma estimacion que `eta_estimado_s`. "
    + _PROCEDENCIA_DEL_ETA
    + " "
    + _AUSENCIA_DEL_ETA
)
_DESCRIPCION_HORA_DE_SALIDA = (
    "Hora local de la jornada (America/Argentina/Buenos_Aires) a la que la "
    "Ruta sale del Deposito, desde la que se cuentan el ETA y su banda en cada "
    "Parada. Es un parametro de operacion con que se arranco el servidor, no "
    "un dato registrado de la Ruta. Null si el servidor no tiene configurado "
    "el servicio de estimacion del ETA."
)

# Demanda: entero de bultos, estrictamente positivo (B-55). El limite
# superior no es una regla de negocio sino un tope defensivo del borde: una
# demanda absurda no deberia llegar al solver.
DEMANDA_MAXIMA_ADMITIDA = 10_000

# Rango geografico admisible. No acota a San Isidro a proposito --el partido
# es un parametro del artefacto, no del contrato-- pero rechaza coordenadas
# imposibles antes de buscar un nodo cercano.
LATITUD_MINIMA, LATITUD_MAXIMA = -90.0, 90.0
LONGITUD_MINIMA, LONGITUD_MAXIMA = -180.0, 180.0

Demanda = Annotated[int, pydantic.Field(ge=1, le=DEMANDA_MAXIMA_ADMITIDA)]
Prioridad = Annotated[int, pydantic.Field(ge=PRIORIDAD_BAJA, le=PRIORIDAD_ALTA)]
Latitud = Annotated[float, pydantic.Field(ge=LATITUD_MINIMA, le=LATITUD_MAXIMA)]
Longitud = Annotated[float, pydantic.Field(ge=LONGITUD_MINIMA, le=LONGITUD_MAXIMA)]


class Rotulo(pydantic.BaseModel):
    """Como se nombra un punto (RB-02)."""

    texto: str
    origen: OrigenRotulo
    calles: tuple[str, ...]

    @classmethod
    def desde(cls, rotulo: RotuloNodo) -> Rotulo:
        return cls(texto=rotulo.texto, origen=rotulo.origen, calles=rotulo.calles)


class Punto(pydantic.BaseModel):
    """Un nodo del grafo vial con su ubicacion y su rotulo."""

    nodo: int
    lat: float
    lon: float
    rotulo: Rotulo

    @classmethod
    def desde(cls, rotulo: RotuloNodo) -> Punto:
        return cls(
            nodo=rotulo.nodo,
            lat=rotulo.lat,
            lon=rotulo.lon,
            rotulo=Rotulo.desde(rotulo),
        )


class DeclarantePublicado(pydantic.BaseModel):
    """Quien declaro un resultado de Parada (ARQ-65)."""

    usuario_id: str
    nombre_visible: str | None
    """El nombre visible de hoy; null si el usuario ya no existe."""

    rol: str
    """El rol al momento de declarar."""

    medio: MedioDeDeclaracion

    @classmethod
    def desde(
        cls, declarante: Declarante, nombre_visible: Callable[[str], str | None]
    ) -> DeclarantePublicado:
        return cls(
            usuario_id=declarante.usuario_id,
            nombre_visible=nombre_visible(declarante.usuario_id),
            rol=declarante.rol,
            medio=declarante.medio,
        )


def _sin_nombres(_usuario_id: str) -> str | None:
    return None


def _ausente(valor: object) -> bool:
    """Para los campos que aparecen solo cuando corresponden (D11 del encargo
    2026-11-20A): sin valor, el campo no se publica, y el cuerpo de lo que no
    cambio queda byte a byte igual (NFR-07)."""
    return valor is None


class ReasignacionPublicada(pydantic.BaseModel):
    """De que Ruta a cual se mudo la Orden al reemplazarse su Ruta (CU-07) o al
    activarse una contingencia (CU-11), sin cambiar de estado (D9)."""

    ruta_anterior: str
    secuencia_anterior: int | None
    ruta_nueva: str
    secuencia_nueva: int
    motivo: str


class DeclaracionDeCampoPublicada(pydantic.BaseModel):
    """La parte de campo de un asiento (CU-06, 4a; D29)."""

    declarado_en: dt.datetime
    """La hora que envio el dispositivo, tal como llego."""

    recibido_en: dt.datetime
    sin_conexion: bool
    hora_del_dispositivo_descartada: bool
    """True si la hora del dispositivo quedo fuera de rango (antes del despacho
    o mas de 2 min despues de la recepcion): el asiento lleva la de recepcion."""

    aplicada: bool
    """False en una declaracion de campo no aplicada: el estado no cambio."""

    nota: str
    ruta_declarada: str
    secuencia_declarada: int


class TransicionPublicada(pydantic.BaseModel):
    """Un asiento del historial de la Orden (FR-03)."""

    estado_anterior: EstadoOrden | None
    estado_nuevo: EstadoOrden
    marca_temporal: dt.datetime
    declarante: DeclarantePublicado | None = None
    """Quien declaro el resultado, en la transicion a Completada o Fallida;
    null en las demas."""

    reasignacion: ReasignacionPublicada | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Solo en los asientos de reasignacion de Ruta (estado anterior y nuevo
    iguales, En transito). Ausente en los demas."""

    declaracion_de_campo: DeclaracionDeCampoPublicada | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Solo en los asientos de un resultado declarado desde la aplicacion con su
    hora (CU-06, 4a; encargo 2026-11-23B, D29): `marca_temporal` es la hora del
    hecho y aca van la de recepcion, la marca sin conexion y la nota. Ausente
    en los demas."""


class AsignacionPublicada(pydantic.BaseModel):
    """Donde quedo una Orden planificada (RB-09, CU-04 pasos 2 y 4)."""

    ruta_id: str
    secuencia_parada: int
    """Numero de Parada dentro de la Ruta, empezando en 1."""


class OrdenPublicada(pydantic.BaseModel):
    """Una Orden tal como la ve la interfaz (CU-01, CU-04)."""

    id: str
    demanda: int
    estado: EstadoOrden
    destino: Punto
    asignacion: AsignacionPublicada | None
    """None mientras la Orden no este planificada: todavia no hay Ruta que
    informar, y un identificador vacio se leeria como una Ruta que existe."""

    historial: tuple[TransicionPublicada, ...]
    prioridad: int
    """Prioridad declarada: 1 (baja), 2 (media) o 3 (alta)."""

    plazo_comprometido: dt.date | None
    """Jornada comprometida para la entrega; null si no se comprometio una."""

    punto_del_alta: Coordenada | None = None
    """La coordenada con que se dio de alta: el punto marcado o el de la
    direccion elegida, antes de anclarla al nodo de `destino` (B-259, punto
    6). null en las Ordenes que no se dieron de alta por coordenada."""

    ventana_horaria: VentanaPublicada | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Ventana horaria admisible (CU-01, paso 2). Ausente si no se declaro."""

    modalidad: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """CU-01, flujo 3a (2026-11-22A): `recoleccion`. Ausente en una Orden de
    entrega, que es la modalidad por omision."""

    @classmethod
    def desde(
        cls,
        orden: Orden,
        destino: RotuloNodo,
        nombre_visible: Callable[[str], str | None] = _sin_nombres,
    ) -> OrdenPublicada:
        asignacion = None
        if orden.ruta_id is not None and orden.secuencia_parada is not None:
            asignacion = AsignacionPublicada(
                ruta_id=str(orden.ruta_id),
                secuencia_parada=orden.secuencia_parada,
            )
        return cls(
            id=str(orden.id),
            demanda=orden.demanda,
            estado=orden.estado,
            destino=Punto.desde(destino),
            asignacion=asignacion,
            historial=tuple(
                TransicionPublicada(
                    estado_anterior=t.estado_anterior,
                    estado_nuevo=t.estado_nuevo,
                    marca_temporal=t.marca_temporal,
                    declarante=(
                        None
                        if t.declarante is None
                        else DeclarantePublicado.desde(t.declarante, nombre_visible)
                    ),
                    declaracion_de_campo=(
                        None
                        if t.campo is None
                        else DeclaracionDeCampoPublicada(
                            declarado_en=t.campo.declarado_en,
                            recibido_en=t.campo.recibido_en,
                            sin_conexion=t.campo.sin_conexion,
                            hora_del_dispositivo_descartada=t.campo.hora_del_dispositivo_descartada,
                            aplicada=t.campo.aplicada,
                            nota=t.campo.nota,
                            ruta_declarada=str(t.campo.ruta_declarada),
                            secuencia_declarada=t.campo.secuencia_declarada,
                        )
                    ),
                    reasignacion=(
                        None
                        if t.reasignacion is None
                        else ReasignacionPublicada(
                            ruta_anterior=str(t.reasignacion.ruta_anterior),
                            secuencia_anterior=t.reasignacion.secuencia_anterior,
                            ruta_nueva=str(t.reasignacion.ruta_nueva),
                            secuencia_nueva=t.reasignacion.secuencia_nueva,
                            motivo=t.reasignacion.motivo,
                        )
                    ),
                )
                for t in orden.historial
            ),
            prioridad=orden.prioridad,
            plazo_comprometido=orden.plazo_comprometido,
            punto_del_alta=(
                None
                if orden.punto_del_alta is None
                else Coordenada(lat=orden.punto_del_alta.lat, lon=orden.punto_del_alta.lon)
            ),
            ventana_horaria=(
                None
                if orden.ventana_horaria is None
                else VentanaPublicada(desde=orden.ventana_horaria.desde, hasta=orden.ventana_horaria.hasta)
            ),
            modalidad=None if orden.modalidad is Modalidad.ENTREGA else orden.modalidad.value,
        )


class VentanaPublicada(pydantic.BaseModel):
    """Ventana horaria admisible de una Orden (CU-01, paso 2; glosario,
    entidad 9), en hora local de la operacion."""

    model_config = pydantic.ConfigDict(extra="forbid")

    desde: dt.time
    hasta: dt.time


class AltaDeOrden(pydantic.BaseModel):
    """Alta de una Orden de entrega (CU-01).

    El destino llega como coordenada y no como nodo del grafo: quien opera
    marca un punto en el mapa, y traducirlo al nodo mas cercano de la red
    vial es responsabilidad del sistema, no de la interfaz. El nodo elegido
    vuelve en la respuesta, de modo que se pueda ver a que punto de la calle
    quedo anclada la Orden.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    lat: Latitud
    lon: Longitud
    demanda: Demanda
    prioridad: Prioridad = PRIORIDAD_POR_OMISION
    """1 (baja), 2 (media) o 3 (alta). Omitida, media (B-220, punto 2)."""

    plazo_comprometido: dt.date | None = None
    """Jornada comprometida para la entrega, si se comprometio una. Con la
    prioridad da la criticidad con la que se planifica cuando no entran
    todas las Ordenes."""

    ventana_horaria: VentanaPublicada | None = None
    """Ventana horaria admisible, opcional (CU-01, paso 2). Se registra y se
    publica; el motor no la usa (CVRP sin ventanas, B-12)."""

    modalidad: Literal["entrega", "recoleccion"] = "entrega"
    """CU-01, flujo 3a (2026-11-22A): de entrega, por omision, o de
    recoleccion. Se planifica igual: una Parada donde se entrega o se retira."""


class OrdenDeParada(pydantic.BaseModel):
    """Una Orden vista desde la Parada que la entrega."""

    id: str
    demanda: int
    estado: EstadoOrden
    """Estado vigente, del catalogo canonico del glosario.

    Viaja aca aunque el modelo lo implique --una Orden que aparece en una Ruta
    esta Planificada-- porque implicarlo no es informarlo: quien consume la
    respuesta tendria que deducirlo, y deducir un estado es afirmarlo sin
    dato. Sin este campo hacia falta una consulta adicional al listado de
    Ordenes solo para mostrarlo."""


@dataclasses.dataclass(frozen=True)
class EtaPublicado:
    """El ETA de una Parada en la unidad del contrato: segundos desde la hora
    de salida de la Ruta."""

    mediana_s: float
    p10_s: float
    p90_s: float


class ParadaPublicada(pydantic.BaseModel):
    """Una visita dentro de la Ruta (ARQ-05).

    Su identidad es `(ruta_id, secuencia)` y por eso no lleva identificador
    propio. Puede agrupar varias Ordenes: el Vehiculo hace una sola visita y
    entrega todas (relacion 3 del glosario).
    """

    secuencia: int
    punto: Punto
    ordenes: tuple[OrdenDeParada, ...]
    demanda: int
    """Suma de la demanda de las Ordenes que agrupa."""

    eta_estimado_s: Annotated[
        float | None, pydantic.Field(description=_DESCRIPCION_ETA_ESTIMADO)
    ]
    """Se calcula al responder y no se persiste (B-193, punto 3, criterio d):
    la columna de la base sigue en None. Ausente se declara, no como cero."""

    eta_p10_s: Annotated[float | None, pydantic.Field(description=_DESCRIPCION_ETA_P10)]
    eta_p90_s: Annotated[float | None, pydantic.Field(description=_DESCRIPCION_ETA_P90)]

    @classmethod
    def desde(
        cls,
        parada: ParadaPlaneada,
        punto: RotuloNodo,
        ordenes_del_sistema: dict[str, Orden],
        eta: EtaPublicado | None = None,
    ) -> ParadaPublicada:
        ordenes = tuple(
            OrdenDeParada(
                id=str(identificador),
                demanda=ordenes_del_sistema[str(identificador)].demanda,
                estado=ordenes_del_sistema[str(identificador)].estado,
            )
            for identificador in parada.ordenes
            # Una Orden que la Ruta referencia y que ya no existe en el
            # sistema se omite en vez de publicarse con datos inventados. No
            # deberia ocurrir --la desasignacion elimina la Ruta entera-- y si
            # ocurre, omitirla es preferible a afirmar una demanda que no se
            # conoce.
            if str(identificador) in ordenes_del_sistema
        )
        return cls(
            secuencia=parada.secuencia,
            punto=Punto.desde(punto),
            ordenes=ordenes,
            demanda=sum(orden.demanda for orden in ordenes),
            eta_estimado_s=None if eta is None else eta.mediana_s,
            eta_p10_s=None if eta is None else eta.p10_s,
            eta_p90_s=None if eta is None else eta.p90_s,
        )


class TrazadoPublicado(pydantic.BaseModel):
    """Geometria dibujable del recorrido (FR-02, NFR-08).

    `coordenadas` es la polilinea completa, en pares (latitud, longitud) y en
    el orden del recorrido: incluye los puntos intermedios de cada calle, no
    solo las Paradas. Dibujar rectas entre Paradas mostraria segmentos que no
    existen en la red vial.
    """

    coordenadas: tuple[tuple[float, float], ...]
    distancia_m: float
    arcos: int

    @classmethod
    def desde(cls, trazado: Trazado) -> TrazadoPublicado:
        return cls(
            coordenadas=trazado.coordenadas,
            distancia_m=trazado.distancia_m,
            arcos=trazado.arcos,
        )


class RutaEnListado(pydantic.BaseModel):
    """Una Ruta sin su geometria, para listar.

    El trazado son cientos de coordenadas por Ruta y no entra en el listado:
    quien dibuja pide el detalle de la Ruta que va a mostrar. El resto de los
    campos son los mismos, de modo que el listado alcance para las tarjetas
    y el resumen.
    """

    id: str
    plan_id: str
    """Plan de Distribucion del que la Ruta forma parte (glosario, entidad
    13). Es la corrida que la produjo."""

    vehiculo_id: str
    fecha_planificacion: dt.date
    distancia_m: float
    carga: int
    capacidad_vehiculo: int
    """Capacidad con la que se resolvio esta Ruta, en bultos.

    Es el denominador de la ocupacion. Sin este campo, la carga se publicaba
    sin con que compararla, y derivarla dividiendo la capacidad total de la
    Flota por la cantidad de Vehiculos supondria una flota homogenea, que no
    esta declarado en ninguna parte."""

    cantidad_paradas: int
    cantidad_ordenes: int
    reemplazada_por: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """La Ruta que la reemplazo (CU-07; glosario, estado "Reemplazada").
    Ausente en una Ruta no reemplazada."""

    reemplaza_a: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """La Ruta a la que reemplaza, si nacio de un reemplazo. Ausente en las
    demas."""

    @classmethod
    def desde(cls, persistida: RutaPersistida) -> RutaEnListado:
        return cls(
            id=str(persistida.ruta.id),
            plan_id=str(persistida.plan_id),
            vehiculo_id=str(persistida.ruta.vehiculo_id),
            fecha_planificacion=persistida.fecha_planificacion,
            distancia_m=persistida.ruta.distancia_m,
            carga=persistida.ruta.carga,
            capacidad_vehiculo=persistida.ruta.capacidad_vehiculo,
            cantidad_paradas=len(persistida.ruta.paradas),
            cantidad_ordenes=len(persistida.ruta.ordenes),
        )


class RutaPublicada(pydantic.BaseModel):
    """Una Ruta completa, con sus Paradas y su geometria (CU-03)."""

    id: str
    plan_id: str
    """Plan de Distribucion del que forma parte (glosario, entidad 13)."""

    vehiculo_id: str
    fecha_planificacion: dt.date
    hora_de_salida: Annotated[
        dt.time | None, pydantic.Field(description=_DESCRIPCION_HORA_DE_SALIDA)
    ]
    """Parametro del ensamblado, no entidad del dominio (B-193, punto 3,
    criterio c; B-134)."""

    distancia_m: float
    carga: int
    capacidad_vehiculo: int
    """Capacidad con la que se resolvio esta Ruta, en bultos. Denominador de
    la ocupacion."""

    deposito: Punto
    """Punto del que la Ruta parte y al que vuelve."""

    paradas: tuple[ParadaPublicada, ...]
    trazado: TrazadoPublicado
    reemplazada_por: ReemplazoPublicado | None = pydantic.Field(default=None, exclude_if=_ausente)
    """CU-07, paso 6: la Ruta esta Reemplazada (estado terminal del glosario) y
    conserva sus Paradas cumplidas. Ausente en una Ruta no reemplazada."""

    reemplaza_a: ReemplazoPublicado | None = pydantic.Field(default=None, exclude_if=_ausente)
    """La Ruta nacio de un reemplazo: su recorrido empieza en el origen del
    recalculo y no en el Deposito. Ausente en las demas."""

    de_contingencia: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """CU-11, paso 6: la Ruta es la de contingencia que se activo (el
    identificador de la contingencia); sale del Deposito en el instante de la
    activacion. Ausente en las demas."""

    contingencias_activadas: tuple[str, ...] | None = pydantic.Field(default=None, exclude_if=_ausente)
    """CU-11, paso 6: contingencias activadas sobre esta Ruta; las Ordenes de
    sus Paradas comprometidas pertenecen ahora a la Ruta de contingencia y la
    Ruta sigue con las que le quedan. Ausente si no hubo ninguna."""

    mu_de_los_caminos_m: float | None = pydantic.Field(default=None, exclude_if=_ausente)
    """D36 (2026-11-23F): si la Ruta nacio de una propuesta por riesgo vial (o de
    un recalculo o una contingencia de una Ruta asi), el mu con que se trazan
    sus caminos, se estima su ETA y se recalcula (metros por hecho vial anual
    predicho). Ausente en las demas."""


class ReemplazoPublicado(pydantic.BaseModel):
    """Lo que queda asentado de un reemplazo de Ruta (CU-07, paso 6)."""

    ruta_reemplazada: str
    ruta_nueva: str
    motivo: str
    """`incidente` (CU-07), `contingencia` (CU-11), `desvio` (2026-11-22A) o
    `riesgo_vial` (2026-11-23E)."""

    referencia: str
    """El Incidente o la contingencia que lo motivo."""

    propuesta_id: str | None
    instante: dt.datetime
    origen: Punto
    """Desde donde se recalculo: la posicion del Vehiculo anclada o, sin
    posicion reciente, la Parada en curso."""

    fuente_del_origen: str
    paradas_cumplidas: tuple[int, ...]
    """Secuencias de la Ruta reemplazada ya cumplidas al reemplazarse."""
    mu_m: float | None = pydantic.Field(default=None, exclude_if=_ausente)
    """D36 (2026-11-23F): el mu guardado con la Ruta nueva. Ausente con los
    caminos del proceso."""


class AvancePublicado(pydantic.BaseModel):
    """CU-07, paso 2: el avance vigente de una Ruta en ejecucion."""

    ruta_id: str
    paradas_cumplidas: tuple[int, ...]
    parada_en_curso: int | None
    """La pendiente de menor secuencia: la proxima del plan."""

    paradas_pendientes: tuple[int, ...]
    """Las pendientes sin la en curso, por secuencia."""


class PropuestaSolicitada(pydantic.BaseModel):
    """Pedido de recalculo de una Ruta afectada por el Incidente (CU-07,
    pasos 2 y 3)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    ruta_id: str


class OrigenPublicado(pydantic.BaseModel):
    punto: Punto
    fuente: str
    """`telemetria` (posicion de a lo sumo 120 s, anclada al grafo) o
    `parada_en_curso` (sin posicion reciente)."""

    antiguedad_s: float | None
    distancia_del_anclaje_m: float | None


class ParadaComparada(pydantic.BaseModel):
    """Una Parada pendiente en uno de los dos planes, con su secuencia en la
    Ruta vigente."""

    secuencia_vigente: int
    punto: Punto


class CompromisoDeRiesgoVialPublicado(pydantic.BaseModel):
    """Propuesta con motivo `riesgo_vial` (D31, encargo 2026-11-23E): el
    compromiso medido sobre los caminos reales de lo pendiente, del origen al
    Deposito. La exposicion es la suma de la frecuencia anual predicha de
    hechos viales de los tramos recorridos (hechos por anio): una prediccion
    anual y estatica por tramo, no una deteccion en tiempo real."""

    mu_m: float
    """Metros por hecho vial anual predicho con que se calculo la alternativa."""
    distancia_vigente_m: float
    distancia_propuesta_m: float
    minutos_vigentes: float
    """Suma del tiempo base de los arcos; no es una estimacion de ETA."""
    minutos_propuestos: float
    exposicion_vigente: float
    exposicion_propuesta: float
    baja_de_exposicion: float
    """Fraccion: 1 - propuesta / vigente."""
    suba_de_distancia: float
    """Fraccion: propuesta / vigente - 1."""
    baja_minima_de_exposicion: float
    suba_maxima_de_distancia: float
    tabla: str
    tabla_sha256: str


class PropuestaPublicada(pydantic.BaseModel):
    """CU-07, paso 4: el plan resultante y su diferencia contra el vigente."""

    id: str
    ruta_id: str
    motivo: str
    incidente_id: str
    creada_en: dt.datetime
    estado: str
    """`pendiente`, `aprobada`, `rechazada` o `vencida` (una propuesta por
    riesgo vial pendiente cuando aparece la de un desvio o de un Incidente;
    2026-11-23E)."""

    origen: OrigenPublicado
    paradas_cumplidas: tuple[int, ...]
    parada_en_curso: int | None
    plan_vigente: tuple[ParadaComparada, ...]
    """Lo pendiente en el orden vigente, con la en curso primera."""

    plan_propuesto: tuple[ParadaComparada, ...]
    """Lo pendiente en el orden recalculado, con la en curso primera."""

    paradas_que_cambian_de_lugar: tuple[int, ...]
    """Secuencias vigentes que en el plan propuesto quedan en otro lugar."""

    distancia_vigente_m: float
    """Del origen al Deposito, recorriendo lo pendiente en el orden vigente."""

    distancia_propuesta_m: float
    diferencia_m: float
    """Propuesta menos vigente: negativa si el recalculo acorta."""

    metodo: str
    segundos_de_calculo: float
    resuelta_en: dt.datetime | None
    ruta_nueva: str | None
    fija_en_curso: bool = True
    """False en el reruteo por desvio (D19): la Parada en curso tambien se recalculo."""

    orden_sin_cambios: bool = False
    """El recalculo dio la misma secuencia que la vigente."""

    contingencias_aplicables: tuple[str, ...] | None = pydantic.Field(default=None, exclude_if=_ausente)
    """CU-07, flujo 5b: con la propuesta pendiente, las contingencias
    aplicables a la Ruta, por prioridad; el operador puede optar por una en
    lugar del recalculo (CU-11). Ausente si no hay ninguna."""

    riesgo_vial: CompromisoDeRiesgoVialPublicado | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Solo con motivo `riesgo_vial` (D31, 2026-11-23E). Ausente en las demas."""

    motivo_del_vencimiento: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Con estado `vencida`: por que vencio (otra propuesta que prevalece, un
    Incidente sobre la Ruta o cuya zona cruza el camino propuesto, o un cambio
    del avance; D35, 2026-11-23F). Ausente en las demas."""


class PlanificacionSolicitada(pydantic.BaseModel):
    """Disparo de una planificacion (CU-02).

    Los tres campos son opcionales y significan "todo lo disponible" cuando
    se omiten, que es el caso corriente de la pantalla: planificar lo que hay
    con la Flota entera.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    deposito: int | None = None
    """Nodo del grafo. El Deposito es parametro de cada planificacion (B-59);
    omitido, se usa el de la instancia vigente."""

    ordenes: tuple[str, ...] | None = None
    """Subconjunto de Ordenes a planificar. Omitido, entran todas las
    planificables."""

    depositos_por_vehiculo: dict[str, int] | None = None
    """CU-02, flujo 1a (2026-11-22A): el Deposito (nodo del grafo) del que sale
    y al que vuelve cada Vehiculo nombrado; los demas usan `deposito`. Omitido,
    todos usan el mismo, como siempre."""

    vehiculos: dict[str, Demanda] | None = None
    """Capacidad en bultos por Vehiculo. Omitido, se usa la Flota vigente.
    Cada Vehiculo tiene que ser de la Flota y llevar su capacidad nominal, tal
    como las publica /v1/flota."""


class OrdenNoPlanificadaPublicada(pydantic.BaseModel):
    """Una Orden que el Plan parcial dejo afuera. Sigue en Creada."""

    orden: str
    criticidad: int
    """Prioridad declarada mas el atraso respecto del plazo comprometido,
    acotado a 2 dias, a la fecha de planificacion: de 1 a 5. Cuando no
    entran todas, el motor deja afuera primero las de menor criticidad, de
    forma aproximada."""

    motivo: MotivoNoPlanificada

    @classmethod
    def desde(cls, fuera: OrdenNoPlanificada) -> OrdenNoPlanificadaPublicada:
        return cls(orden=str(fuera.orden), criticidad=fuera.criticidad, motivo=fuera.motivo)


class PlanPublicado(pydantic.BaseModel):
    """Resultado de una planificacion: el Plan y una Ruta por Vehiculo.

    El Plan de Distribucion que la corrida produjo viaja en `plan`, con su
    identidad y sus totales, y se puede volver a pedir por /v1/planes; las
    Rutas, por /v1/rutas. Esta respuesta en si no se persiste.
    """

    plan: PlanEnListado
    """El Plan que la corrida produjo, con su identidad y sus totales
    (glosario, entidad 13). Es lo que permite volver a consultarla."""

    fecha_planificacion: dt.date
    distancia_total_m: float
    demanda_total: int
    capacidad_total: int
    rutas: tuple[RutaEnListado, ...]
    ordenes_no_planificadas: tuple[OrdenNoPlanificadaPublicada, ...]
    """Las Ordenes que el Plan deja afuera, con su criticidad y su motivo;
    vacia si se planificaron todas (B-220, punto 2). Un Plan parcial sale
    con 201 y esta lista no vacia; siguen en Creada y se pueden planificar
    en otra corrida."""


class DespachoAplicado(pydantic.BaseModel):
    """Resultado de despachar una Ruta (FR-16).

    La Ruta no tiene estados (B-96): lo que el despacho cambia es el estado
    de sus Ordenes, que pasan a En transito con la transicion en su
    historial. La respuesta lo informa."""

    ruta_id: str
    ordenes: tuple[str, ...]
    estado: EstadoOrden
    """El estado en que quedaron las Ordenes de la Ruta: en_transito."""
    transportista: str | None = None
    """El transportista asignado al despachar, si se asigno (D-C2; 2026-10-27B, T)."""


class DespachoSolicitado(pydantic.BaseModel):
    """Cuerpo opcional del despacho (D-C2; 2026-10-27B, T). Sin cuerpo, o sin
    transportista, el despacho funciona como antes."""

    model_config = pydantic.ConfigDict(extra="forbid")

    transportista: str | None = None
    """Identificador de un usuario activo con rol transportista."""


class TransportistaPublicado(pydantic.BaseModel):
    """Un transportista que se puede asignar al despachar (D-C2)."""

    id: str
    nombre_visible: str


class RutaEnEjecucionPublicada(pydantic.BaseModel):
    """La Ruta que el transportista tiene en ejecucion, o ninguna
    (2026-10-27B, T). `ruta` en null quiere decir que no tiene ninguna Ruta
    despachada con Ordenes sin terminar: la aplicacion deja de capturar."""

    ruta: RutaPublicada | None
    incidentes: tuple[IncidentePublicado, ...] = ()
    """Los Incidentes declarados en la jornada de la Ruta que la afectan."""


class ResultadoDeParadaSolicitado(pydantic.BaseModel):
    """Lo que el transportista declara al intentar una Parada (CU-06)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    resultado: ResultadoDeParada
    declarado_en: pydantic.AwareDatetime | None = None
    """CU-06, flujo 4a (encargo 2026-11-23B, D29), solo desde la aplicacion del
    transportista: la hora del dispositivo en que se declaro, ISO 8601 con
    zona. Si esta entre el despacho y la recepcion mas 2 min, el historial la
    asienta como hora del hecho y la de recepcion aparte; si no, se usa la de
    recepcion y se asienta que la del dispositivo se descarto. Con este campo,
    la declaracion prevalece sobre un resultado distinto que otro declarante
    haya asentado despues, si a la hora del hecho la Orden estaba En transito
    y ese resultado es su ultimo cambio de estado; si la Ruta se reemplazo
    mientras tanto, se aplica sobre la vigente. Sin el campo, todo es como
    antes."""

    sin_conexion: bool | None = None
    """True si se declaro sin conexion y se envia desde la cola local."""


class ResultadoDeParadaAplicado(pydantic.BaseModel):
    """Resultado de declarar una Parada (CU-06, pasos 1 a 3)."""

    ruta_id: str
    secuencia: int
    resultado: ResultadoDeParada
    ordenes: tuple[str, ...]
    """Las Ordenes de la Parada: todas toman el mismo resultado."""

    estado: EstadoOrden
    """El estado en que quedaron: completada o fallida."""

    declarante: DeclarantePublicado
    """Quien declaro: el usuario de la sesion, con su rol y el medio."""


class ZonaAfectadaSolicitada(pydantic.BaseModel):
    """Donde esta el problema (CU-07; encargo 2026-11-23B, bloque K): un punto y
    un radio. La propuesta de recalculo de este Incidente no usa los tramos
    cuyo trazado pasa a menos del radio del punto."""

    model_config = pydantic.ConfigDict(extra="forbid")

    lat: float = pydantic.Field(ge=-90.0, le=90.0)
    lon: float = pydantic.Field(ge=-180.0, le=180.0)
    radio_m: float = pydantic.Field(ge=20.0, le=1000.0, description="Entre 20 y 1.000 m.")


class ZonaAfectadaPublicada(pydantic.BaseModel):
    lat: float
    lon: float
    radio_m: float


class IncidenteSolicitado(pydantic.BaseModel):
    """Lo que el operador declara al registrar un Incidente (FR-18; CU-07,
    paso 1).

    `tipo` y `alcance` llegan como texto y los valida el dominio, que
    responde con su propio codigo (`tipo_de_incidente_invalido`,
    `alcance_invalido`) y no con un rechazo de esquema: la regla es de
    Vigilancia de SLA e Incidentes, no del adaptador. Los valores admitidos
    se publican en los catalogos `TipoDeIncidente` y `AlcanceDelIncidente`
    de la respuesta.
    """

    model_config = pydantic.ConfigDict(extra="forbid")

    tipo: str = pydantic.Field(
        description="Uno de: " + ", ".join(t.value for t in TipoDeIncidente) + " (glosario, entidad 8)."
    )
    alcance: str = pydantic.Field(
        description="Uno de: " + ", ".join(a.value for a in AlcanceDelIncidente)
        + ": el Incidente invalida total o parcialmente el plan vigente."
    )
    descripcion: str = ""
    rutas_afectadas: tuple[str, ...] = ()
    zona_afectada: ZonaAfectadaSolicitada | None = None
    """Opcional (bloque K, 2026-11-23B): sin zona, el recalculo es el de siempre."""
    """Cero o mas Rutas (glosario, relacion 6), que tienen que existir. Una
    Ruta declarada dos veces cuenta una."""


class IncidenteDelTransportistaSolicitado(pydantic.BaseModel):
    """Lo que el transportista declara desde su aplicacion: un problema en la
    calle sobre su Ruta (FR-18). El alcance es siempre parcial y la Ruta
    afectada, la suya: no declara sobre el plan entero ni sobre otras."""

    model_config = pydantic.ConfigDict(extra="forbid")

    tipo: str = pydantic.Field(
        description="Uno de: " + ", ".join(t.value for t in TipoDeIncidente) + " (glosario, entidad 8)."
    )
    ruta: str = pydantic.Field(description="La Ruta despachada a este transportista.")
    descripcion: str = ""
    zona_afectada: ZonaAfectadaSolicitada | None = None
    """Opcional (bloque K, 2026-11-23B): por ejemplo, la posicion del Vehiculo
    y un radio."""


class IncidentePublicado(pydantic.BaseModel):
    """Un Incidente registrado (glosario, entidad 8). Registrarlo no cambia
    ninguna Ruta ni ninguna Orden: el recalculo (FR-05; CU-07, desde el paso
    2) no esta construido."""

    id: str
    tipo: TipoDeIncidente
    alcance: AlcanceDelIncidente
    descripcion: str
    rutas_afectadas: tuple[str, ...]
    declarado_en: dt.datetime
    """Instante de la declaracion, en UTC."""

    zona_afectada: ZonaAfectadaPublicada | None = pydantic.Field(default=None, exclude_if=_ausente)
    """Solo si se declaro (bloque K, 2026-11-23B). Ausente en los demas."""

    @classmethod
    def desde(cls, incidente: Incidente) -> IncidentePublicado:
        return cls(
            id=str(incidente.id),
            tipo=incidente.tipo,
            alcance=incidente.alcance,
            descripcion=incidente.descripcion,
            rutas_afectadas=tuple(str(r) for r in incidente.rutas_afectadas),
            declarado_en=incidente.declarado_en,
            zona_afectada=(
                None
                if incidente.zona is None
                else ZonaAfectadaPublicada(lat=incidente.zona.lat, lon=incidente.zona.lon,
                                           radio_m=incidente.zona.radio_m)
            ),
        )


class AlertaPublicada(pydantic.BaseModel):
    """Una alerta derivada (CU-08; 2026-10-27B, A): una condicion vigente, que
    se calcula al consultar y no se persiste. Consultarla no cambia ninguna
    Orden ni ninguna Ruta."""

    tipo: TipoDeAlerta
    motivo: str
    desde: dt.datetime
    """Desde cuando rige la condicion: la declaracion del Incidente, el
    vencimiento del umbral de permanencia en Creada o la ultima transicion de
    la Orden en riesgo de plazo. En UTC."""
    ruta: str | None = None
    orden: str | None = None
    ordenes: tuple[str, ...] = ()
    """Las Ordenes sin terminar de la Ruta afectada por un Incidente."""
    incidente: str | None = None
    eta_p50: dt.datetime | None = None
    """Hora estimada de la primera entrega (mediana), en UTC. Es una
    estimacion, no una medicion."""
    eta_p90: dt.datetime | None = None
    """Extremo p90 de la banda estimada, en UTC. La banda no esta calibrada
    al 90 % (B-200)."""
    plazo_comprometido: dt.date | None = None
    vehiculo: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """`desvio_de_ruta` (2026-11-22A): el Vehiculo desviado; `riesgo_vial_en_ruta`
    (2026-11-23E): el Vehiculo de la Ruta. Ausente en las demas."""
    distancia_m: float | None = pydantic.Field(default=None, exclude_if=_ausente)
    """`desvio_de_ruta`: distancia del ultimo reporte, anclado al grafo, al
    trazado del tramo en curso. Ausente en las demas."""
    propuesta: str | None = pydantic.Field(default=None, exclude_if=_ausente)
    """`desvio_de_ruta` y `riesgo_vial_en_ruta`: la propuesta de reruteo generada
    (`GET /v1/propuestas/{id}`), para aprobar o rechazar. Ausente si no hay."""

    @classmethod
    def desde_alerta(cls, alerta: Alerta) -> AlertaPublicada:
        return cls(**dataclasses.asdict(alerta))


class AlertasPublicadas(pydantic.BaseModel):
    """Las alertas vigentes de una jornada, evaluadas en un instante."""

    jornada: dt.date
    evaluadas_en: dt.datetime
    """Instante de la evaluacion, en UTC."""
    alertas: tuple[AlertaPublicada, ...]


class DesasignacionAplicada(pydantic.BaseModel):
    """Resultado de desasignar (FR-03 inverso).

    Informa el alcance completo de lo que ocurrio y no solo lo que se pidio:
    desasignar una Orden invalida la Ruta que la contenia, de modo que las
    demas Ordenes de esa Ruta tambien quedan libres. Devolverlo explicito
    evita que quien lo dispare tenga que deducirlo o volver a consultar.
    """

    ruta_invalidada: str
    ordenes_liberadas: tuple[str, ...]
    """Todas las que volvieron a Creada, incluida la pedida. Quedan
    planificables otra vez."""


class GrafoActivoPublicado(pydantic.BaseModel):
    """El artefacto del grafo vial cargado: cual es y como se identifica."""

    archivo: str
    """Nombre del artefacto, por ejemplo `grafo_san_isidro_2026-08-16.graphml`."""

    ambito: str | None
    """Ambito que declara la procedencia del artefacto."""

    sha256: str
    """Primeros caracteres del SHA-256 del artefacto, abreviado."""

    verificado: bool
    """Si el SHA-256 se comprobo contra el que registra el sidecar al cargar.
    Falso cuando el sidecar no lo registra: se calcula y se informa igual."""


class EstadoDelServicio(pydantic.BaseModel):
    """Lo que el servicio dice de si mismo.

    Existe para que quien consume la API pueda mostrar contra que esta
    corriendo. Publica las dos versiones por separado porque son cosas
    distintas y confundirlas lleva a conclusiones erroneas: la del producto
    avanza con cada entrega, la del contrato solo cambia cuando se rompe
    compatibilidad.
    """

    producto: str
    version: str
    """Version del producto, en versionado semantico X.Y.Z. Mayor 0 mientras
    no exista un MVP completo."""

    contrato: str
    """Version del contrato de la API, la que aparece en la ruta. Un campo
    opcional nuevo en una respuesta no la mueve."""

    grafo: GrafoActivoPublicado | None = None
    """El grafo vial con que corre el servicio, elegido al arrancar
    (2026-10-27B, K). Ausente si el servicio no lo informa."""

    notificaciones_pendientes: int | None = None
    """Notificaciones de la salida transaccional hacia telemetria todavia sin
    entregar (ARQ-59). Es una verificacion de salud, no una metrica de
    desempeno (ARQ-13): si crece, telemetria no esta recibiendo."""


class VehiculoPublicado(pydantic.BaseModel):
    """Un Vehiculo de la Flota vigente (FR-15).

    Lleva la capacidad y nada mas. El glosario lo caracteriza tambien por su
    ubicacion y su capacidad remanente, pero las dos dependen de Seguimiento
    de Flota, que es fase 2: publicarlas hoy exigiria inventarlas.
    """

    id: str
    """Matricula del Vehiculo, en formato Mercosur."""

    capacidad: int
    """Capacidad de carga nominal, en cantidad de bultos (B-55)."""

    capacidad_efectiva: int
    """Lo que el calculo carga como maximo en este Vehiculo:
    floor(capacidad x (1 - margen_de_capacidad)) (B-220, punto 2). Es la
    misma que cada Ruta informa en `capacidad_vehiculo` (2026-10-20B, F1.4a;
    B-242, punto 5, propuesta 3)."""

    disponible: bool
    """Si el Vehiculo puede entrar en una planificacion de la jornada
    consultada.

    Un Vehiculo participa de un unico Plan de Distribucion por jornada
    (glosario, entidad 13). Este campo publica esa regla en vez de obligar a
    reconstruirla del otro lado recorriendo las Rutas de la jornada: la regla
    es de dominio, y una segunda derivacion en el cliente puede discrepar de
    la que el sistema aplica al planificar."""

    comprometido_en: str | None
    """Plan de la jornada que ocupa al Vehiculo, o `null` si esta libre.

    Se publica el identificador y no solo el booleano para que quien lo
    presente pueda nombrar la causa concreta --y, con ella, la correccion:
    liberar ese Plan o elegir otro Vehiculo--."""


class VehiculoRegistradoPublicado(VehiculoPublicado):
    """Un Vehiculo de la Flota registrada, tambien si esta dado de baja
    (2026-11-02). Es lo que administra el operador; `/v1/flota` publica solo
    los activos, que son los que se pueden planificar."""

    activo: bool
    """False si esta dado de baja: no entra en ninguna planificacion, pero se
    conserva porque las Rutas que ya recorrio lo nombran."""


class VehiculoSolicitado(pydantic.BaseModel):
    """El alta de un Vehiculo en la Flota (2026-11-02)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    id: str = pydantic.Field(
        description="La patente: Mercosur (AA123BB) o la anterior (ABC123). Se guarda en "
        "mayusculas y sin espacios ni guiones."
    )
    capacidad: int = pydantic.Field(description="Capacidad de carga nominal, en bultos.")


class CambioDeVehiculoSolicitado(pydantic.BaseModel):
    """Lo que el operador cambia de un Vehiculo: la capacidad, o darlo de baja
    o reactivarlo con `activo`. Lo que no viene, no cambia (2026-11-02)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    capacidad: int | None = None
    activo: bool | None = None


class FlotaPublicada(pydantic.BaseModel):
    """La Flota vigente, legible antes de que exista plan alguno (FR-15).

    Hasta ahora los Vehiculos solo aparecian dentro del detalle de una Ruta,
    de modo que componer una planificacion --elegir Vehiculos y comparar
    demanda contra capacidad-- exigia conocerlos de antemano. El contrato los
    aceptaba en la solicitud y no los devolvia.

    La Flota es una coleccion y no una entidad con identidad propia
    (glosario, entidad 5): esto la publica, no la convierte en entidad ni le
    da ciclo de vida.
    """

    vehiculos: tuple[VehiculoPublicado, ...]
    capacidad_total: int
    """Suma de las capacidades. Derivada, para no obligar a recalcularla del
    otro lado."""

    fecha: dt.date
    """Jornada contra la que se evaluo la disponibilidad de cada Vehiculo.

    Viaja en la respuesta aunque la peticion la omita: la disponibilidad no
    significa nada sin la jornada a la que corresponde, y omitida se resuelve
    con la fecha operativa en curso, que quien consulta no tiene por que
    adivinar."""

    capacidad_disponible: int
    """Suma de las capacidades de los Vehiculos libres en esa jornada.

    Es el techo real de lo que se puede planificar hoy, que es distinto de
    `capacidad_total` en cuanto haya un Plan vigente."""

    margen_de_capacidad: float
    """Margen de la operacion: la fraccion de la capacidad nominal que el
    calculo no usa (0,05 en el producto; 0 sin margen)."""

    capacidad_efectiva_total: int
    """Suma de las capacidades efectivas de todos los Vehiculos."""

    capacidad_efectiva_disponible: int
    """Suma de las capacidades efectivas de los Vehiculos libres en la
    jornada: lo que entra de verdad en una planificacion de hoy."""


class DepositoPublicado(pydantic.BaseModel):
    """Un Deposito de la operacion (FR-14).

    Se expone con independencia de que exista un plan: es el origen y retorno
    de toda Ruta y una precondicion de la planificacion, asi que tiene que
    poder leerse antes de que haya Rutas.
    """

    punto: Punto


class DepositosPublicados(pydantic.BaseModel):
    """Los Depositos de la operacion, como coleccion.

    Fase 1 opera con un Deposito unico (B-12), asi que hoy la coleccion tiene
    exactamente un elemento. Se publica como coleccion igual porque el
    glosario admite varios (entidad 6) y FR-12 los contempla: pasar de objeto
    unico a coleccion mas adelante seria un cambio incompatible, y hacerlo
    ahora --con el endpoint recien nacido y un solo consumidor-- no cuesta
    nada.

    Publicar la coleccion no habilita altas ni convierte al Deposito en una
    entidad con escritura: sigue siendo parametro de cada planificacion
    (B-59).
    """

    depositos: tuple[DepositoPublicado, ...]


class Coordenada(pydantic.BaseModel):
    """Un punto geografico, sin relacion con el grafo."""

    lat: float
    lon: float


class AnclajePublicado(pydantic.BaseModel):
    """A que nodo del grafo engancha una coordenada, y a que distancia.

    Es una consulta sin efecto: no crea nada. La distancia es lo que permite
    juzgar el resultado antes de usarlo, porque el anclaje siempre devuelve
    algo --el nodo mas proximo existe siempre-- y sin ella no hay forma de
    distinguir un enganche correcto de uno que cayo en otra cuadra.
    """

    consultado: Coordenada
    nodo: Punto
    distancia_m: float
    """Metros entre el punto consultado y el nodo, en linea recta. Es la
    misma medida con la que se eligio el nodo, no una recalculada aparte."""

    es_el_deposito: bool
    """Si el nodo resuelto es el del Deposito, en cuyo caso registrar una
    Orden sobre esta coordenada seria rechazado (CU-01, flujo 4b). Se informa
    aca para que la interfaz pueda advertirlo antes del intento."""

    @classmethod
    def desde(
        cls, anclaje: Anclaje, rotulo: RotuloNodo, es_el_deposito: bool
    ) -> AnclajePublicado:
        return cls(
            consultado=Coordenada(
                lat=anclaje.lat_consultada, lon=anclaje.lon_consultada
            ),
            nodo=Punto.desde(rotulo),
            distancia_m=anclaje.distancia_m,
            es_el_deposito=es_el_deposito,
        )


class CandidatoDeDireccionPublicado(pydantic.BaseModel):
    """Una direccion que encontro Georef, ya anclada al grafo activo."""

    direccion: str
    """La direccion normalizada que devolvio Georef."""
    consultado: Coordenada
    """Su ubicacion segun Georef: la que el alta envia como `lat` y `lon`."""
    nodo: Punto
    """El nodo mas cercano de la componente mayor del grafo activo."""
    distancia_m: float
    """Metros en linea recta entre la ubicacion y el nodo."""
    es_el_deposito: bool


class DireccionesEncontradas(pydantic.BaseModel):
    """Resultado de una busqueda por direccion (RB-10; 2026-10-27C, G)."""

    texto: str
    candidatos: tuple[CandidatoDeDireccionPublicado, ...]
    """Vacio si Georef no reconocio la direccion."""
    atribucion: str
    """La que exige la licencia de Georef (CC BY 4.0); la interfaz la muestra."""


class PlanEnListado(pydantic.BaseModel):
    """Un Plan de Distribucion con sus totales, sin sus Rutas.

    Los totales son datos del Plan y no una suma del listado de Rutas: esa
    suma deja de ser correcta en cuanto conviven varios Planes en una
    jornada, que es lo que el sistema admite.
    """

    id: str
    fecha_planificacion: dt.date
    cantidad_rutas: int
    cantidad_paradas: int
    demanda_total: int
    """Bultos que el Plan mueve. Es la carga de sus Rutas, no la demanda de
    la instancia: una Orden que quedo fuera del plan no cuenta."""

    distancia_total_m: float
    vehiculos: tuple[str, ...]
    """Los Vehiculos que el Plan ocupa. Un Vehiculo participa de un unico
    Plan por jornada, asi que estos no estan disponibles para otra corrida
    del mismo dia."""

    @classmethod
    def desde(cls, plan: PlanDeDistribucion) -> PlanEnListado:
        return cls(
            id=str(plan.id),
            fecha_planificacion=plan.fecha_planificacion,
            cantidad_rutas=plan.cantidad_rutas,
            cantidad_paradas=plan.cantidad_paradas,
            demanda_total=plan.demanda_total,
            distancia_total_m=plan.distancia_total_m,
            vehiculos=tuple(str(vehiculo) for vehiculo in plan.vehiculos),
        )


class PlanConRutas(pydantic.BaseModel):
    """Un Plan con las Rutas que lo componen, sin geometria.

    El trazado son cientos de coordenadas por Ruta: quien dibuja pide el
    detalle de la Ruta que va a mostrar.
    """

    plan: PlanEnListado
    deposito: Punto
    rutas: tuple[RutaEnListado, ...]


class JornadasPublicadas(pydantic.BaseModel):
    """Las jornadas que tienen al menos un Plan (RB-11).

    Es lo que permite ofrecer navegacion entre jornadas sin adivinar: sin
    esto, la unica forma de saber si un dia tiene contenido era pedir sus
    Rutas y ver si volvia vacio.
    """

    fechas: tuple[dt.date, ...]


class EtaDeParadaPublicada(pydantic.BaseModel):
    """CU-09: la hora estimada de arribo de una Parada y el instante en que se
    calculo. `fuente` dice de donde sale: `posicion` (desde la posicion del
    Vehiculo de a lo sumo 120 s y la secuencia pendiente, paso 2), `plan`
    (desde la salida del Deposito, la de `GET /v1/rutas/{id}`, para una Ruta
    sin despachar o sin posicion ni estimacion previa) o `ultima_estimacion`
    (flujo 2a: sin posicion reciente, la ultima calculada desde la posicion,
    con su antiguedad). Mismo estimador que el de la Ruta (B-193): no esta
    validado para el AMBA, la velocidad es un supuesto y no depende del
    transito."""

    ruta_id: str
    secuencia: int
    hora_estimada: dt.datetime | None
    hora_p10: dt.datetime | None
    hora_p90: dt.datetime | None
    calculado_en: dt.datetime
    antiguedad_s: float
    """Segundos desde que se calculo la estimacion que se devuelve."""

    fuente: str
    antiguedad_de_la_posicion_s: float | None
    motivo_sin_estimacion: str | None


class ReprogramacionSolicitada(pydantic.BaseModel):
    """CU-06, flujo 1a: reprogramar una Orden Fallida, con su plazo nuevo si se
    confirma uno."""

    model_config = pydantic.ConfigDict(extra="forbid")

    plazo_comprometido: dt.date | None = None


class ContingenciaSolicitada(pydantic.BaseModel):
    """CU-11, pasos 1 y 2: las Paradas comprometidas de una Ruta (por su
    secuencia), el Vehiculo de reserva (patente) y la prioridad relativa (1 es
    la mayor)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    ruta_id: str = pydantic.Field(min_length=1)
    paradas: tuple[pydantic.PositiveInt, ...] = pydantic.Field(min_length=1)
    vehiculo_reserva: str = pydantic.Field(min_length=1)
    prioridad: int = pydantic.Field(ge=1)


class ContingenciaPublicada(pydantic.BaseModel):
    """Una contingencia registrada (CU-11, paso 4) y, si se activo, la Ruta de
    contingencia (paso 6)."""

    id: str
    ruta_id: str
    paradas: tuple[int, ...]
    """Secuencias de la Ruta, en el orden preparado para el Vehiculo de
    reserva (desde el Deposito y de vuelta a el, con el algoritmo del
    recalculo de CU-07)."""

    vehiculo_reserva: str
    prioridad: int
    estado: str
    """`disponible` o `activada`."""

    demanda: int
    capacidad_efectiva: int
    distancia_m: float
    metodo: str
    creada_en: dt.datetime
    activada_en: dt.datetime | None
    ruta_de_contingencia: str | None


class EvaluacionDeContingenciaPublicada(pydantic.BaseModel):
    """Si una contingencia es aplicable ahora (CU-11, paso 5) y, si no, por que."""

    contingencia: ContingenciaPublicada
    aplicable: bool
    causa: str | None


class ActivacionSolicitada(pydantic.BaseModel):
    """CU-11, paso 5: el transportista del Vehiculo de reserva, si se asigna."""

    model_config = pydantic.ConfigDict(extra="forbid")

    transportista_id: str | None = None


class ClienteComercialSolicitado(pydantic.BaseModel):
    """CU-10, precondicion: un Cliente comercial (glosario, actor 1)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    nombre: str = pydantic.Field(min_length=1, max_length=200)


class ClienteComercialPublicado(pydantic.BaseModel):
    id: str
    nombre: str
    ordenes: tuple[str, ...]
    """Las Ordenes que origino, asentadas con su operacion."""


class OrdenesDelClienteSolicitadas(pydantic.BaseModel):
    """CU-10, paso 2: Ordenes existentes que origino el Cliente comercial."""

    model_config = pydantic.ConfigDict(extra="forbid")

    ordenes: tuple[str, ...] = pydantic.Field(min_length=1)


class ProveedorSolicitado(pydantic.BaseModel):
    """Un Proveedor de ultima milla (glosario, actor 3) y los Vehiculos de la
    Flota que aporta (relacion 5, opcional)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    nombre: str = pydantic.Field(min_length=1, max_length=200)
    vehiculos: tuple[str, ...] = ()


class ProveedorPublicado(pydantic.BaseModel):
    id: str
    nombre: str
    vehiculos: tuple[str, ...]


class AcuerdoSolicitado(pydantic.BaseModel):
    """CU-10, paso 1: el acuerdo pactado con un Cliente comercial."""

    model_config = pydantic.ConfigDict(extra="forbid")

    cliente_comercial_id: str = pydantic.Field(min_length=1)
    umbral_de_cumplimiento: float = pydantic.Field(gt=0.0, le=1.0)
    """Proporcion minima pactada, por ejemplo 0,95."""

    plazo_comprometido_dias: int = pydantic.Field(ge=0)
    """Dias desde el alta de la Orden; una Orden sin plazo propio toma este."""


class AcuerdoPublicado(pydantic.BaseModel):
    id: str
    cliente_comercial_id: str
    umbral_de_cumplimiento: float
    plazo_comprometido_dias: int
    registrado_en: dt.datetime
    vigente: bool
    """El ultimo registrado para el Cliente: el que rige sus Ordenes."""

    ordenes: tuple[str, ...]
    """Las Ordenes que rige (paso 2); vacio si no es el vigente."""


class EvaluacionDeCumplimientoPublicada(pydantic.BaseModel):
    """CU-10, pasos 4 y 5, y flujos 4a y 4b. No altera ninguna Orden."""

    acuerdo_id: str
    proveedor_id: str
    desde: dt.date
    hasta: dt.date
    umbral_de_cumplimiento: float
    ordenes_del_periodo: int
    sin_proveedor: int
    atribuible: bool
    base: int
    base_suficiente: bool
    tamano_minimo_representativo: int
    exitosas: int
    proporcion_exitosas: float | None
    con_plazo: int
    plazos_respetados: int
    proporcion_plazos_respetados: float | None
    cumple_exitosas: bool | None
    cumple_plazos: bool | None
    observacion: str | None
    """4a: no se puede atribuir el resultado; 4b: la base es chica (con su tamano)."""


class MapaDeEstadosSolicitado(pydantic.BaseModel):
    """CU-12, precondicion: el mapa de traduccion de un Proveedor, de su codigo
    al estado canonico (`completada`, `fallida` o `devuelta`)."""

    model_config = pydantic.ConfigDict(extra="forbid")

    codigos: dict[str, str] = pydantic.Field(min_length=1)


class MapaDeEstadosPublicado(pydantic.BaseModel):
    proveedor_id: str
    codigos: dict[str, str]


class ReporteDeProveedorSolicitado(pydantic.BaseModel):
    """CU-12, paso 1: el estado de una Orden en el vocabulario del Proveedor."""

    model_config = pydantic.ConfigDict(extra="forbid")

    orden_id: str = pydantic.Field(min_length=1)
    codigo: str = pydantic.Field(min_length=1, max_length=100)


class ReporteRechazadoPublicado(pydantic.BaseModel):
    """Un reporte rechazado: la notificacion al operador (3a) o la constancia
    de la inconsistencia (2a, 4a). El codigo es el que llego; no es un estado."""

    id: str
    proveedor_id: str
    referencia_de_orden: str
    codigo_reportado: str
    tipo: str
    causa: str
    recibido_en: dt.datetime


class UsuarioDeIntegracionSolicitado(pydantic.BaseModel):
    """CU-04 por su integracion: el usuario (rol `cliente_comercial`) que actua
    por el Cliente comercial."""

    model_config = pydantic.ConfigDict(extra="forbid")

    usuario_id: str = pydantic.Field(min_length=1)


class AsignacionEnLaJornadaPublicada(pydantic.BaseModel):
    """Pedido nuevo durante la jornada (2026-11-22A): a que Vehiculo y a que
    viaje siguiente fue la Orden, con la regla (b) y R3."""

    orden_id: str
    vehiculo_id: str
    ruta_siguiente: str
    """La Ruta del viaje siguiente del Vehiculo, Planificada y sin despachar."""

    secuencia: int
    costo_min: float
    """Aumento del recorrido previsto, en minutos a la velocidad de trabajo
    (supuesto de B-196), mas la carga si hizo falta un regreso nuevo."""

    politica: str
    ruta_en_curso: str | None
    """La Ruta que el Vehiculo esta recorriendo, que no se toca. null si no
    estaba en ejecucion."""


class BrechaPublicada(pydantic.BaseModel):
    """D23 (2026-11-23A): la distancia de una Ruta al mejor orden posible de
    sus propias Paradas, para el mismo objetivo con que se planifico. No es
    un puntaje de calidad del sistema ni un porcentaje de optimalidad del
    agente."""

    ruta_id: str
    objetivo: str
    """Lo que se mide: la distancia por la red o, con la capa de riesgo
    activa, la distancia mas mu por el riesgo vial."""

    paradas: int
    """Paradas distintas de la Ruta (dos en el mismo nodo cuentan una)."""

    calculada: bool
    costo_m: float
    """El costo del orden vigente: origen, Paradas en orden y vuelta al Deposito."""

    optimo_m: float | None
    """El optimo exacto (Held-Karp) del mismo recorrido; null si no se calcula."""

    brecha: float | None
    """(costo - optimo) / optimo; 0 si la Ruta ya esta en el orden optimo."""

    orden_optimo: tuple[int, ...] | None
    """Los nodos de las Paradas en el orden optimo."""

    motivo: str | None
    """Por que no se calculo (mas de 12 Paradas distintas, o ninguna)."""


class ExposicionAlRiesgoVialPublicada(pydantic.BaseModel):
    """D25 (2026-11-23A): suma del riesgo esperado de los arcos que recorre la
    Ruta (hechos con lesionados o fallecidos por anio en esos tramos, segun el
    modelo del Observatorio de Movilidad y Seguridad Vial). Es un indicador de
    los tramos, no del Vehiculo ni una medida de seguridad real."""

    ruta_id: str
    calculada: bool
    exposicion_hechos_por_anio: float | None
    arcos: int | None
    mu: float | None
    """El peso del riesgo en el costo del ruteo (0: el riesgo no se usa para rutear)."""

    tabla: str | None
    tabla_sha256: str | None
    motivo: str | None
