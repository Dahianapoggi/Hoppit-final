/**
 * El mapa de la aplicacion del transportista (2026-11-03B): la pagina que la
 * aplicacion muestra en un WebView (Android) o en un iframe (web), las dos
 * veces desde el servidor con el que se ingreso, y maneja con mensajes.
 *
 * Mensajes que recibe (`{ tipo, ... }`):
 *   - `datos`: la Ruta (Deposito, Paradas con su estado, trazado), la posicion
 *     del transportista y los Incidentes marcados;
 *   - `encuadrar`: `siguiente` (la posicion y la proxima Parada), `todo` (la
 *     Ruta entera), `posicion` o `parada`;
 *   - `marcando`: el modo de marcar un problema tocando el mapa.
 * Mensajes que manda: `toque` (lat, lon) mientras marca, `sin_teselas` si el
 * fondo no carga, y `listo` al cargar.
 *
 * Todo sale del mismo origen: Leaflet vendorizado, este archivo y su hoja de
 * estilos, sin scripts en linea, de modo que la pagina cumple la CSP del
 * borde. El fondo es el de la constante unica de teselas (PB-21); el
 * proveedor sigue abierto en PUX-21.
 */
import { TESELAS_PROVISIONALES } from './tokens.js';

/* global L */

const CENTRO_INICIAL = [-34.4745, -58.515];
const ZOOM_INICIAL = 13;
const ZOOM_AL_ACERCAR = 16;
const DURACION_DEL_VUELO_S = 0.6;
const MARGEN_SUPERIOR_PX = 120;
const MARGEN_LATERAL_PX = 56;
const MARGEN_INFERIOR_POR_OMISION_PX = 140;
const ALTO_DE_LA_ATRIBUCION_PX = 18;
const RADIO_DEL_INCIDENTE_M = 150;
const COLOR_DEL_INCIDENTE = '#A8322B';
const COLOR_DEL_TRAZADO = '#34383A';

/* Prioridad de dibujo de cada capa de marcadores. */
const PRIORIDAD = { deposito: 100, parada: 400, siguiente: 600, incidente: 900, posicion: 1000 };

const enviar = (mensaje) => {
  const texto = JSON.stringify(mensaje);
  if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(texto);
  else if (window.parent !== window) window.parent.postMessage(texto, window.location.origin);
};

const mapa = L.map('map', { zoomControl: false, attributionControl: true }).setView(CENTRO_INICIAL, ZOOM_INICIAL);
mapa.attributionControl.setPrefix(false);

const fondo = L.tileLayer(TESELAS_PROVISIONALES.url, {
  maxZoom: TESELAS_PROVISIONALES.zoomMaximo,
  attribution: TESELAS_PROVISIONALES.atribucion,
}).addTo(mapa);
let avisadoSinTeselas = false;
fondo.on('tileerror', () => {
  if (avisadoSinTeselas) return;
  avisadoSinTeselas = true;
  enviar({ tipo: 'sin_teselas' });
});

const capaRuta = L.layerGroup().addTo(mapa);
const capaParadas = L.layerGroup().addTo(mapa);
const capaIncidentes = L.layerGroup().addTo(mapa);
const capaDeposito = L.layerGroup().addTo(mapa);
let yo = null;
let datos = null;
let marcando = false;
let encuadrada = false;

if (window.ResizeObserver) new ResizeObserver(() => mapa.invalidateSize()).observe(document.getElementById('map'));

const escapar = (t) =>
  String(t == null ? '' : t).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

function iconoDeParada(p) {
  const clase = 'mk' + (p.estado === 'entregada' ? ' done' : '') + (p.estado === 'fallida' ? ' fail' : '') + (p.siguiente ? ' next' : '');
  const lado = p.siguiente ? 34 : p.estado !== 'pendiente' ? 18 : 26;
  const texto = p.estado === 'entregada' ? '&#10003;' : p.estado === 'fallida' ? '!' : p.secuencia;
  return L.divIcon({ className: '', html: `<div class="${clase}">${texto}</div>`, iconSize: [lado, lado], iconAnchor: [lado / 2, lado / 2] });
}

function dibujar() {
  capaRuta.clearLayers();
  capaParadas.clearLayers();
  capaIncidentes.clearLayers();
  capaDeposito.clearLayers();
  if (!datos) return;
  // La atribucion del fondo, a la vista justo arriba de la hoja.
  const atribucion = document.querySelector('.leaflet-bottom.leaflet-right');
  if (atribucion) atribucion.style.marginBottom = Math.max(0, (datos.margenInferior || 0) - ALTO_DE_LA_ATRIBUCION_PX) + 'px';
  const r = datos.ruta;
  if (r) {
    if (r.deposito) {
      L.marker([r.deposito.lat, r.deposito.lon], {
        icon: L.divIcon({ className: '', html: '<div class="depot">CD</div>', iconSize: [24, 24], iconAnchor: [12, 12] }),
        zIndexOffset: PRIORIDAD.deposito,
      })
        .addTo(capaDeposito)
        .bindPopup('<b>Centro de distribución</b><br>' + escapar(r.deposito.rotulo));
    }
    if (r.trazado && r.trazado.length > 1) {
      L.polyline(r.trazado, { color: '#fff', weight: 10, opacity: 0.95, lineCap: 'round', lineJoin: 'round' }).addTo(capaRuta);
      L.polyline(r.trazado, { color: COLOR_DEL_TRAZADO, weight: 5, opacity: 0.85, lineCap: 'round', lineJoin: 'round' }).addTo(capaRuta);
    }
    r.paradas.forEach((p) => {
      const m = L.marker([p.lat, p.lon], { icon: iconoDeParada(p), zIndexOffset: p.siguiente ? PRIORIDAD.siguiente : PRIORIDAD.parada }).addTo(capaParadas);
      m.bindPopup('<b>' + p.secuencia + '. ' + escapar(p.rotulo) + '</b><br>' + escapar(p.detalle));
      if (p.siguiente) m.bindTooltip(escapar(p.rotulo), { permanent: true, direction: 'top', offset: [0, -20], className: 'nxt' });
    });
  }
  (datos.incidentes || []).forEach((i) => {
    L.circle([i.lat, i.lon], { radius: RADIO_DEL_INCIDENTE_M, color: COLOR_DEL_INCIDENTE, weight: 2, fillColor: COLOR_DEL_INCIDENTE, fillOpacity: 0.15 }).addTo(capaIncidentes);
    L.marker([i.lat, i.lon], {
      icon: L.divIcon({ className: '', html: '<div class="inc">!</div>', iconSize: [30, 30], iconAnchor: [15, 15] }),
      zIndexOffset: PRIORIDAD.incidente,
    })
      .addTo(capaIncidentes)
      .bindPopup('<b>' + escapar(i.texto) + '</b><br>Avisado por vos · ' + escapar(i.hora));
  });
  if (datos.posicion) {
    const punto = [datos.posicion.lat, datos.posicion.lon];
    if (!yo) {
      yo = L.marker(punto, {
        icon: L.divIcon({ className: '', html: '<div class="me"></div>', iconSize: [20, 20], iconAnchor: [10, 10] }),
        zIndexOffset: PRIORIDAD.posicion,
      }).addTo(mapa);
    } else yo.setLatLng(punto);
  } else if (yo) {
    mapa.removeLayer(yo);
    yo = null;
  }
  if (!encuadrada && r) {
    encuadrada = true;
    encuadrar('todo');
  }
}

function encuadrar(modo) {
  try {
    const r = datos && datos.ruta;
    let puntos = [];
    if (modo === 'posicion' && datos.posicion) {
      mapa.flyTo([datos.posicion.lat, datos.posicion.lon], ZOOM_AL_ACERCAR, { duration: DURACION_DEL_VUELO_S });
      return;
    }
    if (r) {
      const siguiente = r.paradas.filter((p) => p.siguiente)[0];
      if (modo === 'siguiente' && siguiente) {
        puntos = [[siguiente.lat, siguiente.lon]];
        if (datos.posicion) puntos.push([datos.posicion.lat, datos.posicion.lon]);
        else if (r.deposito) puntos.push([r.deposito.lat, r.deposito.lon]);
      } else {
        if (r.deposito) puntos.push([r.deposito.lat, r.deposito.lon]);
        r.paradas.forEach((p) => puntos.push([p.lat, p.lon]));
      }
    }
    if (puntos.length === 1) {
      mapa.flyTo(puntos[0], ZOOM_AL_ACERCAR, { duration: DURACION_DEL_VUELO_S });
      return;
    }
    if (puntos.length) {
      mapa.fitBounds(L.latLngBounds(puntos), {
        paddingTopLeft: [MARGEN_LATERAL_PX, MARGEN_SUPERIOR_PX],
        paddingBottomRight: [MARGEN_LATERAL_PX, datos.margenInferior || MARGEN_INFERIOR_POR_OMISION_PX],
        maxZoom: ZOOM_AL_ACERCAR,
      });
    }
  } catch {
    /* sin datos para encuadrar */
  }
}

function recibir(ev) {
  // Del iframe, sólo la página que lo contiene (mismo origen); del WebView,
  // los eventos que inyecta la aplicación, que no traen origen.
  if (ev.origin && ev.origin !== window.location.origin) return;
  let m;
  try {
    m = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
  } catch {
    return;
  }
  if (!m || !m.tipo) return;
  if (m.tipo === 'datos') {
    datos = m;
    dibujar();
  } else if (m.tipo === 'encuadrar') {
    if (m.modo === 'parada' && datos && datos.ruta) {
      const p = datos.ruta.paradas.filter((x) => x.secuencia === m.secuencia)[0];
      if (p) mapa.flyTo([p.lat, p.lon], ZOOM_AL_ACERCAR, { duration: DURACION_DEL_VUELO_S });
    } else encuadrar(m.modo);
  } else if (m.tipo === 'marcando') {
    marcando = !!m.activo;
    mapa.getContainer().classList.toggle('placing-mode', marcando);
  }
}

window.addEventListener('message', recibir);
document.addEventListener('message', recibir);
mapa.on('click', (e) => {
  if (marcando) enviar({ tipo: 'toque', lat: e.latlng.lat, lon: e.latlng.lng });
});
enviar({ tipo: 'listo' });
