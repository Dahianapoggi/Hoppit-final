"""Verifica que ambas implementaciones del motor respetan el contrato
comun -- ARQ-30."""
from __future__ import annotations

from hoppit.planificacion_rutas.motor._politica_dqn import EvaluadorPoliticaDQN
from hoppit.planificacion_rutas.puerto import SolverVRPDeterminista
from hoppit.planificacion_rutas.motor.base import MotorDeRuteo


def test_ambas_implementaciones_respetan_la_interfaz() -> None:
    assert issubclass(SolverVRPDeterminista, MotorDeRuteo)
    assert issubclass(EvaluadorPoliticaDQN, MotorDeRuteo)


def test_no_se_puede_instanciar_la_interfaz_directamente() -> None:
    import pytest

    with pytest.raises(TypeError):
        MotorDeRuteo()  # type: ignore[abstract]
