"""HOPPIT -- plataforma de gestion de flotas y logistica de ultima milla.

Arquitectura logica: monolito modular con arquitectura hexagonal,
particionado por capacidad de negocio, preparado para que cada capacidad
pueda extraerse a un proceso propio (documento integrador, seccion 2.3.2).
Fase 1: un unico proceso, con fronteras de paquete tan disciplinadas
como si cada una fuera a extraerse a un proceso propio.
Ver README.md para el detalle del criterio.
"""
from __future__ import annotations

from importlib import metadata

# Version del producto, en versionado semantico X.Y.Z y sin relleno de
# ceros. Se lee de la metadata del paquete instalado en vez de escribirse
# aca: la fuente de verdad es pyproject.toml, y tenerla en dos lugares abre
# la puerta a que el numero que se publica no sea el que se empaqueto.
#
# NO es la version del contrato de la API. Son dos cosas que conviven: el
# prefijo /v1 solo cambia cuando se rompe compatibilidad, mientras que esta
# avanza con cada entrega. Agregar un campo opcional a una respuesta sube la
# menor y deja el contrato donde esta.
_VERSION_SIN_INSTALAR = "0.0.0"

try:
    __version__ = metadata.version("hoppit")
except metadata.PackageNotFoundError:  # pragma: no cover
    # El paquete no esta instalado (se corre desde el arbol de fuentes sin
    # `pip install -e .`). Se declara desconocida en vez de inventar un
    # numero que podria no corresponder a nada.
    __version__ = _VERSION_SIN_INSTALAR

__all__ = ["__version__"]
