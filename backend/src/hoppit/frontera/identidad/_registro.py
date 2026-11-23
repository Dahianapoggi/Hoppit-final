"""Las contrasenas no llegan a los registros (seccion 4.2).

Ningun modulo registra el cuerpo de `/v1/sesiones`; esto es la segunda
linea. Mientras se atiende un ingreso, la contrasena recibida queda anotada
en una variable de contexto, y un filtro de `logging` reemplaza cualquier
aparicion suya en un registro --en el mensaje o en sus argumentos-- por un
marcador, antes de que lo vea ningun manejador.

El filtro se aplica en la fabrica de registros y no en un manejador: los
manejadores los configura quien arranca el proceso (uvicorn, pytest), y un
filtro puesto en uno no cubre a los demas.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
from collections.abc import Callable, Iterator

MARCADOR_DE_CREDENCIAL = "[credencial oculta]"

_CREDENCIALES: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "credenciales_en_curso", default=()
)


@contextlib.contextmanager
def credenciales_ocultas(*credenciales: str) -> Iterator[None]:
    """Mientras dure el bloque, ningun registro muestra estos valores."""
    previas = _CREDENCIALES.get()
    marca = _CREDENCIALES.set(previas + tuple(c for c in credenciales if c))
    try:
        yield
    finally:
        _CREDENCIALES.reset(marca)


class FiltroDeCredenciales(logging.Filter):
    """Reemplaza las credenciales en curso por un marcador."""

    def filter(self, record: logging.LogRecord) -> bool:
        credenciales = _CREDENCIALES.get()
        if not credenciales:
            return True
        mensaje = record.getMessage()
        if any(c in mensaje for c in credenciales):
            for credencial in credenciales:
                mensaje = mensaje.replace(credencial, MARCADOR_DE_CREDENCIAL)
            record.msg = mensaje
            record.args = None
        return True


_FILTRO = FiltroDeCredenciales()
_INSTALADO = False


def instalar_filtro_de_credenciales() -> None:
    """Pasa todo registro nuevo por el filtro. Idempotente."""
    global _INSTALADO
    if _INSTALADO:
        return
    fabrica_previa: Callable[..., logging.LogRecord] = logging.getLogRecordFactory()

    def fabrica(*args: object, **kwargs: object) -> logging.LogRecord:
        registro = fabrica_previa(*args, **kwargs)
        _FILTRO.filter(registro)
        return registro

    logging.setLogRecordFactory(fabrica)
    _INSTALADO = True
