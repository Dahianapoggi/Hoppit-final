/**
 * La posicion del transportista para dibujarla en el mapa ("vos estas aca").
 * Es solo para la pantalla: lo que se registra y se envia es la captura de
 * `captura.ts`, cada INTERVALO_DE_CAPTURA_MS. Se sigue mientras el viaje esta en curso, con el
 * mismo permiso que pidio la captura.
 */
import * as Location from 'expo-location';

import { EN_LA_WEB } from './captura';

export interface Posicion {
  lat: number;
  lon: number;
}

/** Empieza a seguir la posicion; devuelve como dejar de seguirla. Sin
    permiso o sin geolocalizacion, no avisa nada y no falla. */
export async function seguirPosicion(alMoverse: (p: Posicion) => void): Promise<() => void> {
  if (EN_LA_WEB) {
    if (typeof navigator === 'undefined' || !('geolocation' in navigator)) return () => {};
    const id = navigator.geolocation.watchPosition(
      (lugar) => alMoverse({ lat: lugar.coords.latitude, lon: lugar.coords.longitude }),
      () => {},
      { enableHighAccuracy: true, maximumAge: 10_000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }
  const permiso = await Location.getForegroundPermissionsAsync();
  if (permiso.status !== 'granted') return () => {};
  const suscripcion = await Location.watchPositionAsync(
    { accuracy: Location.Accuracy.Balanced, timeInterval: 5_000, distanceInterval: 10 },
    (lugar) => alMoverse({ lat: lugar.coords.latitude, lon: lugar.coords.longitude }),
  );
  return () => suscripcion.remove();
}
