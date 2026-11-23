"""Seguimiento de Flota (2026-10-27B, T): clasificacion de cada posicion,
repetidas, aceptacion por instante de captura, eventos del nucleo en orden y
retencion; identificadores UUID version 4 (2026-10-27C, F2). Datos de
prueba, armados aca; ninguno simula el producto."""
from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import uuid

import pytest

from hoppit.seguimiento_flota.puerto import (
    RETENCION_DE_POSICIONES_DIAS,
    TOLERANCIA_DE_RELOJ_S,
    DispositivoInvalido,
    MotivoDeRechazo,
    PosicionRecibida,
    RepositorioDeSeguimientoSQLite,
    SecuenciaFueraDeOrden,
    SinPosiciones,
    crear_servicio,
    es_uuid_v4_canonico,
)
from hoppit.shared.almacen import conectar

DESPACHO = dt.datetime(2026, 11, 3, 11, 0, tzinfo=dt.timezone.utc)
RUTA = "ruta-prueba"
TRANSPORTISTA = "usuario-transportista"


def ID(nombre: str) -> str:
    """Un UUID version 4 fijo por nombre, para que las pruebas lean claro."""
    return str(uuid.UUID(bytes=hashlib.sha256(nombre.encode()).digest()[:16], version=4))


DISPOSITIVO = ID("dispositivo-de-prueba")


class Reloj:
    def __init__(self, ahora: dt.datetime) -> None:
        self.ahora = ahora

    def __call__(self) -> dt.datetime:
        return self.ahora


@pytest.fixture()
def reloj() -> Reloj:
    return Reloj(DESPACHO + dt.timedelta(minutes=30))


@pytest.fixture(params=["memoria", "sqlite"])
def servicio(request: pytest.FixtureRequest, tmp_path: pathlib.Path, reloj: Reloj):  # type: ignore[no-untyped-def]
    repositorio = None if request.param == "memoria" else RepositorioDeSeguimientoSQLite(conectar(tmp_path / "t.sqlite3"))
    servicio = crear_servicio(repositorio, reloj)
    assert servicio.recibir_evento(
        "evento-1",
        "RutaDespachada",
        RUTA,
        1,
        {"vehiculo_id": "AE472LP", "transportista_id": TRANSPORTISTA, "despachada_en": DESPACHO.isoformat()},
    )
    return servicio


def _posicion(nombre: str, minutos: float, lat: float = -34.6, lon: float = -58.4) -> PosicionRecibida:
    return PosicionRecibida(id=ID(nombre), capturada_en=DESPACHO + dt.timedelta(minutes=minutos), latitud=lat, longitud=lon, precision_m=10)


def test_cada_posicion_se_clasifica_con_su_motivo(servicio, reloj: Reloj) -> None:  # type: ignore[no-untyped-def]
    futuro = (reloj.ahora - DESPACHO).total_seconds() / 60 + (TOLERANCIA_DE_RELOJ_S + 60) / 60
    resultado = servicio.recibir_lote(
        TRANSPORTISTA,
        RUTA,
        DISPOSITIVO,
        [
            _posicion("p-buena", 10),
            _posicion("p-antes-del-despacho", -5),
            _posicion("p-del-futuro", futuro),
            _posicion("p-fuera-de-rango", 12, lat=-95.0),
        ],
    )
    assert resultado.aceptadas == (ID("p-buena"),)
    motivos = {r.id: (r.motivo, r.reintentable) for r in resultado.rechazadas}
    assert motivos == {
        ID("p-antes-del-despacho"): (MotivoDeRechazo.FUERA_DE_EJECUCION, False),
        ID("p-del-futuro"): (MotivoDeRechazo.MARCA_TEMPORAL_INVALIDA, False),
        ID("p-fuera-de-rango"): (MotivoDeRechazo.COORDENADAS_INVALIDAS, False),
    }


def test_otra_ruta_u_otro_transportista_se_rechazan(servicio) -> None:  # type: ignore[no-untyped-def]
    (desconocida,) = servicio.recibir_lote(TRANSPORTISTA, "ruta-sin-despacho", DISPOSITIVO, [_posicion("p1", 1)]).rechazadas
    assert (desconocida.motivo, desconocida.reintentable) == (MotivoDeRechazo.EJECUCION_DESCONOCIDA, True)
    (ajena,) = servicio.recibir_lote("otro-transportista", RUTA, DISPOSITIVO, [_posicion("p2", 1)]).rechazadas
    assert (ajena.motivo, ajena.reintentable) == (MotivoDeRechazo.RUTA_NO_ASIGNADA, False)


def test_una_posicion_repetida_se_descarta_y_vale_la_primera(servicio) -> None:  # type: ignore[no-untyped-def]
    assert servicio.recibir_lote(TRANSPORTISTA, RUTA, DISPOSITIVO, [_posicion("p1", 5)]).aceptadas == (ID("p1"),)
    otra = servicio.recibir_lote(TRANSPORTISTA, RUTA, DISPOSITIVO, [_posicion("p1", 5, lat=-34.0)])
    assert otra.duplicadas == (ID("p1"),) and otra.aceptadas == ()
    assert servicio.ultima_posicion(RUTA).posicion.latitud == -34.6


def test_una_posicion_atrasada_se_acepta_por_su_instante_de_captura(servicio, reloj: Reloj) -> None:  # type: ignore[no-untyped-def]
    # Capturada a los 10 minutos del despacho y recibida horas despues, tras
    # una desconexion simulada en la prueba.
    reloj.ahora = DESPACHO + dt.timedelta(hours=5)
    assert servicio.recibir_lote(TRANSPORTISTA, RUTA, DISPOSITIVO, [_posicion("tarde", 10)]).aceptadas == (ID("tarde"),)
    ultima = servicio.ultima_posicion(RUTA)
    assert ultima.antiguedad_s == pytest.approx((reloj.ahora - (DESPACHO + dt.timedelta(minutes=10))).total_seconds())


def test_sin_posiciones_no_hay_ultima(servicio) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SinPosiciones):
        servicio.ultima_posicion(RUTA)


def test_los_eventos_repetidos_se_descartan_y_los_adelantados_se_rechazan(servicio) -> None:  # type: ignore[no-untyped-def]
    cuerpo = {"vehiculo_id": "AE472LP", "transportista_id": TRANSPORTISTA, "despachada_en": DESPACHO.isoformat()}
    assert servicio.recibir_evento("evento-1", "RutaDespachada", RUTA, 1, cuerpo) is False
    with pytest.raises(SecuenciaFueraDeOrden):
        servicio.recibir_evento("evento-9", "RutaDespachada", "ruta-nueva", 2, cuerpo)


def test_la_retencion_borra_lo_que_lleva_mas_de_30_dias_sin_posiciones(servicio, reloj: Reloj) -> None:  # type: ignore[no-untyped-def]
    servicio.recibir_lote(TRANSPORTISTA, RUTA, DISPOSITIVO, [_posicion("p1", 5), _posicion("p2", 6)])
    assert servicio.aplicar_retencion() == 0
    reloj.ahora = reloj.ahora + dt.timedelta(days=RETENCION_DE_POSICIONES_DIAS, minutes=1)
    assert servicio.aplicar_retencion() == 2
    with pytest.raises(SinPosiciones):
        servicio.ultima_posicion(RUTA)


# --- Identificadores (ARQ-52; 2026-10-27C, F2) -------------------------------


def test_una_posicion_sin_uuid_v4_se_rechaza_sola_y_de_forma_definitiva(servicio) -> None:  # type: ignore[no-untyped-def]
    invalida = PosicionRecibida(id="p-1", capturada_en=DESPACHO + dt.timedelta(minutes=5), latitud=-34.6, longitud=-58.4, precision_m=10)
    resultado = servicio.recibir_lote(TRANSPORTISTA, RUTA, DISPOSITIVO, [invalida, _posicion("valida", 6)])
    assert resultado.aceptadas == (ID("valida"),)
    (rechazo,) = resultado.rechazadas
    assert (rechazo.id, rechazo.motivo, rechazo.reintentable) == ("p-1", MotivoDeRechazo.IDENTIFICADOR_INVALIDO, False)


def test_un_dispositivo_sin_uuid_v4_rechaza_el_lote_entero_sin_guardar_nada(servicio) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DispositivoInvalido):
        servicio.recibir_lote(TRANSPORTISTA, RUTA, "dispositivo-1", [_posicion("p1", 5)])
    with pytest.raises(SinPosiciones):
        servicio.ultima_posicion(RUTA)


@pytest.mark.parametrize(
    ("texto", "valido"),
    [
        (str(uuid.UUID(int=0x0123456789AB4CDE8F0123456789ABCD)), True),
        ("0123456789ab4cde8f0123456789abcd", False),  # sin guiones
        ("0123456789AB-4CDE-8F01-23456789ABCD".lower().replace("-", "", 1), False),
        ("01234567-89AB-4CDE-8F01-23456789ABCD", False),  # mayusculas: no es la forma canonica
        ("01234567-89ab-1cde-8f01-23456789abcd", False),  # version 1
        ("01234567-89ab-4cde-cf01-23456789abcd", False),  # variante que no es 10xx
        ("{01234567-89ab-4cde-8f01-23456789abcd}", False),
        ("", False),
    ],
)
def test_la_forma_canonica_del_uuid_v4(texto: str, valido: bool) -> None:
    assert es_uuid_v4_canonico(texto) is valido
