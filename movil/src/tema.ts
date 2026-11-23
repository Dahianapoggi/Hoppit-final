/**
 * Los tokens de la marca para la aplicacion del transportista (tesis, tabla
 * 12.XVIII; maqueta v6 del chofer aprobada en 2026-11). Son los mismos de la
 * consola del operador: ambar para la accion principal con texto grafito
 * (nunca blanco sobre ambar), grafito para las Paradas en el mapa, y azul
 * solo para "vos estas aca".
 */
import { Platform } from 'react-native';

export const COLOR = {
  ambar: '#F0A92D',
  ambarBorde: '#DE9A22',
  ambarSuave: '#FDF3E1',
  ambarLinea: '#F3D7A6',
  ambarTexto: '#8A5B0A',
  crema: '#F9E0B4',
  grafito: '#34383A',
  tinta: '#1B1B1B',
  secundario: '#6B7075',
  texto2: '#48484A',
  separador: 'rgba(52,56,58,0.14)',
  relleno: 'rgba(52,56,58,0.08)',
  agrupado: '#F5F4F1',
  bordeAgrupado: '#ECE9E4',
  blanco: '#FFFFFF',
  verde: '#2E7350',
  verdeSuave: '#E4F1E9',
  rojo: '#A8322B',
  rojoSuave: '#F8E6E4',
  gps: '#2F5D8A',
  hecha: '#B9BCBF',
  inactivo: 'rgba(60,60,67,0.3)',
  chipTexto: '#3B3F42',
} as const;

/* Las familias que carga App.tsx con expo-font. Mientras no cargaron, el
   sistema usa la suya: los nombres no rompen nada. */
export const FUENTE = {
  sans: 'IBMPlexSans_400Regular',
  sansMedia: 'IBMPlexSans_500Medium',
  sansSemi: 'IBMPlexSans_600SemiBold',
  sansNegrita: 'IBMPlexSans_700Bold',
  mono: 'IBMPlexMono_500Medium',
  monoSemi: 'IBMPlexMono_600SemiBold',
  logo: 'ChakraPetch_700Bold',
} as const;

/* Sombra suave de las piezas que flotan sobre el mapa. */
export const SOMBRA = Platform.select({
  web: { boxShadow: '0 1px 8px rgba(0,0,0,0.16)' },
  default: { shadowColor: '#000', shadowOpacity: 0.16, shadowRadius: 8, shadowOffset: { width: 0, height: 1 }, elevation: 4 },
}) as object;

export const SOMBRA_HOJA = Platform.select({
  web: { boxShadow: '0 -4px 24px rgba(0,0,0,0.12)' },
  default: { shadowColor: '#000', shadowOpacity: 0.12, shadowRadius: 24, shadowOffset: { width: 0, height: -4 }, elevation: 12 },
}) as object;

/* Ocupa todo el contenedor. `StyleSheet.absoluteFillObject` ya no existe en
   esta version de React Native. */
export const LLENO = { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 } as const;
