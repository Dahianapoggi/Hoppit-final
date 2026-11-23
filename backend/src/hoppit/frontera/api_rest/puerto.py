"""Fachada de integracion -- API REST (FR-10, NFR-06).

Unico modulo de este adaptador que el resto del sistema importa. `crear_app`
recibe servicios ya ensamblados: quien los construye es `bootstrap`, y un
adaptador que armara sus propias dependencias dejaria de ser reemplazable
(ARQ-30).
"""
from __future__ import annotations

from hoppit.gestion_ordenes.puerto import Orden, ServicioGestionOrdenes
from hoppit.shared.ids import OrdenId

from ._aplicacion import ContextoDeLaApi, crear_app
from ._errores import CodigoDeError, ErrorDeContrato, ErrorPublicado
from ._memoria_de_eta import CAPACIDAD_DE_MEMORIA_DE_ETA, MemoriaDeEta


class FachadaIntegracion:
    """Superficie minima sobre Gestion de Ordenes.

    Precede a la app FastAPI y se conserva porque es lo que consumen los
    pasos 1-2 de ARQ-P1 sin pasar por HTTP: sirve para ejercitar el flujo
    desde una prueba o un tool sin levantar un servidor.
    """

    def __init__(self, gestion_ordenes: ServicioGestionOrdenes) -> None:
        self._ordenes = gestion_ordenes

    def post_ordenes(self, orden: Orden) -> None:
        """Pasos 1-2 de ARQ-P1."""
        self._ordenes.dar_de_alta(orden)

    def get_orden(self, id_orden: OrdenId) -> Orden:
        return self._ordenes.consultar(id_orden)


__all__ = [
    "crear_app",
    "ContextoDeLaApi",
    "FachadaIntegracion",
    "ErrorDeContrato",
    "ErrorPublicado",
    "CodigoDeError",
    "CAPACIDAD_DE_MEMORIA_DE_ETA",
    "MemoriaDeEta",
]
