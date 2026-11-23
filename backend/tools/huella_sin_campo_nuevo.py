"""Huella de la instancia canonica sin el campo `ordenes_no_planificadas`
(sesion 2026-10-13F, bloque P4; versionado en la sesion 2026-10-15A, S2,
desde el script `huella_diff.py` del directorio temporal de esa sesion, sin
cambiar su logica).

Cifra de B-221 que produce (punto 5): sin los 29 bytes del campo nuevo, las
dos SHA-256 de la huella con margen 0 son `1fa8a84c…` y `6e8f1f8f…`, las del
modelo hibrido sin dispersion, y los cuerpos de `GET /v1/rutas/{id}` son
identicos byte a byte.

Quita del cuerpo del POST, byte a byte, el sufijo
`,"ordenes_no_planificadas":[]` y recalcula las dos SHA-256 como lo hace
`tools/huella_instancia_canonica.py`, con la instancia canonica sin margen de
capacidad.

Unico cambio frente al script de 13F: `construir_contexto` exige desde el
bloque R de esa misma sesion el grafo y el Deposito, que antes tomaba por
omision; se pasan los que eran la omision entonces (`GRAFO_PREDETERMINADO` y
`ARTEFACTO_DEPOSITO_PREDETERMINADO`).

    python tools/huella_sin_campo_nuevo.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import pathlib
import sys
import tempfile
from typing import Final

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

FORMATO_LOG: Final[str] = "%(message)s"
_LOG = logging.getLogger("hoppit.tools.huella_sin_campo_nuevo")

CAMPO: Final[bytes] = b',"ordenes_no_planificadas":[]'
CIERRE: Final[bytes] = b"}"
JORNADA: Final[dt.date] = dt.date(2026, 9, 27)
NOMBRE_DE_LA_BASE: Final[str] = "h.sqlite3"


def sin_el_campo(cuerpo: bytes) -> bytes:
    """El cuerpo sin el campo, que se supone lo ultimo antes de la llave de
    cierre. Como en 13F, no se verifica aca: `main` informa si el cuerpo
    termina con el campo y la llave."""
    return cuerpo[: -len(CAMPO) - len(CIERRE)] + CIERRE


def main() -> int:
    from hoppit.bootstrap import GRAFO_PREDETERMINADO
    from hoppit.planificacion_rutas.puerto import SIN_MARGEN
    from hoppit.shared.semillas import SEMILLA_DE_INSTANCIA
    from tools.acceso_en_proceso import clave_efimera, cliente_autenticado
    from tools.servir_api import ARTEFACTO_DEPOSITO_PREDETERMINADO, DIRECTORIO_INSTANCIA, construir_contexto

    logging.basicConfig(level=logging.INFO, format=FORMATO_LOG)
    temporal = tempfile.mkdtemp()
    contexto = construir_contexto(
        DIRECTORIO_INSTANCIA,
        SEMILLA_DE_INSTANCIA,
        base_de_datos=pathlib.Path(temporal) / NOMBRE_DE_LA_BASE,
        reloj=lambda: JORNADA,
        margen_de_capacidad=SIN_MARGEN,
        grafo=GRAFO_PREDETERMINADO,
        deposito=ARTEFACTO_DEPOSITO_PREDETERMINADO,
        clave_privada=clave_efimera(),
    )
    cliente = cliente_autenticado(contexto)
    r = cliente.post("/v1/planificaciones", json={})
    cuerpo = r.content
    _LOG.info("el campo aparece %d vez; termina con el campo y '}': %s", cuerpo.count(CAMPO), cuerpo.endswith(CAMPO + CIERRE))
    sin = sin_el_campo(cuerpo)
    _LOG.info("diferencia de bytes entre el cuerpo y el cuerpo sin el campo: %d = %d", len(cuerpo) - len(sin), len(CAMPO))
    post = hashlib.sha256(sin)
    completo = hashlib.sha256(sin)
    for ruta in r.json()["rutas"]:
        completo.update(cliente.get(f"/v1/rutas/{ruta['id']}").content)
    _LOG.info("POST sin el campo       %s", post.hexdigest())
    _LOG.info("con GET, sin el campo   %s", completo.hexdigest())
    _LOG.info("plan %s | ordenes_no_planificadas %s", r.json()["plan"]["id"], r.json()["ordenes_no_planificadas"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
