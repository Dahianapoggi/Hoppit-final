/**
 * Incidentes (DFE-17).
 *
 * Con las tres operaciones que el contrato publica (FR-18; CU-07, paso 1):
 * `GET /v1/incidentes` para la lista, filtrada aca por la jornada de la
 * operacion; `GET /v1/incidentes/{id}` para el detalle, y
 * `POST /v1/incidentes` para declarar. Las Rutas que se ofrecen como
 * afectadas son las de la jornada (`GET /v1/rutas?fecha=`).
 *
 * Registrar un Incidente no recalcula nada y la pantalla lo dice. Las alertas
 * que deriva un Incidente se ven en alertas.html y en la ficha de la Ruta
 * (2026-10-27B, A).
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { avisoBreve, escapar, modalSinConexion } from './modal.js';
import { exigirSesion } from './sesion.js';
import { fechaOperativa, paletaPorVehiculo } from './tokens.js';
import { icono } from './iconos.js';

const $ = (id) => document.getElementById(id);

/* Los catalogos `TipoDeIncidente` y `AlcanceDelIncidente` del contrato, con
   los nombres del glosario (entidad 8). */
const ETIQUETA_TIPO = {
  corte_de_calle: 'Corte de calle',
  siniestro: 'Siniestro',
  desvio: 'Desvío',
  cancelacion: 'Cancelación',
  condicion_climatica_adversa: 'Condición climática adversa',
};
const ETIQUETA_ALCANCE = { total: 'Total', parcial: 'Parcial' };

const ZONA_DE_LA_OPERACION = 'America/Argentina/Buenos_Aires';
const FORMATO_HORA = new Intl.DateTimeFormat('es-AR', {
  timeZone: ZONA_DE_LA_OPERACION, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

function fechaLegible(iso) {
  const [anio, mes, dia] = iso.split('-');
  return `${Number(dia)} ${MESES[Number(mes) - 1]} ${anio}`;
}

function jornadaPedida() {
  const pedida = new URLSearchParams(window.location.search).get('fecha');
  return /^\d{4}-\d{2}-\d{2}$/.test(pedida ?? '') ? pedida : fechaOperativa();
}

const estado = {
  jornada: jornadaPedida(),
  incidentes: [],
  rutas: [],
  paleta: new Map(),
  seleccion: null,
  // La Ruta desde la que se llego a declarar (G1-A), marcada de entrada.
  rutaPedida: new URLSearchParams(window.location.search).get('ruta'),
};

const etiquetaTipo = (t) => ETIQUETA_TIPO[t] ?? t;
const deLaJornada = () => estado.incidentes.filter(
  (i) => fechaOperativa(new Date(i.declarado_en)) === estado.jornada,
);

/* ------------------------------------------------------------------ */
/* Lista y detalle                                                     */
/* ------------------------------------------------------------------ */

/* La Ruta como etiqueta: su numero en el color de la paleta y el Vehiculo,
   como en el plan (la marca pide color y numero). */
function etiquetaDeRuta(ruta) {
  const ids = [...estado.paleta.keys()];
  return `<span class="tag-ruta"><span class="disco-ruta" style="background:${estado.paleta.get(ruta.vehiculo_id)}">${ids.indexOf(ruta.vehiculo_id) + 1}</span>${escapar(ruta.vehiculo_id)}</span>`;
}

/* Las Rutas afectadas como etiquetas; las de otra jornada, contadas. */
function rutasAfectadas(i) {
  if (i.alcance === 'total' && !i.rutas_afectadas.length) return '<span>Todo el plan</span>';
  const propias = i.rutas_afectadas.map((r) => estado.rutas.find((x) => x.id === r)).filter(Boolean);
  const otras = i.rutas_afectadas.length - propias.length;
  return propias.map(etiquetaDeRuta).join('')
    + (otras ? `<span>${otras} ${otras === 1 ? 'Ruta' : 'Rutas'} de otra jornada</span>` : '');
}

function pintarLista() {
  const lista = deLaJornada();
  $('cuenta').textContent = lista.length ? `${lista.length} ${lista.length === 1 ? 'Incidente' : 'Incidentes'}` : '';
  if (!lista.length) {
    $('lista').innerHTML = `
      <p class="vacio-texto">No se declaró ningún Incidente el
        ${fechaLegible(estado.jornada)}.${estado.jornada === fechaOperativa()
          ? ' Si algo afecta el plan, declaralo con el formulario.' : ''}</p>`;
    return;
  }
  $('lista').innerHTML = lista.map((i) => `
    <div class="inc${estado.seleccion === i.id ? ' sel' : ''}" data-incidente="${escapar(i.id)}" tabindex="0" role="button"
        aria-label="${escapar(etiquetaTipo(i.tipo))}, ${FORMATO_HORA.format(new Date(i.declarado_en))}">
      <span class="inc-ico${i.tipo === 'condicion_climatica_adversa' ? ' clima' : ''}">${icono(i.tipo === 'condicion_climatica_adversa' ? 'nube' : 'incidentes')}</span>
      <div class="inc-txt">
        <div class="inc-l1"><b>${escapar(etiquetaTipo(i.tipo))}</b><span class="hora">${FORMATO_HORA.format(new Date(i.declarado_en))}</span></div>
        <div class="meta"><span class="prio${i.alcance === 'total' ? ' alta' : ''}">${ETIQUETA_ALCANCE[i.alcance] ?? escapar(i.alcance)}</span>${rutasAfectadas(i)}</div>
        ${i.descripcion ? `<p>${escapar(i.descripcion)}</p>` : ''}
      </div>
    </div>`).join('');
}

function pintarErrorDeLista(e) {
  $('lista').innerHTML = `
    <div class="error-panel">
      <div class="t">No se pudieron leer los Incidentes</div>
      <div class="s">${e instanceof ApiInalcanzable ? 'Sin conexión con el servidor.' : escapar(e.message)}</div>
      <div class="acts-min"><button class="btn-ghost" id="reintentar-lista">Reintentar</button></div>
    </div>`;
  $('reintentar-lista').addEventListener('click', cargarLista);
}

async function verDetalle(id) {
  estado.seleccion = id;
  pintarLista();
  const caja = $('detalle');
  caja.hidden = false;
  caja.innerHTML = '<p class="cargando">Cargando el Incidente…</p>';
  try {
    const i = await api.consultarIncidente(id);
    const rutas = i.rutas_afectadas.length
      ? i.rutas_afectadas.map((r) => {
          const ruta = estado.rutas.find((x) => x.id === r);
          return `<div class="drow"><span class="k">${ruta ? escapar(ruta.vehiculo_id) : 'Ruta de otra jornada'}</span><span class="v"></span></div>`;
        }).join('')
      : '<div class="drow"><span class="k">Ninguna</span><span class="v">—</span></div>';
    caja.innerHTML = `
      <div class="eyebrow">Incidente</div>
      <h2>${escapar(etiquetaTipo(i.tipo))}</h2>
      <div class="drow"><span class="k">Alcance</span><span class="v">${ETIQUETA_ALCANCE[i.alcance] ?? escapar(i.alcance)}</span></div>
      <div class="drow"><span class="k">Declarado</span><span class="v">${fechaLegible(fechaOperativa(new Date(i.declarado_en)))} · ${FORMATO_HORA.format(new Date(i.declarado_en))}</span></div>
      <p>${i.descripcion ? escapar(i.descripcion) : '<span class="note-s">Sin descripción.</span>'}</p>
      <div class="eyebrow" style="margin-top:12px">Rutas afectadas</div>
      ${rutas}
      <p class="s-nota">El registro no cambió ninguna Ruta: el recálculo no está construido.</p>
      <details class="ayuda-breve"><summary>Datos técnicos</summary>
        <div class="mono">Incidente ${escapar(i.id)}${i.rutas_afectadas.length ? `<br>Rutas ${i.rutas_afectadas.map(escapar).join(', ')}` : ''}</div></details>`;
  } catch (e) {
    if (e instanceof ApiInalcanzable) { caja.innerHTML = '<p class="cifra-pie">Sin conexión con el servidor.</p>'; return; }
    if (!(e instanceof ErrorDeApi)) throw e;
    caja.innerHTML = `<div class="error-panel"><div class="t">No se pudo leer el Incidente</div><div class="s">${escapar(e.message)}</div></div>`;
  }
}

async function cargarLista() {
  $('lista').innerHTML = '<p class="cargando">Cargando los Incidentes…</p>';
  try {
    estado.incidentes = await api.listarIncidentes();
    pintarLista();
  } catch (e) {
    if (e instanceof ApiInalcanzable || e instanceof ErrorDeApi) pintarErrorDeLista(e);
    else throw e;
  }
}

/* ------------------------------------------------------------------ */
/* Formulario                                                          */
/* ------------------------------------------------------------------ */

function pintarRutas() {
  if (!estado.rutas.length) {
    $('rutas').innerHTML = `<p class="cifra-pie">No hay Rutas en la jornada del ${fechaLegible(estado.jornada)}.
      Un Incidente puede declararse sin Rutas afectadas.</p>`;
    return;
  }
  const ids = [...estado.paleta.keys()];
  $('rutas').innerHTML = estado.rutas.map((r) => `
    <label class="chk-ruta"><input type="checkbox" name="ruta" value="${escapar(r.id)}"
      ${r.id === estado.rutaPedida ? 'checked' : ''}>
      <span class="disco-ruta" style="background:${estado.paleta.get(r.vehiculo_id)}">${ids.indexOf(r.vehiculo_id) + 1}</span>
      <span class="pat">${escapar(r.vehiculo_id)}</span>
      <small>${r.cantidad_paradas} ${r.cantidad_paradas === 1 ? 'Parada' : 'Paradas'}</small></label>`).join('');
}

async function cargarRutas() {
  try {
    estado.rutas = await api.listarRutas({ fecha: estado.jornada });
    estado.paleta = paletaPorVehiculo(estado.rutas.map((r) => r.vehiculo_id));
    pintarRutas();
    pintarLista();
  } catch (e) {
    if (!(e instanceof ApiInalcanzable || e instanceof ErrorDeApi)) throw e;
    $('rutas').innerHTML = '<p class="cifra-pie">No se pudieron leer las Rutas de la jornada.</p>';
  }
}

/* Zona afectada (encargo 2026-11-23B, bloque K): las tres casillas o
   ninguna. Devuelve null si estan vacias; levanta con el motivo si estan a
   medias o el radio esta fuera de rango. */
const RADIO_MINIMO_M = 20;
const RADIO_MAXIMO_M = 1000;

function zonaDelFormulario() {
  const valores = ['zona-radio', 'zona-lat', 'zona-lon'].map((id) => $(id).value.trim());
  if (valores.every((v) => v === '')) return null;
  if (valores.some((v) => v === '')) throw new Error('Completá el radio, la latitud y la longitud, o dejá las tres vacías.');
  const [radio, lat, lon] = valores.map(Number);
  if (!(radio >= RADIO_MINIMO_M && radio <= RADIO_MAXIMO_M)) throw new Error('El radio tiene que estar entre 20 y 1.000 m.');
  return { lat, lon, radio_m: radio };
}

/* El boton "Usar la posicion del Vehiculo" se quito (encargo 2026-11-23E,
   D33): una zona centrada en el Vehiculo contiene el origen del recalculo y la
   propuesta responde `zona_contiene_al_vehiculo`. El punto es el del corte. */

function limpiarErrores() {
  ['error-tipo', 'error-alcance', 'error-rutas', 'error-zona'].forEach((id) => { $(id).textContent = ''; });
  $('error-general').innerHTML = '';
}

/* Cada codigo del contrato junto al campo que lo causa. */
function mostrarError(e) {
  if (e.codigo === 'tipo_de_incidente_invalido') { $('error-tipo').textContent = e.message; return; }
  if (e.codigo === 'alcance_invalido') { $('error-alcance').textContent = e.message; return; }
  if (e.codigo === 'rutas_inexistentes') {
    $('error-rutas').textContent =
      `Estas Rutas ya no existen (se descartaron): ${(e.detalle?.rutas ?? []).join(', ')}. Desmarcalas y volvé a declarar.`;
    cargarRutas();
    return;
  }
  $('error-general').innerHTML = `<div class="error-panel"><div class="t">No se pudo declarar el Incidente</div><div class="s">${escapar(e.message)}</div></div>`;
}

async function declarar(ev) {
  ev.preventDefault();
  limpiarErrores();
  let zonaAfectada = null;
  try {
    zonaAfectada = zonaDelFormulario();
  } catch (e) {
    $('error-zona').textContent = e.message;
    return;
  }
  const boton = $('enviar');
  boton.disabled = true;
  boton.textContent = 'Declarando…';
  try {
    const incidente = await api.declararIncidente({
      zonaAfectada,
      tipo: $('tipo').value,
      alcance: document.querySelector('input[name="alcance"]:checked')?.value,
      descripcion: $('descripcion').value.trim(),
      rutasAfectadas: [...document.querySelectorAll('input[name="ruta"]:checked')].map((c) => c.value),
    });
    avisoBreve(`Incidente declarado: ${etiquetaTipo(incidente.tipo).toLowerCase()}.`);
    $('formulario').reset();
    estado.rutaPedida = null;
    pintarRutas();
    habilitarEnvio();
    // La lista muestra la jornada elegida; un Incidente nuevo es de hoy.
    if (estado.jornada !== fechaOperativa()) cambiarJornada(fechaOperativa());
    await cargarLista();
    verDetalle(incidente.id);
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalSinConexion();
    else if (e instanceof ErrorDeApi) mostrarError(e);
    else throw e;
  } finally {
    boton.textContent = 'Declarar el Incidente';
    habilitarEnvio();
  }
}

function cambiarJornada(fecha) {
  estado.jornada = fecha;
  $('fecha').value = fecha;
  estado.seleccion = null;
  $('detalle').hidden = true;
  pintarLista();
  cargarRutas();
}

/* Sin tipo elegido no se declara: antes venia preseleccionado y un clic
   registraba un Incidente que no se puede borrar (P25, N29; 2026-10-20B, C2). */
function habilitarEnvio() {
  $('enviar').disabled = !$('tipo').value;
}

function conectar() {
  $('tipo').innerHTML = '<option value="" selected disabled>Elegí el tipo</option>' + Object.entries(ETIQUETA_TIPO)
    .map(([valor, texto]) => `<option value="${valor}">${texto}</option>`).join('');
  $('tipo').addEventListener('change', habilitarEnvio);
  habilitarEnvio();
  $('formulario').addEventListener('submit', declarar);
  $('fecha').value = estado.jornada;
  $('fecha').addEventListener('change', (ev) => cambiarJornada(ev.target.value || fechaOperativa()));

  const elegir = (ev) => {
    const fila = ev.target.closest('[data-incidente]');
    if (fila) verDetalle(fila.dataset.incidente);
  };
  $('lista').addEventListener('click', elegir);
  $('lista').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); elegir(ev); }
  });
}

function iniciar() {
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'incidentes' });
  conectar();
  cargarLista();
  cargarRutas();
  if (window.location.hash === '#declarar') $('tipo').focus();
}

iniciar();
