"""Memoria del ETA por Ruta (B-260, punto 5c; 2026-10-27C, F3).

El ETA de una Ruta es determinista: sale de la tabla verificada, con semilla
fija, desde la hora de salida del ensamblado, a la velocidad declarada y con
las distancias de la matriz de Cartografia. Todo eso es fijo para un
`ContextoDeLaApi`. Lo unico que varia es la Ruta, y su identificador se
deriva de su contenido (B-82): la instancia con el Deposito y la jornada, el
Vehiculo y las Paradas con sus Ordenes en orden. Una Ruta que cambia es otra
Ruta, con otro identificador; desasignar una Orden invalida la Ruta, no la
modifica.

Por eso el ETA se puede guardar por identificador de Ruta sin que la
respuesta cambie. Lo usan `GET /v1/rutas/{id}` y la regla `riesgo_de_plazo`
de las alertas, que antes lo recalculaban en cada consulta. La capacidad es
acotada y se descarta el menos usado. No se persiste: el ETA se sigue
calculando al responder (B-193, punto 3, criterio d), sólo que una vez por
Ruta y proceso.
"""
from __future__ import annotations

import collections
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

CAPACIDAD_DE_MEMORIA_DE_ETA = 256
"""Rutas cuyo ETA se guarda. Una jornada de demostracion tiene pocas Rutas;
el tope impide que un proceso largo acumule las de todas las jornadas."""

_Valor = TypeVar("_Valor")


class MemoriaDeEta(Generic[_Valor]):
    """Guarda un valor por identificador de Ruta, con descarte del menos usado.

    Con capacidad 0 no guarda nada y calcula siempre: es la referencia con que
    las pruebas comparan. Los endpoints sincronos de FastAPI corren en varios
    hilos, asi que el acceso va con cerrojo; el calculo, fuera de el."""

    def __init__(self, capacidad: int = CAPACIDAD_DE_MEMORIA_DE_ETA) -> None:
        if capacidad < 0:
            raise ValueError(f"La capacidad de la memoria del ETA no puede ser negativa: {capacidad}")
        self._capacidad = capacidad
        self._valores: collections.OrderedDict[str, _Valor] = collections.OrderedDict()
        self._cerrojo = threading.Lock()

    def obtener(self, ruta_id: str, calcular: Callable[[], _Valor]) -> _Valor:
        with self._cerrojo:
            if ruta_id in self._valores:
                self._valores.move_to_end(ruta_id)
                return self._valores[ruta_id]
        valor = calcular()
        if self._capacidad == 0:
            return valor
        with self._cerrojo:
            self._valores[ruta_id] = valor
            self._valores.move_to_end(ruta_id)
            while len(self._valores) > self._capacidad:
                self._valores.popitem(last=False)
        return valor

    def __len__(self) -> int:
        with self._cerrojo:
            return len(self._valores)
