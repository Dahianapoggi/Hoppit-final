"""Motor de Ruteo: interfaz intercambiable entre D1 (fase 1) y D2 (fase 2).
Ver base.py para el contrato; ARQ-30 para la justificacion arquitectonica."""
from .base import InstanciaProblema, MotorDeRuteo, SolucionRuteo

__all__ = ["MotorDeRuteo", "InstanciaProblema", "SolucionRuteo"]
