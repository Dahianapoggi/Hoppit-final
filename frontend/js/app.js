/**
 * Pantalla G1-A -- Plan calculado sobre el mapa.
 *
 * Estado 1: reposo, el plan completo.
 * Estado 2: una Ruta seleccionada, con su recorrido rectificado en el panel.
 *
 * Cubre CU-03. Muestra el resultado del ruteo, no el avance de la ejecucion:
 * Seguimiento de Flota no esta poblado en fase 1 (ARQ-03), y esta pantalla
 * no simula lo que no existe. El ETA de cada Parada se muestra cuando la API
 * lo publica, rotulado como estimacion con datos de referencia de otra
 * ciudad (B-193); si no llega, no se muestra nada en su lugar.
 */

import * as api from './api.js';
import { ApiInalcanzable, ErrorDeApi } from './api.js';
import { montarEncabezado } from './encabezado.js';
import { icono } from './iconos.js';
import { abrirModal, avisoBreve, confirmar, escapar, modalSinConexion } from './modal.js';
import { exigirSesion } from './sesion.js';
import {
  CapaDePosicion, FACTORES, FACTOR_POR_OMISION, SIN_ETA, lineaTemporal,
} from './recorrido_estimado.js';
import {
  animarConstruccion, detener as detenerConstruccion, fraccionesDeParadas,
  movimientoReducido,
} from './construccion.js';
import { apagado, colorDeToken, ETIQUETA_ESTADO, TESELAS_PROVISIONALES, fechaOperativa, horaDeLaOperacion,
         paletaPorVehiculo, textoDeRotulo, textoDelDeclarante, transicionDelResultado,
         vehiculoComprometido } from './tokens.js';

/* Trazado: 4,6 px de color sobre orla blanca de 10 px (hoja de traspaso). */
const GROSOR_TRAZO = 4.6;
const GROSOR_ORLA = 10;

const DIAMETRO_PARADA = 25;

/* Debajo de este alto de ventana, la leyenda del mapa arranca plegada (C8). */
const ALTO_CON_LEYENDA_ABIERTA = 820;
const DIAMETRO_APAGADO = 14;
/* Opacidad de una Ruta que el operador apago. Conserva color y numero: lo
   que baja es el peso visual, no la identidad. */
const OPACIDAD_ATENUADA = 0.3;
const DIAMETRO_PARADA_SEL = 30;
const HALO_PARADA_SEL = 48;
const ARO_PARADA = 2.5;

/* Encuadre mientras la API no respondio. Se reemplaza por `fitBounds` sobre
   la geometria real apenas llega el plan. */
const VISTA_PREVIA = { centro: [-34.4875, -58.5150], zoom: 14 };

/* Encuadre del estado vacio: sin plan no hay geometria que enmarcar, y el
   unico punto real es el Deposito. */
const ZOOM_SIN_PLAN = 14;

/**
 * Jornada a consultar, del parametro de la URL o la operativa en curso.
 *
 * Se admite por URL para que volver desde G1-B --que la recibe de aca-- caiga
 * en la misma jornada y no en la de hoy. El calendario sigue admitiendo
 * cualquier fecha: consultar una jornada anterior es legitimo y es de solo
 * lectura.
 */
function jornadaPedida() {
  const pedida = new URLSearchParams(window.location.search).get('fecha');
  return /^\d{4}-\d{2}-\d{2}$/.test(pedida ?? '') ? pedida : fechaOperativa();
}

const estado = {
  rutas: [],
  seleccion: null,      // id de la Ruta seleccionada
  parada: null,         // secuencia de la Parada seleccionada dentro de esa Ruta
  paleta: new Map(),    // color por Vehiculo, compartido con las demas pantallas
  apagadas: new Set(),  // ids de Ruta atenuadas: filtro visual, no exclusion
  fecha: jornadaPedida(),   // jornada solicitada (CU-03 paso 1)
  deposito: null,       // el de la instancia, legible sin plan (FR-14)
  flota: null,          // { vehiculos, capacidad_total }, para la cuenta del vacio
  presentados: new Set(),   // Planes cuya construccion ya se animo
  declaraciones: new Map(), // `${ruta}:${secuencia}` -> { resultado, instante, declarante } (B)
  alertas: [],          // las de la jornada, para la ficha de la Ruta (2026-10-27B, A)
};

let mapa = null;
let capaPlan = null;
/* El recorrido estimado de la Ruta elegida (DFE-32) y de que Ruta es. */
let capaDePosicion = null;
let rutaDelRecorrido = null;

/**
 * Renderizador de las trazas del plan.
 *
 * El mapa usa Canvas por preferencia general, pero las trazas del plan van a
 * SVG a proposito: la construccion progresiva se hace recortando el path con
 * `stroke-dashoffset`, y en Canvas no hay path que recortar --Leaflet dibuja
 * los pixeles y la capa no expone ningun elemento--. Son seis polilineas: el
 * costo de sacarlas del Canvas es despreciable y a cambio la animacion corre
 * en el compositor.
 *
 * El margen es amplio para que el plan entre entero en el area que el
 * renderizador cubre. Si quedara fuera, Leaflet recortaria la polilinea y el
 * path mediria menos que el recorrido, de modo que las Paradas apareceran
 * corridas respecto de la punta de la traza.
 */
let dibujoDelPlan = null;
let capaDeposito = null;

/* ------------------------------------------------------------------ */
/* Formato                                                             */
/* ------------------------------------------------------------------ */

const nf = (n, dec = 2) =>
  n.toLocaleString('es-AR', { minimumFractionDigits: dec, maximumFractionDigits: dec });

const km = (metros) => nf(metros / 1000) + ' km';

const plural = (n, sing, pl) => `${n} ${n === 1 ? sing : pl}`;

/**
 * La jornada en prosa: "24 ago 2026".
 *
 * `YYYY-MM-DD` es una fecha civil, no un instante: se parte a mano en vez de
 * pasarla por `new Date()`, que la interpretaria como medianoche UTC y en
 * esta zona la correria al dia anterior.
 */
const MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun',
               'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

function fechaLegible(iso) {
  const [anio, mes, dia] = iso.split('-');
  return `${Number(dia)} ${MESES[Number(mes) - 1]} ${anio}`;
}

/* "Lunes 24 de agosto", para el titulo del plan. El dia de la semana se
   calcula al mediodia UTC de la fecha civil, que cae en el mismo dia en
   cualquier huso de la Argentina. */
const DIAS_LARGOS = ['Domingo', 'Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado'];
const MESES_LARGOS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
                      'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
function fechaLarga(iso) {
  const [, mes, dia] = iso.split('-');
  const semana = new Date(`${iso}T12:00:00Z`).getUTCDay();
  return `${DIAS_LARGOS[semana]} ${Number(dia)} de ${MESES_LARGOS[Number(mes) - 1]}`;
}

const colorDe = (ruta) => estado.paleta.get(ruta.vehiculo_id);

/* ------------------------------------------------------------------ */
/* ETA de la Parada                                                    */
/* ------------------------------------------------------------------ */

/* Que es el ETA y de donde sale, visible junto a las horas (B-193, punto 3,
   criterio g). El backend lo estima por simulacion con el modelo hibrido de
   B-196, sin la dispersion relativa de B-201 desde el 12 de octubre de 2026
   (B-205): viaje por la red a una velocidad supuesta, mas una componente de
   parada sorteada, con su dispersion, de una tabla de LaDe en Hangzhou. No es
   una prediccion validada ni depende del transito (B-19). La banda es la del modelo: no se afirma una cobertura en el AMBA,
   donde no esta medida. */
const ROTULO_ETA =
  'Horas estimadas de la primera entrega en cada Parada, con la banda de p10 a p90 del modelo. ' +
  'Suman un viaje por la red a una velocidad supuesta y tiempos de parada, con su dispersión, ' +
  'de otra ciudad (Hangzhou, LaDe): no validadas para el AMBA ni dependientes del tránsito; ' +
  'la banda no está medida en el AMBA.';
/* Lo que queda a la vista sobre la espina (F1.5): que las horas son
   estimadas. El resto de la procedencia, plegado debajo. */
const ROTULO_ETA_A_LA_VISTA = 'Horas estimadas de la primera entrega, con su rango: no son horas reales.';
const ROTULO_ETA_BREVE =
  'Viaje por la red a una velocidad supuesta, más tiempos de parada de referencia de ' +
  'otra ciudad; no validada para el AMBA.';

const SEGUNDOS_POR_MINUTO = 60;
const MINUTOS_POR_HORA = 60;

/* El ETA se muestra sólo si llegan la salida y los tres valores: con uno
   faltante no se completa con nada. */
const tieneEta = (ruta, parada) =>
  ruta.hora_de_salida != null && parada.eta_estimado_s != null &&
  parada.eta_p10_s != null && parada.eta_p90_s != null;

/**
 * Hora del dia, HH:MM, de un instante publicado como segundos desde la salida
 * de la Ruta. `hora_de_salida` llega como "HH:MM:SS", hora local de la
 * jornada: se suma a mano en vez de pasar por `Date`, que la llevaria a la
 * zona del navegador. Redondeada al minuto, la resolucion de los datos.
 */
function horaDelDia(horaDeSalida, segundos) {
  const [h, m, s = 0] = horaDeSalida.split(':').map(Number);
  const minutos = Math.round(
    ((h * MINUTOS_POR_HORA + m) * SEGUNDOS_POR_MINUTO + s + segundos) / SEGUNDOS_POR_MINUTO);
  const dosCifras = (n) => String(n).padStart(2, '0');
  return `${dosCifras(Math.floor(minutos / MINUTOS_POR_HORA))}:${dosCifras(minutos % MINUTOS_POR_HORA)}`;
}

function etaEnHoras(ruta, parada) {
  return {
    hora: horaDelDia(ruta.hora_de_salida, parada.eta_estimado_s),
    desde: horaDelDia(ruta.hora_de_salida, parada.eta_p10_s),
    hasta: horaDelDia(ruta.hora_de_salida, parada.eta_p90_s),
  };
}

const rutaSeleccionada = () =>
  estado.rutas.find((r) => r.id === estado.seleccion) ?? null;

/**
 * Apagar una Ruta es un filtro de lectura, no una exclusion del plan.
 *
 * Los totales del pie no cambian: el plan sigue siendo el mismo y sus cifras
 * tambien. Lo unico que cambia es cuanto pesa cada Ruta en la pantalla. Por
 * eso el estado vive solo aca y no se persiste ni se envia al backend.
 */
const apagada = (ruta) => estado.apagadas.has(ruta.id);

const encendidas = () => estado.rutas.filter((r) => !apagada(r));

/* ------------------------------------------------------------------ */
/* Avisos                                                              */
/* ------------------------------------------------------------------ */

/**
 * Aviso de carga, sobre el mapa y sin oscurecerlo.
 *
 * Es un estado transitorio, no un error: informa que la consulta esta en
 * curso y desaparece sola. Los errores no pasan por aca --van a modal, sobre
 * el contenido-- porque un error que ocupa la pantalla del mapa la deja sin
 * lo unico que esta pantalla tiene para mostrar.
 */
function mostrarAviso({ tipo, titulo, texto }) {
  const caja = document.getElementById('aviso');
  document.getElementById('aviso-tipo').textContent = tipo;
  document.getElementById('aviso-titulo').textContent = titulo;
  document.getElementById('aviso-texto').innerHTML = texto ?? '';
  caja.hidden = false;
}

const ocultarAviso = () => { document.getElementById('aviso').hidden = true; };

/* ------------------------------------------------------------------ */
/* Mapa                                                                */
/* ------------------------------------------------------------------ */

function crearMapa() {
  mapa = L.map('mapa', { preferCanvas: true, zoomControl: false });

  // La atribucion la maneja el control propio de Leaflet, y es el unico
  // mecanismo: antes habia tres apagandolo --opcion del mapa, hoja de estilos
  // y opcion de la capa-- mas un div a mano que lo suplia. Se elige el control
  // porque Leaflet apila los controles de una misma esquina en columna, de
  // modo que la atribucion y la escala dejan de solaparse en la esquina
  // inferior derecha. Ocupa lugar fijo y no se oculta nunca (PUX-17).
  //
  // Se le quita el prefijo, que anuncia la libreria: el credito que la
  // licencia exige es el de la fuente cartografica, no el de quien la dibuja.
  mapa.attributionControl.setPrefix(false);

  // Fondo de teselas, no render del grafo (PUX-17): que la traza coincida
  // visiblemente con la calle es la unica forma de que NFR-08 se lea en la
  // pantalla y no solo en el test.
  L.tileLayer(TESELAS_PROVISIONALES.url, {
    maxZoom: TESELAS_PROVISIONALES.zoomMaximo,
    attribution: TESELAS_PROVISIONALES.atribucion,
  }).addTo(mapa);
  L.control.zoom({ position: 'topright' }).addTo(mapa);
  capaDePosicion = new CapaDePosicion(L, mapa, document.querySelector('.map'), pintarEstadoDelRecorrido);
  L.control.scale({ position: 'bottomright', imperial: false, maxWidth: 120 }).addTo(mapa);

  // Leaflet exige una vista antes de dibujar. El encuadre real lo fija
  // `fitBounds` con la geometria que devuelve la API; esto es solo el estado
  // previo a que llegue, y por eso es el unico par de coordenadas del
  // frontend. No es dato de dominio: no hay Deposito ni Parada hasta que el
  // backend los informe.
  mapa.setView(VISTA_PREVIA.centro, VISTA_PREVIA.zoom);
  dibujoDelPlan = L.svg({ padding: 0.6 }).addTo(mapa);
}

function iconoParada(secuencia, color, { atenuado = false } = {}) {
  const total = DIAMETRO_PARADA + ARO_PARADA * 2;
  // El disco de una Ruta apagada no debe ofrecer la afordancia de abrir la
  // Parada, porque lo que hace es encender la Ruta: la clase le cambia el
  // cursor y el estado de hover.
  return L.divIcon({
    className: '',
    html:
      `<div class="parada-marca${atenuado ? ' encender' : ''}"` +
      ` style="width:${total}px;height:${total}px` +
      `${atenuado ? `;opacity:${OPACIDAD_ATENUADA}` : ''}">` +
      `<span style="background:${color};width:${DIAMETRO_PARADA}px;height:${DIAMETRO_PARADA}px">` +
      `${secuencia}</span></div>`,
    iconSize: [total, total],
    iconAnchor: [total / 2, total / 2],
  });
}

/* Una Parada de Ruta no seleccionada: pierde el numero pero no desaparece.
   El operador necesita ver el reparto territorial para juzgar el plan. */
function iconoApagado() {
  const total = DIAMETRO_APAGADO + ARO_PARADA * 2;
  return L.divIcon({
    className: '',
    html:
      `<div class="parada-marca" style="width:${total}px;height:${total}px">` +
      `<span style="background:${apagado()};width:${DIAMETRO_APAGADO}px;height:${DIAMETRO_APAGADO}px"></span></div>`,
    iconSize: [total, total],
    iconAnchor: [total / 2, total / 2],
  });
}

/* Parada seleccionada: disco mayor con halo del color al 14 % (PUX-35: el
   mismo disco numerado identifica la Parada en el mapa y en la espina). */
function iconoParadaSeleccionada(secuencia, color) {
  const aro = DIAMETRO_PARADA_SEL + ARO_PARADA * 2;
  return L.divIcon({
    className: '',
    html:
      `<div class="marca-sel" style="width:${HALO_PARADA_SEL}px;height:${HALO_PARADA_SEL}px">` +
      `<i style="background:${color}"></i>` +
      `<div class="parada-marca" style="width:${aro}px;height:${aro}px">` +
      `<span style="background:${color};width:${DIAMETRO_PARADA_SEL}px;height:${DIAMETRO_PARADA_SEL}px">` +
      `${secuencia}</span></div></div>`,
    iconSize: [HALO_PARADA_SEL, HALO_PARADA_SEL],
    iconAnchor: [HALO_PARADA_SEL / 2, HALO_PARADA_SEL / 2],
  });
}

function iconoDeposito() {
  return L.divIcon({
    className: '',
    html: '<div class="deposito-marca" style="width:26px;height:26px"><i><b></b></i></div>',
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  });
}

/**
 * El Deposito en su propia capa, independiente del plan.
 *
 * Vive fuera de `capaPlan` porque tiene que sobrevivir a que no haya Rutas:
 * PUX-33 exige que en el estado vacio el mapa conserve el sustrato y el
 * Deposito, atenuados por el velo. Que la marca este bajo el velo y no
 * encima es lo que la atenua; no se le baja la opacidad aparte.
 */
function dibujarDeposito(deposito, { encuadrar = false } = {}) {
  if (capaDeposito) capaDeposito.remove();
  capaDeposito = null;
  if (!deposito) return;

  capaDeposito = L.layerGroup().addTo(mapa);
  L.marker([deposito.lat, deposito.lon], { icon: iconoDeposito(), zIndexOffset: 1000 })
    .bindTooltip(`<b>Depósito</b><br>${textoDeRotulo(deposito.rotulo)}`, { direction: 'top', offset: [0, -14] })
    .addTo(capaDeposito);

  if (encuadrar) mapa.setView([deposito.lat, deposito.lon], ZOOM_SIN_PLAN);
}

/**
 * Tres modos de trazo, y la diferencia entre dos de ellos importa.
 *
 * - `plena`: la Ruta se lee entera.
 * - `contexto`: hay otra Ruta seleccionada y esta acompaña. Va en gris plano,
 *   porque en ese estado no compite por identidad: lo unico que interesa es
 *   el reparto territorial alrededor de la elegida.
 * - `atenuada`: el operador la apago para leer mejor el resto. **Conserva su
 *   color y su numero de secuencia**, con menos opacidad. Si fuera al gris
 *   plano dejaria de distinguirse de las otras apagadas, y las Rutas se
 *   distinguen por color Y por numero, nunca solo por uno de los dos.
 */
function dibujarRuta(ruta, { modo }) {
  const contexto = modo === 'contexto';
  const atenuada = modo === 'atenuada';
  const tenue = contexto || atenuada;
  const color = contexto ? apagado() : colorDe(ruta);
  const coords = ruta.trazado.coordenadas;

  // Orla blanca primero, color encima: el recorrido se despega del fondo sin
  // depender del contraste de lo que tenga debajo.
  const orla = L.polyline(coords, {
    color: colorDeToken('--blanco'), weight: GROSOR_ORLA, opacity: tenue ? 0.4 : 0.95,
    lineJoin: 'round', lineCap: 'round', renderer: dibujoDelPlan,
  }).addTo(capaPlan);
  const trazo = L.polyline(coords, {
    color, weight: GROSOR_TRAZO, opacity: atenuada ? OPACIDAD_ATENUADA : 1,
    lineJoin: 'round', lineCap: 'round', renderer: dibujoDelPlan,
  }).addTo(capaPlan);

  // Apagar una Ruta es un recurso visual para hacer foco en otra, no una
  // forma de deshabilitarla: un clic sobre su traza la enciende de vuelta.
  if (atenuada) {
    trazo.on('click', () => alternarRuta(ruta.id));
    trazo.bindTooltip(`Encender ${ruta.vehiculo_id}`, { direction: 'top', sticky: true });
  }

  const marcas = [];
  ruta.paradas.forEach((parada) => {
    const { lat, lon, rotulo } = parada.punto;
    const elegida = !tenue && estado.parada === parada.secuencia;

    // Con la Ruta en contexto el disco pierde el numero: no hay una secuencia
    // que seguir en una Ruta que solo acompaña. Atenuada, en cambio, lo
    // conserva: el operador la apago para leerla menos, no para dejar de
    // reconocerla.
    let icono;
    if (contexto) icono = iconoApagado();
    else if (elegida) icono = iconoParadaSeleccionada(parada.secuencia, color);
    else icono = iconoParada(parada.secuencia, color, { atenuado: atenuada });

    const marca = L.marker([lat, lon], {
      icon: icono,
      zIndexOffset: tenue ? 0 : (elegida ? 900 : 500),
    });
    if (atenuada) {
      // El rotulo anuncia lo que el clic hace, no el contenido de la Parada:
      // la Parada no se abre desde una Ruta apagada, se enciende la Ruta.
      marca.bindTooltip(`Encender ${ruta.vehiculo_id}`, { direction: 'top', offset: [0, -14] });
    } else if (!tenue && !elegida) {
      marca.bindTooltip(
        `<b>${parada.secuencia}</b> · ${textoDeRotulo(rotulo)}<br>` +
        `${plural(parada.ordenes.length, 'Orden', 'Órdenes')} · ` +
        `${plural(parada.demanda, 'bulto', 'bultos')}`,
        { direction: 'top', offset: [0, -14] }
      );
    }
    // Un clic sobre la Parada la abre venga de donde venga: en reposo, sobre
    // una Ruta en contexto o sobre la ya seleccionada. Exigir seleccionar
    // primero la Ruta partiria en dos el paso 4 de CU-03, que es un solo
    // gesto del operador.
    //
    // Sobre una Ruta apagada el clic la ENCIENDE en vez de abrir la Parada.
    // No es una excepcion al paso 4 sino su condicion previa: una Ruta
    // apagada esta fuera de foco, y no responder la volveria deshabilitada,
    // que es lo contrario de lo que el control significa. Encendida, el paso
    // 4 vuelve a valer con un segundo clic.
    marca.on('click', () => (atenuada
      ? alternarRuta(ruta.id)
      : elegirParadaDe(ruta.id, parada.secuencia)));
    marca.addTo(capaPlan);
    marcas.push(marca);
  });

  // Lo que la animacion de construccion necesita para revelar esta Ruta. Se
  // devuelve siempre, se anime o no: dibujar no decide si hay animacion.
  return { orla, trazo, marcas, paradas: ruta.paradas };
}

function dibujarMapa({ encuadrar = false, animar = false } = {}) {
  // Un repintado descarta lo que hubiera a medio revelar: lo que se dibuja
  // ahora es el estado nuevo, no la continuacion del anterior.
  detenerConstruccion();
  if (capaPlan) capaPlan.remove();
  capaPlan = L.layerGroup().addTo(mapa);

  const sel = rutaSeleccionada();

  // Las apagadas primero y las no seleccionadas despues, para que lo que el
  // operador esta mirando quede siempre encima.
  const orden = [...estado.rutas].sort((a, b) => {
    const peso = (r) => (apagada(r) ? 0 : (sel && r === sel ? 2 : 1));
    return peso(a) - peso(b);
  });

  const dibujadas = orden.map((ruta) => dibujarRuta(ruta, {
    modo: apagada(ruta) ? 'atenuada'
      : (sel && ruta !== sel ? 'contexto' : 'plena'),
  }));

  // El Deposito que se dibuja con un plan es el de la Ruta, no el que
  // `GET /v1/depositos` informa hoy: la Ruta se calculo contra aquel, y si
  // el Deposito de la instancia cambiara, un plan ya publicado seguiria
  // partiendo del que uso. Misma razon por la que la capacidad viaja con la
  // Ruta. Es unico en fase 1 (B-12) y comun a todas.
  const dep = estado.rutas[0]?.deposito ?? estado.deposito;
  dibujarDeposito(dep);

  // Solo se encuadra al cargar el plan. Seleccionar una Ruta no mueve el
  // mapa: perder el encuadre del reparto territorial al hacer foco en una
  // Ruta es justamente lo que el diseno evita al no ocultar las demas.
  if (encuadrar) {
    const puntos = estado.rutas.flatMap((r) => r.trazado.coordenadas);
    if (dep) puntos.push([dep.lat, dep.lon]);
    // Sin animacion de encuadre cuando despues viene la construccion: medir
    // un path que todavia se esta reproyectando da un largo que deja de
    // valer apenas el movimiento termina.
    if (puntos.length) {
      mapa.fitBounds(puntos, { padding: [70, 70], animate: !animar });
    }
  }

  // La animacion va despues del encuadre: las fracciones se miden sobre las
  // coordenadas proyectadas, que dependen del zoom con el que se va a ver.
  if (animar) construir(dibujadas);
}

/**
 * Identidad del plan que se esta presentando.
 *
 * Sirve para animar la construccion una sola vez por plan. Se toma del Plan
 * de Distribucion que las Rutas referencian; si el backend no lo informara,
 * los identificadores de las Rutas alcanzan, porque tambien son derivados de
 * la corrida.
 */
function identidadDelPlan() {
  if (!estado.rutas.length) return null;
  return estado.rutas[0].plan_id
    ?? estado.rutas.map((r) => r.id).sort().join('|');
}

/**
 * Lanza la construccion progresiva sobre lo que se acaba de dibujar.
 *
 * Todas las Rutas avanzan a la misma velocidad y arrancan juntas: con 18
 * Paradas, hacerlo Ruta por Ruta seria una espera larga, y simultaneo
 * comunica ademas que la flota sale junta.
 */
function construir(dibujadas) {
  const conTraza = dibujadas.filter((d) => d.trazo.getElement());
  if (!conTraza.length) {
    // No callar: sin path no hay nada que revelar, y la unica senal seria
    // que el plan aparece dibujado de golpe, que es lo que la animacion
    // viene a evitar.
    console.warn(
      'No se pudo animar la construcción: las trazas no exponen un path SVG. ' +
      'Revisar que las polilíneas del plan usen el renderizador SVG.'
    );
    return;
  }

  // La fraccion se mide sobre el path que se va a recortar, no sobre las
  // coordenadas del trazado: Leaflet simplifica la polilinea antes de
  // dibujarla, de modo que el path es mas corto que el recorrido y lo pierde
  // de forma despareja.
  const medidas = conTraza.map((d) => {
    const { fracciones, largo } = fraccionesDeParadas(
      d.trazo.getElement(), d.paradas,
      ([lat, lon]) => mapa.latLngToLayerPoint([lat, lon])
    );
    return { ...d, fracciones, largo };
  });
  const mayor = Math.max(...medidas.map((m) => m.largo)) || 1;

  animarConstruccion(
    medidas.map((m) => ({
      paths: [m.orla.getElement(), m.trazo.getElement()].filter(Boolean),
      marcas: m.marcas.map((marca, i) => ({
        elemento: marca.getElement(),
        fraccion: m.fracciones[i] ?? 1,
      })).filter((x) => x.elemento),
      largoRelativo: m.largo / mayor,
    }))
  );
}

/* ------------------------------------------------------------------ */
/* Panel                                                               */
/* ------------------------------------------------------------------ */

/**
 * Ocupacion de la Ruta: la proporcion de capacidad del Vehiculo que consume
 * su carga, que es el primer criterio de aceptacion de HU-05.
 *
 * La capacidad viaja CON la Ruta y no se lee de la Flota vigente: si la
 * Flota cambia, la ocupacion de una Ruta ya publicada no cambia
 * retroactivamente, porque se calculo contra el Vehiculo que la Ruta tenia
 * asignado. Son dos fuentes distintas a proposito.
 *
 * Sin capacidad no hay barra: se declara el hueco en vez de dibujar una
 * proporcion contra un denominador supuesto.
 */
function ocupacion(ruta) {
  const capacidad = ruta.capacidad_vehiculo;
  if (!capacidad) return null;
  return { capacidad, porcentaje: (ruta.carga / capacidad) * 100 };
}

/* El numero de la Ruta: su lugar en la paleta, que ordena los Vehiculos
   alfabeticamente. La marca pide distinguir las Rutas por color Y por numero,
   para quien no distingue los colores. */
const numeroDe = (ruta) => {
  const ids = [...estado.paleta.keys()];
  return ids.indexOf(ruta.vehiculo_id) + 1 || '·';
};

/* Lo que se puede decir de la Ruta mirando sus Ordenes: la Ruta no tiene
   estados (B-96). */
function estadoDeLaRuta(ruta) {
  // Una Ruta Reemplazada publica solo lo que cumplio (22A), y puede quedar sin
  // Ordenes: no es una Ruta sin despachar (2026-11-23A, visto en el navegador).
  if (ruta.reemplazada_por) return { clase: 'st-completada', texto: 'Reemplazada' };
  const ordenes = ordenesDe(ruta);
  if (!ordenes.length || ordenes.every((o) => o.estado === 'planificada')) {
    return { clase: 'st-planificada', texto: 'Sin despachar' };
  }
  if (ordenes.every((o) => o.estado === 'completada' || o.estado === 'fallida')) {
    return { clase: 'st-completada', texto: 'Terminada' };
  }
  return { clase: 'st-en_transito', texto: 'En ejecución' };
}

function tarjeta(ruta, { seleccionada = false } = {}) {
  const ocu = ocupacion(ruta);

  // La barra toma el color de la Ruta (propuesta de 2026-11), sin umbral ni
  // alerta: bajo funcion objetivo de distancia (B-56) un reparto
  // desbalanceado es resultado legitimo del solver, no un defecto que la
  // pantalla deba señalar. El ancho se limita al 100% para que una carga que
  // excediera la capacidad no desborde la barra en silencio.
  const barra = ocu
    ? `<div class="occ">
        <div class="occ-bar"><div class="occ-fill" style="width:${Math.min(ocu.porcentaje, 100)}%"></div></div>
        <div class="occ-lab">
          <span>Capacidad utilizada</span><span>${nf(ocu.porcentaje, 1)} %</span>
        </div>
      </div>`
    : '';
  const est = estadoDeLaRuta(ruta);

  // Encender y apagar Rutas se hace desde las referencias del mapa
  // (propuesta de 2026-11): la tarjeta solo dice si esta apagada.
  const off = apagada(ruta);
  // La cabecera de la tarjeta es el control: `tabindex` y `role` porque el
  // clic la selecciona y tiene que alcanzarse sin mouse. `aria-pressed`
  // porque seleccionar es un estado que permanece, no una accion puntual.
  // Elegida, la tarjeta se despliega con sus Paradas y sus acciones, como en
  // la propuesta; las demas siguen en la lista.
  return `
    <div class="rcard ${seleccionada ? 'sel' : ''} ${off ? 'off' : ''}"
         data-ruta="${ruta.id}" style="color:${colorDe(ruta)};--c:${colorDe(ruta)}">
      <div class="rcard-cabeza" data-elegir-ruta="${ruta.id}" role="button" tabindex="0" aria-pressed="${seleccionada}"
           aria-label="${off ? 'Encender' : seleccionada ? 'Cerrar' : 'Ver'} la Ruta ${numeroDe(ruta)}, ${ruta.vehiculo_id}">
        <div class="rcard-top">
          <span class="r-disco">${numeroDe(ruta)}</span>
          <span class="rname">${ruta.vehiculo_id}</span>
          <span class="stchip ${est.clase}">${off ? 'Oculta en el mapa' : est.texto}</span>
        </div>
        <div class="rdatos">
          <span><b>${ruta.paradas.length}</b> ${ruta.paradas.length === 1 ? 'parada' : 'paradas'}</span>
          <span><b>${nf(ruta.distancia_m / 1000, 1)}</b> km</span>
          <span><b>${ocu ? `${ruta.carga}</b>/${ocu.capacidad}` : `${ruta.carga}</b>`} bultos</span>
        </div>
        ${barra}
      </div>
      ${seleccionada ? detalleDeLaRuta(ruta) : ''}
    </div>`;
}

/** Lo que la tarjeta elegida despliega: alertas, Paradas con su hora,
    acciones y, plegado al pie, de donde sale la hora y los datos tecnicos. */
function detalleDeLaRuta(ruta) {
  const conEta = ruta.paradas.some((p) => tieneEta(ruta, p));
  return `
    ${bloqueDeAlertas(ruta)}
    ${paradasDeLaRuta(ruta)}
    ${bloqueDeEjecucion(ruta)}
    <div class="pie-de-ruta">
      ${bloqueDeRecorrido(ruta)}
      ${conEta ? `<details class="ayuda-breve"><summary>¿De dónde sale esta hora?</summary>
        <div>${ROTULO_ETA_A_LA_VISTA} ${ROTULO_ETA}</div></details>` : ''}
      <details class="ayuda-breve"><summary>Datos técnicos</summary>
        <div class="mono">Ruta ${escapar(ruta.id)}<br>Plan ${escapar(ruta.plan_id ?? '—')}</div></details>
    </div>`;
}

/* Una fila del Deposito: el cuadrado grafito con la D, como en el mapa. */
function filaDeposito(texto, rotulo, hora = '') {
  return `
    <div class="parada parada-deposito" title="${escapar(rotulo)}">
      <span class="p-disco p-deposito">D</span>
      <span class="p-lugar">${texto}</span>
      ${hora ? `<span class="p-hora">${hora}</span>` : ''}
    </div>`;
}

/* La hora estimada, en su propia columna a la derecha de la Parada, con el
   rango debajo (propuesta de 2026-11). */
function filaDeEta(ruta, parada) {
  if (!tieneEta(ruta, parada)) return '';
  const eta = etaEnHoras(ruta, parada);
  return `<span class="p-hora" title="Primera entrega estimada">${eta.hora}<small>${eta.desde}–${eta.hasta}</small></span>`;
}

/** Las Paradas de la Ruta elegida, en orden, del Deposito al Deposito. Cada
    una abre su ficha en el mapa. */
/* CU-05, paso 2 (2026-11-20A): la Parada en curso de una Ruta en ejecucion
   es la pendiente de menor secuencia, la misma definicion del avance del
   nucleo (`GET /v1/rutas/{id}/avance`). Una Ruta reemplazada no tiene. */
function paradaEnCurso(ruta) {
  if (todasPlanificadas(ruta) || ruta.reemplazada_por) return null;
  const pendiente = ruta.paradas.find((p) => p.ordenes.length && sinResultado(p));
  return pendiente ? pendiente.secuencia : null;
}

function paradasDeLaRuta(ruta) {
  const conEta = ruta.paradas.some((p) => tieneEta(ruta, p));
  const enCurso = paradaEnCurso(ruta);
  const filas = ruta.paradas.map((p) => `
    <div class="parada ${estado.parada === p.secuencia ? 'on' : ''}" data-parada="${p.secuencia}"
         role="button" tabindex="0" aria-label="Parada ${p.secuencia}, ${escapar(p.punto.rotulo.texto)}${p.secuencia === enCurso ? ', en curso' : ''}"
         title="${plural(p.ordenes.length, 'Orden', 'Órdenes')} · ${plural(p.demanda, 'bulto', 'bultos')}">
      <span class="p-disco">${p.secuencia}</span>
      <span class="p-lugar">${textoDeRotulo(p.punto.rotulo)}${p.secuencia === enCurso ? ' <span class="p-en-curso">en curso</span>' : ''}</span>
      ${filaDeEta(ruta, p)}
    </div>`).join('');
  return `
    <div class="paradas">
      ${filaDeposito('Salida del Depósito', ruta.deposito.rotulo.texto, conEta ? horaDelDia(ruta.hora_de_salida, 0) : '')}
      ${filas}
      ${filaDeposito('Retorno al Depósito', ruta.deposito.rotulo.texto)}
    </div>`;
}

/* ------------------------------------------------------------------ */
/* Recorrido estimado (B-243, H5; DFE-32)                              */
/* ------------------------------------------------------------------ */

/* En el pie de la ficha de la Ruta y no arriba: no empuja la espina, cuyas
   primeras Paradas tienen que verse sin desplazar (C8). Sin ETA, el control
   no aparece y se dice por que. */
function bloqueDeRecorrido(ruta) {
  if (!lineaTemporal(ruta)) return `<div class="recorrido-estimado"><p class="s-nota">${SIN_ETA}</p></div>`;
  return `<div class="recorrido-estimado">
      <button class="btn-ghost" type="button" data-recorrido="${ruta.id}">Ver recorrido estimado</button>
      <p class="s-nota">Un marcador recorre el trazado con las horas estimadas, acelerado. No es una posición real.</p>
    </div>`;
}

function iniciarRecorrido(idRuta) {
  const ruta = estado.rutas.find((r) => r.id === idRuta);
  const linea = ruta && lineaTemporal(ruta);
  if (!linea) return;
  rutaDelRecorrido = idRuta;
  capaDePosicion.reproducir(ruta, linea, {
    factor: FACTOR_POR_OMISION, color: colorDe(ruta),
  });
  pintarControlesDelRecorrido();
}

function detenerRecorrido() {
  capaDePosicion?.detener();
  rutaDelRecorrido = null;
  document.getElementById('controles-recorrido')?.remove();
}

function pintarControlesDelRecorrido() {
  document.getElementById('controles-recorrido')?.remove();
  const ruta = estado.rutas.find((r) => r.id === rutaDelRecorrido);
  if (!ruta) return;
  const opciones = FACTORES.map((f) =>
    `<option value="${f}"${f === FACTOR_POR_OMISION ? ' selected' : ''}>×${f}</option>`).join('');
  document.querySelector('.map').insertAdjacentHTML('beforeend', `
    <div class="controles-recorrido" id="controles-recorrido">
      <div class="eyebrow">Recorrido estimado · ${escapar(ruta.vehiculo_id)}</div>
      <div class="recorrido-estado" id="recorrido-estado" aria-live="off">Hora estimada —</div>
      <div class="acts-min">
        <label class="velocidad">Velocidad <select id="recorrido-factor">${opciones}</select></label>
        <button class="btn-ghost" type="button" id="recorrido-pausa">Pausar</button>
        <button class="btn-ghost" type="button" id="recorrido-reinicio">Reiniciar</button>
        <button class="btn-ghost" type="button" id="recorrido-cerrar">Cerrar</button>
      </div>
    </div>`);
  document.getElementById('recorrido-factor').addEventListener('change', (ev) => capaDePosicion.acelerar(Number(ev.target.value)));
  document.getElementById('recorrido-pausa').addEventListener('click', () => {
    if (capaDePosicion.estado?.pausado) capaDePosicion.reanudar(); else capaDePosicion.pausar();
  });
  document.getElementById('recorrido-reinicio').addEventListener('click', () => capaDePosicion.reiniciar());
  document.getElementById('recorrido-cerrar').addEventListener('click', detenerRecorrido);
}

/* Lo que cambia en cada cuadro: la hora estimada, la proxima Parada y el
   rotulo del boton de pausa. */
function pintarEstadoDelRecorrido(pos) {
  const caja = document.getElementById('recorrido-estado');
  if (!caja) return;
  caja.textContent = pos.terminado
    ? `Hora estimada ${pos.hora} · llegó a la última Parada`
    : `Hora estimada ${pos.hora} · próxima Parada ${pos.proxima}`;
  const pausa = document.getElementById('recorrido-pausa');
  const texto = pos.pausado ? (pos.terminado ? 'Repetir' : 'Seguir') : 'Pausar';
  if (pausa.textContent !== texto) pausa.textContent = texto;
}

/* ------------------------------------------------------------------ */
/* Ejecucion: despacho, descarte y resultado (DFE-14)                  */
/* ------------------------------------------------------------------ */

/* La Ruta no tiene estados (B-96): lo que se muestra es el estado de sus
   Ordenes, que es donde el contrato registra el despacho y los resultados.
   Ninguna frase dice "Ruta despachada" como estado de la Ruta. */
const ordenesDe = (ruta) => ruta.paradas.flatMap((p) => p.ordenes);

const todasPlanificadas = (ruta) => !ruta.reemplazada_por && ordenesDe(ruta).every((o) => o.estado === 'planificada');

function cuentaPorEstado(ordenes) {
  const cuentas = {};
  ordenes.forEach((o) => { cuentas[o.estado] = (cuentas[o.estado] ?? 0) + 1; });
  return Object.keys(ETIQUETA_ESTADO)
    .filter((e) => cuentas[e])
    .map((e) => `${cuentas[e]} ${ETIQUETA_ESTADO[e]}`)
    .join(' · ');
}

/**
 * Lo que se puede hacer con la Ruta seleccionada. Antes del despacho:
 * despacharla o descartarla. Despues: nada de eso, porque el contrato lo
 * rechaza (`ruta_en_ejecucion`), y se dice por que. Declarar un Incidente
 * vale siempre.
 */
function bloqueDeEjecucion(ruta) {
  const incidente = `<a class="link-incidente" href="incidentes.html?ruta=${encodeURIComponent(ruta.id)}#declarar">Declarar un Incidente sobre esta Ruta</a>`;
  if (todasPlanificadas(ruta)) {
    return `
      <div class="acciones-ruta">
        <button class="btn btn-osc" type="button" data-despachar="${ruta.id}">Despachar la Ruta</button>
        <button class="btn-ghost" type="button" data-descartar-ruta="${ruta.id}">Descartar</button>
      </div>
      ${incidente}`;
  }
  if (ruta.reemplazada_por) {
    // CU-07, paso 6: la Ruta quedo Reemplazada (estado terminal del glosario)
    // y conserva sus Paradas cumplidas; lo pendiente sigue en la nueva.
    const cumplidas = ruta.reemplazada_por.paradas_cumplidas ?? [];
    return `
    <div class="ejecucion-ruta">
      <div class="estado-ordenes">Ruta reemplazada a las ${horaDeLaOperacion(ruta.reemplazada_por.instante)} por un recálculo
        (${escapar(ruta.reemplazada_por.motivo)}). Conserva ${cumplidas.length ? `las Paradas cumplidas ${enumeracion(cumplidas)}` : 'su registro; no tenía Paradas cumplidas'};
        lo pendiente sigue en la Ruta vigente.</div>
      <div class="acciones-ruta">
        <button class="btn-ghost" type="button" data-elegir-ruta="${ruta.reemplazada_por.ruta_nueva}">Ver la Ruta vigente</button>
      </div>
    </div>`;
  }
  const origen = ruta.reemplaza_a
    ? `<div class="estado-ordenes">Nació de un recálculo de las ${horaDeLaOperacion(ruta.reemplaza_a.instante)}: sale de donde estaba el Vehículo.</div>`
    : '';
  return `
    <div class="ejecucion-ruta">
      <div class="estado-ordenes">Despacho registrado. Órdenes: ${cuentaPorEstado(ordenesDe(ruta))}.
        Ya no se descarta ni se le quitan Órdenes. El resultado de cada Parada se declara en su ficha.</div>
      ${origen}
      ${ordenDeDeclaracion(ruta)}
      <div class="estado-ordenes" id="posicion-informada" aria-live="polite">Consultando la última posición del Vehículo…</div>
      <div class="acciones-ruta">
        <button class="btn-ghost" type="button" data-replanificar="${ruta.id}">Replanificar por un Incidente</button>
      </div>
    </div>
    ${incidente}`;
}

/* ------------------------------------------------------------------ */
/* Replanificacion ante un Incidente (CU-07, pasos 4 a 6 y 5a)          */
/* ------------------------------------------------------------------ */

function listaDelPlan(paradas) {
  return `<ol>${paradas.map((p) => `<li>Parada ${p.secuencia_vigente} · ${escapar(textoDeRotulo(p.punto.rotulo))}</li>`).join('')}</ol>`;
}

const kilometros = (m) => `${(m / 1000).toLocaleString('es-AR', { maximumFractionDigits: 2 })} km`;

/** Pide la propuesta por el Incidente mas reciente que afecta a la Ruta, la
    muestra con su diferencia contra el plan vigente y deja aprobarla o
    rechazarla. Sin Incidente, lo dice: el recalculo responde a uno. */
async function replanificar(idRuta) {
  let incidentes;
  try {
    incidentes = (await api.listarIncidentes()).filter((i) => (i.rutas_afectadas ?? []).includes(idRuta));
  } catch (e) {
    modalDeAccionRechazada(e, 'No se pudieron leer los Incidentes');
    return;
  }
  if (!incidentes.length) {
    abrirModal({
      eyebrow: 'replanificar', tono: 'informativo', titulo: 'Primero hay que declarar el Incidente',
      cuerpo: '<p>El recálculo responde a un Incidente que afecta a esta Ruta. Declaralo con el enlace de la Ruta y volvé a pedirlo.</p>',
    });
    return;
  }
  const incidente = incidentes[incidentes.length - 1];
  let p;
  try {
    p = await api.proponerReemplazo(incidente.id, idRuta);
  } catch (e) {
    modalDeAccionRechazada(e, 'No hay un plan alternativo');
    return;
  }
  const origen = p.origen.fuente === 'telemetria'
    ? `la posición del Vehículo de hace ${Math.round(p.origen.antiguedad_s)} s`
    : 'la Parada en curso (no hay una posición reciente del Vehículo)';
  let diferencia = 'no cambia la distancia';
  if (p.diferencia_m < 0) diferencia = `acorta ${kilometros(-p.diferencia_m)}`;
  else if (p.diferencia_m > 0) diferencia = `alarga ${kilometros(p.diferencia_m)}`;
  const cambian = p.paradas_que_cambian_de_lugar.length
    ? `Cambian de lugar las Paradas ${enumeracion(p.paradas_que_cambian_de_lugar)}.`
    : 'El orden no cambia.';
  abrirModal({
    eyebrow: 'replanificar', tono: 'informativo',
    titulo: `Plan propuesto: ${diferencia}`,
    cuerpo: `
      <p>Recalculado desde ${origen}, con la Parada en curso (${p.parada_en_curso}) primera y la vuelta al Depósito.
        Vigente: ${kilometros(p.distancia_vigente_m)}; propuesto: ${kilometros(p.distancia_propuesta_m)} (${escapar(p.metodo)}). ${cambian}</p>
      <div class="comparacion-de-planes">
        <div><div class="h">Plan vigente</div>${listaDelPlan(p.plan_vigente)}</div>
        <div><div class="h">Plan propuesto</div>${listaDelPlan(p.plan_propuesto)}</div>
      </div>
      <p class="s-nota">Aprobar reemplaza la Ruta: la vigente queda Reemplazada con lo cumplido y lo pendiente pasa a una Ruta nueva del mismo Vehículo, que el transportista ve en su aplicación. Rechazar conserva el plan; el Incidente queda registrado igual.</p>`,
    acciones: [
      { texto: 'Aprobar el plan', principal: true, alPulsar: () => resolverPropuesta(p, true) },
      { texto: 'Rechazar y conservar el vigente', alPulsar: () => resolverPropuesta(p, false) },
    ],
  });
}

async function resolverPropuesta(propuesta, aprobar) {
  try {
    if (aprobar) {
      const r = await api.aprobarPropuesta(propuesta.id);
      avisoBreve('Ruta reemplazada: el Vehículo sigue con el plan nuevo.');
      await recargarConservando();
      seleccionar(r.ruta_nueva);
    } else {
      await api.rechazarPropuesta(propuesta.id);
      avisoBreve('Se conserva el plan vigente. El Incidente queda registrado.');
    }
  } catch (e) {
    modalDeAccionRechazada(e, aprobar ? 'No se pudo reemplazar la Ruta' : 'No se pudo rechazar la propuesta');
  }
}

/* La ultima posicion se vuelve a consultar cada 10 s mientras la Ruta
   despachada sigue elegida (B-273, punto 2): la aplicacion envia cada 15 s. */
const INTERVALO_DE_CONSULTA_DE_POSICION_S = 10;
/* Mas vieja que esto, la posicion se rotula fuera del intervalo esperado
   (CU-05, 2a): la aplicacion captura cada 10 s y envia cada 15 s; es la misma
   tolerancia con que el nucleo acepta una posicion para replanificar. */
const ANTIGUEDAD_DE_POSICION_VIGENTE_S = 120;
let consultaDePosicion = null;

/** Consulta la posicion de la Ruta elegida y la repite; sin Ruta, deja de
    consultar. */
function seguirPosicion(ruta) {
  clearInterval(consultaDePosicion);
  consultaDePosicion = null;
  if (!ruta) return;
  cargarPosicion(ruta);
  consultaDePosicion = setInterval(() => {
    if (rutaSeleccionada()?.id !== ruta.id) {
      clearInterval(consultaDePosicion);
      consultaDePosicion = null;
      return;
    }
    cargarPosicion(ruta);
  }, INTERVALO_DE_CONSULTA_DE_POSICION_S * 1000);
}

/** La ultima posicion que informo el Vehiculo de una Ruta despachada, del
    servicio de telemetria (FR-17; ARQ-53; DFE-38). Si telemetria no
    responde, la Ruta se sigue viendo y se avisa. Nada se simula. */
async function cargarPosicion(ruta) {
  const caja = () => document.getElementById('posicion-informada');
  const escribir = (texto) => { if (caja()) caja().textContent = texto; };
  try {
    const p = await api.ultimaPosicion(ruta.id);
    if (rutaSeleccionada()?.id !== ruta.id) return;
    const minutos = Math.round(p.antiguedad_s / 60);
    const hace = minutos < 1 ? 'hace menos de un minuto' : `hace ${minutos} min`;
    // CU-05, flujo 2a (2026-11-20A): fuera del intervalo esperado se dice, en
    // lugar de presentar la posicion como vigente.
    const vieja = p.antiguedad_s > ANTIGUEDAD_DE_POSICION_VIGENTE_S;
    escribir(vieja
      ? `Sin reporte en el intervalo esperado: la última posición es de ${hace} y puede no ser la actual.`
      : `Última posición informada ${hace}.`);
    // El recorrido estimado, si esta corriendo, no se interrumpe.
    if (!rutaDelRecorrido) {
      capaDePosicion.mostrarPosicion({ lat: p.latitud, lon: p.longitud, origen: 'telemetria', color: colorDe(ruta),
        detalle: vieja ? `desactualizada, ${hace}` : hace });
    }
  } catch (e) {
    if (e instanceof ErrorDeApi && e.codigo === 'sin_posiciones') {
      escribir('El Vehículo todavía no informó su posición.');
    } else {
      escribir('La telemetría no responde: la Ruta se ve igual, sin la posición del Vehículo.');
    }
  }
}

/* ------------------------------------------------------------------ */
/* Quien declaro y en que orden (ARQ-65; 2026-10-20C, B; DFE-27)        */
/* ------------------------------------------------------------------ */

const claveDeDeclaracion = (idRuta, secuencia) => `${idRuta}:${secuencia}`;
const declarada = (parada) => parada.ordenes.length > 0
  && parada.ordenes.every((o) => o.estado === 'completada' || o.estado === 'fallida');
const sinResultado = (parada) => parada.ordenes.every((o) => o.estado === 'en_transito');

/* "1", "1 y 2", "1, 2 y 4". */
function enumeracion(numeros) {
  if (numeros.length <= 1) return numeros.join('');
  return `${numeros.slice(0, -1).join(', ')} y ${numeros[numeros.length - 1]}`;
}

/**
 * Lee quien declaro cada Parada con resultado de la Ruta, con la consulta de
 * una de sus Ordenes (todas comparten la declaracion). Solo pide las que
 * faltan y, si encontro alguna, vuelve a pintar el panel y la ficha.
 */
async function cargarDeclaraciones(ruta) {
  const faltantes = ruta.paradas.filter(
    (p) => declarada(p) && !estado.declaraciones.has(claveDeDeclaracion(ruta.id, p.secuencia)));
  if (!faltantes.length) return;
  const leidas = await Promise.all(faltantes.map(async (p) => {
    try {
      const transicion = transicionDelResultado(await api.consultarOrden(p.ordenes[0].id));
      return transicion ? [p.secuencia, transicion] : null;
    } catch {
      return null;   // sin el dato no se afirma nada: la ficha no lo muestra
    }
  }));
  let alguna = false;
  leidas.filter(Boolean).forEach(([secuencia, t]) => {
    estado.declaraciones.set(claveDeDeclaracion(ruta.id, secuencia), {
      resultado: t.estado_nuevo, instante: t.marca_temporal, declarante: t.declarante,
    });
    alguna = true;
  });
  if (alguna && estado.seleccion === ruta.id) {
    pintarPanel();
    pintarFicha();
  }
}

/* El orden en que se declararon los resultados, nunca "orden real de
   ejecucion" (ARQ-65, G-6): la mesa de control puede declarar por lotes. */
function ordenDeDeclaracion(ruta) {
  const con = ruta.paradas
    .map((p) => ({ p, d: estado.declaraciones.get(claveDeDeclaracion(ruta.id, p.secuencia)) }))
    .filter(({ d }) => d)
    .sort((a, b) => a.d.instante.localeCompare(b.d.instante));
  const pendientes = ruta.paradas.filter(sinResultado).map((p) => p.secuencia);
  if (!con.length) return '';
  const lista = con.map(({ p, d }) =>
    `<li>Parada ${p.secuencia} · ${ETIQUETA_ESTADO[d.resultado]} · ${horaDeLaOperacion(d.instante)}</li>`).join('');
  const desvio = desvioAcumulado(ruta, con);
  return `
      <div class="orden-declaracion">
        <div class="h">Orden de declaración</div>
        <ol>${lista}</ol>
        ${pendientes.length ? `<div class="s-nota">Sin resultado: ${pendientes.length === 1 ? 'Parada' : 'Paradas'} ${enumeracion(pendientes)}.</div>` : ''}
        ${desvio}
        <div class="s-nota">Es el orden en que se declararon los resultados, no necesariamente el de las entregas.</div>
      </div>`;
}

/* CU-05, paso 3 (2026-11-20A): el avance real contra la secuencia planificada.
   Dos medidas, ninguna inventada: cuantas Paradas se declararon en otro lugar
   que el del plan, y cuanto despues (o antes) de su ETA estimado se declaro la
   ultima. El ETA es el del plan, con su salida a la hora publicada; la hora
   de la operacion es la de Buenos Aires (UTC-3, sin horario de verano). */
const DESFASE_DE_LA_OPERACION = '-03:00';

function desvioAcumulado(ruta, declaradas) {
  if (!declaradas.length) return '';
  const enOrden = declaradas.map(({ p }) => p.secuencia).sort((a, b) => a - b);
  const fuera = declaradas.filter(({ p }, i) => p.secuencia !== enOrden[i]).length;
  const partes = [fuera
    ? `${plural(fuera, 'Parada declarada', 'Paradas declaradas')} en otro lugar que el del plan`
    : 'las Paradas se declararon en el orden del plan'];
  const ultima = declaradas[declaradas.length - 1];
  if (ruta.hora_de_salida && tieneEta(ruta, ultima.p)) {
    const salida = new Date(`${ruta.fecha_planificacion}T${ruta.hora_de_salida}${DESFASE_DE_LA_OPERACION}`);
    const estimada = salida.getTime() + ultima.p.eta_estimado_s * 1000;
    const minutos = Math.round((new Date(ultima.d.instante).getTime() - estimada) / 60000);
    partes.push(minutos === 0
      ? `la última declarada (Parada ${ultima.p.secuencia}) coincide con su ETA estimado`
      : `la última declarada (Parada ${ultima.p.secuencia}), ${Math.abs(minutos)} min ${minutos > 0 ? 'después' : 'antes'} de su ETA estimado`);
  }
  return `<div class="s-nota">Desvío acumulado: ${partes.join('; ')}.</div>`;
}

/** Releer el plan sin perder la Ruta ni la Parada que se estaban mirando. */
async function recargarConservando() {
  // Dos recargas pueden solaparse (declarar dos Paradas seguidas), y el
  // operador puede elegir otra Parada mientras una viaja. Solo la ultima
  // recarga restaura, y restaura la eleccion vigente al terminar, no la de
  // cuando empezo (2026-10-20A, P22 y N25; 2026-10-20B, C1; DFE-25).
  const turno = ++recargaVigente;
  const versionAlEmpezar = versionDeEleccion;
  const guardada = { seleccion: estado.seleccion, parada: estado.parada };
  await cargar();
  if (turno !== recargaVigente) return;
  const destino = versionDeEleccion !== versionAlEmpezar ? ultimaEleccion : guardada;
  if (destino.seleccion && estado.rutas.some((r) => r.id === destino.seleccion)) {
    estado.seleccion = destino.seleccion;
    estado.parada = destino.parada;
    repintar();
  }
}

function modalDeAccionRechazada(e, titulo) {
  if (e instanceof ApiInalcanzable) { modalSinConexion(); return; }
  if (!(e instanceof ErrorDeApi)) throw e;
  abrirModal({ eyebrow: e.codigo, titulo, cuerpo: `<p>${escapar(e.message)}</p>` });
}

async function despachar(idRuta) {
  const ruta = estado.rutas.find((r) => r.id === idRuta);
  if (!ruta) return;
  const n = ordenesDe(ruta).length;
  // El transportista es opcional (D-C2; DFE-37): sin elegir, el despacho de
  // siempre. Si la lista no se puede consultar, se despacha sin el.
  const transportistas = await api.transportistas().catch(() => []);
  const selector = transportistas.length
    ? `<div class="campo"><label for="transportista-del-despacho">Transportista <span class="note-s">(opcional)</span></label>
        <select id="transportista-del-despacho"><option value="">Sin asignar</option>${transportistas
          .map((t) => `<option value="${escapar(t.id)}">${escapar(t.nombre_visible)}</option>`).join('')}</select>
        <div class="s-nota">El transportista elegido ve esta Ruta en su aplicación.</div></div>`
    : '<p class="s-nota">No hay transportistas con cuenta: la Ruta se despacha sin asignar.</p>';
  const pedido = confirmar({
    titulo: `¿Despachar la Ruta de ${ruta.vehiculo_id}?`,
    cuerpo: `<p>Sus ${n === 1 ? 'Orden pasa' : `${n} Órdenes pasan`} de Planificada a <b>En tránsito</b>.
      Una Ruta despachada ya no se puede descartar ni se le pueden quitar Órdenes.</p>${selector}`,
    accion: 'Despachar',
  });
  let elegido = '';
  document.getElementById('transportista-del-despacho')
    ?.addEventListener('change', (ev) => { elegido = ev.target.value; });
  const confirmado = await pedido;
  if (!confirmado) return;
  try {
    await api.despacharRuta(idRuta, { transportista: elegido || null });
    avisoBreve(`Ruta de ${ruta.vehiculo_id} despachada: ${n === 1 ? 'su Orden está' : `sus ${n} Órdenes están`} en tránsito.`);
    await recargarConservando();
  } catch (e) {
    modalDeAccionRechazada(e, 'No se pudo despachar la Ruta');
  }
}

async function descartarRuta(idRuta) {
  const ruta = estado.rutas.find((r) => r.id === idRuta);
  if (!ruta) return;
  const n = ordenesDe(ruta).length;
  const confirmado = await confirmar({
    eyebrow: 'acción que no se deshace',
    titulo: `¿Descartar la Ruta de ${ruta.vehiculo_id}?`,
    cuerpo: `<p>La Ruta se elimina y sus ${n === 1 ? 'Orden vuelve' : `${n} Órdenes vuelven`} a
      <b>Creada</b>, listas para planificar de nuevo. El Vehículo queda libre para otro Plan de
      la jornada. Las demás Rutas no cambian.</p>`,
    accion: 'Descartar la Ruta',
    peligro: true,
  });
  if (!confirmado) return;
  try {
    const r = await api.invalidarRuta(idRuta);
    const liberadas = r?.ordenes_liberadas?.length ?? n;
    avisoBreve(`Ruta descartada: ${liberadas === 1 ? 'una Orden volvió' : `${liberadas} Órdenes volvieron`} a Creada.`);
    estado.seleccion = null;
    estado.parada = null;
    await cargar();
  } catch (e) {
    modalDeAccionRechazada(e, 'No se pudo descartar la Ruta');
  }
}

/* El resultado que se declara y en que estado quedan las Ordenes. */
const RESULTADOS = {
  cumplida: { texto: 'Cumplida', estado: 'Completada' },
  fallida: { texto: 'Fallida', estado: 'Fallida' },
};

/* Estado en que queda cada Orden de la Parada segun el resultado declarado
   (glosario: Completada o Fallida). */
const ESTADO_DE_LA_ORDEN_POR_RESULTADO = { cumplida: 'completada', fallida: 'fallida' };

async function declararResultado(idRuta, secuencia, resultado) {
  const ruta = estado.rutas.find((r) => r.id === idRuta);
  const parada = ruta?.paradas.find((p) => p.secuencia === secuencia);
  if (!parada) return;
  const n = parada.ordenes.length;
  // Fuera de orden se admite, con aviso (ARQ-65): la operacion real saltea Paradas.
  const anteriores = ruta.paradas.filter((p) => p.secuencia < secuencia && sinResultado(p)).map((p) => p.secuencia);
  const fueraDeOrden = anteriores.length
    ? `<p><b>${anteriores.length === 1 ? `La Parada ${anteriores[0]} todavía no tiene resultado`
      : `Las Paradas ${enumeracion(anteriores)} todavía no tienen resultado`}; vas a declarar la ${secuencia}
      fuera del orden planificado.</b></p>`
    : '';
  const confirmado = await confirmar({
    titulo: `¿Parada ${secuencia} ${RESULTADOS[resultado].texto.toLowerCase()}?`,
    cuerpo: `${fueraDeOrden}<p>${n === 1 ? 'Su Orden pasa' : `Sus ${n} Órdenes pasan`} de En tránsito a
      <b>${RESULTADOS[resultado].estado}</b>. El resultado de una Parada se declara una sola vez.</p>
      <p>Vas a declarar el resultado como operador (mesa de control). Cuando exista la aplicación,
      lo declara el transportista.</p>`,
    accion: `Declarar ${RESULTADOS[resultado].texto.toLowerCase()}`,
    peligro: resultado === 'fallida',
  });
  if (!confirmado) return;
  // Mientras se declara, los botones de la ficha quedan inactivos (C1).
  document.querySelectorAll('#ficha [data-resultado]').forEach((b) => { b.disabled = true; });
  try {
    await api.declararResultado(idRuta, secuencia, resultado);
    // La ficha muestra el resultado con la respuesta del pedido, sin esperar
    // la recarga: antes quedaba un rato en "En transito" con los botones
    // activos (2026-10-20A, P21).
    parada.ordenes.forEach((o) => { o.estado = ESTADO_DE_LA_ORDEN_POR_RESULTADO[resultado] ?? o.estado; });
    if (estado.seleccion === idRuta && estado.parada === secuencia) pintarFicha();
    avisoBreve(`Parada ${secuencia}: ${RESULTADOS[resultado].estado.toLowerCase()}.`);
    await recargarConservando();
  } catch (e) {
    document.querySelectorAll('#ficha [data-resultado]').forEach((b) => { b.disabled = false; });
    modalDeAccionRechazada(e, 'No se pudo declarar el resultado');
  }
}

/* ------------------------------------------------------------------ */
/* Ficha de Parada (Estado 3)                                          */
/* ------------------------------------------------------------------ */

function filaDeOrden(ordenEnParada) {
  // El estado vigente viene dentro de la propia Parada. Antes hacia falta una
  // consulta aparte al listado de Ordenes porque el detalle de la Ruta traia
  // solo id y demanda; deducir "Planificada" de que la Orden apareciera en
  // una Ruta habria sido afirmar un dato que la respuesta no traia. Ahora si
  // lo trae. Si faltara, se declara en vez de suponerlo.
  const etiqueta = ordenEnParada.estado
    ? (ETIQUETA_ESTADO[ordenEnParada.estado] ?? ordenEnParada.estado)
    : 'sin dato';
  return `
    <div class="orow">
      <span>${ordenEnParada.id}</span>
      <span>${plural(ordenEnParada.demanda, 'bulto', 'bultos')}</span>
      <span class="st st-${ordenEnParada.estado ?? 'sin-dato'}">${etiqueta}</span>
    </div>`;
}

/* La seccion de la ficha con el ETA. Sin ETA no hay seccion: no se muestra
   nada en su lugar. */
function seccionDeEta(ruta, parada) {
  if (!tieneEta(ruta, parada)) return '';
  const eta = etaEnHoras(ruta, parada);
  return `
      <div class="sect">
        <div class="h">Primera entrega estimada</div>
        <div class="eta-ficha"><b>${eta.hora}</b> · entre ${eta.desde} y ${eta.hasta}</div>
        <div class="s-nota">${ROTULO_ETA_BREVE}</div>
      </div>`;
}

/* El resultado de la Parada (CU-06 sin telemetria) se declara cuando sus
   Ordenes estan En transito: antes del despacho no corresponde y despues de
   declarado no se repite (`resultado_no_admitido`). */
function seccionDeResultado(parada) {
  if (!parada.ordenes.every((o) => o.estado === 'en_transito')) return '';
  return `
      <div class="sect">
        <div class="h">Resultado de la Parada</div>
        <div class="resultado-parada">
          <button class="btn" type="button" data-resultado="cumplida">Cumplida</button>
          <button class="btn-ghost" type="button" data-resultado="fallida">Fallida</button>
        </div>
        <div class="s-nota">Lo registra el operador mientras la aplicación del transportista
          no está construida.</div>
      </div>`;
}

/* CU-06, flujo 1a (2026-11-20A): una Orden Fallida se reprograma (vuelve a
   ser planificable) o se devuelve al Deposito (terminal). */
function seccionTrasLaFalla(parada) {
  const fallidas = parada.ordenes.filter((o) => o.estado === 'fallida');
  if (!fallidas.length) return '';
  const filas = fallidas.map((o) => `
        <div class="resultado-parada">
          <span>Orden ${escapar(o.id)}</span>
          <button class="btn-ghost" type="button" data-reprogramar="${o.id}">Reprogramar</button>
          <button class="btn-ghost" type="button" data-devolver="${o.id}">Devolver al Depósito</button>
        </div>`).join('');
  return `
      <div class="sect">
        <div class="h">Después del intento fallido</div>
        ${filas}
        <div class="s-nota">Reprogramar la deja lista para la próxima planificación; devolverla la cierra.</div>
      </div>`;
}

async function trasLaFalla(idOrden, accion) {
  try {
    if (accion === 'reprogramar') await api.reprogramarOrden(idOrden);
    else await api.devolverOrden(idOrden);
    avisoBreve(accion === 'reprogramar'
      ? `Orden ${idOrden} reprogramada: entra en la próxima planificación.`
      : `Orden ${idOrden} devuelta al Depósito.`);
    await recargarConservando();
  } catch (e) {
    modalDeAccionRechazada(e, accion === 'reprogramar' ? 'No se pudo reprogramar la Orden' : 'No se pudo devolver la Orden');
  }
}

/* Quien declaro el resultado, si la Parada ya lo tiene. */
function seccionDelDeclarante(ruta, parada) {
  if (!declarada(parada)) return '';
  const d = estado.declaraciones.get(claveDeDeclaracion(ruta.id, parada.secuencia));
  if (!d) return '';
  return `
      <div class="sect">
        <div class="h">Resultado declarado</div>
        <div class="declarante"><b>${ETIQUETA_ESTADO[d.resultado]}</b> · a las ${horaDeLaOperacion(d.instante)}</div>
        <div class="s-nota">Lo declaró ${escapar(textoDelDeclarante(d.declarante))}.</div>
      </div>`;
}

function fichaDeParada(ruta, parada) {
  const color = colorDe(ruta);
  const { nodo, lat, lon, rotulo } = parada.punto;

  return `
    <div class="stopcard" id="ficha">
      <div class="hd">
        <div class="disco" style="background:${color}">${parada.secuencia}</div>
        <div>
          <div class="eyebrow">Parada ${parada.secuencia} de ${ruta.paradas.length}</div>
          <h3>${textoDeRotulo(rotulo)}</h3>
          <details class="ayuda-breve datos-tecnicos"><summary>Datos técnicos</summary>
            <div class="coords">nodo ${nodo}<br>${nf(lat, 4)} / ${nf(lon, 4)}</div></details>
        </div>
        <button class="cerrar" id="cerrar-ficha" title="Cerrar la ficha" aria-label="Cerrar la ficha de la Parada">✕</button>
      </div>

      <div class="sect">
        <div class="h">Órdenes que agrupa (${parada.ordenes.length})</div>
        ${parada.ordenes.map(filaDeOrden).join('')}
        <div class="tot">
          <span>Demanda de la Parada</span>
          <b>${plural(parada.demanda, 'bulto', 'bultos')}</b>
        </div>
      </div>

      ${seccionDeEta(ruta, parada)}

      ${seccionDeResultado(parada)}

      ${seccionDelDeclarante(ruta, parada)}

      ${seccionTrasLaFalla(parada)}

      <div class="ft">
        <span class="lline" style="width:14px;height:4px;border-radius:2px;background:${color}"></span>
        Ruta ${ruta.vehiculo_id}
      </div>
    </div>`;
}

function pintarFicha() {
  document.getElementById('ficha')?.remove();
  const ruta = rutaSeleccionada();
  if (!ruta || estado.parada === null) return;

  const parada = ruta.paradas.find((p) => p.secuencia === estado.parada);
  if (!parada) return;

  document.querySelector('.map').insertAdjacentHTML('beforeend', fichaDeParada(ruta, parada));
  document.getElementById('cerrar-ficha').addEventListener('click', cerrarParada);
  document.querySelectorAll('#ficha [data-resultado]').forEach((boton) => {
    boton.addEventListener('click', () => declararResultado(ruta.id, parada.secuencia, boton.dataset.resultado));
  });
  document.querySelectorAll('#ficha [data-reprogramar]').forEach((boton) => {
    boton.addEventListener('click', () => trasLaFalla(boton.dataset.reprogramar, 'reprogramar'));
  });
  document.querySelectorAll('#ficha [data-devolver]').forEach((boton) => {
    boton.addEventListener('click', () => trasLaFalla(boton.dataset.devolver, 'devolver'));
  });
}

/** Las alertas de la jornada, para mostrar la de Incidente en la ficha de la
    Ruta. Si la consulta falla, la ficha se ve igual, sin alertas. */
async function cargarAlertas(generacion) {
  try {
    const { alertas } = await api.alertas({ jornada: estado.fecha });
    if (generacion !== generacionDeCarga) return;
    estado.alertas = alertas;
    if (rutaSeleccionada()) pintarPanel();
  } catch {
    estado.alertas = [];
  }
}

/** La alerta de Incidente de la Ruta elegida, si la hay (CU-08). */
function bloqueDeAlertas(ruta) {
  const deLaRuta = estado.alertas.filter((a) => a.tipo === 'incidente_en_ruta' && a.ruta === ruta.id);
  if (!deLaRuta.length) return '';
  return deLaRuta.map((a) => `
    <div class="aviso-plan" role="status">
      <div class="t">Alerta: Incidente en esta Ruta</div>
      <div class="s">${escapar(a.motivo)}</div>
      <div class="link"><a href="alertas.html?jornada=${encodeURIComponent(estado.fecha)}">Ver las alertas de la jornada</a></div>
    </div>`).join('');
}

function pintarPanel() {
  const lista = document.getElementById('rail-list');
  const cabecera = document.getElementById('rail-head');
  const pie = document.getElementById('rail-foot');
  const sel = rutaSeleccionada();
  cabecera.hidden = false;
  pie.hidden = false;

  // El titulo dice cuantas hay; si alguna esta apagada, se declara y se
  // ofrece reponerlas. Ocultar el filtro haria que los totales del pie
  // parecieran no coincidir con lo que se ve.
  const visibles = encendidas().length;
  const total = estado.rutas.length;
  document.getElementById('rail-titulo').textContent =
    visibles === total
      ? `Rutas publicadas · ${total}`
      : `Rutas publicadas · ${visibles} de ${total} visibles`;
  pintarEstadoDelPlan();

  // "Ver todas" vuelve a la vista de todas las Rutas: cierra la elegida y
  // enciende las apagadas. Solo aparece cuando hay algo que deshacer.
  document.getElementById('rail-filtro').innerHTML = visibles === total && !sel
    ? ''
    : `<button class="ver-todas" id="encender-todas" type="button">Ver todas</button>`;

  // La lista conserva su desplazamiento al elegir: la tarjeta se despliega
  // donde esta, sin saltar al principio.
  const desplazamiento = lista.scrollTop;
  lista.innerHTML = estado.rutas.map((r) => tarjeta(r, { seleccionada: r.id === sel?.id })).join('');
  lista.scrollTop = desplazamiento;
  if (sel) lista.querySelector('.rcard.sel')?.scrollIntoView({ block: 'nearest' });
  seguirPosicion(sel && !todasPlanificadas(sel) && !sel.reemplazada_por ? sel : null);

  // El pie desglosa lo que el encabezado resume: Ordenes, que el encabezado
  // no muestra porque la demanda ya las representa en bultos. Una sola fuente
  // de verdad para las cifras, `totalesDelPlan`.
  const t = totalesDelPlan();
  pie.innerHTML =
    `${t.ordenes} Órdenes <span class="sep">·</span> ${t.paradas} Paradas ` +
    `<span class="sep">·</span> ${t.bultos} bultos<br>${km(t.metros)}` +
    (visibles === total ? '' : '<br><span class="sep">totales del plan completo</span>');
}

/**
 * Totales del Plan de Distribucion.
 *
 * Se derivan sumando el listado de `GET /v1/rutas`, que es lo unico
 * consultable. Los totales **autoritativos** los calcula el backend y los
 * devuelve en la respuesta de `POST /v1/planificaciones`
 * --`distancia_total_m`, `demanda_total`, `capacidad_total`-- pero solo ahi:
 * no hay recurso que permita volver a consultarlos, ni identificador de
 * corrida con el que pedirlos. Es **RB-12**, propuesto y no implementado.
 *
 * Sumar el listado no es fabricar el dato: cada Ruta publica su distancia, su
 * carga y sus cuentas, y la suma de un conjunto publicado es derivacion, no
 * invencion. Lo que no se puede es afirmar que ese conjunto **es** una corrida:
 * hoy es "las Rutas de esta jornada", que con un plan por jornada coincide.
 *
 * Se cuenta sobre TODAS las Rutas y no sobre las encendidas: apagar es filtro
 * de lectura y no exclusion del plan.
 */
function totalesDelPlan() {
  const rutas = estado.rutas;
  return {
    rutas: rutas.length,
    paradas: rutas.reduce((a, r) => a + r.paradas.length, 0),
    ordenes: rutas.reduce((a, r) => a + r.paradas.reduce((b, p) => b + p.ordenes.length, 0), 0),
    bultos: rutas.reduce((a, r) => a + r.carga, 0),
    metros: rutas.reduce((a, r) => a + r.distancia_m, 0),
    fecha: rutas[0]?.fecha_planificacion ?? null,
  };
}

/**
 * Encabezado del Plan de Distribucion: la corrida como una sola pieza.
 *
 * Sin pliegue, por decision: con un plan vigente por jornada, plegarlo
 * esconderia todo el contenido para ahorrar tres filas, y agregaria un cuarto
 * estado que PUX-18 no contempla --reposo, Ruta seleccionada, Parada
 * seleccionada--. Vive en su propio contenedor para que el pliegue se pueda
 * incorporar despues sin rehacer el riel.
 *
 * Sin Rutas no dibuja totales en cero: un plan vacio no existe, lo que falta
 * es el plan. De eso se ocupa el estado vacio (PUX-33).
 */
/* Aviso que dejo la planificacion (capacidad sin utilizar), mostrado sobre
   el plan sin bloquear (2026-10-20B, F1.4; DFE-23). Se lee una vez. */
const CLAVE_AVISO_DEL_PLAN = 'hoppit.aviso_del_plan';
let avisoDelPlan = null;
try {
  avisoDelPlan = JSON.parse(sessionStorage.getItem(CLAVE_AVISO_DEL_PLAN) ?? 'null');
  sessionStorage.removeItem(CLAVE_AVISO_DEL_PLAN);
} catch { avisoDelPlan = null; }

function bloqueDelAvisoDelPlan() {
  if (!avisoDelPlan || avisoDelPlan.fecha !== estado.fecha) return '';
  return `
    <div class="aviso-amb" role="status">
      ${icono('info')}
      <div><b>${escapar(avisoDelPlan.titulo)}</b> ${escapar(avisoDelPlan.texto)}
        <button class="link" type="button" id="cerrar-aviso-plan">Entendido</button></div>
    </div>`;
}

function pintarEncabezadoDelPlan() {
  const caja = document.getElementById('plan-head');
  if (!estado.rutas.length) { caja.hidden = true; caja.innerHTML = ''; return; }

  const t = totalesDelPlan();
  caja.hidden = false;
  // Las cifras del plan quedan a la vista tambien con una Ruta elegida, como
  // en la propuesta de 2026-11: la tarjeta elegida se despliega en la lista y
  // ya no compite por el alto con una espina aparte.
  const despachadas = estado.rutas.filter((r) => !todasPlanificadas(r)).length;
  caja.innerHTML = `
    <div class="plan-top">
      <div class="eyebrow">Plan de Distribución</div>
      <span class="stchip ${despachadas ? 'st-en_transito' : 'st-planificada'}">${despachadas ? 'En ejecución' : 'Planificado'}</span>
    </div>
    <div class="plan-fecha">${t.fecha ? fechaLarga(t.fecha) : '—'}</div>
    <div class="plan-cifras">
      <div><span class="k">Rutas</span><span class="v">${t.rutas}</span></div>
      <div><span class="k">Paradas</span><span class="v">${t.paradas}</span></div>
      <div><span class="k">Bultos</span><span class="v">${t.bultos}</span></div>
      <div><span class="k">Km</span><span class="v">${nf(t.metros / 1000, 1)}</span></div>
    </div>
    ${bloqueDelAvisoDelPlan()}`;
  document.getElementById('cerrar-aviso-plan')?.addEventListener('click', () => {
    avisoDelPlan = null;
    pintarEncabezadoDelPlan();
  });
}

/**
 * Control para volver a ver la construccion.
 *
 * En una defensa hay que poder mostrarla de nuevo sin recargar la pagina. Es
 * un boton chico con simbolo de rehacer en la esquina del mapa, sin texto: lo
 * unico que hace es repetir la animacion. Se oculta cuando no hay plan, y con
 * movimiento reducido: ahi no hay animacion que repetir.
 */
function pintarRepetir() {
  const boton = document.getElementById('rever');
  if (!boton) return;
  boton.hidden = !estado.rutas.length || movimientoReducido();
}

/**
 * El disparador de una planificacion nueva, atado a la jornada elegida.
 *
 * Lleva la jornada consigo --G1-B compone para ese dia y contra el resuelve
 * que Vehiculos estan disponibles-- y se deshabilita cuando la jornada ya
 * paso, diciendo por que. Consultar una jornada anterior sigue siendo
 * legitimo: lo que se bloquea es la accion que muta, no la lectura.
 *
 * La fecha operativa se resuelve en la zona de la operacion y no en la del
 * navegador, igual que en el backend: un operador en otro huso tiene que ver
 * la misma jornada que el sistema registra.
 */
function pintarDisparador() {
  const control = document.getElementById('nueva-planificacion');
  if (!control) return;
  const pasada = estado.fecha < fechaOperativa();

  control.href = `planificacion.html?fecha=${estado.fecha}`;
  control.classList.toggle('deshabilitado', pasada);
  if (pasada) {
    control.setAttribute('aria-disabled', 'true');
    control.title =
      `La jornada del ${fechaLegible(estado.fecha)} ya pasó: no se planifica hacia atrás.`;
  } else {
    control.removeAttribute('aria-disabled');
    control.title = '';
  }
}

/**
 * Un ancla no admite `disabled`, asi que el clic se intercepta y se explica.
 *
 * Callarlo dejaria un control que no responde sin decir por que, que se lee
 * como una falla. El rechazo se presenta en modal, como todos los demas.
 */
function conectarDisparador() {
  document.getElementById('nueva-planificacion')?.addEventListener('click', (ev) => {
    if (estado.fecha >= fechaOperativa()) return;
    ev.preventDefault();
    abrirModal({
      eyebrow: 'jornada anterior',
      titulo: 'No se planifica hacia atrás',
      cuerpo:
        `<p>La jornada del <b>${fechaLegible(estado.fecha)}</b> ya pasó. Un plan `
        + 'para un día que terminó no describe ningún reparto que pueda ocurrir.</p>'
        + '<p>Sus Rutas se siguen consultando desde acá, en sólo lectura.</p>',
      acciones: [
        { texto: 'Planificar la jornada en curso', principal: true, alPulsar: () => {
          window.location.href = `planificacion.html?fecha=${fechaOperativa()}`;
        } },
        { texto: 'Cerrar' },
      ],
    });
  });
}

/* En la barra superior: si hay plan y cuantas Rutas esperan el despacho. */
function pintarEstadoDelPlan() {
  const caja = document.getElementById('estado-del-plan');
  if (!caja) return;
  if (!estado.rutas.length) { caja.hidden = true; return; }
  const sinDespachar = estado.rutas.filter(todasPlanificadas).length;
  caja.hidden = false;
  caja.innerHTML = `<i></i>Plan vigente · ${sinDespachar
    ? `${plural(sinDespachar, 'Ruta sin despachar', 'Rutas sin despachar')}`
    : 'todas las Rutas despachadas'}`;
}

function pintarLeyenda() {
  document.getElementById('legend-rutas').innerHTML = estado.rutas
    .map((r) => `<button type="button" class="lrow ${apagada(r) ? 'off' : ''}" data-alternar="${r.id}"
        aria-pressed="${!apagada(r)}" title="${apagada(r) ? 'Mostrar' : 'Ocultar'} la Ruta en el mapa">` +
      `<span class="lline" style="background:${colorDe(r)}"></span>${r.vehiculo_id}</button>`)
    .join('');
}

/**
 * C1 -- sin Rutas publicadas (CU-03 flujo 1a, HU-06).
 *
 * El mapa no se apaga: el sustrato cartografico y el Deposito siguen visibles
 * y atenuados, porque lo que falta es el plan y no los datos (PUX-33). Un
 * lienzo en blanco sugeriria que el sistema no tiene nada.
 */
async function pintarVacio() {
  panelVacio();
  ocultarAviso();

  const mapaDiv = document.querySelector('.map');
  document.getElementById('velo')?.remove();
  document.getElementById('tarjeta-vacio')?.remove();
  mapaDiv.insertAdjacentHTML('beforeend', '<div class="veil" id="velo"></div>');

  // El Deposito sigue dibujado y atenuado bajo el velo (PUX-33): lo que falta
  // es el plan, no el sustrato. Es dato real de `GET /v1/depositos`, legible
  // sin que exista Ruta alguna, no una marca decorativa.
  dibujarDeposito(estado.deposito, { encuadrar: true });

  // La cuenta convierte el vacio en una accion evidente, y las dos cifras son
  // dato real: Ordenes en Creada y Vehiculos libres para ESTA jornada. Se
  // cuentan los disponibles y no la Flota entera: con un plan vigente los dos
  // numeros difieren, y el que importa para planificar es el primero.
  let creadas = '—';
  try {
    creadas = (await api.listarOrdenes({ estado: 'creada' })).length;
  } catch { /* la cuenta es accesoria: el vacio se muestra igual */ }
  const vehiculos = estado.flota
    ? estado.flota.vehiculos.filter((v) => !vehiculoComprometido(v)).length
    : '—';

  mapaDiv.insertAdjacentHTML('beforeend', `
    <div class="empty" id="tarjeta-vacio"
         style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:700">
      <div class="eyebrow">Sin plan para la jornada</div>
      <h3>No hay Rutas publicadas para el ${fechaLegible(estado.fecha)}</h3>
      <p>${estado.deposito ? 'El Depósito y el grafo vial están disponibles' : 'El grafo vial está disponible'}.
         Falta ejecutar la planificación.</p>
      <div class="cnt">
        <div><div class="k">Órdenes creadas</div><div class="v">${creadas}</div></div>
        <div><div class="k">Vehículos disponibles</div><div class="v">${vehiculos}</div></div>
      </div>
      <div class="acts">${estado.fecha < fechaOperativa()
        ? '<span class="note-s">Esta jornada ya pasó: se consulta, no se planifica.</span>'
        : `<a class="btn" href="planificacion.html?fecha=${estado.fecha}">Planificar rutas</a>`}</div>
    </div>`);
}

function panelVacio() {
  // Sin Rutas el encabezado del plan no se dibuja: no hay plan que encabezar.
  pintarEncabezadoDelPlan();
  document.getElementById('rail-head').hidden = false;
  document.getElementById('rail-foot').hidden = false;
  document.getElementById('rail-titulo').textContent = 'Rutas publicadas · 0';
  pintarEstadoDelPlan();
  document.getElementById('rail-filtro').innerHTML = '';
  // Se insinua la forma de lo que va a aparecer, en vez de dejar el hueco.
  document.getElementById('rail-list').innerHTML = `
    <div class="rail-blank">
      <div class="dashrow"></div><div class="dashrow"></div><div class="dashrow"></div>
      Cuando se planifique esta jornada, cada Vehículo utilizado aparece acá
      con sus Paradas, su distancia y su carga.
    </div>`;
  document.getElementById('rail-foot').innerHTML =
    '0 Órdenes <span class="sep">·</span> 0 Paradas <span class="sep">·</span> 0 bultos';
  document.getElementById('legend-rutas').innerHTML = '';
  document.getElementById('fecha').textContent = '—';
}

/* ------------------------------------------------------------------ */
/* Interaccion                                                         */
/* ------------------------------------------------------------------ */

/* Ultima Ruta y Parada que eligio el operador, y cuantas veces eligio:
   `recargarConservando` las usa para no pisar una eleccion hecha mientras
   recargaba (C1). */
let recargaVigente = 0;
let generacionDeCarga = 0;
let versionDeEleccion = 0;
let ultimaEleccion = { seleccion: null, parada: null };

function repintar() {
  ultimaEleccion = { seleccion: estado.seleccion, parada: estado.parada };
  versionDeEleccion += 1;
  if (rutaDelRecorrido && estado.seleccion !== rutaDelRecorrido) detenerRecorrido();
  // La posicion informada es de la Ruta elegida: si no hay recorrido estimado
  // corriendo, se borra, y la ficha la vuelve a pedir para la Ruta nueva.
  if (!rutaDelRecorrido) capaDePosicion?.detener();
  pintarEncabezadoDelPlan();
  pintarPanel();
  dibujarMapa();
  pintarFicha();
  const sel = rutaSeleccionada();
  if (sel) cargarDeclaraciones(sel);
}

/** Selecciona una Ruta; volver a elegir la misma deshace la seleccion. */
function seleccionar(idRuta) {
  estado.seleccion = estado.seleccion === idRuta ? null : idRuta;
  estado.parada = null;
  repintar();
}

function deseleccionar() {
  estado.seleccion = null;
  estado.parada = null;
  repintar();
}

/** Selecciona una Parada dentro de la Ruta ya seleccionada (Estado 3). */
function elegirParada(secuencia) {
  estado.parada = estado.parada === secuencia ? null : secuencia;
  repintar();
}

/**
 * Abre una Parada nombrando tambien su Ruta.
 *
 * Si esa Ruta no era la seleccionada, la selecciona y abre la Parada en el
 * mismo gesto. Si ya lo era, alterna la Parada como `elegirParada`.
 */
function elegirParadaDe(idRuta, secuencia) {
  if (estado.seleccion !== idRuta) {
    estado.seleccion = idRuta;
    estado.parada = secuencia;
    repintar();
    return;
  }
  elegirParada(secuencia);
}

function cerrarParada() {
  estado.parada = null;
  repintar();
}

/**
 * Apaga o enciende una Ruta.
 *
 * Apagar la que estaba seleccionada la deselecciona: mantener abierto el
 * detalle de una Ruta que el operador acaba de sacar de la vista dejaria la
 * pantalla diciendo dos cosas a la vez.
 */
function alternarRuta(idRuta) {
  if (estado.apagadas.has(idRuta)) estado.apagadas.delete(idRuta);
  else {
    estado.apagadas.add(idRuta);
    if (estado.seleccion === idRuta) { estado.seleccion = null; estado.parada = null; }
  }
  repintarPlan();
}

function encenderTodas() {
  estado.apagadas.clear();
  repintarPlan();
}

/**
 * Vuelve a mostrar la construccion, a pedido.
 *
 * Repite la entrada sin tocar el estado: las Rutas apagadas siguen apagadas,
 * la seleccion se conserva y los colores y las secuencias no cambian. Es la
 * misma transicion, otra vez.
 */
function reverConstruccion() {
  if (!estado.rutas.length || movimientoReducido()) return;
  dibujarMapa({ animar: true });
}

/** Repinta sin volver a consultar la API: el filtro es de interfaz. */
function repintarPlan() {
  document.getElementById('ficha')?.remove();
  pintarEncabezadoDelPlan();
  pintarPanel();
  pintarLeyenda();
  dibujarMapa();
}

/**
 * Cualquier gesto del operador completa la construccion y sigue su curso.
 *
 * Se escucha `pointerdown` --antes que `click`-- y no se cancela nada: para
 * cuando el clic llega, el plan ya esta entero y el destino del clic esta
 * visible y es alcanzable. La interfaz nunca queda esperando que la
 * animacion termine.
 */
function conectarInterrupcion() {
  const completar = () => detenerConstruccion();
  ['pointerdown', 'wheel', 'keydown'].forEach((evento) => {
    document.addEventListener(evento, completar, { capture: true, passive: true });
  });
  // Mover o acercar el mapa reproyecta las trazas, y lo que se estuviera
  // revelando dejaria de corresponderse con la geometria nueva.
  mapa.on('zoomstart movestart', completar);

  document.getElementById('rever')?.addEventListener('click', reverConstruccion);
}

function conectarPanel() {
  // Sobre una Ruta apagada la cabecera de la tarjeta la enciende, igual que
  // su traza y sus discos en el mapa. Seleccionarla sin encenderla dejaria
  // la pantalla mostrando el detalle de algo que el operador saco de foco.
  // Sobre la elegida, la cierra (`seleccionar` alterna).
  const elegirTarjeta = (idRuta) => {
    if (estado.apagadas.has(idRuta)) alternarRuta(idRuta);
    else seleccionar(idRuta);
  };

  document.getElementById('rail-list').addEventListener('click', (ev) => {
    const despacho = ev.target.closest('[data-despachar]');
    if (despacho) { despachar(despacho.dataset.despachar); return; }
    const replanificacion = ev.target.closest('[data-replanificar]');
    if (replanificacion) { replanificar(replanificacion.dataset.replanificar); return; }
    const recorrido = ev.target.closest('[data-recorrido]');
    if (recorrido) { iniciarRecorrido(recorrido.dataset.recorrido); return; }
    const descarte = ev.target.closest('[data-descartar-ruta]');
    if (descarte) { descartarRuta(descarte.dataset.descartarRuta); return; }

    const fila = ev.target.closest('.parada[data-parada]');
    if (fila) { elegirParada(Number(fila.dataset.parada)); return; }

    const cabeza = ev.target.closest('[data-elegir-ruta]');
    if (cabeza) elegirTarjeta(cabeza.dataset.elegirRuta);
  });

  // Todo lo que el riel deja hacer con el mouse se puede hacer con teclado.
  // Enter y Espacio activan lo que tenga el foco, en el mismo orden de
  // prioridad que el manejador de clic; `preventDefault` porque Espacio
  // desplazaria la pagina. Los botones ya responden solos.
  document.getElementById('rail-list').addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const fila = ev.target.closest?.('.parada[data-parada]');
    if (fila) { ev.preventDefault(); elegirParada(Number(fila.dataset.parada)); return; }
    const cabeza = ev.target.closest?.('[data-elegir-ruta]');
    if (cabeza) { ev.preventDefault(); elegirTarjeta(cabeza.dataset.elegirRuta); }
  });

  document.getElementById('rail-filtro').addEventListener('click', (ev) => {
    if (!ev.target.closest('#encender-todas')) return;
    estado.seleccion = null;
    estado.parada = null;
    encenderTodas();
  });

  // Las referencias del mapa encienden y apagan cada Ruta (propuesta de
  // 2026-11): el filtro de lectura vive junto a lo que filtra.
  document.getElementById('legend-rutas').addEventListener('click', (ev) => {
    const fila = ev.target.closest('[data-alternar]');
    if (fila) alternarRuta(fila.dataset.alternar);
  });

  // Cambiar de jornada es una consulta nueva, no un filtro de cliente: el
  // contrato ya publica `fecha` en GET /v1/rutas y es el backend quien
  // decide que Rutas pertenecen a esa jornada.
  document.getElementById('fecha').addEventListener('change', (ev) => {
    const pedida = ev.target.value;
    // Un campo de fecha vaciado no significa "todas": significa que no hay
    // jornada pedida. Se repone la vigente en vez de consultar sin filtro.
    estado.fecha = pedida || fechaOperativa();
    ev.target.value = estado.fecha;
    pintarDisparador();
    // La disponibilidad de la Flota depende de la jornada, asi que se relee.
    // El Deposito no: es de la instancia y no cambia con el dia.
    estado.flota = null;
    estado.seleccion = null;
    estado.parada = null;
    estado.apagadas.clear();
    cargar();
  });

  // Escape deshace un nivel por vez: primero la Parada, despues la Ruta
  // seleccionada, despues el filtro de encendido.
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (estado.parada !== null) cerrarParada();
    else if (estado.seleccion) deseleccionar();
    else if (estado.apagadas.size) encenderTodas();
  });
}

/* ------------------------------------------------------------------ */
/* Arranque                                                            */
/* ------------------------------------------------------------------ */

async function cargar() {
  // Una carga que termina despues de otra mas nueva no toca nada: su
  // respuesta ya no describe lo que se esta mirando (C1).
  const generacion = ++generacionDeCarga;
  // La tarjeta de la jornada anterior no queda debajo del aviso de carga
  // (2026-10-20A, P18 y N27; C5): si la nueva tampoco tiene plan, se repinta.
  document.getElementById('tarjeta-vacio')?.remove();
  mostrarAviso({ tipo: 'Cargando', titulo: 'Consultando el plan…' });
  try {
    // El Deposito y la Flota son de la instancia, no del plan: se leen una
    // sola vez y sobreviven a que no haya Rutas, que es lo que el estado
    // vacio necesita (FR-14, FR-15).
    const [rutas, deposito, flota] = await Promise.all([
      api.planVigente({ fecha: estado.fecha }),
      estado.deposito ? Promise.resolve(estado.deposito) : api.depositoUnico(),
      estado.flota ? Promise.resolve(estado.flota) : api.flota({ fecha: estado.fecha }),
    ]);
    if (generacion !== generacionDeCarga) return;
    estado.deposito = deposito;
    estado.flota = flota;
    estado.rutas = rutas;
    estado.paleta = paletaPorVehiculo(rutas.map((r) => r.vehiculo_id));
    estado.seleccion = null;
    estado.parada = null;
    detenerRecorrido();
    document.getElementById('ficha')?.remove();

    if (!estado.rutas.length) {
      if (capaPlan) { capaPlan.remove(); capaPlan = null; }
      await pintarVacio();
      return;
    }
    document.getElementById('velo')?.remove();
    document.getElementById('tarjeta-vacio')?.remove();

    pintarEncabezadoDelPlan();
    pintarPanel();
    pintarLeyenda();

    // La construccion se anima la PRIMERA vez que se presenta cada plan.
    // Volver a una jornada ya vista no la repite: lo que la animacion
    // explica --como se construyo el recorrido-- ya se explico.
    const plan = identidadDelPlan();
    const primeraVez = plan !== null && !estado.presentados.has(plan);
    if (plan !== null) estado.presentados.add(plan);
    dibujarMapa({ encuadrar: true, animar: primeraVez && !movimientoReducido() });

    pintarRepetir();
    ocultarAviso();
    cargarAlertas(generacion);
  } catch (e) {
    ocultarAviso();
    if (e instanceof ApiInalcanzable) {
      modalSinConexion(cargar);
    } else if (e instanceof ErrorDeApi) {
      abrirModal({
        eyebrow: e.codigo,
        titulo: 'No se pudo consultar el plan',
        cuerpo: `<p>${escapar(e.message)}</p>`,
        acciones: [{ texto: 'Reintentar', principal: true, alPulsar: cargar },
                   { texto: 'Cerrar' }],
      });
    } else {
      throw e;
    }
  }
}

async function iniciar() {
  // Sin sesion, a ingresar; con terminos pendientes, a los terminos.
  if (!exigirSesion()) return;
  montarEncabezado({ seccion: 'plan' });
  crearMapa();
  // En pantallas bajas la leyenda arranca plegada para no tapar Paradas (C8).
  if (window.innerHeight < ALTO_CON_LEYENDA_ABIERTA) document.getElementById('leyenda').open = false;
  document.getElementById('fecha').value = estado.fecha;
  pintarDisparador();
  conectarPanel();
  conectarInterrupcion();
  conectarDisparador();

  try {
    const info = await api.estado();
    if (!info.compatible) {
      console.warn('El backend sirve el contrato', info.contrato, '— este cliente espera v1.');
    }
    console.info(`Backend ${info.producto} ${info.version}, contrato ${info.contrato}`);
  } catch {
    // La falta de /v1/estado no bloquea: cargar() ya informa si la API no responde.
  }
  await cargar();
  // Desde la lista de Ordenes se llega con la Ruta y la Parada a mostrar (DFE-29).
  const pedido = new URLSearchParams(window.location.search);
  const rutaPedida = pedido.get('ruta');
  if (rutaPedida && estado.rutas.some((r) => r.id === rutaPedida)) {
    const parada = Number(pedido.get('parada'));
    elegirParadaDe(rutaPedida, Number.isInteger(parada) && parada > 0 ? parada : null);
  }
}

iniciar();
