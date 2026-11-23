/**
 * Lo que la pantalla le pasa al mapa y lo que el mapa le avisa, comun a la
 * version Android (WebView) y a la web (iframe).
 */

export interface ParadaEnElMapa {
  secuencia: number;
  lat: number;
  lon: number;
  rotulo: string;
  detalle: string;
  estado: 'pendiente' | 'entregada' | 'fallida';
  siguiente: boolean;
}

export interface DatosDelMapa {
  ruta: {
    deposito: { lat: number; lon: number; rotulo: string } | null;
    paradas: ParadaEnElMapa[];
    trazado: [number, number][];
  } | null;
  posicion: { lat: number; lon: number } | null;
  incidentes: { lat: number; lon: number; texto: string; hora: string }[];
  margenInferior: number;
}

/* Una orden puntual al mapa. `n` cambia en cada pedido, para que repetir la
   misma orden (centrar dos veces) se vuelva a cumplir. */
export type OrdenAlMapa =
  | { tipo: 'encuadrar'; modo: 'siguiente' | 'todo' | 'posicion'; n: number }
  | { tipo: 'encuadrar'; modo: 'parada'; secuencia: number; n: number }
  | { tipo: 'marcando'; activo: boolean; n: number };

export type MensajeDelMapa =
  | { tipo: 'listo' }
  | { tipo: 'toque'; lat: number; lon: number }
  | { tipo: 'sin_teselas' };

export interface PropsDelMapa {
  /** El servidor con el que se ingreso: de ahi se carga la pagina del mapa. */
  servidor: string;
  datos: DatosDelMapa;
  orden: OrdenAlMapa | null;
  alMensaje: (mensaje: MensajeDelMapa) => void;
}
