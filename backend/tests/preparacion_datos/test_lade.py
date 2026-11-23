"""Verifica el loader de LaDe: normalizacion del esquema (DAT-02, DAT-13),
periodo validado sobre ds y ano de cada marca relativo a su ds (DAT-14,
DAT-15), los deltas entre finalizaciones consecutivas y la aceptacion frente a
la finalizacion.

Todo corre sobre CSV chicos armados en el test: ninguna prueba depende de la
copia local del dataset, que no esta versionada.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from hoppit.preparacion_datos import _lade
from hoppit.preparacion_datos._lade import (
    CIUDADES_LADE,
    REPOSITORIO_LADE,
    SUBCONJUNTO_ENTREGA,
    SUBCONJUNTO_RECOLECCION,
    ArchivoLaDe,
    EsquemaLaDeInvalido,
    RangoTemporalLaDeInvalido,
    analizar_aceptacion,
    catalogo_lade,
    deltas_entre_finalizaciones,
    extraer_lade,
    normalizar_columnas_lade,
    parsear_tiempo_lade,
    resumir_lade,
    url_lade,
    validar_esquema_lade,
    validar_jornadas_lade,
    validar_lade,
)
from hoppit.preparacion_datos._procedencia import SUFIJO_SIDECAR

# Encabezados de los CSV publicados, tal como vienen en la revision fija de
# LaDe (delivery_jl.csv y pickup_jl.csv; identicos en las cinco ciudades de
# cada subconjunto). Son el esquema real contra el que tiene que pasar la
# validacion, no una reconstruccion desde el paper (DAT-13, B-151).
_COLUMNAS_ENTREGA = [
    "order_id",
    "region_id",
    "city",
    "courier_id",
    "lng",
    "lat",
    "aoi_id",
    "aoi_type",
    "accept_time",
    "accept_gps_time",
    "accept_gps_lng",
    "accept_gps_lat",
    "delivery_time",
    "delivery_gps_time",
    "delivery_gps_lng",
    "delivery_gps_lat",
    "ds",
]

_COLUMNAS_RECOLECCION = [
    "order_id",
    "region_id",
    "city",
    "courier_id",
    "accept_time",
    "time_window_start",
    "time_window_end",
    "lng",
    "lat",
    "aoi_id",
    "aoi_type",
    "pickup_time",
    "pickup_gps_time",
    "pickup_gps_lng",
    "pickup_gps_lat",
    "accept_gps_time",
    "accept_gps_lng",
    "accept_gps_lat",
    "ds",
]


def _fila_de_entrega(
    courier: int, ds: int, delivery: str, accept: str = "09-25 08:00:00"
) -> dict[str, object]:
    return {
        "order_id": courier * 1000 + ds,
        "region_id": 1,
        "city": "Jilin",
        "courier_id": courier,
        "lng": 126.5,
        "lat": 43.8,
        "aoi_id": 7,
        "aoi_type": 14,
        "accept_time": accept,
        "accept_gps_time": accept,
        "accept_gps_lng": 126.5,
        "accept_gps_lat": 43.8,
        "delivery_time": delivery,
        "delivery_gps_time": delivery,
        "delivery_gps_lng": 126.5,
        "delivery_gps_lat": 43.8,
        "ds": ds,
    }


def _csv_de_entrega(destino: Path, filas: list[dict[str, object]]) -> Path:
    ruta = destino / "delivery_jl.csv"
    pd.DataFrame(filas, columns=_COLUMNAS_ENTREGA).to_csv(ruta, index=False)
    return ruta


# --- Catalogo y URL ------------------------------------------------------


def test_el_catalogo_no_descarta_ninguna_ciudad() -> None:
    """Elegir ciudad es DAT-08, decision abierta: el catalogo las lista todas."""
    archivos = catalogo_lade((SUBCONJUNTO_ENTREGA,))
    assert len(archivos) == len(CIUDADES_LADE)
    assert {archivo.ciudad for archivo in archivos} == set(CIUDADES_LADE.values())


def test_el_catalogo_rechaza_un_subconjunto_inexistente() -> None:
    with pytest.raises(ValueError):
        catalogo_lade(("trajectory",))


def test_la_url_apunta_a_una_revision_fija() -> None:
    """Sin revision fija la procedencia no seria reproducible."""
    url = url_lade(ArchivoLaDe(SUBCONJUNTO_ENTREGA, "sh", "Shanghai"))
    assert REPOSITORIO_LADE in url
    assert "/resolve/" in url
    assert url.endswith("delivery/delivery_sh.csv")


# --- Extraccion ----------------------------------------------------------


def test_repetir_la_extraccion_no_reescribe_un_sidecar_que_coincide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sobre una copia verificada, el sidecar conserva la descarga original.

    Si un archivo deja de coincidir con lo registrado, su sidecar si se
    reescribe, y solo el suyo. Ninguna corrida descarga: los archivos ya
    estan en disco, y una descarga haria fallar la prueba.
    """

    def _sin_red(url: str, destino: Path) -> tuple[int, str]:
        raise AssertionError(f"No deberia descargar: {url}")

    monkeypatch.setattr(_lade, "_bajar_a_disco", _sin_red)
    archivos = catalogo_lade((SUBCONJUNTO_ENTREGA,))
    for archivo in archivos:
        ruta = tmp_path / archivo.subconjunto / archivo.nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(f"order_id\n{archivo.codigo_ciudad}\n", encoding="utf-8")

    def _sidecars() -> dict[str, str]:
        return {
            archivo.nombre: (
                tmp_path / archivo.subconjunto / f"{archivo.nombre}{SUFIJO_SIDECAR}"
            ).read_text(encoding="utf-8")
            for archivo in archivos
        }

    extraer_lade(tmp_path, subconjuntos=(SUBCONJUNTO_ENTREGA,))
    primera = _sidecars()

    # DAT-12: rige la licencia del paper; la de Hugging Face va aparte.
    parametros = json.loads(next(iter(primera.values())))["parametros"]
    assert parametros["licencia"] == "CC BY-NC 4.0"
    assert parametros["licencia_discrepante_en_hugging_face"] == "apache-2.0"

    extraer_lade(tmp_path, subconjuntos=(SUBCONJUNTO_ENTREGA,))
    assert _sidecars() == primera

    alterado = archivos[0]
    (tmp_path / alterado.subconjunto / alterado.nombre).write_text(
        "order_id\notro contenido\n", encoding="utf-8"
    )
    extraer_lade(tmp_path, subconjuntos=(SUBCONJUNTO_ENTREGA,))
    tercera = _sidecars()

    sha_antes = json.loads(primera[alterado.nombre])["parametros"]["sha256"]
    sha_despues = json.loads(tercera[alterado.nombre])["parametros"]["sha256"]
    assert sha_despues != sha_antes
    del primera[alterado.nombre], tercera[alterado.nombre]
    assert tercera == primera


# --- DAT-02, nomenclatura de columnas ------------------------------------


def test_se_aplica_el_mapa_oficial_si_hay_nombres_crudos() -> None:
    crudo = pd.DataFrame(
        [{"got_time": "05-02 10:00:00", "from_city_name": "Shanghai", "poi_lng": 121.0}]
    )
    normalizado, diferencia = normalizar_columnas_lade(crudo, SUBCONJUNTO_RECOLECCION)

    assert diferencia.nombres_crudos_detectados is True
    assert "pickup_time" in normalizado.columns
    assert "city" in normalizado.columns
    assert "lng" in normalizado.columns


def test_no_se_renombra_si_el_archivo_ya_esta_renombrado() -> None:
    ya_renombrado = pd.DataFrame([{"pickup_time": "05-02 10:00:00", "city": "Shanghai"}])
    normalizado, diferencia = normalizar_columnas_lade(ya_renombrado, SUBCONJUNTO_RECOLECCION)

    assert diferencia.nombres_crudos_detectados is False
    assert "pickup_time" in normalizado.columns


@pytest.mark.parametrize(
    ("subconjunto", "encabezado"),
    [
        (SUBCONJUNTO_ENTREGA, _COLUMNAS_ENTREGA),
        (SUBCONJUNTO_RECOLECCION, _COLUMNAS_RECOLECCION),
    ],
)
def test_el_encabezado_publicado_pasa_la_validacion_del_esquema(
    subconjunto: str, encabezado: list[str]
) -> None:
    """El loader tiene que aceptar los archivos que existen.

    Faltaba esta prueba: las que habia usaban el encabezado real solo como
    ejemplo de esquema invalido, y ninguna esperaba que pasara.
    """
    _, diferencia = normalizar_columnas_lade(pd.DataFrame(columns=encabezado), subconjunto)

    assert diferencia.coincide is True
    validar_esquema_lade(diferencia)


def _encabezado_con_package_id() -> list[str]:
    """Un archivo hipotetico con el nombre del paper en vez del publicado."""
    return ["package_id" if columna == "order_id" else columna for columna in _COLUMNAS_ENTREGA]


def test_la_diferencia_de_esquema_se_reporta_sin_abortar() -> None:
    """Normalizar informa; rechazar es responsabilidad de validar."""
    datos = pd.DataFrame(columns=_encabezado_con_package_id())
    _, diferencia = normalizar_columnas_lade(datos, SUBCONJUNTO_ENTREGA)

    assert diferencia.coincide is False
    assert "order_id" in diferencia.faltantes
    assert "package_id" in diferencia.sobrantes


def test_un_archivo_con_package_id_se_rechaza_sin_alias() -> None:
    """DAT-13: sin alias, el nombre del paper se rechaza a la vista."""
    datos = pd.DataFrame(columns=_encabezado_con_package_id())
    _, diferencia = normalizar_columnas_lade(datos, SUBCONJUNTO_ENTREGA)

    with pytest.raises(EsquemaLaDeInvalido):
        validar_esquema_lade(diferencia)


# --- DAT-14 y DAT-15, periodo sobre ds y ano de cada marca -----------------


@pytest.mark.parametrize(
    ("marca", "ds", "esperado"),
    [
        # Aceptacion de abril de un retiro de mayo: mismo ano.
        ("04-30 18:00:00", "0501", "2022-04-30 18:00:00"),
        # Entrega que desborda el cierre del periodo: mismo ano.
        ("11-01 09:00:00", "1031", "2022-11-01 09:00:00"),
        # Entrega de enero de una tarea de octubre: ano siguiente.
        ("01-13 11:00:00", "1031", "2023-01-13 11:00:00"),
    ],
)
def test_el_ano_de_la_marca_es_el_mas_cercano_a_su_ds(marca: str, ds: str, esperado: str) -> None:
    parseados = parsear_tiempo_lade(pd.Series([marca]), pd.Series([ds]))

    assert parseados.iloc[0] == pd.Timestamp(esperado)


def test_dentro_del_periodo_se_asigna_el_ano_base() -> None:
    parseados = parsear_tiempo_lade(
        pd.Series(["05-01 00:00:00", "10-31 23:59:59"]), pd.Series(["0501", "1031"])
    )

    assert parseados.iloc[0] == pd.Timestamp("2022-05-01 00:00:00")
    assert parseados.iloc[1] == pd.Timestamp("2022-10-31 23:59:59")


@pytest.mark.parametrize("ds", ["0430", "1101"])
def test_un_ds_fuera_del_periodo_se_rechaza(ds: str) -> None:
    with pytest.raises(RangoTemporalLaDeInvalido):
        validar_jornadas_lade(pd.Series(["0925", ds]))


def test_los_extremos_del_periodo_se_aceptan_como_texto_y_como_entero() -> None:
    """`ds` crudo trae cero inicial; leido como entero lo pierde."""
    validar_jornadas_lade(pd.Series(["0501", "1031"]))
    validar_jornadas_lade(pd.Series([501, 1031]))


def test_un_timestamp_ilegible_queda_nulo_y_no_rechaza() -> None:
    parseados = parsear_tiempo_lade(pd.Series(["", "no es una fecha"]), pd.Series(["0925", "0925"]))

    assert parseados.isna().all()


def test_la_validacion_completa_acepta_un_desborde_y_cuenta_su_ano(tmp_path: Path) -> None:
    """Ninguna fila se excluye por desbordar el periodo: se asigna su ano."""
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 1031, "10-31 18:00:00", accept="10-31 08:00:00"),
            _fila_de_entrega(2, 1031, "01-13 11:00:00", accept="10-31 08:00:00"),
        ],
    )
    validacion = validar_lade(ruta, SUBCONJUNTO_ENTREGA)

    assert validacion.registros == 2
    entrega = {a.columna: a for a in validacion.anios_asignados}["delivery_time"]
    assert (entrega.anio_anterior, entrega.anio_base, entrega.anio_siguiente) == (0, 1, 1)


# --- Descripcion ---------------------------------------------------------


def test_el_resumen_mide_registros_nulos_y_rango_temporal(tmp_path: Path) -> None:
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00"),
            _fila_de_entrega(1, 925, "09-25 10:30:00"),
        ],
    )
    resumen = resumir_lade(ruta, SUBCONJUNTO_ENTREGA)

    assert resumen.registros == 2
    assert resumen.ciudad_esperada == "Jilin"
    assert resumen.ciudades_observadas == {"Jilin": 2}
    assert resumen.jornadas == ("0925",)
    por_nombre = {perfil.nombre: perfil for perfil in resumen.columnas}
    assert por_nombre["courier_id"].proporcion_nulos == 0.0
    rangos = {rango.columna: rango for rango in resumen.rangos_temporales}
    assert rangos["delivery_time"].minimo == pd.Timestamp("2022-09-25 10:00:00")
    assert rangos["delivery_time"].maximo == pd.Timestamp("2022-09-25 10:30:00")
    assert rangos["delivery_time"].desplazamientos_de_anio == {0: 2}
    assert resumen.jornadas_fuera_del_periodo == 0


def test_las_jornadas_se_ordenan_cronologicamente_no_como_texto(tmp_path: Path) -> None:
    """`ds` leido como entero pierde el cero inicial: 930 es anterior a 1001."""
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 1001, "10-01 10:00:00"),
            _fila_de_entrega(1, 930, "09-30 10:00:00"),
        ],
    )
    resumen = resumir_lade(ruta, SUBCONJUNTO_ENTREGA)

    assert resumen.jornadas == ("0930", "1001")


# --- Deltas entre finalizaciones -----------------------------------------


def test_los_deltas_se_calculan_dentro_de_cada_transportista(tmp_path: Path) -> None:
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00"),
            _fila_de_entrega(1, 925, "09-25 10:30:00"),
            _fila_de_entrega(2, 925, "09-25 10:05:00"),
            _fila_de_entrega(2, 925, "09-25 10:15:00"),
        ],
    )
    deltas = deltas_entre_finalizaciones(ruta, SUBCONJUNTO_ENTREGA)

    assert deltas.transportistas == 2
    assert deltas.pares_consecutivos == 2
    assert deltas.minutos["min"] == 10.0
    assert deltas.minutos["max"] == 30.0


def test_el_corte_de_jornada_descarta_el_par_que_cruza_la_noche(tmp_path: Path) -> None:
    filas = [
        _fila_de_entrega(1, 925, "09-25 19:00:00"),
        _fila_de_entrega(1, 926, "09-26 08:00:00"),
    ]
    ruta = _csv_de_entrega(tmp_path, filas)

    con_corte = deltas_entre_finalizaciones(ruta, SUBCONJUNTO_ENTREGA)
    assert con_corte.pares_consecutivos == 0
    assert con_corte.pares_descartados_por_corte_de_jornada == 1

    sin_corte = deltas_entre_finalizaciones(
        ruta, SUBCONJUNTO_ENTREGA, segmentar_por_jornada=False
    )
    assert sin_corte.pares_consecutivos == 1
    assert sin_corte.minutos["min"] == pytest.approx(13 * 60)


def test_los_deltas_leen_un_archivo_con_nombres_crudos(tmp_path: Path) -> None:
    """El camino de `usecols` tambien resuelve el renombrado de DAT-02."""
    ruta = tmp_path / "pickup_jl.csv"
    pd.DataFrame(
        [
            {"delivery_user_id": 1, "ds": 925, "got_time": "09-25 10:00:00"},
            {"delivery_user_id": 1, "ds": 925, "got_time": "09-25 10:20:00"},
        ]
    ).to_csv(ruta, index=False)

    deltas = deltas_entre_finalizaciones(ruta, SUBCONJUNTO_RECOLECCION)

    assert deltas.pares_consecutivos == 1
    assert deltas.minutos["min"] == 20.0


# --- Aceptacion frente a finalizacion ------------------------------------


def test_la_espera_va_de_la_aceptacion_a_la_finalizacion(tmp_path: Path) -> None:
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00", accept="09-25 09:00:00"),
            _fila_de_entrega(1, 925, "09-25 12:00:00", accept="09-25 09:00:00"),
        ],
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert analisis.registros == 2
    assert analisis.espera_en_minutos["min"] == 60.0
    assert analisis.espera_en_minutos["max"] == 180.0
    assert analisis.esperas_negativas == 0


def test_una_finalizacion_anterior_a_su_aceptacion_se_cuenta_no_se_descarta(
    tmp_path: Path,
) -> None:
    """Es una inconsistencia del dato: hay que verla, no taparla."""
    ruta = _csv_de_entrega(
        tmp_path, [_fila_de_entrega(1, 925, "09-25 08:00:00", accept="09-25 10:00:00")]
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert analisis.esperas_negativas == 1
    assert analisis.espera_en_minutos["min"] == -120.0


def test_se_cuentan_las_aceptaciones_posteriores_a_la_primera_finalizacion(
    tmp_path: Path,
) -> None:
    """Criterio del codigo oficial para detectar tareas que entran con la
    ruta ya en ejecucion. Aca: una aceptada antes de arrancar, una despues."""
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00", accept="09-25 08:00:00"),
            _fila_de_entrega(1, 925, "09-25 13:00:00", accept="09-25 11:00:00"),
        ],
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert analisis.aceptadas_tras_la_primera_finalizacion == 1
    assert analisis.proporcion_aceptadas_tras_la_primera_finalizacion == 0.5


def test_la_aceptacion_batcheada_da_amplitud_cero(tmp_path: Path) -> None:
    """Si todas las tareas se aceptan en el mismo instante, la jornada tiene
    una sola aceptacion distinta y amplitud nula."""
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00", accept="09-25 08:00:00"),
            _fila_de_entrega(1, 925, "09-25 11:00:00", accept="09-25 08:00:00"),
        ],
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert analisis.aceptaciones_distintas_por_jornada["max"] == 1.0
    assert analisis.amplitud_de_aceptacion_en_minutos["max"] == 0.0
    assert analisis.aceptadas_tras_la_primera_finalizacion == 0


def test_las_horas_del_dia_incluyen_las_vacias(tmp_path: Path) -> None:
    """Una hora ausente y una hora en cero se leen distinto."""
    ruta = _csv_de_entrega(
        tmp_path, [_fila_de_entrega(1, 925, "09-25 10:00:00", accept="09-25 08:00:00")]
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert len(analisis.aceptaciones_por_hora) == 24
    assert analisis.aceptaciones_por_hora[8] == 1.0
    assert analisis.aceptaciones_por_hora[9] == 0.0
    assert analisis.finalizaciones_por_hora[10] == 1.0


def test_una_fila_sin_aceptacion_queda_afuera_y_no_se_imputa(tmp_path: Path) -> None:
    ruta = _csv_de_entrega(
        tmp_path,
        [
            _fila_de_entrega(1, 925, "09-25 10:00:00", accept="09-25 08:00:00"),
            _fila_de_entrega(1, 925, "09-25 11:00:00", accept=""),
        ],
    )
    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_ENTREGA)

    assert analisis.registros == 1


def test_la_aceptacion_se_analiza_tambien_en_recoleccion(tmp_path: Path) -> None:
    """En pickup la finalizacion es `pickup_time`, no `delivery_time`."""
    ruta = tmp_path / "pickup_jl.csv"
    pd.DataFrame(
        [
            {
                "courier_id": 1,
                "ds": 925,
                "accept_time": "09-25 08:00:00",
                "accept_gps_time": "09-25 08:00:00",
                "pickup_time": "09-25 09:30:00",
            }
        ]
    ).to_csv(ruta, index=False)

    analisis = analizar_aceptacion(ruta, SUBCONJUNTO_RECOLECCION)

    assert analisis.registros == 1
    assert analisis.espera_en_minutos["max"] == 90.0
    assert analisis.proporcion_de_aceptacion_igual_a_su_gps == 1.0
