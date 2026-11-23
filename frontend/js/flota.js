/**
 * Flota (2026-11-02): la lista de Vehiculos registrados,
 * con su estado del dia, y el formulario para dar de alta uno o cambiarle la
 * capacidad. Dar de baja y reactivar se hace desde la fila.
 *
 * Usa `GET`, `POST` y `PATCH` de `/v1/vehiculos`, y `GET /v1/flota` solo
 * para leer el margen de la operacion con que se calcula la capacidad
 * efectiva. Las reglas las aplica el backend: la pantalla anticipa los
 * errores, pero el rechazo viene de alla.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { avisoBreve, confirmar, escapar, modalSinConexion } from './modal.js';
import { exigirSesion } from './sesion.js';

const $ = (id) => document.getElementById(id);
const PATENTE = /^(?:[A-Z]{2}\d{3}[A-Z]{2}|[A-Z]{3}\d{3})$/;

const estado = {
  vehiculos: [],
  margen: 0,
  editando: null,   // patente del Vehiculo que se esta editando, o null
};

const normalizada = (texto) => texto.replace(/[\s-]/g, '').toUpperCase();
const efectiva = (capacidad) => Math.floor(capacidad * (1 - estado.margen));
const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`;

/* El estado del dia, como en el resto de la consola: dado de baja, en un
   plan de hoy o libre. */
function estadoDelDia(v) {
  if (!v.activo) return '<span class="stchip st-cancelada">Dado de baja</span>';
  if (v.comprometido_en) return '<span class="stchip st-planificada">En el plan de hoy</span>';
  return '<span class="stchip st-completada">Libre</span>';
}

function pintarResumen() {
  const activos = estado.vehiculos.filter((v) => v.activo);
  const libres = activos.filter((v) => !v.comprometido_en);
  const capacidad = activos.reduce((a, v) => a + v.capacidad_efectiva, 0);
  const tarjeta = (ico, valor, texto, clase) => `
    <div class="tarjeta al-tipo ${clase}">
      <span class="ico">${icono(ico)}</span>
      <div><div class="v">${valor}</div><div class="k">${texto}</div></div>
    </div>`;
  $('resumen').innerHTML =
    tarjeta('camion', activos.length, activos.length === 1 ? 'Vehículo activo' : 'Vehículos activos', 't-flota')
    + tarjeta('caja', capacidad, 'bultos de capacidad efectiva', 't-flota')
    + tarjeta('ok', libres.length, libres.length === 1 ? 'libre para planificar hoy' : 'libres para planificar hoy', 't-libre');
}

function pintarLista() {
  pintarResumen();
  $('cuenta').textContent = plural(estado.vehiculos.length, 'Vehículo', 'Vehículos');
  if (!estado.vehiculos.length) {
    $('lista').innerHTML = '<p class="vacio-texto">Todavía no hay Vehículos. Agregá el primero con el formulario.</p>';
    return;
  }
  const filas = estado.vehiculos.map((v) => `
    <tr class="${estado.editando === v.id ? 'sel' : ''} ${v.activo ? '' : 'inactivo'}">
      <td class="c-id">${escapar(v.id)}</td>
      <td class="c-b">${v.capacidad}</td>
      <td class="c-b">${v.capacidad_efectiva}</td>
      <td>${estadoDelDia(v)}</td>
      <td class="c-acciones">
        ${v.activo
          ? `<button class="link" type="button" data-editar="${escapar(v.id)}">Editar</button>
             <button class="link link-peligro" type="button" data-baja="${escapar(v.id)}">Dar de baja</button>`
          : `<button class="link" type="button" data-reactivar="${escapar(v.id)}">Reactivar</button>`}
      </td>
    </tr>`).join('');
  $('lista').innerHTML = `
    <div class="tabla-wrap">
      <table class="lista-ordenes lista-flota">
        <thead><tr><th>Patente</th><th class="c-b">Capacidad</th><th class="c-b">Efectiva</th><th>Hoy</th><th><span class="sr">Acciones</span></th></tr></thead>
        <tbody>${filas}</tbody>
      </table>
    </div>`;
}

async function cargar() {
  try {
    const [vehiculos, flota] = await Promise.all([api.vehiculos(), api.flota()]);
    estado.vehiculos = vehiculos;
    estado.margen = flota.margen_de_capacidad ?? 0;
    pintarLista();
    pintarFormulario();
  } catch (e) {
    if (e instanceof ApiInalcanzable) { modalSinConexion(cargar); return; }
    if (!(e instanceof ErrorDeApi)) throw e;
    $('lista').innerHTML = `<p class="vacio-texto">No se pudo leer la Flota: ${escapar(e.message)}</p>`;
  }
}

/* ------------------------------------------------------------------ */
/* Formulario: alta o edicion                                          */
/* ------------------------------------------------------------------ */

function limpiarErrores() {
  ['error-patente', 'error-capacidad'].forEach((id) => { $(id).textContent = ''; });
  $('error-general').innerHTML = '';
}

/** Lo que el formulario dice del valor escrito, antes de mandarlo. */
function pintarFormulario() {
  const patente = normalizada($('patente').value);
  const capacidad = Number($('capacidad').value);
  const capacidadValida = Number.isInteger(capacidad) && capacidad >= 1 && capacidad <= 999 && efectiva(capacidad) >= 1;
  $('efectiva').textContent = capacidadValida
    ? `Capacidad efectiva: ${plural(efectiva(capacidad), 'bulto', 'bultos')} (se reserva el ${Math.round(estado.margen * 100)} %).`
    : 'Lo que carga el Vehículo, en bultos.';
  const patenteValida = estado.editando !== null || PATENTE.test(patente);
  $('guardar').disabled = !(patenteValida && capacidadValida);
}

function editar(id) {
  const v = estado.vehiculos.find((x) => x.id === id);
  if (!v) return;
  estado.editando = id;
  limpiarErrores();
  $('modo-vehiculo').textContent = 'Editar';
  $('titulo-vehiculo').textContent = `Vehículo ${id}`;
  $('patente').value = id;
  $('patente').disabled = true;
  $('capacidad').value = String(v.capacidad);
  $('guardar').textContent = 'Guardar los cambios';
  $('cancelar-edicion').hidden = false;
  pintarLista();
  pintarFormulario();
  $('capacidad').focus();
}

function volverAlAlta() {
  estado.editando = null;
  limpiarErrores();
  $('formulario-vehiculo').reset();
  $('modo-vehiculo').textContent = 'Nuevo';
  $('titulo-vehiculo').textContent = 'Agregar un Vehículo';
  $('patente').disabled = false;
  $('guardar').textContent = 'Agregar a la Flota';
  $('cancelar-edicion').hidden = true;
  pintarLista();
  pintarFormulario();
}

/* Cada codigo del contrato junto al campo que lo causa. */
function mostrarError(e) {
  if (e.codigo === 'patente_invalida' || e.codigo === 'vehiculo_existente') { $('error-patente').textContent = e.message; return; }
  if (e.codigo === 'capacidad_invalida') { $('error-capacidad').textContent = e.message; return; }
  const titulo = e.codigo === 'vehiculo_en_uso' ? 'El Vehículo tiene una Ruta en el plan de hoy' : 'No se pudo guardar';
  $('error-general').innerHTML = `<div class="error-panel"><div class="t">${titulo}</div><div class="s">${escapar(e.message)}</div></div>`;
}

async function guardar(ev) {
  ev.preventDefault();
  limpiarErrores();
  const boton = $('guardar');
  boton.disabled = true;
  boton.textContent = 'Guardando…';
  const capacidad = Number($('capacidad').value);
  try {
    if (estado.editando) {
      const v = await api.modificarVehiculo(estado.editando, { capacidad });
      avisoBreve(`Vehículo ${v.id}: capacidad ${plural(v.capacidad, 'bulto', 'bultos')}.`);
      volverAlAlta();
    } else {
      const v = await api.darDeAltaVehiculo({ id: normalizada($('patente').value), capacidad });
      avisoBreve(`Vehículo ${v.id} agregado a la Flota.`);
      $('formulario-vehiculo').reset();
    }
    await cargar();
  } catch (e) {
    if (e instanceof ApiInalcanzable) modalSinConexion();
    else if (e instanceof ErrorDeApi) mostrarError(e);
    else throw e;
  } finally {
    boton.textContent = estado.editando ? 'Guardar los cambios' : 'Agregar a la Flota';
    pintarFormulario();
  }
}

async function cambiarActivo(id, activo) {
  if (!activo) {
    const seguro = await confirmar({
      eyebrow: 'Flota',
      titulo: `¿Dar de baja el Vehículo ${id}?`,
      cuerpo: '<p>Deja de ofrecerse para planificar. No se borra: las Rutas que ya recorrió lo siguen nombrando, y lo podés reactivar cuando quieras.</p>',
      accion: 'Dar de baja',
      peligro: true,
    });
    if (!seguro) return;
  }
  try {
    await api.modificarVehiculo(id, { activo });
    avisoBreve(activo ? `Vehículo ${id} reactivado.` : `Vehículo ${id} dado de baja.`);
    if (estado.editando === id) volverAlAlta();
    await cargar();
  } catch (e) {
    if (e instanceof ApiInalcanzable) { modalSinConexion(); return; }
    if (!(e instanceof ErrorDeApi)) throw e;
    mostrarError(e);
  }
}

function conectar() {
  $('formulario-vehiculo').addEventListener('submit', guardar);
  $('patente').addEventListener('input', pintarFormulario);
  $('capacidad').addEventListener('input', pintarFormulario);
  $('cancelar-edicion').addEventListener('click', volverAlAlta);
  $('cab-agregar').addEventListener('click', (ev) => {
    ev.preventDefault();
    if (estado.editando) volverAlAlta();
    $('patente').focus();
  });
  $('lista').addEventListener('click', (ev) => {
    const editarBoton = ev.target.closest('[data-editar]');
    if (editarBoton) { editar(editarBoton.dataset.editar); return; }
    const baja = ev.target.closest('[data-baja]');
    if (baja) { cambiarActivo(baja.dataset.baja, false); return; }
    const reactivar = ev.target.closest('[data-reactivar]');
    if (reactivar) cambiarActivo(reactivar.dataset.reactivar, true);
  });
}

function iniciar() {
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'flota' });
  conectar();
  cargar();
}

iniciar();
