"""Logica de `tools/entrenar_dqn_etapa1.py` (sesion 2026-10-19A, Q4): epsilon,
regla del presupuesto, buffer con la mascara siguiente, objetivo de Double
DQN con mascara y terminales, exploracion entre acciones validas y paridad
entre la red de PyTorch y la inferencia en numpy del evaluador. Requiere el
extra `entrenamiento`."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from hoppit.planificacion_rutas.motor._politica_dqn import (  # noqa: E402
    CLAVE_CODIFICACION,
    CLAVE_FORMATO,
    FORMATO_DE_LOS_PESOS,
    EvaluadorPoliticaDQN,
    codificacion_esperada,
    escalas_esperadas,
)
from hoppit.shared.codificacion_observacion import N_MAX, TAMANO_DE_LA_OBSERVACION  # noqa: E402
from hoppit.shared.intervalo_por_celda import ruta_del_sidecar  # noqa: E402
from tools.entrenar_dqn_etapa1 import (  # noqa: E402
    Buffer,
    elegir,
    epsilon,
    exportar_pesos,
    objetivo_double_dqn,
    presupuesto_que_entra,
    proyeccion_en_segundos,
    red,
)


def test_epsilon_lineal_en_el_primer_30_por_ciento() -> None:
    assert epsilon(0, 200_000) == 1.0
    assert epsilon(30_000, 200_000) == pytest.approx(1.0 - 0.95 * 0.5)
    assert epsilon(60_000, 200_000) == pytest.approx(0.05)
    assert epsilon(150_000, 200_000) == pytest.approx(0.05)


def test_regla_del_presupuesto() -> None:
    # 300 s hasta el paso 5.000 y 0,06 s por paso: 200.000 pasos proyectan
    # 300 + 195.000 x 0,06 = 12.000 s, mas de 3 h; entran 180.000.
    assert proyeccion_en_segundos(300.0, 0.06, 200_000) == pytest.approx(12_000.0)
    assert presupuesto_que_entra(300.0, 0.06) == 180_000
    assert presupuesto_que_entra(300.0, 0.001) % 10_000 == 0


def test_el_buffer_guarda_la_mascara_siguiente() -> None:
    buffer = Buffer(3)
    for i in range(5):
        mascara = np.zeros(N_MAX, dtype=bool)
        mascara[i % N_MAX] = True
        siguiente = np.zeros(N_MAX, dtype=bool)
        siguiente[(i + 1) % N_MAX] = True
        buffer.guardar(np.full(TAMANO_DE_LA_OBSERVACION, i, dtype=np.float32), mascara, i % N_MAX, -float(i),
                       np.zeros(TAMANO_DE_LA_OBSERVACION, dtype=np.float32), siguiente, i == 4)
    assert buffer.tamano == 3
    lote = buffer.muestra(np.random.default_rng(0), 50)
    for observacion, mascara_siguiente in zip(lote["observacion"], lote["mascara_siguiente"]):
        i = int(observacion[0])
        assert i in (2, 3, 4) and mascara_siguiente[(i + 1) % N_MAX]


def _lineal_constante(valores: list[float]) -> torch.nn.Module:
    capa = torch.nn.Linear(TAMANO_DE_LA_OBSERVACION, N_MAX)
    with torch.no_grad():
        capa.weight.zero_()
        capa.bias.copy_(torch.tensor(valores))
    return capa


def test_objetivo_double_dqn_con_mascara_y_terminales() -> None:
    en_linea = _lineal_constante([9.0, 1.0, 5.0] + [0.0] * (N_MAX - 3))
    objetivo = _lineal_constante([100.0, 10.0, 20.0] + [0.0] * (N_MAX - 3))
    mascara = torch.zeros((2, N_MAX), dtype=torch.bool)
    mascara[:, 1:3] = True  # el slot 0 (el de mayor valor en linea) es invalido
    lote = {
        "recompensa": torch.tensor([-3.0, -4.0]),
        "terminal": torch.tensor([False, True]),
        "siguiente": torch.zeros((2, TAMANO_DE_LA_OBSERVACION)),
        "mascara_siguiente": mascara,
    }
    blanco = objetivo_double_dqn(en_linea, objetivo, lote)
    # Elige el slot 2 con la red en linea entre los validos y lee 20 de la
    # red objetivo; el terminal queda en su recompensa.
    assert blanco.tolist() == [17.0, -4.0]


def test_la_exploracion_solo_elige_acciones_validas() -> None:
    torch.manual_seed(0)
    en_linea = red()
    mascara = np.zeros(N_MAX, dtype=bool)
    mascara[[2, 5]] = True
    generador = np.random.default_rng(1)
    observacion = np.zeros(TAMANO_DE_LA_OBSERVACION, dtype=np.float32)
    assert {elegir(en_linea, observacion, mascara, 1.0, generador) for _ in range(50)} == {2, 5}
    assert elegir(en_linea, observacion, mascara, 0.0, generador) in (2, 5)


def test_los_pesos_exportados_dan_en_numpy_lo_mismo_que_en_pytorch(tmp_path: Path) -> None:
    torch.manual_seed(3)
    en_linea = red()
    contenido = exportar_pesos(en_linea)
    ruta = tmp_path / "pesos.npz"
    ruta.write_bytes(contenido)
    ruta_del_sidecar(ruta).write_text(json.dumps({"parametros": {
        "sha256": hashlib.sha256(contenido).hexdigest(), CLAVE_FORMATO: FORMATO_DE_LOS_PESOS,
        CLAVE_CODIFICACION: codificacion_esperada(), "escalas": escalas_esperadas()}}), encoding="utf-8")
    evaluador = EvaluadorPoliticaDQN(ruta)
    observaciones = np.random.default_rng(4).normal(size=(20, TAMANO_DE_LA_OBSERVACION)).astype(np.float32)
    with torch.no_grad():
        esperados = en_linea(torch.from_numpy(observaciones)).numpy()
    obtenidos = np.stack([evaluador.valores(o) for o in observaciones])
    assert np.allclose(obtenidos, esperados, atol=1e-5)
    assert all(int(np.argmax(o)) == int(np.argmax(e)) for o, e in zip(obtenidos, esperados))
