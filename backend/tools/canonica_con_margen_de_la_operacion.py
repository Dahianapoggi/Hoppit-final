"""Que Plan da la instancia canonica con el margen de la operacion (0,05),
frente al de margen 0 (sesion 2026-10-13F, bloque P3; versionado en la
sesion 2026-10-15A, S2, desde el script `p3_canonica_005.py` del directorio
temporal de esa sesion, sin cambiar su logica).

Cifra de B-221 que produce (punto 6): con margen 0,05 la instancia canonica
da `plan-a2da58df5c05`, de 41.445,08 m, frente a `plan-0054fdf8cd71`, de
42.056,28 m, con margen 0.

Unico cambio frente al script de 13F: `construir_contexto` exige desde el
bloque R de esa misma sesion el grafo y el Deposito, que antes tomaba por
omision; se pasan los que eran la omision entonces (`GRAFO_PREDETERMINADO` y
`ARTEFACTO_DEPOSITO_PREDETERMINADO`). Los `print` pasan a `logging`.

    python tools/canonica_con_margen_de_la_operacion.py
"""
from __future__ import annotations

import datetime as dt
import logging
import pathlib
import sys
import tempfile
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.canonica_con_margen_de_la_operacion")

JORNADA: Final[dt.date] = dt.date(2026, 9, 27)
NOMBRE_DE_LA_BASE: Final[str] = "m.sqlite3"
NODOS_SI_NO_HAY_PUNTO: Final[int] = 3


def main() -> int:
    from hoppit.bootstrap import GRAFO_PREDETERMINADO
    from hoppit.planificacion_rutas.puerto import MARGEN_DE_LA_OPERACION, SIN_MARGEN
    from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
    from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
    from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, DIRECTORIO_INSTANCIA, construir_contexto

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    for ruidoso in ("hoppit", "httpx"):
        logging.getLogger(ruidoso).setLevel(logging.CRITICAL)
    _LOG.setLevel(logging.INFO)
    for margen in (SIN_MARGEN, MARGEN_DE_LA_OPERACION):
        temporal = tempfile.mkdtemp()
        contexto = construir_contexto(
            DIRECTORIO_INSTANCIA,
            SEMILLA_DE_INSTANCIA,
            base_de_datos=pathlib.Path(temporal) / NOMBRE_DE_LA_BASE,
            reloj=lambda: JORNADA,
            margen_de_capacidad=margen,
            grafo=GRAFO_PREDETERMINADO,
            deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
            clave_privada=clave_efimera(),
        )
        cliente = cliente_autenticado(contexto)
        cuerpo = cliente.post("/v1/planificaciones", json={}).json()
        _LOG.info(
            "margen %s: plan %s | distancia %.2f m | bultos %s | capacidad_total (nominal) %s | afuera %s",
            margen, cuerpo["plan"]["id"], cuerpo["distancia_total_m"], cuerpo["demanda_total"],
            cuerpo["capacidad_total"], cuerpo["ordenes_no_planificadas"],
        )
        for ruta in cuerpo["rutas"]:
            detalle = cliente.get(f"/v1/rutas/{ruta['id']}").json()
            _LOG.info(
                "   %s | %d Paradas | %d de %d | %.1f m | nodos %s",
                ruta["vehiculo_id"], ruta["cantidad_paradas"], ruta["carga"], ruta["capacidad_vehiculo"],
                ruta["distancia_m"],
                [p["punto"]["nodo"] if "punto" in p else list(p)[:NODOS_SI_NO_HAY_PUNTO] for p in detalle["paradas"]],
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
