/**
 * Los iconos de la maqueta v6 del chofer, con los mismos trazos. Toman el
 * color que se les pasa.
 */
import Svg, { Circle, Path } from 'react-native-svg';

interface PropsDeIcono {
  tam?: number;
  color: string;
}

export const IconoPin = ({ tam = 15, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill={color}>
    <Path d="M12 2C8.1 2 5 5.1 5 9c0 5.2 7 13 7 13s7-7.8 7-13c0-3.9-3.1-7-7-7zm0 9.5A2.5 2.5 0 1 1 12 6.5a2.5 2.5 0 0 1 0 5z" />
  </Svg>
);

export const IconoPersona = ({ tam = 15, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill={color}>
    <Path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zm0 2c-4.4 0-8 2.2-8 5v1.5h16V19c0-2.8-3.6-5-8-5z" />
  </Svg>
);

export const IconoReloj = ({ tam = 13, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.2} strokeLinecap="round">
    <Circle cx="12" cy="12" r="9" />
    <Path d="M12 7v5l3 2" />
  </Svg>
);

export const IconoListo = ({ tam = 19, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
    <Path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
);

export const IconoUbicacion = ({ tam = 21, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill={color}>
    <Path d="M3.5 11 20.5 3.5 13 20.5l-2-7.5z" />
  </Svg>
);

export const IconoCapas = ({ tam = 21, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round">
    <Path d="M12 3 2 8l10 5 10-5-10-5zM2 13l10 5 10-5" />
  </Svg>
);

export const IconoProblema = ({ tam = 19, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill={color}>
    <Path d="M12 2.6c.6 0 1.2.3 1.5.9l8.4 14.6c.7 1.2-.2 2.8-1.6 2.8H3.7c-1.4 0-2.3-1.6-1.6-2.8L10.5 3.5c.3-.6.9-.9 1.5-.9zM12 9a1 1 0 0 0-1 1v4a1 1 0 1 0 2 0v-4a1 1 0 0 0-1-1zm0 8a1.2 1.2 0 1 0 0 2.4A1.2 1.2 0 0 0 12 17z" />
  </Svg>
);

/* Los de la barra inferior: mapa, lista de entregas, viajes y perfil. */
export const IconoMapa = ({ tam = 24, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinejoin="round" strokeLinecap="round">
    <Path d="M9 4 3 6.5v13.5L9 17.5l6 2.5 6-2.5V4l-6 2.5zM9 4v13.5M15 6.5V20" />
  </Svg>
);

export const IconoLista = ({ tam = 24, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round">
    <Path d="M9 6h11M9 12h11M9 18h11" />
    <Circle cx="4.5" cy="6" r="1.2" fill={color} stroke="none" />
    <Circle cx="4.5" cy="12" r="1.2" fill={color} stroke="none" />
    <Circle cx="4.5" cy="18" r="1.2" fill={color} stroke="none" />
  </Svg>
);

export const IconoViajes = ({ tam = 24, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round">
    <Circle cx="6" cy="18" r="2.5" />
    <Circle cx="18" cy="6" r="2.5" />
    <Path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5" />
  </Svg>
);

export const IconoPerfil = ({ tam = 24, color }: PropsDeIcono) => (
  <Svg width={tam} height={tam} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={1.8} strokeLinecap="round">
    <Circle cx="12" cy="8" r="4" />
    <Path d="M4 21a8 8 0 0 1 16 0" />
  </Svg>
);
