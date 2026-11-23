"""Capa opcional de riesgo vial (D25, encargo 2026-11-23A).

El riesgo de un arco es la frecuencia esperada de hechos con lesionados o
fallecidos por anio en ese tramo de calle, precalculada fuera de linea y guardada
como tabla por arco no dirigido, sobre los hechos viales del Observatorio de
Movilidad y Seguridad Vial (BA Data). Desde el encargo 2026-11-23B (D27, D28)
la tabla por omision es la del modelo elegido con validacion temporal y
adoptado por la regla de 2025 (gradient boosting de Poisson con la historia
de hechos del arco); la del 23A (binomial negativa) sigue versionada. Los dos
sentidos de una calle comparten el arco.

Con `mu = 0` (por omision) el riesgo no entra en ningun costo: el plan y la
huella de NFR-07 no cambian. Con `mu > 0` el costo de un arco para el ruteo
es `distancia + mu x riesgo` (mu en metros por hecho anual esperado); la
distancia y el tiempo que se informan siguen siendo los del camino elegido.

La tabla solo se carga si su sidecar declara el SHA-256 del grafo cargado:
una tabla de CABA sobre el grafo de San Isidro no tendria ningun arco en
comun y daria exposiciones nulas que parecerian reales.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
import os
from pathlib import Path
from typing import Final

VARIABLE_MU: Final[str] = "HOPPIT_RIESGO_VIAL_MU"
VARIABLE_TABLA: Final[str] = "HOPPIT_RIESGO_VIAL_TABLA"
DIRECTORIO_RIESGO_VIAL: Final[Path] = Path(__file__).resolve().parents[3] / "datos" / "riesgo_vial"
TABLA_PREDETERMINADA: Final[Path] = DIRECTORIO_RIESGO_VIAL / "riesgo_por_arco_caba_2026-11-23B.csv"
ATRIBUTO_RIESGO: Final[str] = "riesgo_vial"
ATRIBUTO_COSTO_GENERALIZADO: Final[str] = "costo_generalizado_m"
COLUMNA_ARCO: Final[str] = "arco"
COLUMNA_RIESGO: Final[str] = "riesgo_hechos_por_anio"


class ConfiguracionDeRiesgoInvalida(ValueError):
    """mu negativo, o mu positivo sin una tabla de riesgo para el grafo cargado."""


def clave_del_arco(origen: int, destino: int, largo_m: float) -> str:
    """El arco no dirigido de la tabla: menor nodo, mayor nodo y largo en metros
    redondeado (distingue calzadas paralelas entre el mismo par de nodos)."""
    return f"{min(origen, destino)}-{max(origen, destino)}-{int(round(largo_m))}"


@dataclasses.dataclass(frozen=True)
class ParametrosDeRiesgoVial:
    mu: float = 0.0
    tabla: Path | None = TABLA_PREDETERMINADA

    @classmethod
    def desde_el_entorno(cls) -> ParametrosDeRiesgoVial:
        mu = float(os.environ.get(VARIABLE_MU, "0") or "0")
        tabla = os.environ.get(VARIABLE_TABLA)
        return cls(mu=mu, tabla=Path(tabla) if tabla else TABLA_PREDETERMINADA)


@dataclasses.dataclass(frozen=True)
class CapaDeRiesgoVial:
    archivo: str
    sha256: str
    mu: float
    riesgo_por_arco: dict[str, float]

    @classmethod
    def cargar(cls, parametros: ParametrosDeRiesgoVial, sha256_del_grafo: str) -> CapaDeRiesgoVial | None:
        """La capa si la tabla existe y es del grafo cargado; None si no.
        Con mu > 0 y sin capa, falla: no se rutea con un riesgo que no hay."""
        if parametros.mu < 0:
            raise ConfiguracionDeRiesgoInvalida(f"{VARIABLE_MU} no puede ser negativo ({parametros.mu})")
        tabla = parametros.tabla
        capa = None
        if tabla is not None and tabla.is_file():
            sidecar = Path(f"{tabla}.procedencia.json")
            declarado = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
            contenido = tabla.read_bytes()
            sha = hashlib.sha256(contenido).hexdigest()
            if declarado.get("grafo_sha256") == sha256_del_grafo and declarado.get("sha256") == sha:
                filas = csv.DictReader(contenido.decode("utf-8").splitlines())
                capa = cls(tabla.name, sha, parametros.mu,
                           {f[COLUMNA_ARCO]: float(f[COLUMNA_RIESGO]) for f in filas})
        if capa is None and parametros.mu > 0:
            raise ConfiguracionDeRiesgoInvalida(
                f"{VARIABLE_MU} = {parametros.mu}, pero no hay tabla de riesgo vial verificada para el grafo cargado")
        return capa


@dataclasses.dataclass(frozen=True)
class ExposicionAlRiesgoVial:
    """Suma del riesgo esperado de los arcos que recorre un camino (hechos por
    anio en esos tramos). Es un indicador del tramo, no del Vehiculo: no hay
    exposicion por vehiculo-kilometro."""

    hechos_por_anio: float
    arcos: int
    mu: float
    archivo: str
    sha256: str
