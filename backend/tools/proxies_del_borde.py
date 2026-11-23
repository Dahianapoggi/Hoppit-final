"""Lo comun a los dos servidores que corren detras del borde: en que
direcciones se confia para leer `X-Forwarded-For` (2026-10-28C, H1).

Detras de Caddy, todo pedido llega al nucleo con la direccion del contenedor
del borde. Sin confiar en ella, el limite de registros fallidos por origen se
vuelve uno solo para todos los clientes. El Compose define
`HOPPIT_PROXIES_CONFIABLES` con la subred de su red interna; fuera del
Compose la variable no existe y uvicorn sigue con su valor por omision, que
solo confia en 127.0.0.1 y ::1: un pedido directo no puede elegir su origen
con el encabezado.

Liviano a proposito: lo importa tambien el servicio de telemetria, que no
carga el grafo ni las capacidades del nucleo.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

VARIABLE_PROXIES_CONFIABLES = "HOPPIT_PROXIES_CONFIABLES"


def proxies_confiables(entorno: Mapping[str, str] = os.environ) -> str | None:
    """Las direcciones o subredes de las que se acepta `X-Forwarded-For`, en
    el formato de `forwarded_allow_ips` de uvicorn. Sin la variable, o en
    blanco, None: el valor por omision de uvicorn."""
    valor = entorno.get(VARIABLE_PROXIES_CONFIABLES, "").strip()
    return valor or None
