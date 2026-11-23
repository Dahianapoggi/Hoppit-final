"""Semillas de reproducibilidad compartidas entre containers y herramientas.

Viven aca -- y no en cada tool -- porque son el mismo parametro: la semilla
que fija la instancia de demostracion tiene que ser la misma con la que se
mide la matriz de costos y la misma con la que se mide la planificacion. Con
una constante por tool, tres archivos pueden desincronizarse y los numeros
medidos dejan de ser comparables entre si sin que nada lo detecte, que es
justamente la clase de divergencia silenciosa que NFR-07 no admite.

Este modulo no importa nada del proyecto a proposito: es un valor, no una
politica, y cualquiera puede depender de el.
"""
from __future__ import annotations

# Semilla unica de las corridas reproducibles de fase 1. Cambiarla cambia la
# instancia de demostracion completa -- que nodos son Parada y que demanda
# tiene cada Orden -- y por lo tanto invalida toda medicion anterior. No es un
# parametro que se ajuste: es el que hace que dos corridas sean la misma.
SEMILLA_DE_INSTANCIA = 20260817
