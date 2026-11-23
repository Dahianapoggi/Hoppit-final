"""Alertas por reglas (CU-08, FR-07, FR-18; B-11; B-233, puntos 12 y 14;
2026-10-27B, A). Las reglas son propuesta del chat central (B-259, punto 3).

Las alertas se derivan: se calculan al consultarlas y no se persisten (B-11).
Cada condicion vigente produce una sola alerta, porque la alerta es la
condicion misma vista en el momento de la consulta: el flujo 4a de CU-08
(una alerta ya emitida no se repite) se cumple por construccion. Evaluarlas
no cambia el estado de ninguna Orden ni de ninguna Ruta (postcondicion de
CU-08). El "intervalo de evaluacion" de CU-08 es el de la consulta que hace
la interfaz, cada 60 s; no hay un proceso temporizado en el servidor.

Este componente es una hoja: no conoce las Ordenes ni las Rutas. Recibe lo
que observa la frontera, ya traducido a estos tipos, y devuelve las alertas.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
from collections.abc import Sequence
from zoneinfo import ZoneInfo

from ._incidentes import Incidente

# Una Orden en Creada por mas de este tiempo desde su ultima transicion: la
# regla relevada en el trabajo de campo (nota de CU-08).
UMBRAL_DE_PERMANENCIA_EN_CREADA_H = 48

# La banda del ETA no esta calibrada al 90 %: en LaDe la banda de p10 a p90
# cubre menos de lo nominal (B-200). La alerta lo dice.
ACLARACION_DE_LA_BANDA = (
    "El p90 es el de la banda estimada, que no está calibrada: en los datos de "
    "calibración cubre menos del 90 %."
)


class TipoDeAlerta(enum.Enum):
    INCIDENTE_EN_RUTA = "incidente_en_ruta"
    PERMANENCIA_EN_CREADA = "permanencia_en_creada"
    RIESGO_DE_PLAZO = "riesgo_de_plazo"
    DESVIO_DE_RUTA = "desvio_de_ruta"
    RESULTADO_DE_CAMPO_SOBRE_RUTA_VIGENTE = "resultado_de_campo_sobre_ruta_vigente"
    DECLARACION_DE_CAMPO_PREVALECE = "declaracion_de_campo_prevalece"
    DECLARACION_DE_CAMPO_NO_APLICADA = "declaracion_de_campo_no_aplicada"
    RIESGO_VIAL_EN_RUTA = "riesgo_vial_en_ruta"


@dataclasses.dataclass(frozen=True)
class OrdenObservada:
    """Lo que las reglas necesitan saber de una Orden, segun la frontera."""

    id: str
    en_creada: bool
    planificada_o_en_transito: bool
    ultima_transicion_en: dt.datetime | None
    plazo_comprometido: dt.date | None = None
    eta_p50: dt.datetime | None = None
    eta_p90: dt.datetime | None = None


@dataclasses.dataclass(frozen=True)
class RutaObservada:
    """Una Ruta de la jornada y sus Ordenes que todavia no terminaron."""

    id: str
    ordenes_no_terminales: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Alerta:
    """Una condicion vigente, con lo que hace falta para actuar sobre ella."""

    tipo: TipoDeAlerta
    motivo: str
    desde: dt.datetime
    ruta: str | None = None
    orden: str | None = None
    ordenes: tuple[str, ...] = ()
    incidente: str | None = None
    eta_p50: dt.datetime | None = None
    eta_p90: dt.datetime | None = None
    plazo_comprometido: dt.date | None = None
    vehiculo: str | None = None
    distancia_m: float | None = None
    propuesta: str | None = None


@dataclasses.dataclass(frozen=True)
class DesvioObservado:
    """Un desvio vigente de una Ruta, segun la frontera (D17 y D18 del encargo
    2026-11-22A), con la propuesta de reruteo que se genero, si alguna."""

    ruta: str
    vehiculo: str
    distancia_m: float
    desde: dt.datetime
    umbral_m: float
    propuesta: str | None = None


@dataclasses.dataclass(frozen=True)
class RiesgoVialObservado:
    """Una propuesta de reruteo por riesgo vial pendiente (D31, encargo
    2026-11-23E), segun la frontera, con su compromiso medido sobre los
    caminos reales de lo pendiente."""

    ruta: str
    vehiculo: str
    propuesta: str
    desde: dt.datetime
    distancia_vigente_m: float
    distancia_propuesta_m: float
    minutos_vigentes: float
    minutos_propuestos: float
    exposicion_vigente: float
    exposicion_propuesta: float


def _numero(valor: float, decimales: int, signo: bool = False) -> str:
    texto = f"{valor:+.{decimales}f}" if signo else f"{valor:.{decimales}f}"
    return texto.replace("-", "\u2212").replace(".", ",")


def texto_de_riesgo_vial(r: RiesgoVialObservado) -> str:
    """El compromiso en numeros (D31): km y minutos de mas contra la
    exposicion predicha de menos."""
    km = (r.distancia_propuesta_m - r.distancia_vigente_m) / 1000.0
    relativa = 0.0 if r.distancia_vigente_m <= 0 else 100.0 * (r.distancia_propuesta_m / r.distancia_vigente_m - 1.0)
    minutos = r.minutos_propuestos - r.minutos_vigentes
    baja = 0.0 if r.exposicion_vigente <= 0 else 100.0 * (r.exposicion_propuesta / r.exposicion_vigente - 1.0)
    return (f"La Ruta de {r.vehiculo} pasa por tramos con más hechos viales predichos. "
            f"Alternativa: {_numero(km, 1, True)} km ({_numero(relativa, 1, True)} %), "
            f"{_numero(minutos, 0, True)} min, exposición predicha {_numero(baja, 0, True)} % "
            f"(de {_numero(r.exposicion_vigente, 1)} a {_numero(r.exposicion_propuesta, 1)} hechos/año "
            f"en los tramos recorridos)")


@dataclasses.dataclass(frozen=True)
class DeclaracionDeCampoObservada:
    """Un asiento de campo de una Orden (CU-06, 4a; encargo 2026-11-23B, D29),
    segun la frontera: un resultado declarado desde la aplicacion con su hora
    que se aplico sobre la Ruta vigente, que prevalecio sobre un resultado
    posterior o que no se aplico."""

    orden: str
    ruta_declarada: str
    secuencia_declarada: int
    ruta_actual: str | None
    hecho_en: dt.datetime
    recibido_en: dt.datetime
    efecto: str
    sobre_otra_ruta: bool
    nota: str


def evaluar_alertas(
    jornada: dt.date,
    ahora: dt.datetime,
    zona: ZoneInfo,
    incidentes: Sequence[Incidente],
    rutas_de_la_jornada: Sequence[RutaObservada],
    ordenes: Sequence[OrdenObservada],
    desvios: Sequence[DesvioObservado] = (),
    declaraciones_de_campo: Sequence[DeclaracionDeCampoObservada] = (),
    riesgos_viales: Sequence[RiesgoVialObservado] = (),
) -> tuple[Alerta, ...]:
    """Las alertas vigentes de la jornada, en un orden estable: por tipo y por
    el identificador de lo que afectan. Un desvio da una alerta por Ruta
    mientras siga vigente (CU-08, 4a: no se duplica)."""
    alertas = [
        *_incidentes_en_ruta(jornada, zona, incidentes, rutas_de_la_jornada),
        *_permanencia_en_creada(ahora, ordenes),
        *_riesgo_de_plazo(zona, ordenes),
        *_desvios_de_ruta(desvios),
        *_declaraciones_de_campo(jornada, zona, declaraciones_de_campo),
        *_riesgos_viales(riesgos_viales),
    ]
    orden_de_tipo = {tipo: posicion for posicion, tipo in enumerate(TipoDeAlerta)}
    return tuple(
        sorted(
            alertas,
            key=lambda a: (orden_de_tipo[a.tipo], a.ruta or "", a.orden or "", a.incidente or ""),
        )
    )


def _incidentes_en_ruta(
    jornada: dt.date,
    zona: ZoneInfo,
    incidentes: Sequence[Incidente],
    rutas: Sequence[RutaObservada],
) -> list[Alerta]:
    """Un Incidente vigente sobre una Ruta con Ordenes no terminales: una
    alerta por Incidente y Ruta, con sus Ordenes afectadas.

    Los Incidentes no tienen cierre en el modelo actual (FR-18): un Incidente
    se considera vigente durante la jornada en que se declaro, en hora local.
    """
    activas = {ruta.id: ruta for ruta in rutas if ruta.ordenes_no_terminales}
    alertas = []
    for incidente in incidentes:
        if incidente.declarado_en.astimezone(zona).date() != jornada:
            continue
        # Sin repetir: una Ruta nombrada dos veces es una sola condicion.
        for id_ruta in dict.fromkeys(incidente.rutas_afectadas):
            ruta = activas.get(str(id_ruta))
            if ruta is None:
                continue
            alertas.append(
                Alerta(
                    tipo=TipoDeAlerta.INCIDENTE_EN_RUTA,
                    motivo=(
                        f"Incidente de tipo {incidente.tipo.value.replace('_', ' ')} sobre la Ruta, "
                        f"que tiene {len(ruta.ordenes_no_terminales)} Órdenes sin terminar."
                    ),
                    desde=incidente.declarado_en,
                    ruta=ruta.id,
                    ordenes=ruta.ordenes_no_terminales,
                    incidente=str(incidente.id),
                )
            )
    return alertas


def _permanencia_en_creada(ahora: dt.datetime, ordenes: Sequence[OrdenObservada]) -> list[Alerta]:
    umbral = dt.timedelta(hours=UMBRAL_DE_PERMANENCIA_EN_CREADA_H)
    alertas = []
    for orden in ordenes:
        if not orden.en_creada or orden.ultima_transicion_en is None:
            continue
        desde = orden.ultima_transicion_en + umbral
        if ahora < desde:
            continue
        alertas.append(
            Alerta(
                tipo=TipoDeAlerta.PERMANENCIA_EN_CREADA,
                motivo=f"La Orden lleva más de {UMBRAL_DE_PERMANENCIA_EN_CREADA_H} h en Creada sin planificarse.",
                desde=desde,
                orden=orden.id,
            )
        )
    return alertas


def _riesgo_de_plazo(zona: ZoneInfo, ordenes: Sequence[OrdenObservada]) -> list[Alerta]:
    """Una Orden planificada o en transito cuyo p90 de la primera entrega
    estimada queda despues del fin del dia del plazo, en hora local."""
    alertas = []
    for orden in ordenes:
        if (
            not orden.planificada_o_en_transito
            or orden.plazo_comprometido is None
            or orden.eta_p90 is None
            or orden.ultima_transicion_en is None
        ):
            continue
        if orden.eta_p90.astimezone(zona).date() <= orden.plazo_comprometido:
            continue
        alertas.append(
            Alerta(
                tipo=TipoDeAlerta.RIESGO_DE_PLAZO,
                motivo=(
                    f"La entrega estimada (p90) queda después del plazo comprometido "
                    f"({orden.plazo_comprometido.isoformat()}). {ACLARACION_DE_LA_BANDA}"
                ),
                desde=orden.ultima_transicion_en,
                orden=orden.id,
                eta_p50=orden.eta_p50,
                eta_p90=orden.eta_p90,
                plazo_comprometido=orden.plazo_comprometido,
            )
        )
    return alertas


def _desvios_de_ruta(desvios: Sequence[DesvioObservado]) -> list[Alerta]:
    """Una alerta por Ruta desviada (la ultima observacion, si llegan dos)."""
    por_ruta = {d.ruta: d for d in desvios}
    return [
        Alerta(
            tipo=TipoDeAlerta.DESVIO_DE_RUTA,
            motivo=(f"El Vehiculo {d.vehiculo} esta a {d.distancia_m:.0f} m del trazado de la Ruta {d.ruta} "
                    f"(umbral {d.umbral_m:.0f} m)"
                    + ("; hay una propuesta de reruteo para aprobar o rechazar" if d.propuesta else "")),
            desde=d.desde, ruta=d.ruta, vehiculo=d.vehiculo, distancia_m=round(d.distancia_m, 1),
            propuesta=d.propuesta,
        )
        for d in por_ruta.values()
    ]


def _riesgos_viales(riesgos: Sequence[RiesgoVialObservado]) -> list[Alerta]:
    """D31 (2026-11-23E): una alerta por propuesta por riesgo vial pendiente;
    desaparece al aprobarla, rechazarla o vencer."""
    return [
        Alerta(tipo=TipoDeAlerta.RIESGO_VIAL_EN_RUTA, motivo=texto_de_riesgo_vial(r), desde=r.desde, ruta=r.ruta,
               vehiculo=r.vehiculo, propuesta=r.propuesta)
        for r in {x.propuesta: x for x in riesgos}.values()
    ]


def _declaraciones_de_campo(
    jornada: dt.date, zona: ZoneInfo, declaraciones: Sequence[DeclaracionDeCampoObservada]
) -> list[Alerta]:
    """D29 (encargo 2026-11-23B): una alerta por Parada declarada y efecto,
    durante la jornada en que se recibio la declaracion. Sin cierre propio,
    como las de Incidente."""
    grupos: dict[tuple[TipoDeAlerta, str, int], list[DeclaracionDeCampoObservada]] = {}
    for d in declaraciones:
        if d.recibido_en.astimezone(zona).date() != jornada:
            continue
        if d.efecto == "no_aplicada":
            tipo = TipoDeAlerta.DECLARACION_DE_CAMPO_NO_APLICADA
        elif d.efecto == "prevalece":
            tipo = TipoDeAlerta.DECLARACION_DE_CAMPO_PREVALECE
        elif d.sobre_otra_ruta:
            tipo = TipoDeAlerta.RESULTADO_DE_CAMPO_SOBRE_RUTA_VIGENTE
        else:
            continue
        grupos.setdefault((tipo, d.ruta_declarada, d.secuencia_declarada), []).append(d)
    alertas = []
    for (tipo, ruta, secuencia), ds in grupos.items():
        primera = ds[0]
        if tipo is TipoDeAlerta.RESULTADO_DE_CAMPO_SOBRE_RUTA_VIGENTE:
            motivo = (f"Resultado declarado sin conexion aplicado sobre la Ruta vigente {primera.ruta_actual}: "
                      f"la Parada {secuencia} de la Ruta {ruta} se reemplazo mientras tanto")
        elif tipo is TipoDeAlerta.DECLARACION_DE_CAMPO_PREVALECE:
            motivo = f"La declaracion de campo de la Parada {secuencia} de la Ruta {ruta}: {primera.nota}"
        else:
            motivo = f"La declaracion de campo de la Parada {secuencia} de la Ruta {ruta} no se aplico: {primera.nota}"
        alertas.append(
            Alerta(
                tipo=tipo,
                motivo=motivo,
                desde=min(d.recibido_en for d in ds),
                ruta=ruta,
                ordenes=tuple(sorted(d.orden for d in ds)),
            )
        )
    return alertas
