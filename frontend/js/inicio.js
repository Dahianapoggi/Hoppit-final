/**
 * Inicio de la jornada (DFE-12).
 *
 * Un resumen armado solo con operaciones que el contrato ya publica, sin
 * ninguna nueva: las Ordenes por estado (`GET /v1/ordenes`), los Planes y las
 * Rutas de la jornada (`GET /v1/planes?fecha=`, `GET /v1/rutas?fecha=`), la
 * Flota disponible (`GET /v1/flota?fecha=`), los Incidentes declarados en la
 * jornada (`GET /v1/incidentes`, filtrados por la fecha de la operacion) y
 * las alertas vigentes (`GET /v1/alertas?jornada=`).
 *
 * Cada cifra sale de su respuesta; si una consulta falla, su tarjeta lo dice
 * y las demas se muestran igual.
 *
 * El recorrido de la jornada (registrar, planificar, despachar, declarar) se
 * deduce de los estados de las Ordenes y de si hay plan: no es un dato del
 * backend, es la lectura de la pantalla sobre lo que el backend publica.
 */

import * as api from './api.js';
import { ApiInalcanzable } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { escapar } from './modal.js';
import { exigirSesion } from './sesion.js';
import { ETIQUETA_ESTADO, fechaOperativa, horaDeLaOperacion, textoDeRotulo } from './tokens.js';

const $ = (id) => document.getElementById(id);
const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`;

/* Los estados con color propio en la barra de Ordenes, en el orden del ciclo
   de vida. El resto (reprogramada, devuelta, cancelada) no tiene operacion
   que lo produzca. */
const ESTADOS_DE_LA_BARRA = ['completada', 'en_transito', 'planificada', 'creada', 'fallida'];
const ESTADOS_TERMINADOS = new Set(['completada', 'fallida']);

const ETIQUETA_ALERTA = {
  incidente_en_ruta: 'Incidente en una Ruta',
  permanencia_en_creada: 'Orden sin planificar',
  riesgo_de_plazo: 'Plazo en riesgo',
};
const ICONO_ALERTA = { incidente_en_ruta: 'incidentes', riesgo_de_plazo: 'reloj', permanencia_en_creada: 'caja' };

/** Buen dia hasta el mediodia, buenas tardes hasta las 20, buenas noches. */
function saludoDeLaHora() {
  const hora = Number(horaDeLaOperacion(new Date()).slice(0, 2));
  if (hora < 12) return 'Buen día';
  if (hora < 20) return 'Buenas tardes';
  return 'Buenas noches';
}

function textoDelError(error) {
  return error instanceof ApiInalcanzable
    ? 'Sin conexión con el servidor.'
    : escapar(error?.message ?? 'No se pudo consultar.');
}

function kpi({ eyebrow, ico, valor, sub = '', barra = '', accion = '', destacado = false }) {
  return `
    <section class="tarjeta kpi${destacado ? ' destacado' : ''}">
      <div class="eyebrow">${eyebrow}</div>
      <div class="kpi-ico">${icono(ico)}</div>
      <div class="kpi-val">${valor}</div>
      ${sub ? `<div class="kpi-sub">${sub}</div>` : ''}
      ${barra ? `<div class="kpi-barra">${barra}</div>` : ''}
      ${accion}
    </section>`;
}

function kpiConError(eyebrow, ico, error) {
  return kpi({
    eyebrow, ico, valor: '—',
    sub: `<span class="kpi-error">No se pudo consultar: ${textoDelError(error)}</span>`,
    accion: '<button class="btn-ghost kpi-accion" data-reintentar>Reintentar</button>',
  });
}

function kpiDeOrdenes(ordenes) {
  const cuentas = {};
  ordenes.forEach((o) => { cuentas[o.estado] = (cuentas[o.estado] ?? 0) + 1; });
  const sub = ordenes.length
    ? `${cuentas.creada ?? 0} Creadas · ${cuentas.planificada ?? 0} Planificadas`
    : 'Ninguna Orden espera plan ni está en el de la jornada';
  const barra = ordenes.length
    ? ESTADOS_DE_LA_BARRA.filter((e) => cuentas[e])
      .map((e) => `<i class="b-${e}" style="width:${(cuentas[e] / ordenes.length) * 100}%" title="${ETIQUETA_ESTADO[e]}: ${cuentas[e]}"></i>`)
      .join('')
    : '';
  return kpi({ eyebrow: 'Órdenes de la jornada', ico: 'caja', valor: ordenes.length, sub, barra });
}

function kpiDeFlota(flota) {
  const total = flota.vehiculos.length;
  const libres = flota.vehiculos.filter((v) => v.disponible !== false).length;
  return kpi({
    eyebrow: 'Flota disponible',
    ico: 'camion',
    valor: `${libres}<small> / ${total}</small>`,
    sub: `Capacidad efectiva: ${plural(flota.capacidad_efectiva_disponible ?? 0, 'bulto', 'bultos')}`,
    barra: total ? `<i class="b-libre" style="width:${(libres / total) * 100}%"></i>` : '',
  });
}

function incidentesDeHoy(incidentes, jornada) {
  return incidentes.filter((i) => fechaOperativa(new Date(i.declarado_en)) === jornada);
}

function kpiDeIncidentes(deHoy, esHoy) {
  const cuando = esHoy ? 'hoy' : 'en la jornada';
  return kpi({
    eyebrow: 'Incidentes',
    ico: 'incidentes',
    valor: deHoy.length,
    sub: deHoy.length ? `${plural(deHoy.length, 'declarado', 'declarados')} ${cuando}` : `Ninguno declarado ${cuando}`,
    accion: deHoy.length ? '<a class="kpi-link" href="incidentes.html">Ver Incidentes →</a>' : '',
  });
}

function kpiDelPlan(planes, rutas, jornada) {
  if (!planes.length) {
    return kpi({
      eyebrow: 'Plan de la jornada', ico: 'planificar', valor: '—', destacado: true,
      sub: 'Todavía no se planificó',
      accion: `<a class="btn btn-con-icono kpi-accion" href="planificacion.html?fecha=${jornada}">Planificar ahora ${icono('flecha')}</a>`,
    });
  }
  const paradas = rutas.reduce((a, r) => a + (r.cantidad_paradas ?? 0), 0);
  return kpi({
    eyebrow: 'Plan de la jornada', ico: 'planificar', destacado: true,
    valor: `${rutas.length}<small> ${rutas.length === 1 ? 'Ruta' : 'Rutas'}</small>`,
    sub: plural(paradas, 'Parada', 'Paradas'),
    accion: `<a class="btn btn-con-icono kpi-accion" href="index.html?fecha=${jornada}">Ver en el mapa ${icono('flecha')}</a>`,
  });
}

/**
 * Los cuatro pasos de la jornada y en cual esta. Un paso esta hecho cuando
 * lo que deja ya existe: Ordenes registradas, un plan, ninguna Orden del
 * plan esperando despacho, ninguna en camino.
 */
function recorrido(ordenes, planes, jornada) {
  const hayPlan = planes.length > 0;
  const enPlan = ordenes.filter((o) => o.asignacion);
  const sinDespachar = enPlan.filter((o) => o.estado === 'planificada').length;
  const enCamino = enPlan.filter((o) => o.estado === 'en_transito').length;
  const terminadas = enPlan.filter((o) => ESTADOS_TERMINADOS.has(o.estado)).length;
  const bultos = ordenes.reduce((a, o) => a + (o.demanda ?? 0), 0);
  const pasos = [
    {
      titulo: 'Registrar Órdenes',
      detalle: ordenes.length
        ? `${plural(ordenes.length, 'Orden registrada', 'Órdenes registradas')} · ${plural(bultos, 'bulto', 'bultos')}`
        : 'Una por una, marcando el destino en el mapa',
      hecho: ordenes.length > 0,
      accion: '<a class="btn" href="orden.html">Registrar Orden</a>',
    },
    {
      titulo: 'Planificar las Rutas',
      detalle: hayPlan ? 'Plan de Distribución publicado' : 'Elegir Vehículos y Órdenes, y calcular sobre la red vial',
      hecho: hayPlan,
      accion: `<a class="btn" href="planificacion.html?fecha=${jornada}">Planificar</a>`,
    },
    {
      titulo: 'Revisar y despachar',
      detalle: hayPlan && sinDespachar
        ? `${plural(sinDespachar, 'Orden espera', 'Órdenes esperan')} el despacho de su Ruta`
        : 'Ver cada Ruta en el mapa con la hora estimada por Parada',
      hecho: hayPlan && sinDespachar === 0 && enPlan.length > 0,
      accion: `<a class="btn" href="index.html?fecha=${jornada}">Ver el plan</a>`,
    },
    {
      titulo: 'Declarar resultados',
      detalle: hayPlan && (enCamino || terminadas)
        ? `${terminadas} de ${enPlan.length} Órdenes con resultado`
        : 'Completada o fallida, Parada por Parada',
      hecho: hayPlan && enPlan.length > 0 && terminadas === enPlan.length,
      accion: `<a class="btn" href="index.html?fecha=${jornada}">Ver el plan</a>`,
    },
  ];
  const actual = pasos.findIndex((p) => !p.hecho);
  $('paso-actual').textContent = actual === -1 ? 'Jornada completa' : `Paso ${actual + 1} de ${pasos.length}`;
  $('pasos').innerHTML = pasos.map((p, i) => {
    const clase = p.hecho ? ' hecho' : i === actual ? ' actual' : '';
    const marca = p.hecho ? icono('ok', { grosor: 2.4 }) : i + 1;
    return `
      <div class="paso${clase}">
        <div class="paso-n">${marca}</div>
        <div class="paso-t"><b>${p.titulo}</b><span>${p.detalle}</span></div>
        ${i === actual ? p.accion : ''}
      </div>`;
  }).join('');
}

/** Las cinco Ordenes registradas mas recientemente. */
function ultimas(ordenes) {
  if (!ordenes.length) {
    $('ultimas').innerHTML = '<p class="vacio-texto">Todavía no hay Órdenes registradas.</p>';
    return;
  }
  const alta = (o) => o.historial?.[0]?.marca_temporal ?? '';
  const recientes = [...ordenes].sort((a, b) => alta(b).localeCompare(alta(a)) || b.id.localeCompare(a.id)).slice(0, 5);
  $('ultimas').innerHTML = recientes.map((o) => `
    <a class="fila-mini" href="ordenes.html?orden=${encodeURIComponent(o.id)}">
      <span class="id">${escapar(o.id)}</span>
      <span class="dir">${textoDeRotulo(o.destino?.rotulo) || '—'}</span>
      <span class="stchip st-${o.estado}">${ETIQUETA_ESTADO[o.estado] ?? escapar(o.estado)}</span>
    </a>`).join('');
}

function alertasMini(resultado) {
  if (resultado.status !== 'fulfilled') {
    $('alertas-mini').innerHTML = `<p class="vacio-texto">No se pudieron consultar las alertas: ${textoDelError(resultado.reason)}</p>`;
    return;
  }
  const { alertas } = resultado.value;
  if (!alertas.length) {
    $('alertas-mini').innerHTML = `
      <div class="vacio-ok"><div class="ilu">${icono('ok', { grosor: 2.4 })}</div>No hay alertas vigentes en la jornada.</div>`;
    return;
  }
  $('alertas-mini').innerHTML = `<div class="lista-mini">${alertas.slice(0, 4).map((a) => `
    <a class="fila-alerta t-${a.tipo}" href="alertas.html">
      <span class="ico">${icono(ICONO_ALERTA[a.tipo] ?? 'alertas')}</span>
      <span class="txt"><b>${ETIQUETA_ALERTA[a.tipo] ?? escapar(a.tipo)}</b><span>${escapar(a.motivo)}</span></span>
    </a>`).join('')}</div>`;
}

function accesos(jornada) {
  const items = [
    { href: 'orden.html', ico: 'orden', titulo: 'Registrar Orden', texto: 'Marcando el destino en el mapa' },
    { href: 'ordenes.html', ico: 'ordenes', titulo: 'Ver Órdenes', texto: 'Filtrar por estado y jornada' },
    { href: `index.html?fecha=${jornada}`, ico: 'plan', titulo: 'Plan de rutas', texto: 'Despacho y resultado de Paradas', id: 'acceso-plan' },
    { href: 'incidentes.html#declarar', ico: 'incidentes', titulo: 'Declarar Incidente', texto: 'Corte, siniestro u otro evento' },
  ];
  $('accesos').innerHTML = items.map((a) => `
    <a class="acceso"${a.id ? ` id="${a.id}"` : ''} href="${a.href}">
      <span class="acceso-ico">${icono(a.ico)}</span>
      <span><b>${a.titulo}</b><span>${a.texto}</span></span>
    </a>`).join('');
}

/* La jornada pedida en la direccion, o la de hoy. */
function jornadaPedida() {
  const pedida = new URLSearchParams(window.location.search).get('fecha');
  return /^\d{4}-\d{2}-\d{2}$/.test(pedida ?? '') ? pedida : fechaOperativa();
}

/**
 * Las Ordenes de la jornada: las que esperan plan (Creada, que entran en
 * cualquier jornada) y las asignadas a una Ruta de esa jornada. Las de otros
 * dias no cuentan, para que el resumen hable del dia que se mira.
 */
function ordenesDeLaJornada(ordenes, rutas) {
  const ids = new Set(rutas.map((r) => r.id));
  return ordenes.filter((o) => o.estado === 'creada' || (o.asignacion && ids.has(o.asignacion.ruta_id)));
}

async function cargar(sesion, jornada) {
  $('resumen').innerHTML = '<p class="cargando">Cargando el resumen…</p>';
  accesos(jornada);

  const [ordenes, planes, rutas, flota, incidentes, alertas] = await Promise.allSettled([
    api.listarOrdenes(),
    api.listarPlanes({ fecha: jornada }),
    api.listarRutas({ fecha: jornada }),
    api.flota({ fecha: jornada }),
    api.listarIncidentes(),
    api.alertas({ jornada }),
  ]);
  const ok = (r) => r.status === 'fulfilled';
  // Las Ordenes se leen todas y se recortan a la jornada con sus Rutas; sin
  // las Rutas, quedan las que esperan plan.
  if (ok(ordenes)) ordenes.value = ordenesDeLaJornada(ordenes.value, ok(rutas) ? rutas.value : []);

  $('resumen').innerHTML = [
    ok(ordenes) ? kpiDeOrdenes(ordenes.value) : kpiConError('Órdenes registradas', 'caja', ordenes.reason),
    ok(flota) ? kpiDeFlota(flota.value) : kpiConError('Flota disponible', 'camion', flota.reason),
    ok(incidentes)
      ? kpiDeIncidentes(incidentesDeHoy(incidentes.value, jornada), jornada === fechaOperativa())
      : kpiConError('Incidentes', 'incidentes', incidentes.reason),
    ok(planes) && ok(rutas)
      ? kpiDelPlan(planes.value, rutas.value, jornada)
      : kpiConError('Plan de la jornada', 'planificar', planes.reason ?? rutas.reason),
  ].join('');
  document.querySelectorAll('[data-reintentar]').forEach((b) => b.addEventListener('click', () => cargar(sesion, jornada)));

  // La bajada del saludo resume lo que espera: Ordenes sin plan y Vehiculos libres.
  if (ok(ordenes) && ok(flota)) {
    const creadas = ordenes.value.filter((o) => o.estado === 'creada').length;
    const libres = flota.value.vehiculos.filter((v) => v.disponible !== false).length;
    $('saludo-bajada').textContent = creadas
      ? `Hay ${plural(creadas, 'Orden', 'Órdenes')} esperando planificación y ${plural(libres, 'Vehículo libre', 'Vehículos libres')}.`
      : `Ninguna Orden espera planificación. ${plural(libres, 'Vehículo libre', 'Vehículos libres')}.`;
  }

  if (ok(ordenes) && ok(planes)) recorrido(ordenes.value, planes.value, jornada);
  else {
    $('paso-actual').textContent = '';
    $('pasos').innerHTML = '<p class="vacio-texto">No se pudo deducir el recorrido: falta la consulta de Órdenes o del plan.</p>';
  }
  if (ok(ordenes)) ultimas(ordenes.value);
  else $('ultimas').innerHTML = `<p class="vacio-texto">No se pudieron leer las Órdenes: ${textoDelError(ordenes.reason)}</p>`;
  alertasMini(alertas);
}

function iniciar() {
  const sesion = exigirSesion();
  if (!sesion) return;
  montarEncabezado({ seccion: 'inicio' });
  $('cab-registrar').innerHTML = `${icono('orden')}Registrar Orden`;
  $('saludo').textContent = `${saludoDeLaHora()}, ${sesion.nombre_visible}`;
  const jornada = jornadaPedida();
  $('fecha').value = jornada;
  $('fecha').addEventListener('change', (ev) => {
    const elegida = ev.target.value || fechaOperativa();
    history.replaceState(null, '', elegida === fechaOperativa() ? 'inicio.html' : `inicio.html?fecha=${elegida}`);
    cargar(sesion, elegida);
  });
  cargar(sesion, jornada);
}

iniciar();
