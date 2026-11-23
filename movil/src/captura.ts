/**
 * Captura de posiciones (seccion 3.4 de ARQ_Pedido2): una cada
 * INTERVALO_DE_CAPTURA_MS (10 s desde B-273), solo con una Ruta en ejecucion
 * y solo con el consentimiento de geolocalizacion aceptado (ley 25.326;
 * B-259).
 *
 * - Android: servicio en primer plano con notificacion persistente
 *   (`expo-location` y `expo-task-manager`), que sigue capturando con la
 *   pantalla apagada. Configurado para una compilacion de desarrollo; el APK
 *   no se compilo en la sesion 2026-10-27B. Sin el permiso de segundo plano
 *   (Expo Go), captura solo con la aplicacion abierta (2026-11-03B).
 * - Version web: el navegador solo da la posicion con la pagina en primer
 *   plano; fuera de eso no hay posiciones, y la pantalla lo dice.
 */
import * as Crypto from 'expo-crypto';
import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { Platform } from 'react-native';

import { INTERVALO_DE_CAPTURA_MS } from './configuracion';
import type { Cola, PosicionEnCola } from './logica/cola';

const TAREA_DE_CAPTURA = 'hoppit-captura-de-posiciones';
export const EN_LA_WEB = Platform.OS === 'web';

export type EstadoDeCaptura = 'detenida' | 'activa' | 'sin_permiso';

/** La cola a la que escribe la tarea de fondo de Android: la tarea se define
    al cargar el modulo, antes de que exista la cola. */
let colaDeLaTarea: Cola | null = null;

function aPosicion(lugar: { coords: { latitude: number; longitude: number; accuracy: number | null; speed: number | null; heading: number | null }; timestamp: number }): PosicionEnCola {
  return {
    id: Crypto.randomUUID(),
    capturada_en: new Date(lugar.timestamp).toISOString(),
    latitud: lugar.coords.latitude,
    longitud: lugar.coords.longitude,
    precision_m: lugar.coords.accuracy ?? 0,
    velocidad_mps: lugar.coords.speed ?? null,
    rumbo_grados: lugar.coords.heading ?? null,
  };
}

if (!EN_LA_WEB) {
  TaskManager.defineTask(TAREA_DE_CAPTURA, async ({ data, error }) => {
    if (error || !colaDeLaTarea) return;
    const lugares = (data as { locations?: Location.LocationObject[] } | undefined)?.locations ?? [];
    for (const lugar of lugares) await colaDeLaTarea.encolar(aPosicion(lugar));
  });
}

let reloj: ReturnType<typeof setInterval> | null = null;
/** La captura con la aplicacion abierta, cuando no hay permiso de segundo plano. */
let suscripcionEnPrimerPlano: Location.LocationSubscription | null = null;

/** Empieza a capturar. Devuelve el estado: sin permiso, no captura nada. */
export async function iniciarCaptura(cola: Cola): Promise<EstadoDeCaptura> {
  if (EN_LA_WEB) {
    if (!('geolocation' in navigator)) return 'sin_permiso';
    const tomar = () =>
      new Promise<EstadoDeCaptura>((resolver) => {
        navigator.geolocation.getCurrentPosition(
          (lugar) => {
            cola.encolar(aPosicion(lugar)).then(() => resolver('activa'));
          },
          () => resolver('sin_permiso'),
          { enableHighAccuracy: true, timeout: INTERVALO_DE_CAPTURA_MS },
        );
      });
    const primero = await tomar();
    if (primero === 'sin_permiso') return primero;
    detenerCapturaWeb();
    reloj = setInterval(tomar, INTERVALO_DE_CAPTURA_MS);
    return 'activa';
  }
  const enUso = await Location.requestForegroundPermissionsAsync();
  if (enUso.status !== 'granted') return 'sin_permiso';
  // Sin el permiso de segundo plano (Expo Go no lo da en Android), se
  // captura igual con la aplicacion abierta, en la misma cola (B-273).
  const deFondo = await Location.requestBackgroundPermissionsAsync().catch(() => null);
  if (deFondo?.status !== 'granted') {
    await detenerCapturaEnPrimerPlano();
    suscripcionEnPrimerPlano = await Location.watchPositionAsync(
      { accuracy: Location.Accuracy.Balanced, timeInterval: INTERVALO_DE_CAPTURA_MS, distanceInterval: 0 },
      (lugar) => {
        cola.encolar(aPosicion(lugar));
      },
    );
    return 'activa';
  }
  colaDeLaTarea = cola;
  await Location.startLocationUpdatesAsync(TAREA_DE_CAPTURA, {
    accuracy: Location.Accuracy.Balanced,
    timeInterval: INTERVALO_DE_CAPTURA_MS,
    distanceInterval: 0,
    foregroundService: {
      notificationTitle: 'HOPPIT está registrando tu ubicación',
      notificationBody: 'Solo mientras tenés una Ruta en curso.',
    },
  });
  return 'activa';
}

function detenerCapturaWeb(): void {
  if (reloj) clearInterval(reloj);
  reloj = null;
}

async function detenerCapturaEnPrimerPlano(): Promise<void> {
  suscripcionEnPrimerPlano?.remove();
  suscripcionEnPrimerPlano = null;
}

export async function detenerCaptura(): Promise<void> {
  if (EN_LA_WEB) {
    detenerCapturaWeb();
    return;
  }
  await detenerCapturaEnPrimerPlano();
  if (await TaskManager.isTaskRegisteredAsync(TAREA_DE_CAPTURA)) {
    await Location.stopLocationUpdatesAsync(TAREA_DE_CAPTURA);
  }
  colaDeLaTarea = null;
}
