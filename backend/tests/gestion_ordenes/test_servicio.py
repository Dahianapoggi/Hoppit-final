"""Verifica ARQ-31: el servicio consume RutaPublicada y ejecuta
Creada -> Planificada (paso 18 de ARQ-P1), dejando la Orden sabiendo en que
Ruta y en que Parada quedo (RB-09)."""
from __future__ import annotations

from hoppit.gestion_ordenes._dominio import EstadoOrden, Orden
from hoppit.gestion_ordenes._repositorio import RepositorioOrdenesEnMemoria
from hoppit.gestion_ordenes._servicio import ServicioGestionOrdenes
from hoppit.shared.bus import Bus
from hoppit.shared.eventos import AsignacionEnRuta, RutaPublicada
from hoppit.shared.ids import OrdenId, RutaId


# Nodo real del grafo de San Isidro (el Deposito de datos/instancia).
# Estas pruebas no consultan el grafo, pero el proyecto no admite
# ubicaciones inventadas ni siquiera como dato de prueba.
NODO_DESTINO = 1855553614

RUTA = "r1"
SECUENCIA = 3


def test_evento_ruta_publicada_marca_planificada() -> None:
    bus = Bus()
    servicio = ServicioGestionOrdenes(RepositorioOrdenesEnMemoria(), bus)
    servicio.dar_de_alta(Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO))

    bus.publicar(
        RutaPublicada(
            ruta_id=RUTA,
            asignaciones=(AsignacionEnRuta(orden_id=OrdenId("o1"), secuencia=SECUENCIA),),
        )
    )

    orden = servicio.consultar(OrdenId("o1"))
    assert orden.estado is EstadoOrden.PLANIFICADA
    # RB-09: la transicion y la asignacion ocurren en el mismo acto, asi que
    # una Orden Planificada nunca queda sin saber donde quedo.
    assert orden.ruta_id == RutaId(RUTA)
    assert orden.secuencia_parada == SECUENCIA
    assert orden.esta_asignada


def test_ordenes_planificables_solo_devuelve_creadas() -> None:
    bus = Bus()
    servicio = ServicioGestionOrdenes(RepositorioOrdenesEnMemoria(), bus)
    servicio.dar_de_alta(Orden(id=OrdenId("o1"), demanda=1, destino=NODO_DESTINO))
    servicio.dar_de_alta(Orden(id=OrdenId("o2"), demanda=1, destino=NODO_DESTINO))
    bus.publicar(
        RutaPublicada(
            ruta_id=RUTA,
            asignaciones=(AsignacionEnRuta(orden_id=OrdenId("o1"), secuencia=1),),
        )
    )

    planificables = {o.id for o in servicio.ordenes_planificables()}
    assert planificables == {OrdenId("o2")}
