"""Puerto de presentacion -- solo lectura (FR-02, FR-07, NFR-10)."""
from __future__ import annotations

from hoppit.planificacion_rutas.puerto import SolucionRuteo


class PuertoPresentacion:
    def mostrar_ruta(self, solucion: SolucionRuteo) -> None:
        """Pasos 19 de ARQ-P1 -- FR-02. Sin implementar: la interfaz del
        operador consume la Ruta y las alertas de FR-07 por la API REST
        (NFR-10), no por este puerto."""
        raise NotImplementedError("renderizado de Ruta pendiente de implementacion")
