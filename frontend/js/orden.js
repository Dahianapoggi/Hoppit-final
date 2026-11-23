/**
 * Pantalla G1-D -- Registrar Orden.
 *
 * Provee la entrada del sistema. Cubre CU-01 con sus flujos 4b y 5a, y HU-03.
 *
 * El enganche al nodo se muestra ANTES de confirmar (PUX-37, estado D2 del
 * mockup): al marcar un punto, `GET /v1/anclajes` resuelve a que nodo
 * enganchara y a cuantos metros, sin crear nada. Es lo que le da al paso su
 * funcion de control -- un enganche a 18 m es correcto; uno a 300 m es un
 * dato para revisar antes de registrar, y despues de registrar ya seria
 * tarde.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { avisoBreve, escapar, modalSinConexion } from './modal.js';
import { exigirSesion } from './sesion.js';
import { ETIQUETA_ESTADO, TESELAS_PROVISIONALES, colorDeToken, fechaOperativa, textoDeRotulo } from './tokens.js';
import { icono } from './iconos.js';

/* Colores del punto marcado: tokens de la hoja de estilos (H7). */
const TOKEN_DEL_PUNTO = '--r3';
const TOKEN_DEL_ERROR = '--aviso';

/* Zona de la operacion. El historial guarda INSTANTES en UTC; mostrarlos en
   la zona del navegador haria que un operador en otro huso leyera horas que
   no coinciden con la fecha operativa que el backend calcula en esta zona. */
const ZONA_DE_LA_OPERACION = 'America/Argentina/Buenos_Aires';

/* Encuadre previo, reemplazado por el Deposito o por el punto marcado. */
const VISTA_PREVIA = { centro: [-34.4875, -58.5150], zoom: 14 };

/* Al marcar, el mapa no se acerca mas que esto, y no se reencuadra si el
   punto y su esquina ya estan a la vista: acercarse hasta la cuadra hacia
   perder el barrio y obligaba a alejarse para corregir (2026-10-20A, P6;
   2026-10-20B, F1.3). */
const ZOOM_MAXIMO_AL_MARCAR = 16;

/* Anclaje lejano (decision provisoria del chat central, B-249): si la esquina
   mas cercana esta a mas de esta distancia del punto marcado, la pantalla lo
   advierte y pide confirmacion para registrar. Es una advertencia de la
   interfaz: el dominio no cambia (2026-10-20A, P9). */
const DISTANCIA_DE_ADVERTENCIA_M = 300;

/* Mensaje de demanda invalida, el mismo para cero, negativa, decimal o vacia. */
const MENSAJE_DEMANDA = 'La demanda es un número entero de bultos, mayor que 0.';

/* A donde vuelve "Cancelar": la pantalla desde la que se llego al alta, que
   "Registrar otra Orden" no debe olvidar (2026-10-20A, N07). */
const CLAVE_ORIGEN = 'hoppit.origen_del_alta';
const ORIGEN_PREDETERMINADO = 'inicio.html';

const ICONO_MARCAR = `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
  <circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="1.8"/>
  <circle cx="12" cy="12" r="2" fill="currentColor"/>
  <path d="M12 1v4M12 19v4M1 12h4M19 12h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
const ICONO_PIN = icono('pin');
const ICONO_LISTO = icono('ok', { grosor: 2.4 });

/* Mensajes por clase de violacion de esquema. Se decide contra `tipo`, que es
   estable y parte del contrato, no contra `motivo`, que llega en ingles y es
   texto de la libreria de validacion. */
const MOTIVO_POR_TIPO = {
  greater_than_equal: 'La demanda debe ser un número entero positivo. La capacidad del Vehículo se consume en bultos completos.',
  int_parsing: 'La demanda debe ser un número entero. La capacidad del Vehículo se consume en bultos completos.',
  int_from_float: 'La demanda debe ser un número entero. La capacidad del Vehículo se consume en bultos completos.',
  missing: 'Falta completar este campo.',
};

const estado = {
  punto: null,     // lo que el operador marco
  anclaje: null,   // { nodo, distancia_m, es_el_deposito }, previsualizado
  orden: null,     // la Orden ya registrada
  lejanoConfirmado: false, // confirmacion explicita del anclaje lejano
  demandaTocada: false,    // el campo se uso: recien ahi se dice que falta
};

/* Cada punto marcado invalida la consulta de anclaje del anterior: sin esto,
   una respuesta lenta de un punto viejo pisaria la del punto vigente. */
let consultaVigente = 0;

let mapa = null;
let ambitoDelGrafo = null; // del servidor (/v1/estado), para nombrarlo en pantalla
let capa = null;

/* ------------------------------------------------------------------ */
/* Utilidades                                                          */
/* ------------------------------------------------------------------ */

const nf = (n, dec = 4) =>
  n.toLocaleString('es-AR', { minimumFractionDigits: dec, maximumFractionDigits: dec });

const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`;

/**
 * Un instante del historial, en la zona de la operacion: "23 ago 2026 · 21:49".
 *
 * Se arma por partes y no con `toLocaleString` entero porque el formato largo
 * de es-AR intercala preposiciones ("23 de ago de 2026") y usa reloj de 12
 * horas. El historial es un registro tecnico: hora de 24 y sin relleno verbal.
 */
const FORMATO_INSTANTE = new Intl.DateTimeFormat('es-AR', {
  timeZone: ZONA_DE_LA_OPERACION,
  day: 'numeric', month: 'short', year: 'numeric',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

function instante(iso) {
  const partes = Object.fromEntries(
    FORMATO_INSTANTE.formatToParts(new Date(iso)).map((p) => [p.type, p.value])
  );
  const mes = partes.month.replace('.', '');
  return `${partes.day} ${mes} ${partes.year} · ${partes.hour}:${partes.minute}`;
}

/**
 * Metros sin decimales.
 *
 * La distancia la informa el backend en `distancia_m`: ya no se calcula en el
 * cliente. Es la misma que usa el alta para elegir el nodo, no una segunda
 * estimacion que pudiera discrepar.
 */
const metros = (m) => `${m.toLocaleString('es-AR', { maximumFractionDigits: 0 })} m`;

/**
 * Aviso de carga, sobre el mapa. Estado transitorio, no error.
 *
 * Los errores no pasan por aca. Los de campo se marcan en el campo, que es lo
 * que PUX-40 fija y lo que corresponde: un modal que diga "la demanda debe ser
 * un entero" aleja la causa del lugar donde se corrige. Los que no tienen
 * campo --la API que no responde-- van a modal, sobre el contenido.
 */
function mostrarAviso({ tipo, titulo, texto }) {
  const caja = document.getElementById('aviso');
  document.getElementById('aviso-tipo').textContent = tipo;
  document.getElementById('aviso-titulo').textContent = titulo;
  document.getElementById('aviso-texto').innerHTML = texto ?? '';
  caja.hidden = false;
}

/* ------------------------------------------------------------------ */
/* Mapa                                                                */
/* ------------------------------------------------------------------ */

function crearMapa() {
  mapa = L.map('mapa', { zoomControl: false });
  mapa.attributionControl.setPrefix(false);
  L.tileLayer(TESELAS_PROVISIONALES.url, {
    maxZoom: TESELAS_PROVISIONALES.zoomMaximo,
    attribution: TESELAS_PROVISIONALES.atribucion,
  }).addTo(mapa);
  L.control.zoom({ position: 'topright' }).addTo(mapa);
  L.control.scale({ position: 'bottomright', imperial: false, maxWidth: 120 }).addTo(mapa);
  mapa.setView(VISTA_PREVIA.centro, VISTA_PREVIA.zoom);
  // El mapa se centra en el Deposito del grafo con que corre el servidor
  // (CABA o San Isidro, 2026-10-27B, K), salvo que ya haya un punto marcado.
  // Si la consulta falla, queda el encuadre previo: el alta sigue andando.
  api.depositoUnico()
    .then((deposito) => {
      if (!estado.anclaje && !estado.orden) mapa.setView([deposito.lat, deposito.lon], VISTA_PREVIA.zoom);
    })
    .catch(() => {});
  // El ambito del grafo activo, que publica /v1/estado: antes la pantalla
  // decia "San Isidro" fijo aunque el servidor corriera sobre CABA
  // (2026-10-27C, N). Si la consulta falla, no se nombra ninguno.
  api.estado()
    .then((info) => {
      ambitoDelGrafo = info.grafo?.ambito ?? null;
      if (!ambitoDelGrafo) return;
      document.getElementById('ambito-del-grafo').textContent = ambitoDelGrafo;
      document.getElementById('pista-ambito').textContent = ambitoDelGrafo;
    })
    .catch(() => {});
  capa = L.layerGroup().addTo(mapa);

  mapa.on('click', (ev) => {
    if (estado.orden) return; // registrada: la pantalla ya no acepta cambios
    desmarcarCandidato();
    marcarPunto({ lat: ev.latlng.lat, lon: ev.latlng.lng });
  });
}

/* ------------------------------------------------------------------ */
/* Busqueda por direccion (RB-10; 2026-10-27C, G; DFE-41)              */
/* ------------------------------------------------------------------ */

let candidatos = [];

/**
 * Busca la direccion en el servidor, que consulta Georef. Elegir un candidato
 * es lo mismo que hacer clic en su ubicacion: pasa por `marcarPunto`, con su
 * anclaje y su aviso de punto lejano, y el alta guarda esa coordenada y el
 * nodo (B-259, punto 6). Si la busqueda falla, el mapa sigue sirviendo.
 */
async function buscarDireccion(ev) {
  ev.preventDefault();
  if (estado.orden) return;
  const texto = document.getElementById('direccion').value.trim();
  const caja = document.getElementById('candidatos');
  if (!texto) { caja.innerHTML = ''; return; }
  const boton = document.getElementById('buscar-direccion');
  boton.disabled = true;
  caja.innerHTML = '<div class="help">Buscando…</div>';
  try {
    const respuesta = await api.buscarDireccion(texto);
    candidatos = respuesta.candidatos;
    caja.innerHTML = candidatos.length
      ? `<ul class="candidatos">${candidatos.map((c, i) => `<li><button type="button" class="cand" data-candidato="${i}">
          ${ICONO_PIN}<span class="cand-txt">${escapar(c.direccion)}<span class="dist">${c.es_el_deposito
            ? 'Es el Depósito: no se puede entregar ahí.'
            : `Esquina más cercana: ${escapar(c.nodo.rotulo.texto)}, a ${Math.round(c.distancia_m)} m`}</span></span>
          <small class="elegido">elegido</small>
        </button></li>`).join('')}</ul>`
      : '<div class="help">No se encontró esa dirección. Probá con calle y altura, o marcá el punto en el mapa.</div>';
  } catch (e) {
    candidatos = [];
    const mensaje = e instanceof ErrorDeApi ? e.message : 'No hay conexión con el servidor.';
    caja.innerHTML = `<div class="err-msg"><span>${escapar(mensaje)}</span></div>`;
  } finally {
    boton.disabled = false;
  }
}

function elegirCandidato(ev) {
  const boton = ev.target.closest('[data-candidato]');
  if (!boton || estado.orden) return;
  const candidato = candidatos[Number(boton.dataset.candidato)];
  desmarcarCandidato();
  boton.classList.add('on');
  mapa.panTo([candidato.consultado.lat, candidato.consultado.lon]);
  marcarPunto({ lat: candidato.consultado.lat, lon: candidato.consultado.lon });
}

function desmarcarCandidato() {
  document.querySelectorAll('#candidatos [data-candidato].on').forEach((b) => b.classList.remove('on'));
}

/**
 * Marca un punto y previsualiza a que nodo engancharia (CU-01 pasos 3 y 4).
 *
 * La consulta no crea nada: es `GET /v1/anclajes`, y el backend garantiza con
 * una prueba que devuelve exactamente el nodo que elegiria el alta. Si
 * difirieran, el control previo seria peor que no tenerlo.
 *
 * Mientras la respuesta viaja, el punto ya se dibuja y el formulario declara
 * que esta resolviendo: la marca del operador no espera a la red.
 */
async function marcarPunto(punto) {
  const consulta = ++consultaVigente;
  estado.punto = punto;
  estado.anclaje = null;
  estado.lejanoConfirmado = false;
  dibujar();
  pintarDestino({ resolviendo: true });
  pintarAcciones();

  let anclaje = null;
  let fallo = null;
  try {
    anclaje = await api.resolverAnclaje(punto);
  } catch (e) {
    fallo = e;
  }

  // Llego tarde: hay un punto mas nuevo y esta respuesta ya no describe nada.
  if (consulta !== consultaVigente) return;

  estado.anclaje = anclaje;
  dibujar({ error: Boolean(anclaje?.es_el_deposito) });
  pintarDestino({ fallo });
  pintarAcciones();
  pintarPistaDelAnclaje(fallo);
}

/**
 * La pista del mapa acompaña lo que el panel dice, sin repetirlo entero.
 */
function pintarPistaDelAnclaje(fallo) {
  if (fallo) {
    pintarPista('No se pudo buscar la esquina', 'Se busca igual al registrar.');
    return;
  }
  const a = estado.anclaje;
  if (!a) return;
  if (a.es_el_deposito) {
    pintarPista('Destino rechazado', 'La esquina más cercana es el Depósito.');
    return;
  }
  pintarPista('Esquina más cercana',
    `${textoDeRotulo(a.nodo.rotulo)} · a ${metros(a.distancia_m)} del punto marcado`);
}

/** Circulo hueco donde el operador marco. */
function marcaPunto(color) {
  return L.divIcon({
    className: '',
    html: `<div class="marca-punto" style="border-color:${color}"></div>`,
    iconSize: [14, 14], iconAnchor: [7, 7],
  });
}

/** Disco lleno con retícula sobre el nodo que el sistema resolvió. */
function marcaNodo(color) {
  return L.divIcon({
    className: '',
    html: `<div class="marca-nodo"><i style="background:${color}"></i>
             <b style="background:${color}"></b></div>`,
    iconSize: [52, 52], iconAnchor: [26, 26],
  });
}

function dibujar({ error = false } = {}) {
  capa.clearLayers();
  const color = colorDeToken(error ? TOKEN_DEL_ERROR : TOKEN_DEL_PUNTO);

  if (estado.punto) {
    L.marker([estado.punto.lat, estado.punto.lon], { icon: marcaPunto(color) }).addTo(capa);
  }

  // El nodo que se dibuja es el de la Orden si ya se registro, y el
  // previsualizado mientras todavia no. Son el mismo nodo: el anclaje
  // previsualiza lo que el alta hace.
  const destino = estado.orden?.destino ?? estado.anclaje?.nodo;
  if (estado.punto && destino) {
    // Línea punteada entre lo indicado y lo resuelto: si el sistema mueve el
    // destino, el operador tiene que verlo (PUX-37).
    L.polyline([[estado.punto.lat, estado.punto.lon], [destino.lat, destino.lon]], {
      color, weight: 1.6, dashArray: '4 3',
    }).addTo(capa);
    L.marker([destino.lat, destino.lon], { icon: marcaNodo(color), zIndexOffset: 500 }).addTo(capa);
    const limites = L.latLngBounds([[estado.punto.lat, estado.punto.lon], [destino.lat, destino.lon]]);
    // Si los dos ya se ven, el mapa queda como esta: no se reencuadra.
    if (!mapa.getBounds().pad(-0.1).contains(limites)) {
      mapa.fitBounds(limites, { padding: [140, 140], maxZoom: ZOOM_MAXIMO_AL_MARCAR });
    }
  } else if (estado.punto && !mapa.getBounds().contains([estado.punto.lat, estado.punto.lon])) {
    mapa.panTo([estado.punto.lat, estado.punto.lon]);
  }
}

function pintarPista(titulo, texto) {
  document.getElementById('pista').innerHTML =
    `<div class="t">${titulo}</div><div class="s">${texto}</div>`;
}

/* ------------------------------------------------------------------ */
/* Formulario                                                          */
/* ------------------------------------------------------------------ */

/**
 * El campo Destino, con el enganche resuelto debajo.
 *
 * `error` es un rechazo del alta ya intentada; `fallo`, que no se haya podido
 * previsualizar el anclaje. El segundo no impide registrar: el alta resuelve
 * el nodo igual, y lo que se pierde es el control previo, no la operacion.
 */
function pintarDestino({ error = null, resolviendo = false, fallo = null } = {}) {
  const caja = document.getElementById('destino');

  // Sin punto: una instruccion, no una caja que parezca un campo de texto
  // (2026-10-20A, P5 y N03). El destino solo se marca en el mapa.
  if (!estado.punto) {
    caja.innerHTML = `
      <div class="destino-instruccion">${ICONO_MARCAR}<span>Hacé clic en el mapa para marcar el destino.</span></div>
      <div class="help">La Orden se registra en la esquina más cercana al punto que marques,
        y la ves antes de registrar.</div>`;
    return;
  }

  const a = estado.anclaje;

  if (error) {
    caja.innerHTML = `
      <div class="err-msg"><div class="t"><b>${error.titulo}</b>${error.texto}</div></div>
      ${datosTecnicos()}`;
    return;
  }

  if (resolviendo) {
    caja.innerHTML = `<div class="destino-instruccion">${ICONO_MARCAR}<span>Buscando la esquina más cercana…</span></div>`;
    return;
  }

  if (fallo) {
    caja.innerHTML = `
      <div class="help">No se pudo buscar la esquina más cercana (${escapar(fallo.codigo ?? 'sin respuesta')}).
        Se busca igual al registrar.</div>
      ${datosTecnicos()}`;
    return;
  }

  if (a?.es_el_deposito) {
    // CU-01 flujo 4b, advertido antes de intentarlo: el alta lo rechazaria.
    caja.innerHTML = `
      <div class="err-msg"><div class="t"><b>El destino es el Depósito</b>
        La esquina más cercana al punto es el Depósito, de donde salen las Rutas:
        una Orden no se entrega ahí. Marcá otro punto.</div></div>
      ${datosTecnicos()}`;
    return;
  }

  if (a) {
    const lejos = a.distancia_m > DISTANCIA_DE_ADVERTENCIA_M;
    caja.innerHTML = `
      <div class="resolved destino-card${lejos ? ' lejos' : ''}">
        <span class="pin">${ICONO_PIN}</span>
        <div>
          <div class="r1">${textoDeRotulo(a.nodo.rotulo)}</div>
          <div class="r3">Esquina más cercana · a ${metros(a.distancia_m)} del punto marcado</div>
        </div>
      </div>
      ${lejos ? `
      <div class="err-msg"><div class="t"><b>Punto lejos de las calles</b>
        La esquina más cercana está a ${metros(a.distancia_m)} del punto que marcaste.
        Revisá el punto antes de registrar.</div></div>
      <label class="confirmar-lejano" for="confirmar-lejano">
        <input type="checkbox" id="confirmar-lejano" ${estado.lejanoConfirmado ? 'checked' : ''}>
        <span>Revisé el punto: registrar en esta esquina igual.</span>
      </label>` : ''}
      ${datosTecnicos()}`;
    document.getElementById('confirmar-lejano')?.addEventListener('change', (ev) => {
      estado.lejanoConfirmado = ev.target.checked;
      pintarAcciones();
    });
    return;
  }

  caja.innerHTML = datosTecnicos();
}

/** Nodo, coordenadas y grafo, plegados: son datos del sistema, no de la
    operacion (2026-10-20A, N08). */
function datosTecnicos(orden = null) {
  const p = estado.punto;
  const nodo = orden?.destino ?? estado.anclaje?.nodo;
  const filas = [];
  if (nodo) filas.push(`nodo ${nodo.nodo} · ${nf(nodo.lat)} / ${nf(nodo.lon)}`);
  if (p) filas.push(`punto marcado · ${nf(p.lat)} / ${nf(p.lon)}`);
  if (ambitoDelGrafo) filas.push(`grafo vial de ${escapar(ambitoDelGrafo)}`);
  return `<details class="datos-tecnicos"><summary>Datos técnicos</summary>
    <div class="r2">${filas.join('<br>')}</div></details>`;
}

function pintarErrorDemanda(texto) {
  document.getElementById('caja-demanda').classList.toggle('err', Boolean(texto));
  document.getElementById('err-demanda').innerHTML = texto
    ? `<div class="err-msg"><div class="t"><b>Demanda inválida</b>${texto}</div></div>`
    : '';
}

/* Prioridad del contrato: 1 baja, 2 media, 3 alta. La media va marcada de
   entrada porque es la que el backend asume si no llega. */
const ETIQUETA_PRIORIDAD = { 1: 'Baja', 2: 'Media', 3: 'Alta' };

function prioridadElegida() {
  const marcada = document.querySelector('input[name="prioridad"]:checked');
  return marcada ? Number(marcada.value) : undefined;
}

/** "20 oct 2026" para una fecha civil `YYYY-MM-DD`, sin pasar por `Date`. */
function fechaLegible(iso) {
  const meses = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const [anio, mes, dia] = iso.split('-');
  return `${Number(dia)} ${meses[Number(mes) - 1]} ${anio}`;
}

function demandaIngresada() {
  const v = document.getElementById('demanda').value.trim();
  return v === '' ? null : Number(v);
}

/** Entero de bultos, mayor que 0. Un texto que el campo numerico no puede
    leer ("e", "1e") llega vacio con `badInput`: tambien es invalido. */
function demandaValida() {
  const campo = document.getElementById('demanda');
  if (campo.validity.badInput) return false;
  const d = demandaIngresada();
  return d !== null && Number.isInteger(d) && d >= 1;
}

/** El mensaje junto al campo, apenas el valor deja de servir (P8, N05). */
function validarDemandaEnLinea() {
  const campo = document.getElementById('demanda');
  const vacia = demandaIngresada() === null && !campo.validity.badInput;
  pintarErrorDemanda(demandaValida() || (vacia && !estado.demandaTocada) ? '' : MENSAJE_DEMANDA);
}

/** Hoy en la zona de la operacion, `YYYY-MM-DD`. */
function hoyEnLaOperacion() {
  return new Intl.DateTimeFormat('en-CA', { timeZone: ZONA_DE_LA_OPERACION }).format(new Date());
}

/** Aviso, no error: un plazo vencido se admite y gana prioridad (P10). */
function pintarAvisoDePlazo() {
  const valor = document.getElementById('plazo').value;
  document.getElementById('err-plazo').innerHTML = valor && valor < hoyEnLaOperacion()
    ? '<div class="help aviso-campo">El plazo ya venció: al planificar, esta Orden gana prioridad.</div>'
    : '';
}

function pintarAcciones() {
  // Registrada la Orden, la confirmacion reemplaza el formulario: no hay
  // campos que leer ni boton que habilitar. Antes de 2026-10-19B esto
  // terminaba en un error de pagina en cada alta.
  if (estado.orden) return;
  const demandaOk = demandaValida();
  const esDeposito = Boolean(estado.anclaje?.es_el_deposito);
  const lejos = (estado.anclaje?.distancia_m ?? 0) > DISTANCIA_DE_ADVERTENCIA_M && !esDeposito;
  const resolviendo = Boolean(estado.punto) && !estado.anclaje
    && Boolean(document.querySelector('#destino .destino-instruccion'));
  const listo = Boolean(estado.punto) && demandaOk && !esDeposito && (!lejos || estado.lejanoConfirmado);

  // Con el destino sobre el Deposito la accion principal queda inactiva
  // (PUX-40): el alta lo rechazaria con `destino_es_el_deposito`, y saberlo
  // de antemano es justamente para lo que sirve previsualizar el anclaje.
  // Con un anclaje lejano, hasta que se confirme (B-249).
  document.getElementById('btn-registrar').disabled = !listo;

  // El pie dice lo que falta de verdad (2026-10-20A, P8).
  let pie;
  if (!estado.punto && !demandaOk) pie = 'Falta marcar el destino en el mapa<br>y completar la demanda.';
  else if (!estado.punto) pie = 'Falta marcar el destino en el mapa.';
  else if (resolviendo) pie = 'Buscando la esquina más cercana…';
  else if (esDeposito) pie = 'La esquina más cercana es el Depósito.<br>Marcá otro punto.';
  else if (!demandaOk) pie = 'Falta la demanda: un número entero<br>de bultos, mayor que 0.';
  else if (lejos && !estado.lejanoConfirmado) pie = 'Revisá el punto lejano y confirmalo<br>para registrar.';
  else pie = `${ICONO_LISTO}Lista para registrar`;
  const caja = document.getElementById('pie-form');
  caja.innerHTML = pie;
  caja.classList.toggle('listo', listo);
}

/* ------------------------------------------------------------------ */
/* Registro                                                            */
/* ------------------------------------------------------------------ */

/** La ventana horaria, si se completaron las dos horas (CU-01, paso 2). */
function ventanaIngresada() {
  const desde = document.getElementById('ventana-desde').value;
  const hasta = document.getElementById('ventana-hasta').value;
  return desde && hasta ? { desde: `${desde}:00`, hasta: `${hasta}:00` } : undefined;
}

async function registrar() {
  const boton = document.getElementById('btn-registrar');
  boton.disabled = true;
  pintarErrorDemanda('');
  document.getElementById('caja-plazo').classList.remove('err');
  document.getElementById('err-plazo').innerHTML = '';
  document.getElementById('caja-ventana').classList.remove('err');
  document.getElementById('err-ventana').innerHTML = '';
  pintarDestino();

  try {
    const orden = await api.crearOrden({
      lat: estado.punto.lat,
      lon: estado.punto.lon,
      demanda: demandaIngresada(),
      prioridad: prioridadElegida(),
      plazoComprometido: document.getElementById('plazo').value || undefined,
      ventanaHoraria: ventanaIngresada(),
    });
    estado.orden = orden;
    dibujar();
    pintarConfirmacion(orden);
    avisoBreve(`Orden ${orden.id} registrada en Creada.`);
  } catch (e) {
    manejarErrorDeAlta(e);
  } finally {
    pintarAcciones();
  }
}

/**
 * Un solo patron de error de campo (PUX-40): borde marcado, bloque al pie del
 * campo con causa y salida, accion principal inactiva. Solo cambia el texto.
 */
function manejarErrorDeAlta(e) {
  if (e instanceof ApiInalcanzable) {
    // Lo marcado en el mapa y la demanda se conservan: reintentar es volver
    // a pulsar Registrar.
    modalSinConexion(registrar);
    return;
  }
  if (!(e instanceof ErrorDeApi)) throw e;

  // CU-01 flujo 4b, resuelto en PB-07: una Orden cuyo destino es el propio
  // Deposito no representa trabajo logistico y se rechaza el registro.
  if (e.codigo === 'destino_es_el_deposito') {
    pintarDestino({ error: {
      titulo: 'El destino es un Depósito',
      texto: 'El nodo al que se ancla este punto está declarado como Depósito. ' +
             'Una Orden no se entrega en el punto del que parte la Ruta. Marcá otro punto.',
    } });
    dibujar({ error: true });
    pintarPista('Destino rechazado', 'El nodo resuelto es el Depósito.');
    return;
  }

  if (e.codigo === 'peticion_mal_formada') {
    const campos = e.detalle?.campos ?? [];
    const dem = campos.find((c) => c.campo === 'demanda');
    if (dem) {
      pintarErrorDemanda(MOTIVO_POR_TIPO[dem.tipo] ?? dem.motivo);
      return;
    }
    // La prioridad sale de tres opciones fijas, asi que el unico rechazo de
    // campo alcanzable fuera de la demanda es un plazo que no es fecha.
    const plazo = campos.find((c) => c.campo === 'plazo_comprometido' || c.campo === 'prioridad');
    if (plazo) {
      document.getElementById('caja-plazo').classList.add('err');
      document.getElementById('err-plazo').innerHTML =
        `<div class="err-msg"><div class="t"><b>${plazo.campo === 'prioridad' ? 'Prioridad inválida' : 'Plazo inválido'}</b>`
        + `${escapar(plazo.motivo)}</div></div>`;
      return;
    }
    const otro = campos[0];
    pintarDestino({ error: {
      titulo: 'Destino inválido',
      texto: otro ? `${otro.campo}: ${MOTIVO_POR_TIPO[otro.tipo] ?? otro.motivo}` : e.message,
    } });
    return;
  }

  // CU-01 flujo 4a (2026-11-20A): el punto queda lejos de toda calle del grafo.
  if (e.codigo === 'destino_no_localizable') {
    pintarDestino({ error: { titulo: 'El destino no se localiza sobre la red vial', texto: e.message } });
    dibujar({ error: true });
    pintarPista('Destino rechazado', 'Marcá un punto sobre una calle de la Ciudad.');
    return;
  }
  if (e.codigo === 'ventana_invalida') {
    document.getElementById('caja-ventana').classList.add('err');
    document.getElementById('err-ventana').innerHTML =
      `<div class="err-msg"><div class="t"><b>Ventana inválida</b>${escapar(e.message)}</div></div>`;
    return;
  }

  // Cualquier otra regla de dominio, con el mismo patron.
  pintarDestino({ error: { titulo: 'No se pudo registrar la Orden', texto: e.message } });
  dibujar({ error: true });
}

/**
 * El historial de transiciones de la Orden (RB-06, glosario relacion 2).
 *
 * `POST /v1/ordenes` asienta la transicion inicial, asi que el bloque tiene
 * contenido desde el alta: una fila `Creada` con su marca temporal. Se pinta
 * solo si llego alguna -- no se dibuja un bloque vacio, ni se supone una
 * transicion que la respuesta no traiga.
 */
function bloqueHistorial(historial) {
  if (!historial?.length) return '';

  const filas = historial.map((t) => `
    <div class="hrow">
      <span class="dot"></span>${ETIQUETA_ESTADO[t.estado_nuevo] ?? t.estado_nuevo}
      <span class="tm">${instante(t.marca_temporal)}</span>
    </div>`).join('');

  return `<div class="hist"><div class="h">Historial de la Orden</div>${filas}</div>`;
}

/**
 * CU-01 pasos 6 y 7: el identificador devuelto es el resultado de la pantalla.
 */
function pintarConfirmacion(orden) {
  const d = orden.destino;
  // La distancia es la que informo el anclaje previo, no una segunda cuenta:
  // el nodo que se registro es el mismo que se previsualizo.
  const distancia = estado.anclaje?.distancia_m ?? null;

  document.getElementById('form').innerHTML = `
    <div class="done">
      <div class="eyebrow">Registro confirmado</div>
      <h2>Orden registrada</h2>
      <p>Queda disponible para la próxima planificación. No está asignada a
         ninguna Ruta ni a ninguna Parada.</p>

      <div class="idbox">
        <div class="hd">${orden.id}<span class="st">${ETIQUETA_ESTADO[orden.estado] ?? orden.estado}</span></div>
        <div class="rows">
          <div class="drow"><span class="k">Destino (esquina más cercana)</span><span class="v">${textoDeRotulo(d.rotulo)}</span></div>
          ${distancia !== null ? `
          <div class="drow"><span class="k">Distancia al punto marcado</span>
            <span class="v">${metros(distancia)}</span></div>` : ''}
          <div class="drow"><span class="k">Demanda</span><span class="v">${plural(orden.demanda, 'bulto', 'bultos')}</span></div>
          <div class="drow"><span class="k">Prioridad</span><span class="v">${ETIQUETA_PRIORIDAD[orden.prioridad] ?? orden.prioridad}</span></div>
          <div class="drow"><span class="k">Plazo comprometido</span><span class="v">${orden.plazo_comprometido ? fechaLegible(orden.plazo_comprometido) : 'sin plazo'}</span></div>
          <div class="drow"><span class="k">Modalidad</span><span class="v">Entrega</span></div>
        </div>
      </div>

      ${datosTecnicos(orden)}

      ${bloqueHistorial(orden.historial)}

      <div class="acts" style="margin-top:26px">
        <a class="btn" href="orden.html">Registrar otra Orden</a>
        <a class="btn-ghost" href="planificacion.html">Ir a la planificación</a>
      </div>
    </div>`;

  pintarPista('Destino registrado',
    textoDeRotulo(d.rotulo) +
    (distancia !== null ? ` · a ${metros(distancia)} del punto marcado` : ''));
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */

/** "Cancelar" vuelve a la pantalla desde la que se llego (N07). Se recuerda
    en la pestaña, porque "Registrar otra Orden" recarga esta misma pantalla. */
function prepararCancelar() {
  let origen = null;
  try {
    const desde = document.referrer ? new URL(document.referrer) : null;
    if (desde && desde.origin === window.location.origin) {
      const pagina = desde.pathname.split('/').pop();
      if (pagina && !['orden.html', 'ingreso.html', 'terminos.html'].includes(pagina)) {
        origen = pagina + desde.search;
        sessionStorage.setItem(CLAVE_ORIGEN, origen);
      }
    }
    origen = origen ?? sessionStorage.getItem(CLAVE_ORIGEN);
  } catch { /* sin almacenamiento: queda el predeterminado */ }
  document.getElementById('cancelar').href = origen || ORIGEN_PREDETERMINADO;
}

function iniciar() {
  // Sin sesion, a ingresar; con terminos pendientes, a los terminos.
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'orden' });
  crearMapa();
  pintarDestino();
  pintarAcciones();

  const campo = document.getElementById('demanda');
  campo.addEventListener('input', () => { estado.demandaTocada = true; validarDemandaEnLinea(); pintarAcciones(); });
  campo.addEventListener('blur', () => { if (estado.demandaTocada) validarDemandaEnLinea(); });
  document.getElementById('plazo').addEventListener('change', pintarAvisoDePlazo);
  prepararCancelar();
  document.getElementById('btn-registrar').addEventListener('click', registrar);
  document.getElementById('form-direccion').addEventListener('submit', buscarDireccion);
  document.getElementById('candidatos').addEventListener('click', elegirCandidato);
  // Sin mouse: el mapa se mueve con las flechas y este boton marca su
  // centro, que es lo mismo que hacer clic ahi (accesibilidad, bloque P).
  document.getElementById('marcar-centro').addEventListener('click', () => {
    if (estado.orden) return;
    const centro = mapa.getCenter();
    marcarPunto({ lat: centro.lat, lon: centro.lng });
  });
  // Los botones del contador mueven la demanda de a un bulto y avisan como
  // si se hubiera escrito: la validacion es la misma.
  const paso = (delta) => () => {
    const actual = Number.parseInt(campo.value, 10);
    campo.value = String(Math.max(1, (Number.isFinite(actual) ? actual : 0) + delta));
    campo.dispatchEvent(new Event('input', { bubbles: true }));
  };
  document.getElementById('demanda-menos').addEventListener('click', paso(-1));
  document.getElementById('demanda-mas').addEventListener('click', paso(1));
  contarRegistradasHoy();
}

/** Cuantas Ordenes se registraron hoy, en la barra superior. Es accesorio:
    si la consulta falla, no se muestra nada. */
async function contarRegistradasHoy() {
  try {
    const hoy = hoyEnLaOperacion();
    const n = (await api.listarOrdenes())
      .filter((o) => o.historial?.[0] && fechaOperativa(new Date(o.historial[0].marca_temporal)) === hoy).length;
    const caja = document.getElementById('registradas-hoy');
    caja.innerHTML = `<i></i>${plural(n, 'Orden registrada', 'Órdenes registradas')} hoy`;
    caja.hidden = false;
  } catch { /* sin cuenta */ }
}

iniciar();
