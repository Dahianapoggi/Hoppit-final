/**
 * Cliente del nucleo y del servicio de telemetria. La direccion del servidor
 * la escribe el transportista: no hay direccion por omision (B-259). Con el
 * despliegue, el nucleo y telemetria comparten origen detras de Caddy; en
 * desarrollo se puede dar la de telemetria por separado.
 */
import { MEDIO_DE_LA_APLICACION } from './configuracion';
import type { Lote, LoteClasificado, RespuestaDelEnvio } from './logica/envio';
import { cuerpoDeLaDeclaracion } from './logica/resultados';
import type { RespuestaDeLaDeclaracion, ResultadoEnCola } from './logica/resultados';

export interface Servidor {
  nucleo: string;
  telemetria: string;
}

export interface DocumentoPendiente {
  documento: string;
  version: number;
  sha256: string;
  titulo: string;
}

export interface Sesion {
  token: string;
  rol: string;
  nombre_visible: string;
  vence_en?: string;
  pendientes: DocumentoPendiente[];
}

export interface OrdenEnParada {
  id: string;
  estado: string;
  demanda?: number;
}

export interface PuntoPublicado {
  nodo: number;
  lat: number;
  lon: number;
  rotulo: { texto: string };
}

/** Una Parada tal como la publica la API (`RutaPublicada` del contrato): la
    ubicacion y el rotulo van en `punto`, y el ETA en segundos desde la salida.
    Hasta 2026-10-27C la aplicacion leia `rotulo`, `lat` y `lon` sueltos, que
    la API no publica, y la pantalla de la Ruta quedaba en blanco. */
export interface ParadaDeLaRuta {
  secuencia: number;
  punto: PuntoPublicado;
  ordenes: OrdenEnParada[];
  demanda?: number;
  eta_estimado_s?: number | null;
  eta_p10_s?: number | null;
  eta_p90_s?: number | null;
}

/** La Ruta como la publica el contrato (`RutaPublicada`): el Deposito, las
    Paradas en orden y el trazado sobre la red vial, para el mapa. */
export interface RutaPublicada {
  id: string;
  vehiculo_id: string;
  fecha_planificacion?: string;
  hora_de_salida?: string | null;
  distancia_m?: number;
  deposito?: PuntoPublicado;
  paradas: ParadaDeLaRuta[];
  trazado?: { coordenadas: [number, number][] } | null;
  /** Solo si la Ruta nacio de un reemplazo (CU-07): la que reemplaza. */
  reemplaza_a?: { ruta_reemplazada: string } | null;
}

export interface IncidentePublicado {
  id: string;
  tipo: string;
  alcance?: string;
  descripcion: string;
  rutas_afectadas?: string[];
  declarado_en?: string;
}

export interface RutaEnEjecucion {
  ruta: RutaPublicada | null;
  incidentes: IncidentePublicado[];
}

export type ResultadoDeParada = 'cumplida' | 'fallida';

export class ErrorDelServidor extends Error {
  constructor(public readonly estado: number, public readonly codigo: string, mensaje: string) {
    super(mensaje);
  }
}

async function pedir<T>(url: string, opciones: RequestInit = {}, token?: string): Promise<T> {
  const encabezados: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) encabezados.Authorization = `Bearer ${token}`;
  const respuesta = await fetch(url, { ...opciones, headers: encabezados });
  const cuerpo = await respuesta.json().catch(() => null);
  if (!respuesta.ok) {
    throw new ErrorDelServidor(respuesta.status, cuerpo?.codigo ?? 'desconocido', cuerpo?.mensaje ?? `Error ${respuesta.status}`);
  }
  return cuerpo as T;
}

const sinBarra = (base: string) => base.replace(/\/+$/, '');

export function ingresar(servidor: Servidor, usuario: string, contrasena: string): Promise<Sesion> {
  return pedir(`${sinBarra(servidor.nucleo)}/v1/sesiones`, { method: 'POST', body: JSON.stringify({ usuario, contrasena }) });
}

export function aceptar(servidor: Servidor, token: string, pendientes: DocumentoPendiente[], dispositivoId: string): Promise<Sesion> {
  return pedir(
    `${sinBarra(servidor.nucleo)}/v1/terminos/aceptaciones`,
    {
      method: 'POST',
      body: JSON.stringify({
        medio: MEDIO_DE_LA_APLICACION,
        dispositivo_id: dispositivoId,
        aceptaciones: pendientes.map((p) => ({ documento: p.documento, version: p.version, sha256: p.sha256 })),
      }),
    },
    token,
  );
}

export function rutaEnEjecucion(servidor: Servidor, token: string): Promise<RutaEnEjecucion> {
  return pedir(`${sinBarra(servidor.nucleo)}/v1/yo/ruta-en-ejecucion`, {}, token);
}

/** "Entregue" o "No pude entregar": el resultado de una Parada de la Ruta
    propia (`POST /v1/yo/rutas/{id}/paradas/{n}/resultado`). */
export function declararResultado(
  servidor: Servidor,
  token: string,
  idRuta: string,
  secuencia: number,
  resultado: ResultadoDeParada,
  declaradoEn?: string,
  sinConexion = false,
): Promise<unknown> {
  // D29 (encargo 2026-11-23B): con la hora de la declaracion, el servidor
  // asienta la hora del hecho y resuelve una Ruta reemplazada mientras tanto.
  const cuerpo = declaradoEn === undefined
    ? { resultado }
    : cuerpoDeLaDeclaracion({ resultado, declarado_en: declaradoEn }, sinConexion);
  return pedir(
    `${sinBarra(servidor.nucleo)}/v1/yo/rutas/${encodeURIComponent(idRuta)}/paradas/${secuencia}/resultado`,
    { method: 'POST', body: JSON.stringify(cuerpo) },
    token,
  );
}

/** "Avisar problema": un Incidente sobre la Ruta propia (`POST /v1/yo/incidentes`). */
export function declararIncidente(
  servidor: Servidor,
  token: string,
  ruta: string,
  tipo: string,
  descripcion: string,
): Promise<IncidentePublicado> {
  return pedir(
    `${sinBarra(servidor.nucleo)}/v1/yo/incidentes`,
    { method: 'POST', body: JSON.stringify({ ruta, tipo, descripcion }) },
    token,
  );
}

/** Manda un lote y traduce la respuesta para el emisor, sin levantar: un
    error de red es `sin_red`, uno HTTP lleva su estado. */
export async function enviarLote(servidor: Servidor, token: string, lote: Lote): Promise<RespuestaDelEnvio> {
  try {
    const clasificacion = await pedir<LoteClasificado>(
      `${sinBarra(servidor.telemetria)}/v1/telemetria/lotes`,
      { method: 'POST', body: JSON.stringify(lote) },
      token,
    );
    return { tipo: 'clasificado', clasificacion };
  } catch (error) {
    if (error instanceof ErrorDelServidor) return { tipo: 'error', estado: error.estado };
    return { tipo: 'sin_red' };
  }
}

/** Envia un resultado de Parada encolado sin conexion (CU-06, 4a) con la
    operacion de siempre y traduce la respuesta para la cola, sin levantar: un
    error de red es `sin_red`; uno HTTP lleva su estado y su codigo. */
export async function enviarResultadoEncolado(
  servidor: Servidor,
  token: string,
  r: ResultadoEnCola,
): Promise<RespuestaDeLaDeclaracion> {
  try {
    await declararResultado(servidor, token, r.ruta_id, r.secuencia, r.resultado, r.declarado_en, true);
    return { tipo: 'aplicado' };
  } catch (error) {
    if (error instanceof ErrorDelServidor) return { tipo: 'error', estado: error.estado, codigo: error.codigo ?? null };
    return { tipo: 'sin_red' };
  }
}
