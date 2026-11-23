/**
 * Recorrido estimado de una Ruta (DFE-32; B-243, H5).
 *
 * Un marcador recorre `trazado.coordenadas` siguiendo la linea temporal del
 * ETA: sale del Deposito a la hora de salida y pasa por cada Parada a la hora
 * de salida mas su `eta_estimado_s`; entre una y otra avanza en proporcion a
 * la distancia recorrida sobre el trazado. Es una animacion del ETA, no una
 * posicion: lo dice un rotulo fijo mientras corre.
 *
 * La capa se arma para recibir despues la ultima posicion de la telemetria
 * (ARQ-53) con su propio rotulo (`ORIGENES.telemetria`). Desde 2026-10-27B (T) la llama
 * con ese origen: no hay telemetria y no se simula.
 */

const METROS_POR_GRADO = 111_320;
const SEGUNDOS_POR_MINUTO = 60;
const MINUTOS_POR_HORA = 60;
export const FACTORES = [60, 120, 300];
export const FACTOR_POR_OMISION = 120;
const MS_POR_S = 1000;
const TAMANO_DEL_MARCADOR = 20;

/* Lo que dice el rotulo de la posicion segun de donde sale. */
export const ORIGENES = {
  estimada: 'Recorrido estimado según el ETA: no es una posición real',
  telemetria: 'Última posición informada por el Vehículo',
};

/* Por que una Ruta no tiene recorrido estimado. */
export const SIN_ETA = 'Esta Ruta no tiene recorrido estimado: le falta la hora de salida o la '
  + 'estimación de alguna Parada, y sin esas horas no hay cómo ubicar el Vehículo en el tiempo.';

/* Distancia aproximada en metros entre dos [lat, lon]: plana, con el coseno
   de la latitud. Alcanza para repartir el tiempo entre puntos cercanos. */
function metros([lat1, lon1], [lat2, lon2]) {
  const coseno = Math.cos(((lat1 + lat2) / 2) * (Math.PI / 180));
  return Math.hypot((lat2 - lat1) * METROS_POR_GRADO, (lon2 - lon1) * METROS_POR_GRADO * coseno);
}

/**
 * Los nudos de la linea temporal: distancia sobre el trazado y segundos desde
 * la salida, del Deposito a la ultima Parada. Cada Parada se ubica en el
 * vertice del trazado mas cercano a su punto, buscando hacia adelante desde
 * la anterior. Null si falta la hora de salida o el ETA de alguna Parada: sin
 * esas horas no se completa nada.
 */
export function lineaTemporal(ruta) {
  const coords = ruta.trazado?.coordenadas ?? [];
  if (ruta.hora_de_salida == null || coords.length < 2
      || ruta.paradas.some((p) => p.eta_estimado_s == null)) return null;
  const acumuladas = [0];
  for (let i = 1; i < coords.length; i += 1) acumuladas.push(acumuladas[i - 1] + metros(coords[i - 1], coords[i]));
  const nudos = [{ indice: 0, distancia: 0, segundos: 0, secuencia: null }];
  let desde = 0;
  for (const p of ruta.paradas) {
    const punto = [p.punto.lat, p.punto.lon];
    let mejor = desde;
    for (let i = desde; i < coords.length; i += 1) {
      if (metros(coords[i], punto) < metros(coords[mejor], punto)) mejor = i;
    }
    nudos.push({ indice: mejor, distancia: acumuladas[mejor], segundos: p.eta_estimado_s, secuencia: p.secuencia });
    desde = mejor;
  }
  return { coords, acumuladas, nudos, fin: nudos[nudos.length - 1].segundos };
}

/** Donde esta el marcador a `segundos` de la salida, y cual es la proxima Parada. */
export function posicionEn(linea, segundos) {
  const { coords, acumuladas, nudos } = linea;
  const t = Math.min(Math.max(segundos, 0), linea.fin);
  let k = 1;
  while (k < nudos.length - 1 && nudos[k].segundos < t) k += 1;
  const [a, b] = [nudos[k - 1], nudos[k]];
  const tramo = b.segundos - a.segundos;
  const fraccion = tramo > 0 ? (t - a.segundos) / tramo : 1;
  const distancia = a.distancia + fraccion * (b.distancia - a.distancia);
  let i = a.indice;
  while (i < b.indice && acumuladas[i + 1] < distancia) i += 1;
  const largo = acumuladas[i + 1] - acumuladas[i];
  const f = i < coords.length - 1 && largo > 0 ? (distancia - acumuladas[i]) / largo : 0;
  const siguiente = coords[Math.min(i + 1, coords.length - 1)];
  const latlng = [coords[i][0] + f * (siguiente[0] - coords[i][0]), coords[i][1] + f * (siguiente[1] - coords[i][1])];
  const proxima = t >= linea.fin ? null : nudos.find((n) => n.secuencia !== null && n.segundos >= t)?.secuencia ?? null;
  return { latlng, proxima, segundos: t, terminado: t >= linea.fin };
}

/** "HH:MM" de la hora de salida ("HH:MM:SS") mas `segundos`. Trunca al
    minuto, como un reloj; las horas de la espina redondean, porque son
    estimaciones y no un reloj que avanza. */
export function horaEstimada(horaDeSalida, segundos) {
  const [h, m, s = 0] = horaDeSalida.split(':').map(Number);
  const total = Math.floor(((h * MINUTOS_POR_HORA + m) * SEGUNDOS_POR_MINUTO + s + segundos) / SEGUNDOS_POR_MINUTO);
  const dos = (n) => String(n).padStart(2, '0');
  return `${dos(Math.floor(total / MINUTOS_POR_HORA) % 24)}:${dos(total % MINUTOS_POR_HORA)}`;
}

/**
 * La capa del mapa: un marcador y un rotulo fijo. `reproducir` anima el
 * recorrido estimado; `mostrarPosicion` deja un punto quieto con el rotulo de
 * su origen, para cuando haya telemetria.
 */
export class CapaDePosicion {
  constructor(L, mapa, contenedor, alCambiar) {
    this.L = L;
    this.mapa = mapa;
    this.contenedor = contenedor;
    this.alCambiar = alCambiar;
    this.marcador = null;
    this.rotulo = null;
    this.cuadro = null;
    this.reloj = null;
    this.estado = null;
  }

  _rotular(origen, detalle = '') {
    if (!this.rotulo) {
      this.rotulo = document.createElement('div');
      this.rotulo.className = 'rotulo-posicion';
      this.rotulo.setAttribute('role', 'status');
      this.contenedor.appendChild(this.rotulo);
    }
    this.rotulo.dataset.origen = origen;
    this.rotulo.textContent = detalle ? `${ORIGENES[origen]} ${detalle}` : ORIGENES[origen];
  }

  /* Un marcador del DOM y no un circulo del lienzo: el mapa dibuja con canvas
     (`preferCanvas`), y el marcador tiene que quedar arriba de los trazos. */
  _ubicar(latlng, color) {
    this.marcador?.remove();
    this.marcador = this.L.marker(latlng, {
      interactive: false, keyboard: false, zIndexOffset: 1000,
      icon: this.L.divIcon({
        className: 'marcador-posicion', iconSize: [TAMANO_DEL_MARCADOR, TAMANO_DEL_MARCADOR],
        html: `<span style="background:${color}"></span>`,
      }),
    }).addTo(this.mapa);
  }

  /** Un punto quieto con el rotulo de su origen (`telemetria`, ARQ-53) y, si
      se da, su antiguedad ("hace 3 min"). */
  mostrarPosicion({ lat, lon, origen, color, detalle = '' }) {
    this.detener();
    this._rotular(origen, detalle);
    this._ubicar([lat, lon], color);
  }

  reproducir(ruta, linea, { factor = FACTOR_POR_OMISION, color }) {
    this.detener();
    this._rotular('estimada');
    this.estado = { ruta, linea, factor, segundos: 0, pausado: false, color };
    this._ubicar(linea.coords[0], color);
    this._seguir();
  }

  _seguir() {
    let anterior = null;
    const paso = (ahora) => {
      const e = this.estado;
      if (!e) return;
      if (anterior !== null && !e.pausado) e.segundos += ((ahora - anterior) / MS_POR_S) * e.factor;
      anterior = ahora;
      const pos = posicionEn(e.linea, e.segundos);
      this.marcador.setLatLng(pos.latlng);
      this.alCambiar?.({ ...pos, hora: horaEstimada(e.ruta.hora_de_salida, pos.segundos), pausado: e.pausado, factor: e.factor });
      if (pos.terminado) { e.pausado = true; }
      this.cuadro = requestAnimationFrame(paso);
    };
    this.cuadro = requestAnimationFrame(paso);
  }

  pausar() { if (this.estado) this.estado.pausado = true; }

  reanudar() {
    if (!this.estado) return;
    if (this.estado.segundos >= this.estado.linea.fin) this.estado.segundos = 0;
    this.estado.pausado = false;
  }

  reiniciar() { if (this.estado) { this.estado.segundos = 0; this.estado.pausado = false; } }

  acelerar(factor) { if (this.estado) this.estado.factor = factor; }

  get activo() { return this.estado !== null; }

  detener() {
    if (this.cuadro) cancelAnimationFrame(this.cuadro);
    this.cuadro = null;
    this.estado = null;
    this.marcador?.remove();
    this.marcador = null;
    this.rotulo?.remove();
    this.rotulo = null;
  }
}
