"""Entorno secuencial de la etapa 1 (sesion 2026-10-19A, Q2; ARQ-42 con la
correccion de DAT-43): apareo por Orden con dos secuencias distintas sobre el
mismo episodio, jornada de 8 a 22, suma de recompensas igual a menos la
duracion total, mismo resultado que la evaluacion de un plan fijo, mascara y
acciones invalidas. Tablas y artefactos sinteticos de test_evaluacion."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from hoppit.entorno_simulacion.puerto import (
    DESCUENTO_DE_LA_ETAPA_1,
    AccionInvalida,
    EntornoSecuencial,
    Instancia,
    InstanciaFueraDeLaEtapa1,
    OrdenDeLaInstancia,
    SemillaDeEpisodio,
    generador_del_episodio,
    leer_instancia,
    leer_plan,
    paradas_de_la_instancia,
    probabilidades_por_orden,
    recorrer_secuencia,
    simular_episodio,
)
from hoppit.shared.codificacion_observacion import TAMANO_DE_LA_OBSERVACION
from hoppit.shared.intervalo_por_celda import (
    Coordenada,
    FranjaFueraDeLaJornada,
    leer_tabla_de_intervalos,
    viaje_por_la_red_min,
)
from tests.entorno_simulacion.test_evaluacion import (
    CUANTILES_DISPERSOS,
    _artefactos,
    _constante,
    _escribir_json,
    _tabla,
)
from tests.shared.test_intervalo_por_celda import CERO_A_CIEN, CIEN_A_250, DESDE_250

BASE = 7
TRAMOS = (CERO_A_CIEN, CIEN_A_250, DESDE_250)
VELOCIDAD = 19.0


def _preparar(tmp_path: Path, cuantiles: tuple[float, ...] = CUANTILES_DISPERSOS) -> tuple[EntornoSecuencial, Instancia, Path]:
    tabla = leer_tabla_de_intervalos(_tabla(tmp_path, {tramo: cuantiles for tramo in TRAMOS}))
    ruta_instancia, _ = _artefactos(tmp_path)
    return EntornoSecuencial(tabla), leer_instancia(ruta_instancia), ruta_instancia


def _slot(entorno: EntornoSecuencial, nodo: int) -> int:
    return [p.nodo for p in entorno.paradas].index(nodo)


def test_las_paradas_agrupan_las_ordenes_por_nodo_en_el_orden_de_la_instancia(tmp_path: Path) -> None:
    _, instancia, _ = _preparar(tmp_path)
    paradas = paradas_de_la_instancia(instancia)
    assert [(p.nodo, p.ordenes, p.demanda) for p in paradas] == [
        (11, ("o1", "o2"), 2), (12, ("o3", "o4", "o5"), 3), (21, ("o6",), 1)
    ]


def test_apareo_por_orden_con_dos_secuencias_distintas_del_mismo_episodio(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path)
    semilla = SemillaDeEpisodio(BASE, 3)
    esperadas = probabilidades_por_orden(instancia, generador_del_episodio(BASE, 3))
    por_secuencia = []
    for nodos in ((11, 12, 21), (21, 12, 11)):
        entorno.reiniciar(instancia, semilla)
        vistas: dict[str, float] = {}
        for nodo in nodos:
            _, _, _, info = entorno.paso(_slot(entorno, nodo))
            vistas.update(zip(entorno.paradas[info.slot].ordenes, info.probabilidades))
        por_secuencia.append(vistas)
    assert por_secuencia[0] == por_secuencia[1] == esperadas


def test_la_suma_de_recompensas_es_menos_la_duracion_total(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path)
    assert DESCUENTO_DE_LA_ETAPA_1 == 1.0
    for indice in range(5):
        observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(BASE, indice))
        total = 0.0
        terminado = False
        pasos = []
        while not terminado:
            accion = int(np.flatnonzero(observacion.mascara)[-1])
            observacion, recompensa, terminado, info = entorno.paso(accion)
            total += recompensa
            pasos.append(info)
        assert total == pytest.approx(-entorno.duracion_min)
        # Cada tramo es viaje determinista mas las componentes de parada, y el
        # regreso va en el ultimo paso, sin componente de parada.
        reconstruida = sum(i.viaje_min + sum(i.componentes_de_parada_min) + i.regreso_min for i in pasos)
        assert reconstruida == pytest.approx(entorno.duracion_min)
        assert [i.regreso_min > 0 for i in pasos] == [False] * (len(pasos) - 1) + [True]
        assert pasos[-1].regreso_min == pytest.approx(
            viaje_por_la_red_min(instancia.distancia_por_la_red_m(pasos[-1].nodo, instancia.deposito_nodo), VELOCIDAD)
        )
        assert all(8 <= franja <= 22 for info in pasos for franja in info.franjas)


def test_una_secuencia_fija_da_lo_mismo_que_la_evaluacion_del_plan(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path)
    plan = leer_plan(
        _escribir_json(tmp_path, "plan_unico.json", {
            "identificador_del_plan": "plan-un-vehiculo",
            "rutas": [{"vehiculo": "V1", "distancia_m": 0.0, "paradas": [
                {"secuencia": 1, "nodo": 21, "ordenes": ["o6"]},
                {"secuencia": 2, "nodo": 11, "ordenes": ["o1", "o2"]},
                {"secuencia": 3, "nodo": 12, "ordenes": ["o3", "o4", "o5"]},
            ]}],
        }),
        instancia,
    )
    tabla = leer_tabla_de_intervalos(tmp_path / next(p.name for p in tmp_path.glob("*.csv")))
    nodos = [p.nodo for p in paradas_de_la_instancia(instancia)]
    slots = [nodos.index(n) for n in (21, 11, 12)]
    for indice in range(10):
        duracion, _ = recorrer_secuencia(entorno, instancia, SemillaDeEpisodio(BASE, indice), slots)
        episodio = simular_episodio(tabla, instancia, plan, BASE, indice)
        assert duracion == pytest.approx(episodio.duracion_por_ruta_min["V1"])


def test_la_jornada_termina_a_las_22(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path, _constante(200.0))
    entorno.reiniciar(instancia, SemillaDeEpisodio(BASE, 0))
    with pytest.raises(FranjaFueraDeLaJornada):
        for nodo in (11, 12, 21):
            entorno.paso(_slot(entorno, nodo))


def test_observacion_mascara_y_acciones_invalidas(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path)
    with pytest.raises(AccionInvalida):
        entorno.paso(0)
    observacion = entorno.reiniciar(instancia, SemillaDeEpisodio(BASE, 0))
    assert observacion.vector.shape == (TAMANO_DE_LA_OBSERVACION,)
    assert observacion.mascara.tolist() == [True, True, True] + [False] * 7
    observacion, _, terminado, _ = entorno.paso(1)
    assert not terminado and observacion.mascara.tolist()[:3] == [True, False, True]
    for accion in (1, 3, 9, -1):
        with pytest.raises(AccionInvalida):
            entorno.paso(accion)
    entorno.paso(0)
    _, _, terminado, _ = entorno.paso(2)
    assert terminado and not entorno.observar().mascara.any()
    with pytest.raises(AccionInvalida):
        entorno.paso(2)


def test_mas_de_10_paradas_no_es_de_la_etapa_1(tmp_path: Path) -> None:
    entorno, instancia, _ = _preparar(tmp_path)
    ordenes = tuple(
        OrdenDeLaInstancia(f"x{i:02d}", 100 + i, Coordenada(-34.5, -58.5), 1) for i in range(11)
    )
    grande = Instancia(instancia.deposito_nodo, instancia.deposito, ordenes, instancia.flota, {}, "s" * 64)
    with pytest.raises(InstanciaFueraDeLaEtapa1):
        entorno.reiniciar(grande, SemillaDeEpisodio(BASE, 0))
