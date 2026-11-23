"""Mapa de traduccion de estados de un Proveedor de ultima milla y registro de
los reportes rechazados (CU-12, FR-09; version minima de la decision D14 del
encargo 2026-11-20A).

El glosario consigna que la tabla de traduccion desde los codigos de cada
proveedor no esta definida (precondicion no satisfecha). Esta version la deja
declarable por Proveedor y trae un mapa de un **proveedor de prueba**
(`MAPA_DEL_PROVEEDOR_DE_PRUEBA`) como supuesto declarado: no es el vocabulario
de ningun proveedor real.

Lo que se guarda: el mapa (codigo del proveedor -> estado canonico) y, por
cada reporte rechazado, el codigo tal como llego, para que el operador sepa
que existe (flujo 3a) o que inconsistencia hubo (2a, 4a). Un codigo ajeno
nunca se guarda ni se publica como estado de una Orden: el estado vigente de
la Orden lo escribe Gestion de Ordenes, solo con valores del catalogo.

Este componente es una hoja: que estados canonicos admite un mapa lo valida
la frontera contra Gestion de Ordenes.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import uuid
from collections.abc import Callable, Mapping
from typing import Final

from hoppit.shared.tiempo import instante_actual

from ._acuerdos import RepositorioDeAcuerdos, RepositorioDeAcuerdosEnMemoria

MAPA_DEL_PROVEEDOR_DE_PRUEBA: Final[Mapping[str, str]] = {
    "ENTREGADO": "completada",
    "NO_ENTREGADO": "fallida",
    "DEVUELTO_A_ORIGEN": "devuelta",
}
"""Proveedor de prueba [supuesto, D14]: tres codigos inventados para ejercitar
CU-12, uno por estado que un ejecutor de la entrega puede informar."""

TABLA_DE_MAPAS: Final[str] = "mapas_de_estados_de_proveedor"
TABLA_DE_RECHAZOS: Final[str] = "reportes_de_proveedor_rechazados"
_LARGO_DEL_ID: Final[int] = 12


class TipoDeRechazo(enum.Enum):
    ORDEN_INEXISTENTE = "orden_inexistente"
    """2a: la referencia no corresponde a ninguna Orden registrada."""

    ORDEN_DE_OTRO_PROVEEDOR = "orden_de_otro_proveedor"
    """Precondicion: la Orden no esta asignada a un Vehiculo del Proveedor."""

    CODIGO_NO_MAPEADO = "codigo_no_mapeado"
    """3a: el codigo no figura en el mapa del Proveedor."""

    TRANSICION_INADMISIBLE = "transicion_inadmisible"
    """4a: la transicion no es admisible desde el estado vigente (inconsistencia)."""


@dataclasses.dataclass(frozen=True)
class ReporteRechazado:
    id: str
    proveedor_id: str
    referencia_de_orden: str
    codigo_reportado: str
    tipo: TipoDeRechazo
    causa: str
    recibido_en: dt.datetime


class ServicioDeReportesDeProveedor:
    """Los mapas declarados y los reportes rechazados, por Proveedor."""

    def __init__(self, repositorio: RepositorioDeAcuerdos | None = None,
                 reloj: Callable[[], dt.datetime] = instante_actual) -> None:
        self._repositorio: RepositorioDeAcuerdos = (
            repositorio if repositorio is not None else RepositorioDeAcuerdosEnMemoria())
        self._reloj = reloj

    def declarar_mapa(self, proveedor_id: str, codigos: Mapping[str, str]) -> dict[str, str]:
        """Reemplaza el mapa del Proveedor (la frontera ya valido los estados)."""
        mapa = {str(c): str(e) for c, e in codigos.items()}
        self._repositorio.guardar(TABLA_DE_MAPAS, proveedor_id, {"proveedor_id": proveedor_id, "codigos": mapa})
        return mapa

    def mapa_de(self, proveedor_id: str) -> dict[str, str] | None:
        d = self._repositorio.obtener(TABLA_DE_MAPAS, proveedor_id)
        return None if d is None else {str(c): str(e) for c, e in d["codigos"].items()}

    def rechazar(self, proveedor_id: str, referencia_de_orden: str, codigo_reportado: str, tipo: TipoDeRechazo,
                 causa: str) -> ReporteRechazado:
        """Asienta el rechazo: es la notificacion al operador (3a) y la
        constancia de la inconsistencia (4a); nada se descarta en silencio (2a)."""
        rechazo = ReporteRechazado(f"rech-{uuid.uuid4().hex[:_LARGO_DEL_ID]}", proveedor_id, referencia_de_orden,
                                   codigo_reportado, tipo, causa, self._reloj())
        d = dataclasses.asdict(rechazo)
        d["tipo"] = rechazo.tipo.value
        d["recibido_en"] = rechazo.recibido_en.isoformat()
        self._repositorio.guardar(TABLA_DE_RECHAZOS, rechazo.id, d)
        return rechazo

    def rechazos_de(self, proveedor_id: str) -> tuple[ReporteRechazado, ...]:
        return tuple(
            ReporteRechazado(d["id"], d["proveedor_id"], d["referencia_de_orden"], d["codigo_reportado"],
                             TipoDeRechazo(d["tipo"]), d["causa"], dt.datetime.fromisoformat(d["recibido_en"]))
            for d in self._repositorio.listar(TABLA_DE_RECHAZOS) if d["proveedor_id"] == proveedor_id
        )
