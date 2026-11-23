"""Puerto publico de Cartografia y Grafo Vial (FR-01, NFR-08)."""
from __future__ import annotations

from pathlib import Path

from hoppit.shared.intervalo_por_celda import ArtefactoNoVerificado
from hoppit.shared.resumen_grafo import ResumenGrafo

from ._grafo import (
    Anclaje,
    ArtefactoDelGrafo,
    CaminoEntreNodos,
    DIRECTORIO_ARTEFACTOS,
    CaminoInexistente,
    CostoArco,
    GrafoVialAMBA,
    MatrizDeCostos,
    NodoGrafo,
    OrigenRotulo,
    ProveedorGrafoVial,
    RotuloNodo,
    Trazado,
    ZonaCircular,
    seleccionar_artefacto,
)
from ._riesgo_vial import (
    VARIABLE_MU,
    VARIABLE_TABLA,
    CapaDeRiesgoVial,
    ConfiguracionDeRiesgoInvalida,
    ExposicionAlRiesgoVial,
    ParametrosDeRiesgoVial,
    clave_del_arco,
)
from ._georef import (
    ATRIBUCION_DE_GEOREF,
    LARGO_MAXIMO_DEL_TEXTO,
    LIMITES_DE_GEOREF,
    CandidatoDeDireccion,
    GeocodificadorDeDirecciones,
    GeocodificadorGeoref,
    GeocodificadorNoResponde,
    LimiteDeConsultasAlcanzado,
)


def crear_servicio(ruta_grafo: Path | None = None,
                   riesgo_vial: ParametrosDeRiesgoVial | None = None) -> GrafoVialAMBA:
    """Punto de ensamblado del container -- llamado unicamente desde
    hoppit.bootstrap. El ensamblado indica que artefacto cargar. Sin
    argumento se carga el unico `grafo_*.graphml` de datos/grafos/; con
    mas de uno, `construir` falla (B-209, punto 6).

    La capa de riesgo vial (D25, 2026-11-23A) se configura con
    `HOPPIT_RIESGO_VIAL_MU` (0 por omision: el plan no cambia) y
    `HOPPIT_RIESGO_VIAL_TABLA`; la tabla solo se carga si es del grafo."""
    return GrafoVialAMBA(ruta_grafo, riesgo_vial or ParametrosDeRiesgoVial.desde_el_entorno())


__all__ = [
    "crear_servicio",
    "ZonaCircular",
    "ATRIBUCION_DE_GEOREF",
    "LARGO_MAXIMO_DEL_TEXTO",
    "LIMITES_DE_GEOREF",
    "CandidatoDeDireccion",
    "GeocodificadorDeDirecciones",
    "GeocodificadorGeoref",
    "GeocodificadorNoResponde",
    "LimiteDeConsultasAlcanzado",
    "seleccionar_artefacto",
    "DIRECTORIO_ARTEFACTOS",
    "GrafoVialAMBA",
    "CostoArco",
    "NodoGrafo",
    "ProveedorGrafoVial",
    "ResumenGrafo",
    "CaminoEntreNodos",
    "MatrizDeCostos",
    "CaminoInexistente",
    "RotuloNodo",
    "OrigenRotulo",
    "Trazado",
    "Anclaje",
    "ArtefactoDelGrafo",
    "ArtefactoNoVerificado",
    "VARIABLE_MU",
    "VARIABLE_TABLA",
    "CapaDeRiesgoVial",
    "ConfiguracionDeRiesgoInvalida",
    "ExposicionAlRiesgoVial",
    "ParametrosDeRiesgoVial",
    "clave_del_arco",
]
