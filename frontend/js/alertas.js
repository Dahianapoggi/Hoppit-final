/**
 * Alertas (DFE-36).
 *
 * Con la unica operacion `GET /v1/alertas?jornada=`: las alertas se derivan
 * al consultar y no se persisten (CU-08; B-259, punto 3). Mirarlas no cambia
 * ninguna Orden ni ninguna Ruta. La pantalla vuelve a consultar cada
 * INTERVALO_DE_CONSULTA_DE_ALERTAS_S, el mismo del indicador de la
 * navegacion, y cada alerta lleva a la Ruta o a la Orden que la causa.
 *
 * El Vehiculo de una Ruta sale de `GET /v1/rutas?fecha=`, solo para nombrarla:
 * si esa consulta falla, la alerta se muestra igual, sin el Vehiculo.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { INTERVALO_DE_CONSULTA_DE_ALERTAS_S, montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { escapar, modalSinConexion } from './modal.js';
import { exigirSesion } from './sesion.js';
import { fechaLegible, fechaOperativa, horaDeLaOperacion } from './tokens.js';

const $ = (id) => document.getElementById(id);

/* Los tipos del contrato (`TipoDeAlerta`), con nombre, icono y el texto de
   su tarjeta de resumen, en el orden de la propuesta. */
const TIPOS = {
  incidente_en_ruta: { nombre: 'Incidente en una Ruta', icono: 'incidentes', resumen: 'Incidente en una Ruta con entregas' },
  riesgo_de_plazo: { nombre: 'Plazo en riesgo', icono: 'reloj', resumen: 'Plazo en riesgo' },
  permanencia_en_creada: { nombre: 'Orden sin planificar', icono: 'caja', resumen: 'Orden sin planificar hace más de 48 h' },
  desvio_de_ruta: { nombre: 'Desvío de la Ruta', icono: 'alertas', resumen: 'Vehículo lejos de su Ruta' },
  /* CU-06, 4a (encargo 2026-11-23B, D29): resultados declarados desde la
     aplicacion con su hora. */
  resultado_de_campo_sobre_ruta_vigente: { nombre: 'Resultado sin conexión sobre la Ruta vigente', icono: 'alertas',
    resumen: 'Resultado sin conexión aplicado sobre la Ruta vigente' },
  declaracion_de_campo_prevalece: { nombre: 'Declaración del transportista que prevalece', icono: 'alertas',
    resumen: 'Declaración del transportista sobre un resultado posterior' },
  declaracion_de_campo_no_aplicada: { nombre: 'Declaración del transportista no aplicada', icono: 'alertas',
    resumen: 'Declaración del transportista no aplicada' },
  /* Encargo 2026-11-23E (D31): propuesta de reruteo por tramos con más hechos
     viales predichos, con el compromiso en números. */
  riesgo_vial_en_ruta: { nombre: 'Tramos con más hechos viales predichos', icono: 'alertas',
    resumen: 'Ruta con alternativa de menos hechos viales predichos' },
};

const estado = { jornada: jornadaPedida(), consulta: null, vehiculos: new Map() };

function jornadaPedida() {
  const pedida = new URLSearchParams(window.location.search).get('jornada');
  return /^\d{4}-\d{2}-\d{2}$/.test(pedida ?? '') ? pedida : fechaOperativa();
}

const vehiculoDe = (idRuta) => estado.vehiculos.get(idRuta) ?? null;

/** A donde lleva cada alerta: la Ruta en el mapa, el detalle de la Orden, o
    planificar si la Orden espera plan. */
function accion(a) {
  if ((a.tipo === 'desvio_de_ruta' || a.tipo === 'riesgo_vial_en_ruta') && a.propuesta) {
    /* 2026-11-22A, D17: el operador queda en el lazo; aprueba o rechaza el
       reruteo desde la posicion (CU-07). */
    return `<a class="btn-ghost" href="index.html?fecha=${encodeURIComponent(estado.jornada)}&ruta=${encodeURIComponent(a.ruta)}">Ver la Ruta</a>
      <button class="btn-ghost" data-aprobar="${escapar(a.propuesta)}">Aprobar el reruteo</button>
      <button class="btn-ghost" data-rechazar="${escapar(a.propuesta)}">Rechazar</button>`;
  }
  if (a.ruta) {
    return `<a class="btn-ghost" href="index.html?fecha=${encodeURIComponent(estado.jornada)}&ruta=${encodeURIComponent(a.ruta)}">Ver la Ruta</a>`;
  }
  if (a.tipo === 'permanencia_en_creada') {
    return `<a class="btn-ghost" href="planificacion.html?fecha=${encodeURIComponent(fechaOperativa())}">Planificar</a>`;
  }
  if (a.orden) {
    return `<a class="btn-ghost" href="ordenes.html?orden=${encodeURIComponent(a.orden)}">Ver la Orden</a>`;
  }
  return '';
}

/** El titulo: que Ruta o que Orden. */
function titulo(a) {
  if (a.tipo === 'riesgo_vial_en_ruta') {
    return `Alternativa para la Ruta de ${escapar(a.vehiculo ?? vehiculoDe(a.ruta) ?? 'un Vehículo')}`;
  }
  if (a.tipo === 'desvio_de_ruta') {
    return `La Ruta de ${escapar(a.vehiculo ?? vehiculoDe(a.ruta) ?? 'un Vehículo')} se apartó del recorrido`;
  }
  if (a.ruta && a.tipo.startsWith('declaracion_de_campo') || a.tipo === 'resultado_de_campo_sobre_ruta_vigente') {
    /* 2026-11-23B (D29): no es un Incidente; visto en el navegador, decia
       "Incidente sobre la Ruta". */
    const vehiculo = vehiculoDe(a.ruta);
    return vehiculo ? `Declaración del transportista en la Ruta ${escapar(vehiculo)}` : 'Declaración del transportista';
  }
  if (a.ruta) {
    const vehiculo = vehiculoDe(a.ruta);
    return vehiculo ? `Incidente sobre la Ruta ${escapar(vehiculo)}` : 'Incidente sobre una Ruta de la jornada';
  }
  if (a.orden) return escapar(a.orden);
  return TIPOS[a.tipo]?.nombre ?? escapar(a.tipo);
}

/** Desde cuando: la hora si es de hoy, o el dia si no. */
function desdeLegible(iso) {
  const dia = fechaOperativa(new Date(iso));
  return dia === fechaOperativa() ? horaDeLaOperacion(iso) : fechaLegible(dia).replace(/ \d{4}$/, '');
}

function detalle(a) {
  if (a.tipo === 'riesgo_de_plazo') {
    return ` Entrega estimada ${horaDeLaOperacion(a.eta_p50)}, y en el peor caso estimado
      ${horaDeLaOperacion(a.eta_p90)} del ${fechaLegible(fechaOperativa(new Date(a.eta_p90)))};
      plazo: ${fechaLegible(a.plazo_comprometido)}. Son estimaciones, no horas medidas.`;
  }
  if (a.tipo === 'desvio_de_ruta') {
    return a.propuesta
      ? ' Se propone recalcular las Paradas pendientes desde la posición del Vehículo; aprobarlo reemplaza la Ruta.'
      : ' La propuesta de reruteo ya se resolvió hace poco: no se genera otra por unos minutos.';
  }
  if (a.tipo === 'riesgo_vial_en_ruta') {
    /* El motivo del servidor no termina en punto (visto en el navegador, 23E). */
    return '. Es una predicción anual por tramo, no una detección en tiempo real. Aprobarlo reemplaza la Ruta; rechazarlo la conserva y no se vuelve a proponer para esta Ruta.';
  }
  if (a.tipo === 'incidente_en_ruta' && a.ordenes.length) {
    return ` Órdenes sin terminar: ${a.ordenes.map(escapar).join(', ')}.`;
  }
  return '';
}

function pintarResumen(alertas) {
  const cuentas = {};
  alertas.forEach((a) => { cuentas[a.tipo] = (cuentas[a.tipo] ?? 0) + 1; });
  $('resumen').innerHTML = Object.entries(TIPOS).map(([tipo, t]) => `
    <div class="tarjeta al-tipo t-${tipo}">
      <span class="ico">${icono(t.icono)}</span>
      <div><div class="v">${cuentas[tipo] ?? 0}</div><div class="k">${t.resumen}</div></div>
    </div>`).join('');
}

function pintar({ alertas, evaluadas_en: evaluadas }) {
  $('cuenta').textContent = alertas.length === 1 ? '1 alerta' : `${alertas.length} alertas`;
  $('evaluadas').innerHTML = `<i></i>Se recalculan cada minuto · última <span class="num">${horaDeLaOperacion(evaluadas)}</span>`;
  pintarResumen(alertas);
  if (!alertas.length) {
    $('lista').innerHTML = `<div class="vacio-ok"><div class="ilu">${icono('ok', { grosor: 2.4 })}</div>No hay alertas vigentes en esta jornada.</div>`;
    return;
  }
  $('lista').innerHTML = alertas.map((a) => `
    <div class="alerta t-${a.tipo}">
      <span class="ico">${icono(TIPOS[a.tipo]?.icono ?? 'alertas')}</span>
      <div class="txt">
        <div class="tit">${TIPOS[a.tipo]?.nombre ?? escapar(a.tipo)}</div>
        <b>${titulo(a)}</b>
        <p>${escapar(a.motivo)}${detalle(a)}</p>
      </div>
      <div class="der">
        <span class="num">desde ${desdeLegible(a.desde)}</span>
        ${accion(a)}
      </div>
    </div>`).join('');
}

async function cargarVehiculos() {
  try {
    const rutas = await api.listarRutas({ fecha: estado.jornada });
    estado.vehiculos = new Map(rutas.map((r) => [r.id, r.vehiculo_id]));
  } catch {
    estado.vehiculos = new Map();
  }
}

async function consultar() {
  try {
    pintar(await api.alertas({ jornada: estado.jornada }));
  } catch (e) {
    if (e instanceof ApiInalcanzable) {
      $('lista').innerHTML = '<p class="vacio-texto">No hay conexión con el servidor: las alertas no se pudieron consultar.</p>';
      modalSinConexion(consultar);
    } else if (e instanceof ErrorDeApi) {
      $('lista').innerHTML = `<p class="vacio-texto">No se pudieron consultar las alertas: ${escapar(e.message)}</p>`;
    } else {
      throw e;
    }
  }
}

async function cambiarJornada(fecha) {
  estado.jornada = fecha;
  $('lista').innerHTML = '<p class="cargando">Consultando las alertas…</p>';
  await cargarVehiculos();
  consultar();
}

/** Aprobar o rechazar el reruteo de una alerta de desvio (2026-11-22A) o de
    riesgo vial (2026-11-23E, D31). */
async function resolverReruteo(ev) {
  const boton = ev.target.closest('button[data-aprobar], button[data-rechazar]');
  if (!boton) return;
  boton.disabled = true;
  try {
    if (boton.dataset.aprobar) await api.aprobarPropuesta(boton.dataset.aprobar);
    else await api.rechazarPropuesta(boton.dataset.rechazar);
  } catch (e) {
    if (!(e instanceof ErrorDeApi || e instanceof ApiInalcanzable)) throw e;
    boton.disabled = false;
    boton.insertAdjacentHTML('afterend', `<p class="vacio-texto">No se pudo: ${escapar(e.message)}</p>`);
    return;
  }
  consultar();
}

function iniciar() {
  if (!exigirSesion()) return;
  $('lista').addEventListener('click', resolverReruteo);
  montarEncabezado({ seccion: 'alertas' });
  $('fecha').value = estado.jornada;
  $('fecha').addEventListener('change', (ev) => cambiarJornada(ev.target.value || fechaOperativa()));
  cambiarJornada(estado.jornada);
  estado.consulta = setInterval(consultar, INTERVALO_DE_CONSULTA_DE_ALERTAS_S * 1000);
}

iniciar();
