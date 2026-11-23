"""Pruebas de `tools/entrenar_dqn_etapa2.py` (encargo 2026-11-19A, bloque 5):
escenarios, semillas, presupuesto y un entrenamiento corto con un contexto
sintetico, interrumpido y retomado desde su punto de control, que tiene que
dar los mismos pesos que sin interrumpir."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from hoppit.entorno_simulacion import puerto_dinamico as pd_  # noqa: E402
from hoppit.shared.politica_dqn_etapa_2 import PoliticaDQNEtapa2  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402
from tools import entrenar_dqn_etapa2 as e2  # noqa: E402

from .test_simulador_dinamico import FORMA, _tabla, plano, sorteador  # noqa: E402


def test_escenarios_de_d4() -> None:
    assert [(n, o, p) for n, o, p in e2.ESCENARIOS] == [
        ("R3_c06_p03", 60, 0.3), ("R3_c06_p05", 60, 0.5), ("R3_c06_p07", 60, 0.7),
        ("R3_c08_p03", 80, 0.3), ("R3_c08_p05", 80, 0.5), ("R3_c08_p07", 80, 0.7),
        ("R3_c10_p03", 100, 0.3), ("R3_c10_p05", 100, 0.5), ("R3_c10_p07", 100, 0.7)]
    assert e2.PRINCIPALES == ("R3_c08_p07", "R3_c10_p07")


def test_semillas_distintas_por_semilla_y_reproducibles() -> None:
    a, b = e2.semillas_derivadas(1), e2.semillas_derivadas(2)
    assert a == e2.semillas_derivadas(1) and set(a.values()).isdisjoint(b.values())


def test_presupuesto_que_entra() -> None:
    # 3 h, 3 semillas, margen 0,2: 2.880 s por semilla menos 11 validaciones de 10 s
    assert e2.presupuesto_que_entra(0.01, 10.0, 3.0, 3, 0.2) == 270_000
    assert e2.presupuesto_que_entra(0.001, 0.0, 3.0, 3, 0.2) == e2.TOPE_DE_PASOS


class _ContextoSintetico:
    def __init__(self) -> None:
        self.sorteador = sorteador(40)
        self.forma = FORMA
        self.tabla = _tabla()
        self.capacidad = 12
        self.parametros = pd_.ParametrosDelSimulador(capacidad=12, regla_de_regreso=pd_.REGRESO_AL_VACIARSE)
        self._matriz = plano(40)

    def jornada(self, conjunto, j, nombre):  # type: ignore[no-untyped-def]
        _, n, p = e2.escenario(nombre)
        return pd_.generar_jornada(self.sorteador, self.forma, j, n, p, conjunto=conjunto), self._matriz


@pytest.fixture
def chico(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(e2, "ESCENARIOS", (("R3_c08_p07", 10, 0.7), ("R3_c10_p07", 14, 0.7)))
    monkeypatch.setattr(e2, "JORNADAS_DE_ENTRENAMIENTO", 4)
    monkeypatch.setattr(e2, "JORNADAS_DE_VALIDACION", 1)
    monkeypatch.setattr(original, "INICIO_DEL_APRENDIZAJE", 40)
    monkeypatch.setattr(original, "LOTE", 8)
    monkeypatch.setattr(original, "VENTANA_DE_LA_CURVA", 25)
    monkeypatch.setattr(original, "COPIA_DE_LA_RED_OBJETIVO", 30)
    return _ContextoSintetico()


def _pesos(entrenador: e2.Entrenador) -> dict[str, np.ndarray]:
    return {k: v.detach().numpy().copy() for k, v in entrenador.en_linea.state_dict().items()}


class _Corte(Exception):
    pass


def test_entrenamiento_corto_retomado_da_los_mismos_pesos(chico, tmp_path) -> None:  # type: ignore[no-untyped-def]
    entero = e2.Entrenador(chico, 1, 200, tmp_path / "entero")  # type: ignore[arg-type]
    (tmp_path / "entero").mkdir()
    entero.entrenar()
    assert entero.pasos == 200 and len(entero.validaciones) == 11
    assert [round(v["fraccion_del_presupuesto"], 1) for v in entero.validaciones][0] == 0.0
    assert entero.curva and all(np.isfinite(f["q_media_de_las_elegidas"]) for f in entero.curva)
    assert sum(1 for f in entero.filas_de_episodios) == entero.episodios or entero.episodios == len(entero.filas_de_episodios) + 1

    (tmp_path / "cortado").mkdir()
    cortado = e2.Entrenador(chico, 1, 200, tmp_path / "cortado")  # type: ignore[arg-type]
    guardar = cortado.guardar_punto_de_control

    def guardar_y_cortar() -> None:
        guardar()
        if len(cortado.validaciones) == 4:
            raise _Corte

    cortado.guardar_punto_de_control = guardar_y_cortar  # type: ignore[method-assign]
    with pytest.raises(_Corte):
        cortado.entrenar()
    retomado = e2.Entrenador(chico, 1, 200, tmp_path / "cortado")  # type: ignore[arg-type]
    retomado.retomar()
    retomado.entrenar()
    assert retomado.pasos == entero.pasos and retomado.episodios == entero.episodios
    for k, v in _pesos(entero).items():
        np.testing.assert_array_equal(v, _pesos(retomado)[k])
    politica = PoliticaDQNEtapa2.desde_bytes(e2.exportar_pesos(retomado.en_linea))
    assert politica.elegir_accion(np.zeros(46, dtype=np.float32), np.array([False, False, False, True])) == 3


def test_la_suma_de_las_recompensas_del_buffer_es_la_medida(chico, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "s").mkdir()
    entrenador = e2.Entrenador(chico, 2, 120, tmp_path / "s", validar_en_el_camino=False)  # type: ignore[arg-type]
    entrenador.entrenar()
    terminados = [f for f in entrenador.filas_de_episodios]
    assert terminados
    for f in terminados:
        assert f["retorno"] == pytest.approx(-f["medida_principal_min"] / 60.0, rel=1e-9)
