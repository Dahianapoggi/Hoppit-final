/**
 * Configuracion de la aplicacion del transportista (2026-10-27B, M).
 *
 * El identificador del paquete de Android no se escribe aca: llega por
 * HOPPIT_ANDROID_PACKAGE al compilar (regla 8 del encargo). La version web
 * no lo necesita.
 */
import type { ExpoConfig } from 'expo/config';

const configuracion: ExpoConfig = {
  name: 'HOPPIT Transportista',
  slug: 'hoppit-transportista',
  version: '1.0.0',
  orientation: 'portrait',
  icon: './assets/icon.png',
  userInterfaceStyle: 'light',
  android: {
    package: process.env.HOPPIT_ANDROID_PACKAGE,
    adaptiveIcon: {
      backgroundColor: '#E8ECEE',
      foregroundImage: './assets/android-icon-foreground.png',
      backgroundImage: './assets/android-icon-background.png',
      monochromeImage: './assets/android-icon-monochrome.png',
    },
    permissions: [
      'ACCESS_COARSE_LOCATION',
      'ACCESS_FINE_LOCATION',
      'ACCESS_BACKGROUND_LOCATION',
      'FOREGROUND_SERVICE',
      'FOREGROUND_SERVICE_LOCATION',
    ],
  },
  web: { favicon: './assets/favicon.png', output: 'single' },
  // La version web la sirve el borde en /app/ (2026-10-27C, W): Expo antepone
  // esta ruta a los enlaces de todos los recursos del paquete.
  experiments: { baseUrl: '/app' },
  plugins: [
    [
      'expo-location',
      {
        locationAlwaysAndWhenInUsePermission:
          'HOPPIT registra tu ubicación solo mientras tenés una Ruta en curso, para que el operador vea el avance.',
        isAndroidBackgroundLocationEnabled: true,
        isAndroidForegroundServiceEnabled: true,
      },
    ],
    'expo-sqlite',
    // IBM Plex y Chakra Petch, las tipografias de la marca (rediseño de 2026-11).
    'expo-font',
  ],
};

export default configuracion;
