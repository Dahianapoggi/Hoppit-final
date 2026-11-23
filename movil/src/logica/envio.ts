/**
 * Envio de la cola por lotes (secciones 3.2 a 3.4 de ARQ_Pedido2), sin red
 * propia: recibe la funcion que manda el lote. Asi se prueba sin servidor.
 *
 * - Envia la primera posicion apenas esta en la cola (B-273), y despues cada
 *   INTERVALO_DE_ENVIO_MS, o antes si la cola llega a UMBRAL_DE_ENVIO.
 * - Con la respuesta del servidor quita de la cola lo aceptado, lo duplicado
 *   y lo rechazado de forma definitiva, y conserva lo reintentable.
 * - Ante error de red o 5xx espera de forma exponencial, con dispersion, de 5
 *   a 300 s. Ante 401 o 403 se detiene: hay que volver a ingresar o aceptar.
 */
import {
  ESPERA_MAXIMA_MS,
  ESPERA_MINIMA_MS,
  INTERVALO_DE_ENVIO_MS,
  MAX_POSICIONES_POR_LOTE,
  UMBRAL_DE_ENVIO,
} from '../configuracion';
import type { Cola, PosicionEnCola } from './cola';

export interface RechazoDelServidor {
  id: string;
  motivo: string;
  reintentable: boolean;
}

export interface LoteClasificado {
  aceptadas: string[];
  duplicadas: string[];
  rechazadas: RechazoDelServidor[];
}

export interface Lote {
  ruta_id: string;
  dispositivo_id: string;
  posiciones: PosicionEnCola[];
  descartadas_por_cola_llena: number;
}

/** Lo que devuelve quien manda el lote: la clasificacion, o el estado HTTP
    de un error, o `null` si no hubo respuesta (sin red). */
export type RespuestaDelEnvio =
  | { tipo: 'clasificado'; clasificacion: LoteClasificado }
  | { tipo: 'error'; estado: number }
  | { tipo: 'sin_red' };

export type ResultadoDelCiclo =
  | { tipo: 'nada_que_enviar' }
  | { tipo: 'todavia_no' }
  | { tipo: 'enviado'; quitadas: number; conservadas: number; rechazos: RechazoDelServidor[] }
  | { tipo: 'reintento'; esperaMs: number; motivo: string }
  | { tipo: 'detenido'; estado: number };

/** Que quitar de la cola y que conservar, segun la clasificacion (seccion 3.3). */
export function repartirClasificacion(clasificacion: LoteClasificado): { quitar: string[]; conservar: string[] } {
  const definitivas = clasificacion.rechazadas.filter((r) => !r.reintentable).map((r) => r.id);
  const reintentables = clasificacion.rechazadas.filter((r) => r.reintentable).map((r) => r.id);
  return { quitar: [...clasificacion.aceptadas, ...clasificacion.duplicadas, ...definitivas], conservar: reintentables };
}

/** Espera exponencial con dispersion ("jitter completo"), de 5 a 300 s. */
export function esperaDeReintento(intentos: number, azar: () => number = Math.random): number {
  const tope = Math.min(ESPERA_MINIMA_MS * 2 ** Math.max(intentos - 1, 0), ESPERA_MAXIMA_MS);
  return Math.round(ESPERA_MINIMA_MS + azar() * (tope - ESPERA_MINIMA_MS));
}

const ESTADO_NO_AUTENTICADO = 401;
const ESTADO_PROHIBIDO = 403;
const PRIMER_ERROR_DEL_SERVIDOR = 500;

export class Emisor {
  private ultimoEnvio = 0;
  private yaEnvio = false;
  private proximoPermitido = 0;
  private intentos = 0;

  constructor(
    private readonly cola: Cola,
    private readonly enviar: (lote: Lote) => Promise<RespuestaDelEnvio>,
    private readonly reloj: () => number = Date.now,
    private readonly azar: () => number = Math.random,
  ) {}

  /** Si corresponde enviar ahora: no se esta esperando un reintento, y es el
      primer envio con algo en la cola (la primera posicion sale enseguida,
      B-273), paso el intervalo o la cola llego al umbral. */
  async corresponde(): Promise<boolean> {
    const ahora = this.reloj();
    if (ahora < this.proximoPermitido) return false;
    const enCola = await this.cola.cantidad();
    if (!this.yaEnvio && enCola > 0) return true;
    if (ahora - this.ultimoEnvio >= INTERVALO_DE_ENVIO_MS) return true;
    return enCola >= UMBRAL_DE_ENVIO;
  }

  /** Un ciclo: si corresponde, manda un lote y actualiza la cola. */
  async ciclo(rutaId: string, dispositivoId: string, forzar = false): Promise<ResultadoDelCiclo> {
    if (!forzar && !(await this.corresponde())) return { tipo: 'todavia_no' };
    const posiciones = await this.cola.primeras(MAX_POSICIONES_POR_LOTE);
    const descartadas = this.cola.tomarDescartadas();
    if (!posiciones.length && !descartadas) return { tipo: 'nada_que_enviar' };
    this.ultimoEnvio = this.reloj();
    this.yaEnvio = true;
    const respuesta = await this.enviar({
      ruta_id: rutaId,
      dispositivo_id: dispositivoId,
      posiciones,
      descartadas_por_cola_llena: descartadas,
    });
    if (respuesta.tipo === 'clasificado') {
      const { quitar, conservar } = repartirClasificacion(respuesta.clasificacion);
      await this.cola.quitar(quitar);
      this.intentos = 0;
      return { tipo: 'enviado', quitadas: quitar.length, conservadas: conservar.length, rechazos: respuesta.clasificacion.rechazadas };
    }
    this.cola.devolverDescartadas(descartadas);
    if (respuesta.tipo === 'error' && (respuesta.estado === ESTADO_NO_AUTENTICADO || respuesta.estado === ESTADO_PROHIBIDO)) {
      return { tipo: 'detenido', estado: respuesta.estado };
    }
    if (respuesta.tipo === 'error' && respuesta.estado < PRIMER_ERROR_DEL_SERVIDOR) {
      // Un 4xx que no es de acceso (413, 422): el lote no mejora reintentando igual.
      return { tipo: 'detenido', estado: respuesta.estado };
    }
    this.intentos += 1;
    const esperaMs = esperaDeReintento(this.intentos, this.azar);
    this.proximoPermitido = this.reloj() + esperaMs;
    return { tipo: 'reintento', esperaMs, motivo: respuesta.tipo === 'sin_red' ? 'sin red' : `error ${respuesta.estado}` };
  }
}
