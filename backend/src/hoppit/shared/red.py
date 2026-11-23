"""Lo comun a todo pedido HTTP que el proyecto hace a un servicio de terceros.

El User-Agent lleva el nombre del proyecto y nada mas: ni correo, ni
contacto, ni identidad (B-225, punto 2). Es el mismo en toda consulta, para
que el servicio pueda reconocer al cliente sin que el proyecto publique
datos de nadie.
"""
from __future__ import annotations

from typing import Final

USER_AGENT_DEL_PROYECTO: Final[str] = "HOPPIT-PFI-UADE/0.11"
