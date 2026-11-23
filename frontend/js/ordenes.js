/**
 * Lista de Ordenes (DFE-29; B-243, punto 6).
 *
 * Todas las Ordenes, con filtro por estado (las pildoras, con su cuenta), por
 * jornada del plan y por texto, orden por columna y un detalle con el
 * historial completo, incluido quien declaro el resultado. Solo operaciones
 * existentes: `GET /v1/ordenes`, `GET /v1/ordenes/{id}` y `GET /v1/rutas`. La
 * jornada de una Orden es la del plan de su Ruta; una Orden sin Ruta no tiene
 * jornada.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { escapar, modalSinConexion } from './modal.js';
import { cabeceraOrdenable, ordenarPor } from './orden_de_tabla.js';
import { exigirSesion } from './sesion.js';
import {
  ETIQUETA_ESTADO, fechaLegible, fechaOperativa, horaDeLaOperacion,
  paletaPorVehiculo, textoDelDeclarante,
} from './tokens.js';

const ETIQUETA_PRIORIDAD = { 1: 'Baja', 2: 'Media', 3: 'Alta' };
const ORDEN_DE_ESTADOS = Object.keys(ETIQUETA_ESTADO);
const SIN_JORNADA = 'sin-ruta';

/* El color del punto de cada estado en las pildoras y en el historial: el
   mismo token que su etiqueta. */
const COLOR_DE_ESTADO = {
  creada: '--estado-creada',
  planificada: '--estado-planificada',
  en_transito: '--estado-en-transito',
  completada: '--estado-completada',
  fallida: '--estado-fallida',
  reprogramada: '--estado-reprogramada',
  devuelta: '--estado-devuelta',
  cancelada: '--estado-cancelada',
};

const $ = (id) => document.getElementById(id);

const estado = {
  ordenes: [],
  rutas: new Map(),         // id -> Ruta del listado (vehiculo y jornada)
  paleta: new Map(),
  filtroEstado: '',
  filtroJornada: '',
  texto: '',
  columna: 'id',
  ascendente: true,
  seleccion: null,
};

const rutaDe = (o) => (o.asignacion ? estado.rutas.get(o.asignacion.ruta_id) ?? null : null);
const jornadaDe = (o) => rutaDe(o)?.fecha_planificacion ?? null;
const ultimaTransicion = (o) => o.historial[o.historial.length - 1] ?? null;

/* "20 oct · 11:54" para un instante del contrato. */
const momento = (instante) => {
  const dia = fechaLegible(fechaOperativa(new Date(instante))).replace(/ \d{4}$/, '');
  return `${dia} · ${horaDeLaOperacion(instante)}`;
};

/* La hora si fue hoy; el dia si no. Para la columna de ultimo cambio. */
const cuandoCorto = (instante) => (fechaOperativa(new Date(instante)) === fechaOperativa()
  ? horaDeLaOperacion(instante)
  : fechaLegible(fechaOperativa(new Date(instante))).replace(/ \d{4}$/, ''));

/* El rotulo escapado; si viene de coordenadas, en monoespaciada (FE-03). */
const rotulo = (r) => (r?.origen === 'coordenadas'
  ? `<span class="rot-derivado">${escapar(r.texto)}</span>`
  : escapar(r?.texto ?? '—'));

/* El numero de la Ruta: su lugar en la paleta, como en el plan. */
const numeroDeVehiculo = (vehiculo) => [...estado.paleta.keys()].indexOf(vehiculo) + 1;

function etiquetaDeRuta(ruta) {
  const color = estado.paleta.get(ruta.vehiculo_id);
  return `<span class="tag-ruta"><span class="disco-ruta" style="background:${color}">${numeroDeVehiculo(ruta.vehiculo_id)}</span>${escapar(ruta.vehiculo_id)}</span>`;
}

const pildoraDePrioridad = (p) =>
  (ETIQUETA_PRIORIDAD[p] ? `<span class="prio${p === 3 ? ' alta' : ''}">${ETIQUETA_PRIORIDAD[p]}</span>` : '—');

/* Las columnas: rotulo y clave de orden. */
const COLUMNAS = [
  { clave: 'id', rotulo: 'Orden', valor: (o) => o.id },
  { clave: 'destino', rotulo: 'Destino', valor: (o) => o.destino.rotulo?.texto ?? '' },
  { clave: 'demanda', rotulo: 'Bultos', valor: (o) => o.demanda, numero: true },
  { clave: 'prioridad', rotulo: 'Prioridad', valor: (o) => o.prioridad, numero: true },
  { clave: 'ruta', rotulo: 'Ruta', valor: (o) => (rutaDe(o) ? `${jornadaDe(o)} ${rutaDe(o).vehiculo_id}` : '~') },
  { clave: 'estado', rotulo: 'Estado', valor: (o) => ORDEN_DE_ESTADOS.indexOf(o.estado), numero: true },
  { clave: 'ultima', rotulo: 'Último cambio', valor: (o) => ultimaTransicion(o)?.marca_temporal ?? '' },
];

const normalizado = (texto) => texto.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

/* Todos los filtros menos el de estado: las cuentas de las pildoras se
   calculan sobre esto, para que digan cuantas hay de cada estado en lo que
   se esta mirando. */
function sinFiltroDeEstado() {
  const buscado = normalizado(estado.texto.trim());
  return estado.ordenes.filter((o) =>
    (!estado.filtroJornada
      || (estado.filtroJornada === SIN_JORNADA ? jornadaDe(o) === null : jornadaDe(o) === estado.filtroJornada))
    && (!buscado || normalizado(`${o.id} ${o.destino.rotulo?.texto ?? ''}`).includes(buscado)));
}

const filtradas = () => sinFiltroDeEstado().filter((o) => !estado.filtroEstado || o.estado === estado.filtroEstado);

const ordenadas = (lista) => ordenarPor(lista, COLUMNAS, estado.columna, estado.ascendente);

function pintarPildoras() {
  const base = sinFiltroDeEstado();
  const cuentas = {};
  base.forEach((o) => { cuentas[o.estado] = (cuentas[o.estado] ?? 0) + 1; });
  const pildora = (valor, texto, cuenta, punto = '') => `
    <button type="button" class="fchip${estado.filtroEstado === valor ? ' on' : ''}" data-estado="${valor}"
            aria-pressed="${estado.filtroEstado === valor}">
      ${punto}${texto}<span class="num">${cuenta}</span>
    </button>`;
  // Solo los estados que tienen Ordenes, y el elegido aunque haya quedado en cero.
  const estados = ORDEN_DE_ESTADOS.filter((e) => cuentas[e] || e === estado.filtroEstado);
  $('filtro-estado').innerHTML = pildora('', 'Todas', base.length)
    + estados.map((e) => pildora(e, ETIQUETA_ESTADO[e], cuentas[e] ?? 0,
      `<i class="dot" style="background:var(${COLOR_DE_ESTADO[e]})"></i>`)).join('');
}

function pintarLista() {
  pintarPildoras();
  const lista = ordenadas(filtradas());
  if (!lista.length) {
    $('lista').innerHTML = `<p class="vacio-texto">${estado.ordenes.length
      ? 'Ninguna Orden cumple los filtros elegidos.'
      : 'Todavía no hay Órdenes. <a href="orden.html">Registrá la primera</a>.'}</p>`;
    return;
  }
  const cabecera = COLUMNAS.map((c) => cabeceraOrdenable(c, estado.columna, estado.ascendente)).join('');
  const filas = lista.map((o) => {
    const ultima = ultimaTransicion(o);
    const ruta = rutaDe(o);
    return `
    <tr data-orden="${escapar(o.id)}" tabindex="0" class="${estado.seleccion === o.id ? 'sel' : ''}">
      <td class="c-id">${escapar(o.id)}</td>
      <td>${rotulo(o.destino.rotulo)}</td>
      <td class="c-b">${o.demanda}</td>
      <td>${pildoraDePrioridad(o.prioridad)}</td>
      <td class="c-ruta">${ruta ? etiquetaDeRuta(ruta) : '<span class="note-s">sin Ruta</span>'}</td>
      <td><span class="stchip st-${escapar(o.estado)}">${ETIQUETA_ESTADO[o.estado] ?? escapar(o.estado)}</span></td>
      <td class="c-hora">${ultima ? cuandoCorto(ultima.marca_temporal) : ''}</td>
    </tr>`;
  }).join('');
  $('lista').innerHTML = `
    <div class="tabla-wrap">
      <table class="lista-ordenes">
        <thead><tr>${cabecera}</tr></thead>
        <tbody>${filas}</tbody>
      </table>
    </div>`;
}

function hitoDelHistorial(t) {
  const declaro = t.declarante ? `Declaró ${escapar(textoDelDeclarante(t.declarante))}` : '';
  return `
    <li class="hito" style="--c:var(${COLOR_DE_ESTADO[t.estado_nuevo] ?? '--tinta-3'})">
      <span class="m" aria-hidden="true"></span>
      <div>
        <b>${ETIQUETA_ESTADO[t.estado_nuevo] ?? escapar(t.estado_nuevo)}</b>
        <div class="cuando">${momento(t.marca_temporal)}</div>
        ${declaro ? `<div class="quien">${declaro}</div>` : ''}
      </div>
    </li>`;
}

async function verDetalle(id) {
  estado.seleccion = id;
  pintarLista();
  const caja = $('detalle');
  caja.innerHTML = '<p class="cargando">Cargando la Orden…</p>';
  try {
    const o = await api.consultarOrden(id);
    const ruta = rutaDe(o);
    const mapa = o.asignacion && ruta
      ? `index.html?fecha=${encodeURIComponent(ruta.fecha_planificacion)}&ruta=${encodeURIComponent(ruta.id)}&parada=${o.asignacion.secuencia_parada}`
      : null;
    const rutaYParada = ruta
      ? `${etiquetaDeRuta(ruta)}${o.asignacion ? ` · ${o.asignacion.secuencia_parada}` : ''}`
      : 'sin Ruta';
    // El historial va del cambio mas reciente al alta, como en la propuesta.
    const historial = [...o.historial].reverse();
    caja.innerHTML = `
      <div class="det-h">
        <div class="id">${escapar(o.id)}</div>
        <h2>${rotulo(o.destino.rotulo)}</h2>
        <span class="stchip st-${escapar(o.estado)}">${ETIQUETA_ESTADO[o.estado] ?? escapar(o.estado)}</span>
      </div>
      <div class="det-datos">
        <div><div class="k">Bultos</div><div class="v num">${o.demanda}</div></div>
        <div><div class="k">Prioridad</div><div class="v">${ETIQUETA_PRIORIDAD[o.prioridad] ?? o.prioridad}</div></div>
        <div><div class="k">Plazo</div><div class="v num">${o.plazo_comprometido ? fechaLegible(o.plazo_comprometido) : 'sin plazo'}</div></div>
        <div><div class="k">Ruta · Parada</div><div class="v">${rutaYParada}</div></div>
      </div>
      <div class="det-historial">
        <div class="eyebrow">Historial</div>
        <ol class="hitos">${historial.map(hitoDelHistorial).join('')}</ol>
      </div>
      ${mapa ? `<div class="det-pie"><a class="btn-ghost btn-ancho-sec" id="ver-en-el-mapa" href="${mapa}">${icono('plan')}Ver su Ruta en el mapa</a></div>` : ''}`;
  } catch (e) {
    if (e instanceof ApiInalcanzable) { caja.innerHTML = '<p class="vacio-texto">Sin conexión con el servidor.</p>'; return; }
    if (!(e instanceof ErrorDeApi)) throw e;
    caja.innerHTML = `<p class="vacio-texto">No se pudo leer la Orden: ${escapar(e.message)}</p>`;
  }
}

function pintarFiltroDeJornada() {
  const jornadas = [...new Set([...estado.rutas.values()].map((r) => r.fecha_planificacion))].sort().reverse();
  $('filtro-jornada').innerHTML = '<option value="">todas</option>'
    + jornadas.map((f) => `<option value="${f}">${fechaLegible(f)}${f === fechaOperativa() ? ' (hoy)' : ''}</option>`).join('')
    + `<option value="${SIN_JORNADA}">sin Ruta</option>`;
  $('filtro-jornada').value = estado.filtroJornada;
}

async function cargar() {
  try {
    const [ordenes, rutas] = await Promise.all([api.listarOrdenes(), api.listarRutas()]);
    estado.ordenes = ordenes;
    estado.rutas = new Map(rutas.map((r) => [r.id, r]));
    estado.paleta = paletaPorVehiculo(rutas.map((r) => r.vehiculo_id));
    pintarFiltroDeJornada();
    pintarLista();
  } catch (e) {
    if (e instanceof ApiInalcanzable) { modalSinConexion(); return; }
    if (!(e instanceof ErrorDeApi)) throw e;
    $('lista').innerHTML = `<p class="vacio-texto">No se pudieron leer las Órdenes: ${escapar(e.message)}</p>`;
  }
}

function conectar() {
  $('filtro-estado').addEventListener('click', (ev) => {
    const pildora = ev.target.closest('[data-estado]');
    if (!pildora) return;
    estado.filtroEstado = pildora.dataset.estado;
    pintarLista();
  });
  $('filtro-jornada').addEventListener('change', (ev) => { estado.filtroJornada = ev.target.value; pintarLista(); });
  $('filtro-texto').addEventListener('input', (ev) => { estado.texto = ev.target.value; pintarLista(); });
  $('lista').addEventListener('click', (ev) => {
    const boton = ev.target.closest('[data-columna]');
    if (boton) {
      estado.ascendente = estado.columna === boton.dataset.columna ? !estado.ascendente : true;
      estado.columna = boton.dataset.columna;
      pintarLista();
      return;
    }
    const fila = ev.target.closest('tr[data-orden]');
    if (fila) verDetalle(fila.dataset.orden);
  });
  $('lista').addEventListener('keydown', (ev) => {
    const fila = ev.target.closest('tr[data-orden]');
    if (fila && (ev.key === 'Enter' || ev.key === ' ')) { ev.preventDefault(); verDetalle(fila.dataset.orden); }
  });
}

function iniciar() {
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'ordenes' });
  const pedido = new URLSearchParams(window.location.search);
  estado.filtroEstado = pedido.get('estado') ?? '';
  conectar();
  cargar().then(() => { if (pedido.get('orden')) verDetalle(pedido.get('orden')); });
}

iniciar();
