"""Motor de Ruteo: interfaz intercambiable (ARQ-30).

Componente puro por diseno -- instancia adentro, solucion afuera, sin E/S
propia. Esta pureza es lo que hace verificable NFR-07 con el metodo que el
propio requisito especifica: ejecucion repetida sobre una instancia fija y
comparacion de las salidas obtenidas.
"""
from __future__ import annotations

import abc
import dataclasses
import datetime as dt
import enum

from hoppit.cartografia.puerto import MatrizDeCostos
from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.ids import OrdenId, RutaId, VehiculoId


class InstanciaInvalida(ErrorDeDominio):
    """La instancia no describe un problema resoluble."""


class MotivoSinSolucion(enum.Enum):
    """Por que el motor termino sin solucion (B-220, punto 2).

    Los dos motivos piden correcciones distintas, y confundirlos seria
    afirmar algo que no se sabe: alcanzar el limite de la busqueda no prueba
    que la instancia no tenga solucion.
    """

    LIMITE_DE_BUSQUEDA = "limite_de_busqueda_alcanzado"
    """La busqueda alcanzo el limite de ramas antes de encontrar una primera
    solucion. La instancia puede tener solucion: tres instancias de CVRPLIB
    con solucion publicada terminan asi (B-217, punto 5)."""

    INFACTIBLE = "infactible"
    """No hay solucion: se sabe por aritmetica antes de buscar, o la busqueda
    de OR-Tools termino sin solucion y sin alcanzar el limite."""


class SinSolucion(ErrorDeDominio):
    """El solver termino sin encontrar una solucion factible."""

    def __init__(self, mensaje: str, motivo: MotivoSinSolucion = MotivoSinSolucion.INFACTIBLE) -> None:
        super().__init__(mensaje)
        self.motivo = motivo


class DemandaExcedeCapacidad(SinSolucion):
    """Flujo 4a de CU-02: la Flota seleccionada no alcanza para la demanda y
    ninguna Orden entra.

    Subclase de SinSolucion y no un error suelto: sigue siendo el caso de
    que no hay solucion, con una causa distinta y una correccion distinta --
    agregar Vehiculos o quitar Ordenes de la seleccion, no reintentar-- que
    el contrato tiene que poder decir.

    Desde B-220, punto 2, la demanda que excede la capacidad efectiva ya no
    rechaza la planificacion: el motor resuelve con Paradas opcionales y
    devuelve un plan parcial que declara las Ordenes que quedaron afuera.
    Este error queda solo para cuando no entra ninguna.
    """

    def __init__(self, demanda_total: int, capacidad_total: int) -> None:
        super().__init__(
            f"Demanda {demanda_total} bultos sobre capacidad total "
            f"{capacidad_total}: ninguna Orden entra en los Vehiculos "
            f"seleccionados. Faltan {demanda_total - capacidad_total} bultos "
            f"de capacidad.",
            MotivoSinSolucion.INFACTIBLE,
        )
        self.demanda_total = demanda_total
        self.capacidad_total = capacidad_total

    @property
    def faltante(self) -> int:
        """Bultos de capacidad que hay que sumar para que sea factible."""
        return self.demanda_total - self.capacidad_total


class SinSolucionConTodosLosVehiculos(SinSolucion):
    """Con la opcion de usar todos los Vehiculos, no se encontro una solucion
    en la que cada uno visite al menos una Parada.

    El motor no devuelve en su lugar una solucion con menos Vehiculos: quien
    pidio exactamente k (B-211, punto 1) compara contra una referencia con k
    rutas, y una solucion con menos no es comparable.
    """


# Criticidad de una Orden (B-220, punto 2, criterio e): prioridad declarada
# (1 a 3) mas el atraso respecto del plazo comprometido, acotado a 2 dias.
# Es un valor derivado que calcula el Ensamblador con la fecha de
# planificacion; no es un atributo de la Orden.
CRITICIDAD_MINIMA = 1
CRITICIDAD_MAXIMA = 5

# La de una Orden de prioridad media sin plazo: la que se asume cuando la
# instancia no trae criticidad, como las que se arman fuera del Ensamblador.
# Con todas iguales, la penalizacion de cada Parada es la misma.
CRITICIDAD_POR_OMISION = 2


class MotivoNoPlanificada(enum.Enum):
    """Por que un plan parcial dejo Ordenes afuera (B-220, punto 2)."""

    DEMANDA_EXCEDE_CAPACIDAD = "demanda_excede_capacidad"
    """La demanda total supera la capacidad efectiva de los Vehiculos
    seleccionados (CU-02, flujo 4a)."""

    SIN_SOLUCION_COMPLETA = "sin_solucion_completa"
    """Ni la estrategia principal ni la de respaldo encontraron, dentro del
    limite de ramas, una solucion que visite todas las Paradas."""


@dataclasses.dataclass(frozen=True)
class OrdenNoPlanificada:
    """Una Orden que el plan parcial dejo afuera. Sigue en Creada."""

    orden: OrdenId
    criticidad: int
    motivo: MotivoNoPlanificada


@dataclasses.dataclass(frozen=True)
class ParadaDeInstancia:
    """Un punto que la Ruta tiene que visitar.

    Una Parada agrupa una o mas Ordenes cuyo destino corresponde al nodo que
    referencia (relacion 3 del glosario). El solver visita Paradas, no
    Ordenes: dos Ordenes al mismo nodo son una sola visita, y su demanda se
    suma porque el Vehiculo carga las dos.
    """

    nodo: int
    indice: int
    """Posicion del nodo dentro de `InstanciaProblema.matriz.nodos`."""

    demanda: int
    """Suma de la demanda de las Ordenes que agrupa, en bultos."""

    ordenes: tuple[OrdenId, ...]


@dataclasses.dataclass(frozen=True)
class ParadaPlaneada:
    """Una Parada ya ubicada en una Ruta, con su secuencia.

    Identidad: `(ruta_id, secuencia)`, segun ARQ-05 -- la Parada no se
    identifica por su ubicacion sino por su pertenencia a la Ruta y su
    secuencia dentro de ella. Dos Rutas que pasen por el mismo nodo son dos
    Paradas distintas, y el mismo nodo no puede aparecer dos veces en una
    Ruta con la misma secuencia. La secuencia arranca en 1 en cada Ruta.

    No se usa un identificador propio (`ParadaId`): seria una segunda
    identidad, capaz de discrepar de la primera, para una entidad cuya
    identidad ya esta definida de forma relacional. Si mas adelante hace falta
    una clave sustituta para persistencia real, se agrega entonces sin cambiar
    lo que la Parada significa.
    """

    ruta_id: RutaId
    secuencia: int
    nodo: int
    ordenes: tuple[OrdenId, ...]
    """Las Ordenes que se entregan en esta visita. Mas de una cuando varias
    comparten destino."""

    eta_estimado_s: float | None

    @property
    def identidad(self) -> tuple[RutaId, int]:
        return (self.ruta_id, self.secuencia)


@dataclasses.dataclass(frozen=True)
class Ruta:
    """Secuencia ordenada de Paradas asignada a un Vehiculo (glosario 3).

    Una Ruta pertenece a un solo Vehiculo, parte del Deposito y retorna a el.
    El orden de la tupla es el orden de visita, y la secuencia de cada Parada
    arranca en 1 dentro de esta Ruta: con una Ruta por Vehiculo,
    `(ruta_id, secuencia)` es identidad sin necesidad de numerar de corrido
    entre Vehiculos.

    Lleva sus propias medidas en vez de dejarlas en un mapa por Vehiculo al
    costado del plan: la distancia y la carga son atributos de la Ruta, y
    tenerlas en dos lugares abre la puerta a que discrepen.
    """

    id: RutaId
    vehiculo_id: VehiculoId
    paradas: tuple[ParadaPlaneada, ...]
    distancia_m: float
    carga: int
    """Bultos que el Vehiculo transporta al salir del Deposito."""

    capacidad_vehiculo: int
    """Capacidad con la que se resolvio esta Ruta, en bultos.

    Viaja con la Ruta y no se lee de la Flota vigente al consultarla: la Ruta
    describe una corrida concreta, y si manana la Flota cambia, la ocupacion
    de una Ruta ya publicada no puede cambiar retroactivamente. Al lado de
    `carga` porque juntas son la ocupacion, y tenerlas en lugares distintos
    abre la puerta a que se informen sobre capacidades distintas."""

    deposito: int | None = None
    """El Deposito del que sale y al que vuelve, cuando el Plan tiene varios
    (CU-02, flujo 1a; 2026-11-22A). None: el Deposito del Plan."""

    @property
    def ocupacion(self) -> float:
        """Proporcion de la capacidad utilizada, entre 0 y 1."""
        return self.carga / self.capacidad_vehiculo

    @property
    def ordenes(self) -> tuple[OrdenId, ...]:
        return tuple(orden for parada in self.paradas for orden in parada.ordenes)


@dataclasses.dataclass(frozen=True)
class InstanciaProblema:
    """Lo que arma el Ensamblador de Instancia (FR-01, FR-12).

    D1 consume grafo + demanda + capacidad + depositos. D2 (fase 2) consume
    ademas prioridad, plazos y la matriz predictiva de tiempos de viaje --
    misma firma de entrada/salida del motor, subconjunto de campos usados
    distinto segun la implementacion (ARQ-29).

    La unidad que el motor rutea es la Parada, no la Orden: el Ensamblador ya
    agrupo las Ordenes por nodo de destino. Cada Parada lleva explicito su
    indice dentro de la matriz, y no por convencion posicional: el motor
    razona sobre indices, y equivocarse en esa traduccion produce una Ruta que
    visita los puntos equivocados sin que nada falle.
    """

    paradas: tuple[ParadaDeInstancia, ...]
    capacidad_por_vehiculo: dict[VehiculoId, int]
    depositos: tuple[int, ...]
    """Nodos del grafo. Fase 1 usa uno solo (B-12); la forma plural queda
    porque FR-12 contempla varios."""

    matriz: MatrizDeCostos
    """Costos en distancia entre todos los nodos de la instancia, con la
    secuencia de arcos de cada camino: es lo que hace verificable NFR-08
    sobre la Ruta publicada."""

    indice_deposito: int

    fecha_planificacion: dt.date
    """Jornada operativa para la que se planifica (RB-01), en hora local.

    Es parte de la instancia y no un dato de registro posterior: la Ruta que
    un Vehiculo recorre el martes no es la misma Ruta que recorre el
    miercoles, aunque el recorrido coincida punto por punto. El glosario fija
    que un Vehiculo participa de un unico Plan por jornada, de modo que la
    jornada es parte de lo que el problema plantea.

    Entra en el resumen canonico del que se derivan los identificadores de
    Ruta y de Plan. Sin ella, dos corridas identicas en jornadas distintas
    producian los mismos identificadores y la segunda pisaba a la primera al
    persistirse. NFR-07 se conserva y se lee entero: misma instancia --que
    incluye la jornada-- da la misma salida.
    """

    prioridad_por_orden: dict[OrdenId, int] | None = None
    plazos_por_orden: dict[OrdenId, float] | None = None
    criticidad_por_orden: dict[OrdenId, int] | None = None
    """Criticidad de cada Orden, de CRITICIDAD_MINIMA a CRITICIDAD_MAXIMA,
    calculada por el Ensamblador con la fecha de planificacion y no con el
    reloj (NFR-07). El motor solo la usa si tiene que dejar Paradas afuera.
    `None` cuando la instancia no la trae: todas valen
    CRITICIDAD_POR_OMISION. No entra en el resumen del que se derivan los
    identificadores de Ruta."""
    matriz_tiempos_prevista: dict[tuple[int, int], float] | None = None
    """La empujaria Prediccion de ETA, que hoy no empuja tiempos: el ETA se
    estima al responder, en la frontera, y no llega al solver (B-195). `None`
    significa ausente, no cero: no hay ninguna prevision y la instancia lo
    declara."""

    deposito_por_vehiculo: dict[VehiculoId, int] | None = None
    """Indice en la matriz del Deposito de cada Vehiculo, si el Plan tiene mas
    de uno (CU-02, flujo 1a; FR-12; 2026-11-22A). None: todos salen y vuelven
    a `indice_deposito`."""

    @property
    def ordenes(self) -> tuple[OrdenId, ...]:
        """Todas las Ordenes que la instancia cubre, agrupadas o no.

        Es lo que viaja en el evento de Ruta publicada: agrupar dos Ordenes en
        una Parada no puede dejar a una de las dos sin transicionar.
        """
        return tuple(orden for parada in self.paradas for orden in parada.ordenes)

    @property
    def demanda_total(self) -> int:
        return sum(parada.demanda for parada in self.paradas)

    @property
    def capacidad_total(self) -> int:
        return sum(self.capacidad_por_vehiculo.values())

    @property
    def parada_por_indice(self) -> dict[int, ParadaDeInstancia]:
        return {parada.indice: parada for parada in self.paradas}


@dataclasses.dataclass(frozen=True)
class SolucionRuteo:
    """El plan de flota que devuelve una corrida: una Ruta por Vehiculo
    utilizado.

    No es una Ruta ni tiene identidad de Ruta. El glosario define la Ruta como
    la secuencia de Paradas de *un* Vehiculo, asi que lo que produce una
    planificacion son N Rutas, cada una con su identificador, y este objeto es
    el conjunto que las agrupa.

    Un Vehiculo al que no se le asignaron Paradas no aparece: no hay Ruta que
    emitir porque no hay secuencia que ordenar. Con una Flota mas grande que
    la que la demanda necesita, `rutas` tiene menos elementos que Vehiculos
    tenga la Flota, y eso es lo correcto.

    Las medidas agregadas son derivadas de las Rutas y no campos propios: el
    motor es el unico que sabe con que costos resolvio, y tener el total en dos
    lugares abre la puerta a que informen algo distinto de lo que se optimizo.
    """

    rutas: tuple[Ruta, ...]
    no_planificadas: tuple[OrdenNoPlanificada, ...] = ()
    """Las Ordenes de la instancia que el plan deja afuera, por
    identificador. Vacia si las cubre todas (B-220, punto 2)."""

    @property
    def es_parcial(self) -> bool:
        return bool(self.no_planificadas)

    @property
    def distancia_total_m(self) -> float:
        return sum(ruta.distancia_m for ruta in self.rutas)

    @property
    def distancia_por_vehiculo(self) -> dict[VehiculoId, float]:
        return {ruta.vehiculo_id: ruta.distancia_m for ruta in self.rutas}

    @property
    def carga_por_vehiculo(self) -> dict[VehiculoId, int]:
        return {ruta.vehiculo_id: ruta.carga for ruta in self.rutas}

    @property
    def ordenes(self) -> tuple[OrdenId, ...]:
        """Todas las Ordenes que el plan cubre, en el orden de sus Rutas."""
        return tuple(orden for ruta in self.rutas for orden in ruta.ordenes)


class MotorDeRuteo(abc.ABC):
    """Interfaz que toda implementacion debe respetar sin excepcion."""

    @abc.abstractmethod
    def resolver(self, instancia: InstanciaProblema) -> SolucionRuteo:
        """Debe ser una funcion pura: misma instancia, misma solucion
        (NFR-07). Ninguna implementacion puede hacer E/S propia aca."""
        raise NotImplementedError
