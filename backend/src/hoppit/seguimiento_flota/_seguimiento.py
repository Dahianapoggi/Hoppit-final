"""Seguimiento de Flota: posiciones de las Rutas en ejecucion (FR-13, FR-17;
NFR-04, NFR-05; ARQ-52, ARQ-53; secciones 3.1 a 3.6 de ARQ_Pedido2;
2026-10-27B, T).

Es la capacidad del servicio de telemetria y seguimiento, que corre en un
proceso propio, con su base (`telemetria.sqlite3`). Sabe que Rutas estan en
ejecucion por los eventos del nucleo (`RutaDespachada`, por la salida
transaccional), no leyendo la base del nucleo.

Las posiciones se aceptan por instante de captura, no por instante de
llegada: una posicion capturada durante la ejecucion y enviada despues, tras
una desconexion, se acepta (seccion 3.2, regla 4). Cada posicion lleva un
identificador que genera el dispositivo, y una repetida se descarta: entrega
al menos una vez desde el dispositivo y almacenamiento una sola vez aca.
Ese identificador y el del dispositivo son UUID version 4 en forma canonica
(ARQ-52; 2026-10-27C, F2): una posicion con otro identificador se rechaza,
y un lote con otro `dispositivo_id` se rechaza entero.

Limitacion declarada: el ciclo de vida de la Ruta no esta implementado
(B-96, B-240), asi que `cerrada_en` queda nulo y el servicio no rechaza
posiciones posteriores al fin real del recorrido. La aplicacion deja de
capturar cuando la Ruta ya no tiene Ordenes sin terminar (B-259, punto 4).

ARQ-13: la cola del dispositivo es un bufer de reenvio. Aca no hay tasas, ni
longitudes de cola, ni metricas de servicio.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from hoppit.shared.errores import ErrorDeDominio
from hoppit.shared.tiempo import instante_actual

_LOG = logging.getLogger(__name__)

# Reglas del envio por lotes (seccion 3.2), con sus valores de diseno.
MAX_POSICIONES_POR_LOTE = 100
MAX_BYTES_DEL_LOTE = 64 * 1024
TOLERANCIA_DE_RELOJ_S = 120
# Retencion (seccion 3.6): 30 dias desde el cierre o, sin cierre, desde la
# ultima posicion recibida.
RETENCION_DE_POSICIONES_DIAS = 30
LATITUD_MAXIMA = 90.0
LONGITUD_MAXIMA = 180.0

# UUID version 4 en forma canonica (RFC 9562, seccion 4): 32 digitos
# hexadecimales en minusculas en grupos de 8-4-4-4-12, la version 4 en el
# primer digito del tercer grupo y la variante 10xx en el del cuarto. Es la
# forma que da `randomUUID` de `expo-crypto` en la aplicacion.
_PATRON_UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def es_uuid_v4_canonico(texto: str) -> bool:
    return _PATRON_UUID_V4.fullmatch(texto) is not None


TIPO_RUTA_DESPACHADA = "RutaDespachada"
# CU-07 (2026-11-20A, D10): la Ruta nueva abre su ejecucion como un despacho y
# dice a cual reemplaza; los reportes que sigan llegando con la reemplazada se
# atribuyen a la vigente.
TIPO_RUTA_REEMPLAZADA = "RutaReemplazada"
TIPOS_DE_EVENTO_ADMITIDOS = (TIPO_RUTA_DESPACHADA, TIPO_RUTA_REEMPLAZADA)
SECUENCIA_INICIAL = 0


class MotivoDeRechazo(enum.Enum):
    IDENTIFICADOR_INVALIDO = "identificador_invalido"
    EJECUCION_DESCONOCIDA = "ejecucion_desconocida"
    RUTA_NO_ASIGNADA = "ruta_no_asignada"
    FUERA_DE_EJECUCION = "fuera_de_ejecucion"
    MARCA_TEMPORAL_INVALIDA = "marca_temporal_invalida"
    COORDENADAS_INVALIDAS = "coordenadas_invalidas"


# Solo una ejecucion desconocida se reintenta: el evento del despacho puede no
# haber llegado todavia (seccion 6.3). El resto es definitivo.
REINTENTABLES = frozenset({MotivoDeRechazo.EJECUCION_DESCONOCIDA})


MAXIMO_DE_POSICIONES_RECIENTES = 20
"""Tope de lo que devuelve `posiciones_recientes`: a un envio cada 15 s,
120 s son unas 8 posiciones (D18 del encargo 2026-11-22A)."""


class SecuenciaFueraDeOrden(ErrorDeDominio):
    """Llego un evento de una Ruta con una secuencia posterior a la esperada."""


class EventoDesconocido(ErrorDeDominio):
    """El tipo de evento no es uno que este servicio consuma."""


class DispositivoInvalido(ErrorDeDominio):
    """El `dispositivo_id` del lote no es un UUID version 4 en forma canonica."""


class SinPosiciones(ErrorDeDominio):
    """La Ruta no tiene ninguna posicion aceptada."""


@dataclasses.dataclass(frozen=True)
class Ejecucion:
    ruta_id: str
    vehiculo_id: str
    transportista_id: str | None
    despachada_en: dt.datetime
    cerrada_en: dt.datetime | None
    ultima_secuencia_de_evento: int


@dataclasses.dataclass(frozen=True)
class PosicionRecibida:
    """Una posicion tal como la manda el dispositivo."""

    id: str
    capturada_en: dt.datetime
    latitud: float
    longitud: float
    precision_m: float
    velocidad_mps: float | None = None
    rumbo_grados: float | None = None


@dataclasses.dataclass(frozen=True)
class Posicion:
    """Una posicion aceptada."""

    id: str
    ruta_id: str
    transportista_id: str
    dispositivo_id: str
    capturada_en: dt.datetime
    recibida_en: dt.datetime
    latitud: float
    longitud: float
    precision_m: float
    velocidad_mps: float | None = None
    rumbo_grados: float | None = None

    def mismo_contenido(self, otra: Posicion) -> bool:
        campos = ("ruta_id", "capturada_en", "latitud", "longitud", "precision_m", "velocidad_mps", "rumbo_grados")
        return all(getattr(self, c) == getattr(otra, c) for c in campos)


@dataclasses.dataclass(frozen=True)
class Rechazo:
    id: str
    motivo: MotivoDeRechazo

    @property
    def reintentable(self) -> bool:
        return self.motivo in REINTENTABLES


@dataclasses.dataclass(frozen=True)
class ResultadoDelLote:
    aceptadas: tuple[str, ...]
    duplicadas: tuple[str, ...]
    rechazadas: tuple[Rechazo, ...]


@dataclasses.dataclass(frozen=True)
class UltimaPosicion:
    """La ultima posicion de una Ruta y su antiguedad (FR-17; ARQ-53)."""

    posicion: Posicion
    antiguedad_s: float
    antiguedad_sobre_la_recepcion: bool
    """Verdadero si la captura figura despues de la recepcion (el reloj del
    dispositivo adelantado dentro de la tolerancia): la antiguedad se cuenta
    entonces desde la recepcion."""
    en_ejecucion: bool


class RepositorioDeSeguimiento(Protocol):
    def ejecucion(self, ruta_id: str) -> Ejecucion | None: ...
    def guardar_ejecucion(self, ejecucion: Ejecucion) -> None: ...
    def evento_recibido(self, evento_id: str) -> bool: ...
    def registrar_evento(self, evento_id: str, recibido_en: dt.datetime) -> None: ...
    def posicion(self, id_posicion: str) -> Posicion | None: ...
    def insertar_si_no_existe(self, posicion: Posicion) -> bool: ...
    def ultima_posicion(self, ruta_id: str) -> Posicion | None: ...
    def ultimas_posiciones(self, ruta_id: str, cantidad: int) -> tuple[Posicion, ...]: ...
    def aplicar_retencion(self, limite: dt.datetime) -> int: ...
    def registrar_reemplazo(self, reemplazada: str, nueva: str) -> None: ...
    def reemplazante_de(self, ruta_id: str) -> str | None: ...


def clasificar(
    posicion: PosicionRecibida,
    ejecucion: Ejecucion | None,
    transportista_id: str,
    ahora: dt.datetime,
) -> MotivoDeRechazo | None:
    """El motivo de rechazo de una posicion, o None si se acepta (seccion 3.2,
    regla 3, en ese orden). Antes que nada, el identificador: sin uno valido
    la posicion no se puede guardar una sola vez (seccion 3.3)."""
    if not es_uuid_v4_canonico(posicion.id):
        return MotivoDeRechazo.IDENTIFICADOR_INVALIDO
    if ejecucion is None:
        return MotivoDeRechazo.EJECUCION_DESCONOCIDA
    if ejecucion.transportista_id != transportista_id:
        return MotivoDeRechazo.RUTA_NO_ASIGNADA
    if posicion.capturada_en < ejecucion.despachada_en or (
        ejecucion.cerrada_en is not None and posicion.capturada_en > ejecucion.cerrada_en
    ):
        return MotivoDeRechazo.FUERA_DE_EJECUCION
    if posicion.capturada_en > ahora + dt.timedelta(seconds=TOLERANCIA_DE_RELOJ_S):
        return MotivoDeRechazo.MARCA_TEMPORAL_INVALIDA
    if not (-LATITUD_MAXIMA <= posicion.latitud <= LATITUD_MAXIMA) or not (
        -LONGITUD_MAXIMA <= posicion.longitud <= LONGITUD_MAXIMA
    ):
        return MotivoDeRechazo.COORDENADAS_INVALIDAS
    return None


class ServicioDeSeguimiento:
    def __init__(
        self, repositorio: RepositorioDeSeguimiento, reloj: Callable[[], dt.datetime] = instante_actual
    ) -> None:
        self._repositorio = repositorio
        self._reloj = reloj

    def recibir_lote(
        self,
        transportista_id: str,
        ruta_id: str,
        dispositivo_id: str,
        posiciones: Sequence[PosicionRecibida],
    ) -> ResultadoDelLote:
        """Clasifica y guarda cada posicion del lote (secciones 3.2 y 3.3).
        Un `dispositivo_id` que no es UUID version 4 rechaza el lote entero
        con `DispositivoInvalido`, sin guardar nada."""
        if not es_uuid_v4_canonico(dispositivo_id):
            raise DispositivoInvalido(
                f"El dispositivo_id '{dispositivo_id}' no es un UUID version 4 en forma canonica"
            )
        ahora = self._reloj()
        ejecucion = self._repositorio.ejecucion(ruta_id)
        # Se clasifica contra la ejecucion de la Ruta informada (la de la
        # aplicacion) y se guarda en la vigente (D10).
        vigente = self.vigente(ruta_id)
        aceptadas: list[str] = []
        duplicadas: list[str] = []
        rechazadas: list[Rechazo] = []
        for recibida in posiciones:
            motivo = clasificar(recibida, ejecucion, transportista_id, ahora)
            if motivo is not None:
                rechazadas.append(Rechazo(recibida.id, motivo))
                continue
            posicion = Posicion(
                id=recibida.id,
                ruta_id=vigente,
                transportista_id=transportista_id,
                dispositivo_id=dispositivo_id,
                capturada_en=recibida.capturada_en,
                recibida_en=ahora,
                latitud=recibida.latitud,
                longitud=recibida.longitud,
                precision_m=recibida.precision_m,
                velocidad_mps=recibida.velocidad_mps,
                rumbo_grados=recibida.rumbo_grados,
            )
            if self._repositorio.insertar_si_no_existe(posicion):
                aceptadas.append(posicion.id)
                continue
            duplicadas.append(posicion.id)
            previa = self._repositorio.posicion(posicion.id)
            if previa is not None and not previa.mismo_contenido(posicion):
                # Vale la primera escritura (seccion 3.3).
                _LOG.warning("Posicion %s repetida con contenido distinto: se conserva la primera", posicion.id)
        return ResultadoDelLote(tuple(aceptadas), tuple(duplicadas), tuple(rechazadas))

    def recibir_evento(
        self, evento_id: str, tipo: str, agregado_id: str, secuencia: int, cuerpo: dict[str, Any]
    ) -> bool:
        """Consume un evento del nucleo. Devuelve False si ya se habia recibido
        (se descarta), y levanta `SecuenciaFueraDeOrden` si llega antes que
        el que le corresponde a esa Ruta (seccion 6.3, paso 3)."""
        if self._repositorio.evento_recibido(evento_id):
            return False
        if tipo not in TIPOS_DE_EVENTO_ADMITIDOS:
            raise EventoDesconocido(
                f"Tipo de evento '{tipo}': este servicio consume {', '.join(TIPOS_DE_EVENTO_ADMITIDOS)}"
            )
        actual = self._repositorio.ejecucion(agregado_id)
        esperada = (actual.ultima_secuencia_de_evento if actual is not None else SECUENCIA_INICIAL) + 1
        if secuencia > esperada:
            raise SecuenciaFueraDeOrden(
                f"Evento {secuencia} de la Ruta {agregado_id}: se esperaba el {esperada}"
            )
        if secuencia < esperada:
            # Ya aplicado con otro identificador: se registra y se descarta.
            self._repositorio.registrar_evento(evento_id, self._reloj())
            return False
        self._repositorio.guardar_ejecucion(
            Ejecucion(
                ruta_id=agregado_id,
                vehiculo_id=str(cuerpo["vehiculo_id"]),
                transportista_id=cuerpo.get("transportista_id"),
                despachada_en=dt.datetime.fromisoformat(str(cuerpo["despachada_en"])),
                cerrada_en=None,
                ultima_secuencia_de_evento=secuencia,
            )
        )
        if tipo == TIPO_RUTA_REEMPLAZADA:
            self._repositorio.registrar_reemplazo(str(cuerpo["reemplaza_a"]), agregado_id)
        self._repositorio.registrar_evento(evento_id, self._reloj())
        return True

    def vigente(self, ruta_id: str) -> str:
        """La Ruta vigente del Vehiculo, siguiendo los reemplazos (D10)."""
        actual, vistas = ruta_id, {ruta_id}
        while (siguiente := self._repositorio.reemplazante_de(actual)) is not None and siguiente not in vistas:
            actual = siguiente
            vistas.add(actual)
        return actual

    def ultima_posicion(self, ruta_id: str) -> UltimaPosicion:
        """La de la Ruta vigente: una Ruta reemplazada responde con la
        posicion de la que la reemplaza (D10)."""
        ruta_id = self.vigente(ruta_id)
        posicion = self._repositorio.ultima_posicion(ruta_id)
        if posicion is None:
            raise SinPosiciones(f"La Ruta {ruta_id} no tiene posiciones informadas")
        ahora = self._reloj()
        sobre_la_recepcion = posicion.capturada_en > posicion.recibida_en
        referencia = posicion.recibida_en if sobre_la_recepcion else posicion.capturada_en
        ejecucion = self._repositorio.ejecucion(ruta_id)
        return UltimaPosicion(
            posicion=posicion,
            antiguedad_s=max((ahora - referencia).total_seconds(), 0.0),
            antiguedad_sobre_la_recepcion=sobre_la_recepcion,
            en_ejecucion=ejecucion is not None and ejecucion.cerrada_en is None,
        )

    def posiciones_recientes(self, ruta_id: str, segundos: float) -> tuple[Posicion, ...]:
        """Las posiciones de la Ruta vigente capturadas en los ultimos
        `segundos`, en orden de captura, a lo sumo `MAXIMO_DE_POSICIONES_RECIENTES`
        (reruteo por desvio, D17 y D18 del encargo 2026-11-22A)."""
        ruta_id = self.vigente(ruta_id)
        desde = self._reloj() - dt.timedelta(seconds=segundos)
        ultimas = self._repositorio.ultimas_posiciones(ruta_id, MAXIMO_DE_POSICIONES_RECIENTES)
        return tuple(sorted((p for p in ultimas if p.capturada_en >= desde), key=lambda p: (p.capturada_en, p.id)))

    def aplicar_retencion(self) -> int:
        """Borra las posiciones vencidas y sus ejecuciones (seccion 3.6).
        Devuelve cuantas posiciones borro."""
        limite = self._reloj() - dt.timedelta(days=RETENCION_DE_POSICIONES_DIAS)
        borradas = self._repositorio.aplicar_retencion(limite)
        _LOG.info("Retencion de %d dias aplicada: %d posiciones borradas", RETENCION_DE_POSICIONES_DIAS, borradas)
        return borradas
