/**
 * La lectura del viaje que hace la pantalla principal (maqueta v6 del
 * chofer): que Parada sigue, cuantas van, a que hora se estima la llegada.
 * Todo sale de la Ruta que publica `GET /v1/yo/ruta-en-ejecucion`; nada se
 * inventa. Funciones puras, sin React, para poder probarlas.
 */
import type { ParadaDeLaRuta, RutaPublicada } from '../api';

export type EstadoDeParada = 'pendiente' | 'entregada' | 'fallida';
export type EstadoDelViaje = 'sin_ruta' | 'por_iniciar' | 'en_curso' | 'terminado';

/** Una Parada esta entregada si todas sus Ordenes se completaron, y sin
    entregar si alguna fallo y ya no queda ninguna en camino. */
export function estadoDeParada(parada: ParadaDeLaRuta): EstadoDeParada {
  const estados = parada.ordenes.map((o) => o.estado);
  if (estados.length && estados.every((e) => e === 'completada')) return 'entregada';
  if (estados.some((e) => e === 'fallida') && estados.every((e) => e === 'completada' || e === 'fallida')) return 'fallida';
  return 'pendiente';
}

export const proximaParada = (ruta: RutaPublicada): ParadaDeLaRuta | null =>
  ruta.paradas.find((p) => estadoDeParada(p) === 'pendiente') ?? null;

export function conteoDelViaje(ruta: RutaPublicada) {
  const estados = ruta.paradas.map(estadoDeParada);
  return {
    total: estados.length,
    entregadas: estados.filter((e) => e === 'entregada').length,
    fallidas: estados.filter((e) => e === 'fallida').length,
    hechas: estados.filter((e) => e !== 'pendiente').length,
  };
}

/**
 * En que punto esta el viaje. Sin Ruta, no hay viaje. Con todas las Paradas
 * resueltas, termino. Si ya hay alguna resuelta, o el transportista toco
 * "Iniciar viaje", esta en curso; si no, espera que lo inicie.
 */
export function estadoDelViaje(ruta: RutaPublicada | null, iniciado: boolean): EstadoDelViaje {
  if (!ruta) return 'sin_ruta';
  const { total, hechas } = conteoDelViaje(ruta);
  if (total > 0 && hechas === total) return 'terminado';
  if (iniciado || hechas > 0) return 'en_curso';
  return 'por_iniciar';
}

/** "08:22": la hora de salida de la Ruta mas unos segundos estimados. */
export function horaMasSegundos(horaDeSalida: string | null | undefined, segundos: number | null | undefined): string | null {
  if (!horaDeSalida || segundos == null) return null;
  const [h, m] = horaDeSalida.split(':').map(Number);
  const total = Math.round(h * 60 + m + segundos / 60);
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/** La llegada estimada a una Parada, con su rango (p10 a p90), si la Ruta
    trae ETA. */
export function llegadaEstimada(ruta: RutaPublicada, parada: ParadaDeLaRuta) {
  const hora = horaMasSegundos(ruta.hora_de_salida, parada.eta_estimado_s);
  if (!hora) return null;
  return {
    hora,
    desde: horaMasSegundos(ruta.hora_de_salida, parada.eta_p10_s) ?? hora,
    hasta: horaMasSegundos(ruta.hora_de_salida, parada.eta_p90_s) ?? hora,
  };
}

const RADIO_TERRESTRE_M = 6_371_000;
const rad = (grados: number) => (grados * Math.PI) / 180;

/** Distancia en linea recta, en metros. */
export function distanciaM(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * RADIO_TERRESTRE_M * Math.asin(Math.sqrt(x));
}

export const textoKm = (metros: number) => `${(metros / 1000).toFixed(1).replace('.', ',')} km`;

export const bultosDe = (parada: ParadaDeLaRuta) =>
  parada.demanda ?? parada.ordenes.reduce((a, o) => a + (o.demanda ?? 0), 0);

/* Lo que el transportista elige al no poder entregar. El contrato no guarda
   un motivo: la eleccion se le muestra al transportista en su lista, y lo que
   se declara es `fallida`. */
export const MOTIVOS_DE_NO_ENTREGA = ['No había nadie', 'Dirección incorrecta', 'Rechazó el pedido', 'Otro motivo'] as const;

/* Los problemas de la maqueta, cada uno con el tipo del catalogo
   `TipoDeIncidente` del contrato que le corresponde. El texto elegido viaja en
   la descripcion, para que el operador lea lo mismo que eligio el
   transportista. */
export const PROBLEMAS = [
  { texto: 'Calle cortada', tipo: 'corte_de_calle' },
  { texto: 'Mucho tránsito', tipo: 'desvio' },
  { texto: 'Obras en la calle', tipo: 'corte_de_calle' },
  { texto: 'Accidente', tipo: 'siniestro' },
  { texto: 'Calle inundada', tipo: 'condicion_climatica_adversa' },
  { texto: 'Otro problema', tipo: 'desvio' },
] as const;

/* Como se nombra un Incidente que ya esta en la Ruta. */
export const ETIQUETA_DEL_INCIDENTE: Record<string, string> = {
  corte_de_calle: 'Corte de calle',
  siniestro: 'Siniestro',
  desvio: 'Desvío',
  cancelacion: 'Cancelación',
  condicion_climatica_adversa: 'Condición climática adversa',
};
