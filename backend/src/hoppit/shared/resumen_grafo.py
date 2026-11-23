"""Medidas de topologia de un grafo vial.

Vive aca -- y no dentro de Preparacion de Datos ni de Cartografia -- porque
es el mismo dato en los dos extremos del artefacto versionado: quien extrae
el grafo lo mide antes de escribirlo, quien lo carga lo mide despues de
leerlo, y comparar ambas medidas campo a campo es lo que detecta que el
artefacto cambio de version sin que nadie lo declarara. Con dos
definiciones paralelas esa comparacion depende de que nadie toque una sola
de las dos.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ResumenGrafo:
    """Conteos de un grafo vial dirigido.

    Se distinguen las dos nociones de conectividad porque significan cosas
    distintas para ruteo: la debil dice si el mapa esta partido en pedazos
    inconexos; la fuerte, si respetando el sentido de circulacion se puede
    ir de cualquier nodo a cualquier otro. Un grafo debilmente conexo pero
    con muchas componentes fuertes tiene calles sin salida de las que un
    Vehiculo no puede volver, y eso rompe el ruteo antes que el tamano.
    """

    nodos: int
    aristas: int
    componentes_debiles: int
    componentes_fuertes: int
    nodos_en_mayor_componente_fuerte: int

    @property
    def fraccion_en_mayor_componente_fuerte(self) -> float:
        if self.nodos == 0:
            return 0.0
        return self.nodos_en_mayor_componente_fuerte / self.nodos
