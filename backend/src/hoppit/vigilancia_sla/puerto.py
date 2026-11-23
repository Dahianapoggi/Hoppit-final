"""Puerto publico de Vigilancia de SLA e Incidentes.

Incidentes (FR-18; CU-07, paso 1): construido desde la sesion 2026-10-15A
solo el registro y la consulta, sin recalculo ni efecto sobre las Rutas ni
las Ordenes, con persistencia propia y el evento `IncidenteDeclarado` en el
bus. La API REST lo expone desde la sesion 2026-10-19A (`/v1/incidentes`).

Alertas por reglas (CU-08, FR-07; 2026-10-27B, A): derivadas, se calculan al
consultarlas y no se persisten (`evaluar_alertas`). La frontera les pasa lo
que observa de las Ordenes y las Rutas, porque este componente es una hoja.

Acuerdos de nivel de servicio (CU-10, FR-08; 2026-11-20A, bloque 4 del encargo,
version minima): Cliente comercial, Proveedor de ultima milla con sus
Vehiculos, acuerdo con umbral y plazo, Ordenes que origina cada Cliente y la
evaluacion por periodo y Proveedor (`evaluar_cumplimiento`), que no persiste.

El resto de la vigilancia de SLA (FR-08: penalizaciones, evaluacion periodica)
no esta implementado. La interfaz se reserva para preservar la frontera tal
como esta trazada en ARQ-N1.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from hoppit.shared.bus import Bus
from hoppit.shared.ids import IncidenteId
from hoppit.shared.tiempo import instante_actual

from ._acuerdos import (
    TAMANO_MINIMO_REPRESENTATIVO,
    AcuerdoDeNivelDeServicio,
    AcuerdoNoEncontrado,
    ClienteComercial,
    ClienteComercialNoEncontrado,
    EvaluacionDeCumplimiento,
    OrdenEjecutada,
    ProveedorDeUltimaMilla,
    ProveedorNoEncontrado,
    RegistroInvalido,
    RepositorioDeAcuerdos,
    RepositorioDeAcuerdosEnMemoria,
    RepositorioDeAcuerdosSQLite,
    ServicioDeAcuerdos,
    evaluar_cumplimiento,
)
from ._reportes_de_proveedor import (
    MAPA_DEL_PROVEEDOR_DE_PRUEBA,
    ReporteRechazado,
    ServicioDeReportesDeProveedor,
    TipoDeRechazo,
)
from ._alertas import (
    UMBRAL_DE_PERMANENCIA_EN_CREADA_H,
    Alerta,
    DeclaracionDeCampoObservada,
    DesvioObservado,
    RiesgoVialObservado,
    texto_de_riesgo_vial,
    OrdenObservada,
    RutaObservada,
    TipoDeAlerta,
    evaluar_alertas,
)
from ._incidentes import (
    AlcanceDeIncidenteInvalido,
    AlcanceDelIncidente,
    Incidente,
    IncidenteNoEncontrado,
    RepositorioIncidentes,
    RutasAfectadasInexistentes,
    RutasInexistentes,
    ServicioIncidentes,
    TipoDeIncidente,
    TipoDeIncidenteInvalido,
    ZonaAfectada,
    ZonaAfectadaInvalida,
    RADIO_MAXIMO_DE_LA_ZONA_M,
    RADIO_MINIMO_DE_LA_ZONA_M,
    nuevo_identificador,
)
from ._repositorio import RepositorioIncidentesEnMemoria, RepositorioIncidentesSQLite


def crear_servicio() -> None:
    raise NotImplementedError("La vigilancia de SLA (FR-08) no esta implementada")


def crear_servicio_de_incidentes(
    bus: Bus,
    rutas_inexistentes: RutasInexistentes,
    repositorio: RepositorioIncidentes | None = None,
    reloj: Callable[[], dt.datetime] = instante_actual,
    generar_identificador: Callable[[], IncidenteId] = nuevo_identificador,
) -> ServicioIncidentes:
    """Punto de ensamblado del registro de Incidentes. `rutas_inexistentes`
    lo provee el ensamblado con el puerto de Planificacion de Rutas; sin
    repositorio queda el de memoria, como en Gestion de Ordenes."""
    return ServicioIncidentes(
        repositorio if repositorio is not None else RepositorioIncidentesEnMemoria(),
        bus,
        rutas_inexistentes,
        reloj=reloj,
        generar_identificador=generar_identificador,
    )


__all__ = [
    "ZonaAfectada",
    "ZonaAfectadaInvalida",
    "RADIO_MAXIMO_DE_LA_ZONA_M",
    "RADIO_MINIMO_DE_LA_ZONA_M",
    "crear_servicio",
    # CU-10 (2026-11-20A).
    "TAMANO_MINIMO_REPRESENTATIVO",
    "AcuerdoDeNivelDeServicio",
    "AcuerdoNoEncontrado",
    "ClienteComercial",
    "ClienteComercialNoEncontrado",
    "EvaluacionDeCumplimiento",
    "OrdenEjecutada",
    "ProveedorDeUltimaMilla",
    "ProveedorNoEncontrado",
    "RegistroInvalido",
    "RepositorioDeAcuerdos",
    "RepositorioDeAcuerdosEnMemoria",
    "RepositorioDeAcuerdosSQLite",
    "ServicioDeAcuerdos",
    "evaluar_cumplimiento",
    # CU-12 (2026-11-20A).
    "MAPA_DEL_PROVEEDOR_DE_PRUEBA",
    "ReporteRechazado",
    "ServicioDeReportesDeProveedor",
    "TipoDeRechazo",
    "evaluar_alertas",
    "Alerta",
    "DeclaracionDeCampoObservada",
    "DesvioObservado",
    "RiesgoVialObservado",
    "texto_de_riesgo_vial",
    "OrdenObservada",
    "RutaObservada",
    "TipoDeAlerta",
    "UMBRAL_DE_PERMANENCIA_EN_CREADA_H",
    "crear_servicio_de_incidentes",
    "AlcanceDeIncidenteInvalido",
    "AlcanceDelIncidente",
    "Incidente",
    "IncidenteNoEncontrado",
    "RepositorioIncidentes",
    "RepositorioIncidentesEnMemoria",
    "RepositorioIncidentesSQLite",
    "RutasAfectadasInexistentes",
    "RutasInexistentes",
    "ServicioIncidentes",
    "TipoDeIncidente",
    "TipoDeIncidenteInvalido",
]
