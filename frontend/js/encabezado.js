/**
 * Encabezado comun a las pantallas del operador (DFE-11; rediseño de
 * 2026-11 con la marca de la tesis, capitulo 12.2).
 *
 * Una barra lateral con la marca, las secciones agrupadas por momento de la
 * jornada, el usuario y la salida. Se puede colapsar a solo iconos, y la
 * eleccion se recuerda en el navegador. La barra superior de cada pantalla no
 * se toca: sigue diciendo que pantalla es y lleva sus controles propios (la
 * jornada, el disparo, cancelar).
 *
 * Las pantallas que no navegan (terminos y la del transportista) llevan solo
 * una franja superior con la marca, el usuario y la salida.
 */

import * as api from './api.js';
import { mostrarAvisoPendiente } from './modal.js';
import { ETIQUETA_ROL, cerrarSesion, sesionActual } from './sesion.js';
import { fechaOperativa } from './tokens.js';
import { icono } from './iconos.js';

const svg = (nombre) => icono(nombre);

/* Las secciones, en el orden de la jornada. `pagina` es el archivo; la
   seccion actual se marca con `aria-current`. `grupo` agrupa en la barra. */
export const SECCIONES = [
  { clave: 'inicio', texto: 'Inicio', pagina: 'inicio.html', grupo: 'Jornada' },
  { clave: 'orden', texto: 'Registrar Orden', pagina: 'orden.html', grupo: 'Jornada' },
  { clave: 'ordenes', texto: 'Órdenes', pagina: 'ordenes.html', grupo: 'Jornada' },
  { clave: 'planificar', texto: 'Planificar', pagina: 'planificacion.html', grupo: 'Jornada' },
  { clave: 'plan', texto: 'Plan de rutas', pagina: 'index.html', grupo: 'Jornada' },
  { clave: 'flota', texto: 'Flota', pagina: 'flota.html', grupo: 'Jornada' },
  { clave: 'incidentes', texto: 'Incidentes', pagina: 'incidentes.html', grupo: 'Seguimiento' },
  { clave: 'alertas', texto: 'Alertas', pagina: 'alertas.html', grupo: 'Seguimiento' },
  { clave: 'ayuda', texto: 'Ayuda', pagina: 'ayuda.html', grupo: 'Sistema' },
  { clave: 'acerca', texto: 'Acerca de', pagina: 'acerca.html', grupo: 'Sistema' },
];

/* El indicador de alertas vuelve a consultar cada este tiempo: es el
   intervalo de evaluacion de CU-08, porque las alertas se derivan al
   consultarlas y no hay un proceso que las evalue en el servidor
   (2026-10-27B, A; B-259, punto 3). */
export const INTERVALO_DE_CONSULTA_DE_ALERTAS_S = 60;
const ROL_OPERADOR = 'operador';
let consultaDeAlertas = null;

/* Preferencia del navegador: la barra colapsada o abierta. Es una comodidad
   de quien mira, no un dato de la operacion. */
const CLAVE_MENU = 'hoppit.menu';
const ISOTIPO = 'img/hoppit-isotipo.png';

const ZONA_DE_LA_OPERACION = 'America/Argentina/Buenos_Aires';
const FORMATO_HORA = new Intl.DateTimeFormat('es-AR', {
  timeZone: ZONA_DE_LA_OPERACION, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const FORMATO_DIA = new Intl.DateTimeFormat('es-AR', {
  timeZone: ZONA_DE_LA_OPERACION, day: 'numeric', month: 'short',
});

const DIAS_CORTOS = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
const MESES_CORTOS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

/** "mar 28 oct 2026" a partir de `YYYY-MM-DD`. El dia de la semana se toma
    al mediodia UTC de la fecha civil, que es el mismo dia en la Argentina. */
export function jornadaCorta(iso) {
  const [anio, mes, dia] = iso.split('-');
  const semana = new Date(`${iso}T12:00:00Z`).getUTCDay();
  return `${DIAS_CORTOS[semana]} ${Number(dia)} ${MESES_CORTOS[Number(mes) - 1]} ${anio}`;
}

/** "hasta las 18:40", o "hasta el 20 oct, 02:10" si vence otro dia. */
function vencimientoLegible(iso) {
  const instante = new Date(iso);
  if (Number.isNaN(instante.getTime())) return '';
  const hora = FORMATO_HORA.format(instante);
  return fechaOperativa(instante) === fechaOperativa()
    ? `hasta las ${hora}`
    : `hasta el ${FORMATO_DIA.format(instante).replace('.', '')}, ${hora}`;
}

const escaparTexto = (texto) => {
  const caja = document.createElement('span');
  caja.textContent = texto ?? '';
  return caja.innerHTML;
};

/** Las iniciales del nombre visible, para el circulo del usuario. */
function iniciales(nombre) {
  const partes = String(nombre ?? '').trim().split(/\s+/).filter(Boolean);
  return ((partes[0]?.[0] ?? '') + (partes[1]?.[0] ?? '')).toUpperCase() || '·';
}

function leerMenuColapsado() {
  try { return localStorage.getItem(CLAVE_MENU) === 'colapsado'; } catch { return false; }
}
function guardarMenuColapsado(colapsado) {
  try { localStorage.setItem(CLAVE_MENU, colapsado ? 'colapsado' : 'abierto'); } catch { /* sin almacenamiento */ }
}

/* El mapa de Leaflet mide su contenedor al crearse: cuando la barra cambia el
   ancho disponible, se le avisa como si la ventana cambiara de tamaño. */
const avisarCambioDeTamano = () => window.dispatchEvent(new Event('resize'));

/**
 * Inserta la barra al principio del cuerpo. `seccion` es la clave de la
 * pantalla actual; `navegacion` en false deja solo la marca, el usuario y la
 * salida (la pantalla de terminos y la del transportista, que no navegan).
 */
export function montarEncabezado({ seccion = null, navegacion = true } = {}) {
  document.getElementById('franja-de-sesion')?.remove();
  const sesion = sesionActual();
  if (navegacion) montarBarraLateral(sesion, seccion);
  else montarFranjaSuperior(sesion);
  ordenarCabecera(seccion);
  document.getElementById('cerrar-sesion')?.addEventListener('click', cerrarSesion);
  if (navegacion && sesion?.rol === ROL_OPERADOR) vigilarAlertas();
  // Lo que la pantalla anterior dejo dicho antes de navegar hasta aca.
  mostrarAvisoPendiente();
}

function montarBarraLateral(sesion, seccion) {
  const barra = document.createElement('aside');
  barra.id = 'franja-de-sesion';
  barra.className = 'lateral';

  let grupo = null;
  const enlaces = SECCIONES.map((s) => {
    const titulo = s.grupo !== grupo ? `<div class="lateral-grupo">${s.grupo}</div>` : '';
    grupo = s.grupo;
    const actual = s.clave === seccion ? ' aria-current="page"' : '';
    const cuenta = s.clave === 'alertas' ? '<span class="lateral-cuenta" hidden></span>' : '';
    return `${titulo}<a href="${s.pagina}" data-clave="${s.clave}" data-tip="${s.texto}"${actual}>${svg(s.clave)}<span class="lateral-texto">${s.texto}</span>${cuenta}</a>`;
  }).join('');

  const usuario = sesion
    ? `<div class="lateral-pie">
         <span class="lateral-avatar" aria-hidden="true">${escaparTexto(iniciales(sesion.nombre_visible))}</span>
         <span class="lateral-usuario">
           <b>${escaparTexto(sesion.nombre_visible)}</b>
           <span>${ETIQUETA_ROL[sesion.rol] ?? escaparTexto(sesion.rol)}${sesion.vence_en ? ` · <span title="La sesión se cierra sola al vencer, o al cerrar esta pestaña">${vencimientoLegible(sesion.vence_en)}</span>` : ''}</span>
         </span>
         <button type="button" class="lateral-salir" id="cerrar-sesion" title="Cerrar sesión" aria-label="Cerrar sesión">${svg('salir')}</button>
       </div>`
    : '';

  barra.innerHTML = `
    <div class="lateral-marca">
      <img src="${ISOTIPO}" alt="" width="36" height="39">
      <span class="lateral-nombre"><b>HOPPIT</b><span>Rutas sobre las calles reales</span></span>
      <button type="button" class="lateral-plegar" aria-controls="franja-de-sesion">${svg('plegar')}</button>
    </div>
    <nav class="lateral-nav" aria-label="Secciones">${enlaces}</nav>
    ${usuario}`;

  // El contenido de la pantalla pasa a una columna propia, al lado de la
  // barra. Se mueven los nodos, no se recrean: conservan sus escuchas.
  let principal = document.querySelector('body > .principal');
  if (!principal) {
    principal = document.createElement('div');
    principal.className = 'principal';
    principal.append(...document.body.childNodes);
    document.body.append(principal);
  }
  document.body.classList.add('con-lateral');
  document.body.prepend(barra);

  const plegar = barra.querySelector('.lateral-plegar');
  const aplicar = (colapsado) => {
    document.body.classList.toggle('menu-colapsado', colapsado);
    plegar.title = colapsado ? 'Expandir el menú' : 'Colapsar el menú';
    plegar.setAttribute('aria-label', plegar.title);
    plegar.setAttribute('aria-expanded', String(!colapsado));
  };
  aplicar(leerMenuColapsado());
  plegar.addEventListener('click', () => {
    const colapsado = !document.body.classList.contains('menu-colapsado');
    aplicar(colapsado);
    guardarMenuColapsado(colapsado);
  });
  barra.addEventListener('transitionend', (e) => { if (e.propertyName === 'width') avisarCambioDeTamano(); });
  avisarCambioDeTamano();
}

/**
 * La barra superior de cada pantalla, como en la propuesta: el momento de la
 * jornada en chico arriba del titulo, y la jornada como un chip con flechas
 * para pasar al dia anterior o al siguiente. Las flechas solo cambian el
 * campo y avisan con `change`, como si se hubiera elegido en el calendario:
 * cada pantalla sigue escuchando su campo como antes.
 */
function ordenarCabecera(seccion) {
  const barra = document.querySelector('.topbar');
  const titulo = barra?.querySelector('.screen-title');
  if (titulo && !titulo.parentElement.classList.contains('cab-tit')) {
    const grupo = SECCIONES.find((s) => s.clave === seccion)?.grupo ?? 'HOPPIT';
    const caja = document.createElement('div');
    caja.className = 'cab-tit';
    const arriba = document.createElement('small');
    arriba.textContent = grupo;
    // La miga que la pantalla completa (la jornada, el ambito del grafo) va en
    // la misma linea chica.
    const miga = barra.querySelector('.crumb');
    if (miga) arriba.append(miga);
    titulo.replaceWith(caja);
    caja.append(arriba, titulo);
  }
  barra?.querySelectorAll('.datectl').forEach((control) => {
    const campo = control.querySelector('input[type="date"]');
    if (!campo || control.dataset.flechas) return;
    control.dataset.flechas = '1';
    const paso = (dias, texto) => {
      const boton = document.createElement('button');
      boton.type = 'button';
      boton.className = 'datectl-paso';
      boton.title = texto;
      boton.setAttribute('aria-label', texto);
      boton.innerHTML = svg(dias < 0 ? 'anterior' : 'siguiente');
      boton.addEventListener('click', () => {
        const base = campo.value ? new Date(`${campo.value}T12:00:00Z`) : new Date();
        base.setUTCDate(base.getUTCDate() + dias);
        campo.value = base.toISOString().slice(0, 10);
        campo.dispatchEvent(new Event('change', { bubbles: true }));
      });
      return boton;
    };
    // El calendario del navegador se abre desde el icono de la marca, no
    // desde el suyo, que queda oculto.
    const calendario = document.createElement('button');
    calendario.type = 'button';
    calendario.className = 'datectl-ico';
    calendario.title = 'Elegir la jornada';
    calendario.setAttribute('aria-label', calendario.title);
    calendario.innerHTML = svg('calendario');
    calendario.addEventListener('click', () => { try { campo.showPicker(); } catch { campo.focus(); } });
    control.prepend(calendario);
    campo.before(paso(-1, 'Jornada anterior'));
    campo.after(paso(1, 'Jornada siguiente'));

    // La jornada se lee como en la propuesta, "mar 28 oct 2026". El campo
    // del navegador sigue ahi, invisible, para el calendario y el teclado.
    const texto = document.createElement('span');
    texto.className = 'datectl-texto';
    texto.setAttribute('aria-hidden', 'true');
    campo.before(texto);
    campo.classList.add('datectl-campo');
    const pintar = () => { texto.textContent = campo.value ? jornadaCorta(campo.value) : '—'; };
    // Las pantallas fijan la jornada asignando `value`, que no avisa con un
    // evento: se envuelve el setter de este campo para enterarse.
    const propiedad = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    Object.defineProperty(campo, 'value', {
      configurable: true,
      get() { return propiedad.get.call(this); },
      set(valor) { propiedad.set.call(this, valor); pintar(); },
    });
    campo.addEventListener('input', pintar);
    campo.addEventListener('change', pintar);
    pintar();
  });
}

function montarFranjaSuperior(sesion) {
  const franja = document.createElement('div');
  franja.id = 'franja-de-sesion';
  franja.className = 'franja-marca';
  franja.innerHTML = `
    <img src="${ISOTIPO}" alt="" width="32" height="35"><b class="franja-nombre">HOPPIT</b>
    <span class="spacer"></span>
    ${sesion ? `<span class="lateral-avatar" aria-hidden="true">${escaparTexto(iniciales(sesion.nombre_visible))}</span>
      <b class="franja-quien">${escaparTexto(sesion.nombre_visible)}</b>
      <span class="franja-rol">${ETIQUETA_ROL[sesion.rol] ?? escaparTexto(sesion.rol)}</span>
      <button type="button" class="franja-salir" id="cerrar-sesion">${svg('salir')}Cerrar sesión</button>` : ''}`;
  document.body.prepend(franja);
}

/** Cuenta las alertas de la jornada en el enlace de la navegacion, y la
    vuelve a contar cada INTERVALO_DE_CONSULTA_DE_ALERTAS_S. Si la consulta
    falla, el enlace queda sin numero: no se inventa una cuenta. */
function vigilarAlertas() {
  const contar = async () => {
    const enlace = document.querySelector('#franja-de-sesion a[data-clave="alertas"]');
    const cuenta = enlace?.querySelector('.lateral-cuenta');
    if (!enlace || !cuenta) return;
    try {
      const { alertas } = await api.alertas();
      cuenta.textContent = String(alertas.length);
      cuenta.hidden = alertas.length === 0;
      enlace.dataset.tip = alertas.length ? `Alertas (${alertas.length})` : 'Alertas';
      enlace.setAttribute('aria-label', enlace.dataset.tip);
      enlace.classList.toggle('con-alertas', alertas.length > 0);
    } catch {
      cuenta.hidden = true;
      enlace.dataset.tip = 'Alertas';
      enlace.removeAttribute('aria-label');
      enlace.classList.remove('con-alertas');
    }
  };
  if (consultaDeAlertas) clearInterval(consultaDeAlertas);
  contar();
  consultaDeAlertas = setInterval(contar, INTERVALO_DE_CONSULTA_DE_ALERTAS_S * 1000);
}
