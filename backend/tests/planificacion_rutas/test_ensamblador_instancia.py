"""Verifica el Ensamblador de Instancia y el Receptor reescritos.

El grafo se sustituye por una implementacion propia del Protocol de
Cartografia. Eso es exactamente lo que el Protocol habilita: este container
no depende de la clase que carga el artefacto de disco, y estas pruebas lo
demuestran corriendo sin artefacto y sin OSMnx.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest

from hoppit.cartografia.puerto import (
    CaminoEntreNodos,
    CostoArco,
    MatrizDeCostos,
    NodoGrafo,
    OrigenRotulo,
    RotuloNodo,
)
from hoppit.gestion_ordenes.puerto import Orden, crear_servicio as crear_ordenes
from hoppit.planificacion_rutas._adaptador_contexto import AdaptadorDatosContexto
from hoppit.planificacion_rutas._ensamblador_instancia import (
    INDICE_DEPOSITO,
    EnsambladorInstancia,
)
from hoppit.planificacion_rutas._receptor import (
    ReceptorSolicitudes,
    SolicitudInvalida,
    TipoSolicitud,
)
from hoppit.planificacion_rutas.motor.base import InstanciaInvalida
from hoppit.shared.bus import Bus
from hoppit.shared.ids import OrdenId, VehiculoId

DEPOSITO = 100
DESTINOS = (200, 300, 400)
NODO_AISLADO = 999
DISTANCIA_POR_SALTO_M = 1000.0

# La jornada es parametro de la instancia y entra en el resumen del que se
# derivan los identificadores. Se fija para que una prueba pueda afirmar algo
# sobre un identificador concreto sin depender del dia en que corra.
JORNADA_DE_PRUEBA = dt.date(2026, 8, 25)


class GrafoDePrueba:
    """Implementacion del Protocol ProveedorGrafoVial sobre nodos ficticios.

    La distancia entre dos nodos es la diferencia de sus identificadores, de
    modo que los costos sean predecibles a mano y las aserciones puedan ser
    exactas en vez de aproximadas.
    """

    def __init__(self, nodos_conectados: tuple[int, ...]) -> None:
        self._conectados = nodos_conectados

    def costo(self, origen: int, destino: int) -> CostoArco:
        return CostoArco(
            origen=origen,
            destino=destino,
            distancia_m=float(abs(destino - origen)),
            tiempo_base_s=float(abs(destino - origen)),
        )

    def coordenadas(self, nodo: int) -> NodoGrafo:
        # Coordenadas derivadas del identificador: no representan un lugar,
        # solo tienen que ser estables y distintas entre nodos.
        return NodoGrafo(id_osm=nodo, lat=float(nodo) / 100, lon=-float(nodo) / 100)

    def rotulo(self, nodo: int) -> RotuloNodo:
        ubicacion = self.coordenadas(nodo)
        return RotuloNodo(
            nodo=nodo,
            lat=ubicacion.lat,
            lon=ubicacion.lon,
            calles=(f"Calle {nodo}",),
            texto=f"Calle {nodo}",
            origen=OrigenRotulo.VIA_UNICA,
        )

    def nodo_mas_cercano(
        self, lat: float, lon: float, restringido_a: Sequence[int] | None = None
    ) -> NodoGrafo:
        return NodoGrafo(id_osm=self._conectados[0], lat=lat, lon=lon)

    def nodos_de_mayor_componente_fuerte(self) -> tuple[int, ...]:
        return self._conectados

    def matriz_de_costos(self, nodos: Sequence[int]) -> MatrizDeCostos:
        caminos = {}
        for origen in nodos:
            for destino in nodos:
                arcos = () if origen == destino else (self.costo(origen, destino),)
                caminos[(origen, destino)] = CaminoEntreNodos(
                    origen=origen,
                    destino=destino,
                    distancia_m=float(abs(destino - origen)),
                    arcos=arcos,
                )
        return MatrizDeCostos(nodos=tuple(nodos), caminos=caminos)


def _armar(destinos: tuple[int, ...] = DESTINOS) -> tuple[EnsambladorInstancia, Bus]:
    bus = Bus()
    ordenes = crear_ordenes(bus)
    for posicion, destino in enumerate(destinos):
        ordenes.dar_de_alta(
            Orden(id=OrdenId(f"o{posicion}"), demanda=posicion + 1, destino=destino)
        )
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    return EnsambladorInstancia(ordenes, contexto), bus


def _solicitud(deposito: int = DEPOSITO):
    return ReceptorSolicitudes().normalizar(
        tipo=TipoSolicitud.NUEVA_PLANIFICACION,
        fecha_planificacion=JORNADA_DE_PRUEBA,
        deposito=deposito,
        capacidad_por_vehiculo={VehiculoId("v1"): 5, VehiculoId("v2"): 5},
    )


# --- Receptor -------------------------------------------------------------


def test_la_solicitud_normalizada_lleva_los_parametros_de_la_instancia() -> None:
    """El resultado de normalizar() ya no se descarta: es lo que consume el
    Ensamblador (pasos 6 a 7 del diagrama de proceso)."""
    solicitud = _solicitud()
    assert solicitud.tipo is TipoSolicitud.NUEVA_PLANIFICACION
    assert solicitud.deposito == DEPOSITO
    assert solicitud.capacidad_por_vehiculo == {VehiculoId("v1"): 5, VehiculoId("v2"): 5}


def test_una_flota_sin_vehiculos_se_rechaza_en_el_borde() -> None:
    with pytest.raises(SolicitudInvalida):
        ReceptorSolicitudes().normalizar(
            tipo=TipoSolicitud.NUEVA_PLANIFICACION,
            fecha_planificacion=JORNADA_DE_PRUEBA,
            deposito=DEPOSITO,
            capacidad_por_vehiculo={},
        )


def test_una_capacidad_no_entera_o_nula_se_rechaza() -> None:
    for invalida in (0, -3, 7.5):
        with pytest.raises(SolicitudInvalida):
            ReceptorSolicitudes().normalizar(
                tipo=TipoSolicitud.NUEVA_PLANIFICACION,
                fecha_planificacion=JORNADA_DE_PRUEBA,
                deposito=DEPOSITO,
                capacidad_por_vehiculo={VehiculoId("v1"): invalida},  # type: ignore[dict-item]
            )


def test_la_solicitud_no_comparte_el_diccionario_que_recibio() -> None:
    """Mutar la Flota afuera no puede cambiar una solicitud ya normalizada."""
    capacidades = {VehiculoId("v1"): 5}
    solicitud = ReceptorSolicitudes().normalizar(
        tipo=TipoSolicitud.NUEVA_PLANIFICACION,
        fecha_planificacion=JORNADA_DE_PRUEBA,
        deposito=DEPOSITO,
        capacidad_por_vehiculo=capacidades,
    )
    capacidades[VehiculoId("v1")] = 99
    assert solicitud.capacidad_por_vehiculo == {VehiculoId("v1"): 5}


# --- Ensamblador ----------------------------------------------------------


def test_la_instancia_lleva_demanda_deposito_y_costos_reales() -> None:
    ensamblador, _ = _armar()
    instancia = ensamblador.ensamblar(_solicitud())

    assert instancia.ordenes == (OrdenId("o0"), OrdenId("o1"), OrdenId("o2"))
    assert {p.nodo: p.demanda for p in instancia.paradas} == {
        DESTINOS[0]: 1,
        DESTINOS[1]: 2,
        DESTINOS[2]: 3,
    }
    assert instancia.demanda_total == 6
    assert instancia.depositos == (DEPOSITO,)
    assert instancia.capacidad_total == 10


def test_el_deposito_ocupa_la_posicion_cero_de_la_matriz() -> None:
    """La correspondencia nodo-indice es lo que el motor usa para razonar;
    si se corre, la Ruta visita los puntos equivocados sin fallar."""
    ensamblador, _ = _armar()
    instancia = ensamblador.ensamblar(_solicitud())

    assert instancia.indice_deposito == INDICE_DEPOSITO
    assert instancia.matriz.nodos[INDICE_DEPOSITO] == DEPOSITO
    for parada in instancia.paradas:
        assert parada.indice != INDICE_DEPOSITO
        assert instancia.matriz.nodos[parada.indice] == parada.nodo
    assert instancia.matriz.nodos == (DEPOSITO,) + DESTINOS


def test_cada_parada_apunta_al_nodo_de_destino_de_sus_ordenes() -> None:
    ensamblador, _ = _armar()
    instancia = ensamblador.ensamblar(_solicitud())

    for posicion, destino in enumerate(DESTINOS):
        parada = next(p for p in instancia.paradas if p.nodo == destino)
        assert parada.ordenes == (OrdenId(f"o{posicion}"),)
        assert instancia.matriz.nodos[parada.indice] == destino


def test_la_matriz_cubre_el_deposito_y_todos_los_destinos() -> None:
    ensamblador, _ = _armar()
    instancia = ensamblador.ensamblar(_solicitud())

    esperados = (DEPOSITO,) + DESTINOS
    assert len(instancia.matriz.caminos) == len(esperados) ** 2
    assert instancia.matriz.distancia_m(DEPOSITO, DESTINOS[0]) == 100.0


def test_la_prevision_de_eta_se_declara_ausente_no_vacia() -> None:
    """Un mapa vacio se leeria como 'todos los tiempos son cero'."""
    ensamblador, _ = _armar()
    instancia = ensamblador.ensamblar(_solicitud())
    assert instancia.matriz_tiempos_prevista is None


def test_la_cache_de_eta_solo_se_puebla_por_empuje() -> None:
    """Prediccion de ETA es fase 2: la estructura existe y esta vacia, pero
    si algo empuja un valor tiene que aparecer, no quedar ignorado."""
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    assert contexto.tiempos_previstos() == {}

    contexto.recibir_eta(DEPOSITO, DESTINOS[0], 42.0)
    assert contexto.tiempos_previstos() == {(DEPOSITO, DESTINOS[0]): 42.0}


def test_sin_ordenes_planificables_no_hay_instancia() -> None:
    ensamblador, _ = _armar(destinos=())
    with pytest.raises(InstanciaInvalida):
        ensamblador.ensamblar(_solicitud())


def test_un_deposito_fuera_de_la_componente_se_rechaza_por_su_nombre() -> None:
    ensamblador, _ = _armar()
    with pytest.raises(InstanciaInvalida) as error:
        ensamblador.ensamblar(_solicitud(deposito=NODO_AISLADO))
    assert str(NODO_AISLADO) in str(error.value)


def test_un_destino_fuera_de_la_componente_se_rechaza_antes_del_calculo() -> None:
    """El error nombra el destino, en vez de aparecer despues como un par
    sin camino dentro de la matriz."""
    bus = Bus()
    ordenes = crear_ordenes(bus)
    ordenes.dar_de_alta(Orden(id=OrdenId("o0"), demanda=1, destino=NODO_AISLADO))
    contexto = AdaptadorDatosContexto(GrafoDePrueba((DEPOSITO,) + DESTINOS))
    ensamblador = EnsambladorInstancia(ordenes, contexto)

    with pytest.raises(InstanciaInvalida) as error:
        ensamblador.ensamblar(_solicitud())
    assert str(NODO_AISLADO) in str(error.value)
