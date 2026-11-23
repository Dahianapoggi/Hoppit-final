"""Pruebas de `tools/entrenar_dqn_etapa2_f2.py` (encargo 2026-11-20A, D15):
mascara sin esperar, epsilon por episodios, politica optima del entorno de
juego, el entorno de juego con la interfaz de la etapa 2, y un entrenamiento
corto en CABA sintetica que nunca espera, cierra la recompensa con la medida
y se retoma desde el punto de control con los mismos pesos."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("torch")

from hoppit.entorno_simulacion import puerto_dinamico as pd_  # noqa: E402
from hoppit.shared import codificacion_observacion_etapa_2 as co  # noqa: E402
from hoppit.shared.politica_dqn_etapa_2 import PoliticaDQNEtapa2  # noqa: E402
from tools import entrenar_dqn_etapa1 as original  # noqa: E402
from tools import entrenar_dqn_etapa2 as e2  # noqa: E402
from tools import entrenar_dqn_etapa2_f2 as f2  # noqa: E402

from .test_entrenar_dqn_etapa2 import _ContextoSintetico, _Corte, _pesos  # noqa: E402


def test_sin_espera_deshabilita_solo_esperar() -> None:
    m = np.array([True, False, True, True])
    assert f2.sin_espera(m).tolist() == [True, False, True, False]
    assert m.tolist() == [True, False, True, True]


def test_epsilon_por_episodios() -> None:
    assert f2.epsilon_por_episodios(0, 6000) == pytest.approx(1.0)
    assert f2.epsilon_por_episodios(900, 6000) == pytest.approx(0.525)
    assert f2.epsilon_por_episodios(1800, 6000) == pytest.approx(0.05)
    assert f2.epsilon_por_episodios(5000, 6000) == pytest.approx(0.05)


def test_politica_optima_del_juego_y_su_trampa() -> None:
    assert f2.politica_optima_del_juego() == {0: 0, 1: 1, 2: 2}
    # En el tipo 0 el Vehiculo 2 es el mas barato de inmediato (5) pero no el optimo.
    assert min(range(3), key=lambda k: f2.COSTO_INMEDIATO_DEL_JUEGO[0][k]) == 2
    assert f2.mascara_del_juego(2).tolist() == [False, True, True, True]


class _Fija:
    def __init__(self, accion: dict[int, int]) -> None:
        self.accion = accion

    def asignar(self, sim, orden, factibles):  # type: ignore[no-untyped-def]
        observacion, _ = sim.observar(orden, factibles)
        return self.accion[int(np.argmax(observacion[20:23]))]


def test_entorno_de_juego_mide_lo_que_cuesta() -> None:
    sim = f2.EntornoDeJuego(7, _Fija({0: 0, 1: 1, 2: 2}))
    r = sim.correr()
    esperado = sum(f2.costo_efectivo_del_juego(int(t), int(t)) for t in sim.tipos)  # type: ignore[misc]
    assert r.medida_principal_min == pytest.approx(esperado)
    assert r.diferidos == int(np.sum(sim.tipos == 2)) and sim.decisiones == f2.DECISIONES_DEL_JUEGO


def test_el_lazo_en_el_juego_cierra_la_recompensa_y_no_espera(tmp_path) -> None:  # type: ignore[no-untyped-def]
    e = f2.EntrenadorDeJuego(1, 30, tmp_path)
    e.entrenar()
    assert e.episodios == 30 and e.pasos == 30 * f2.DECISIONES_DEL_JUEGO
    assert len(e.validaciones) == 11
    for fila in e.filas_de_episodios:
        assert fila["retorno"] == pytest.approx(-fila["medida_principal_min"] / 60.0, rel=1e-9)
    assert not e.buffer.mascara[: e.buffer.tamano, co.ACCION_ESPERAR].any()
    assert not e.buffer.accion[: e.buffer.tamano].tolist().count(co.ACCION_ESPERAR)


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


def test_entrenamiento_corto_sin_esperar_retomado_da_los_mismos_pesos(chico, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "entero").mkdir()
    entero = f2.EntrenadorF2(chico, 1, 20, tmp_path / "entero")
    entero.entrenar()
    assert entero.episodios == 20 and len(entero.validaciones) == 11
    assert all(f["esperas"] == 0 for f in entero.filas_de_episodios)
    for f in entero.filas_de_episodios:
        assert f["retorno"] == pytest.approx(-f["medida_principal_min"] / 60.0, rel=1e-9)

    (tmp_path / "cortado").mkdir()
    cortado = f2.EntrenadorF2(chico, 1, 20, tmp_path / "cortado")
    guardar = cortado.guardar_punto_de_control

    def guardar_y_cortar() -> None:
        guardar()
        if len(cortado.validaciones) == 4:
            raise _Corte

    cortado.guardar_punto_de_control = guardar_y_cortar  # type: ignore[method-assign]
    with pytest.raises(_Corte):
        cortado.entrenar()
    retomado = f2.EntrenadorF2(chico, 1, 20, tmp_path / "cortado")
    retomado.retomar()
    retomado.entrenar()
    assert retomado.pasos == entero.pasos and retomado.episodios == entero.episodios
    for k, v in _pesos(entero).items():
        np.testing.assert_array_equal(v, _pesos(retomado)[k])


def test_tope_de_reloj_corta_al_terminar_el_episodio(chico, tmp_path) -> None:  # type: ignore[no-untyped-def]
    e = f2.EntrenadorF2(chico, 2, 20, tmp_path, tope_de_reloj_s=0.0, validar_en_el_camino=False)
    e.entrenar()
    assert e.cortado_por_reloj and e.episodios == 0 and len(e.validaciones) == 2


def test_la_golosa_sin_espera_nunca_espera() -> None:
    arrays = {f"{c}.peso": np.zeros(s, dtype=np.float32) for c, s in
              (("capa1", (128, 46)), ("capa2", (128, 128)), ("capa3", (4, 128)))}
    arrays.update({f"{c}.sesgo": np.zeros(n, dtype=np.float32) for c, n in (("capa1", 128), ("capa2", 128), ("capa3", 4))})
    arrays["capa3.sesgo"][co.ACCION_ESPERAR] = 100.0
    politica = PoliticaDQNEtapa2(arrays)
    assert politica.elegir_accion(np.zeros(46, dtype=np.float32), np.array([True, True, True, True])) == 3
    golosa = f2.AsignacionGolosaSinEspera(politica)

    class _Sim:
        def observar(self, orden, factibles):  # type: ignore[no-untyped-def]
            return np.zeros(46, dtype=np.float32), np.array([False, True, True, True])

    assert golosa.asignar(_Sim(), None, [1, 2]) in (1, 2)  # type: ignore[arg-type]


def test_el_entrenador_del_19A_conserva_su_epsilon(chico, tmp_path) -> None:  # type: ignore[no-untyped-def]
    e = e2.Entrenador(chico, 1, 1000, tmp_path)  # type: ignore[arg-type]
    e.pasos = 150
    assert e.epsilon_actual() == pytest.approx(original.epsilon(150, 1000))
    assert isinstance(pd_.ESCALA_DE_LA_RECOMPENSA_MIN, float)
