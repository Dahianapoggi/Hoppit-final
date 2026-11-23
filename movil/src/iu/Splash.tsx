/**
 * La pantalla de inicio de la maqueta: el conejo gris se llena de ambar de
 * abajo hacia arriba mientras carga, con el nombre y la bajada. Tocarla la
 * saltea.
 */
import { useEffect, useRef } from 'react';
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from 'react-native';

import { COLOR, FUENTE, LLENO } from '../tema';

const ISOTIPO = require('../../assets/hoppit-isotipo.png');
const ALTO = 250;
const ANCHO = Math.round((ALTO * 689) / 751);
export const DURACION_DEL_INICIO_MS = 3200;

export default function Splash({ alTerminar }: { alTerminar: () => void }) {
  const relleno = useRef(new Animated.Value(0)).current;
  const entrada = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    Animated.timing(entrada, { toValue: 1, duration: 600, useNativeDriver: false }).start();
    Animated.timing(relleno, {
      toValue: 1, duration: 2100, delay: 500, easing: Easing.bezier(0.45, 0.05, 0.35, 1), useNativeDriver: false,
    }).start();
    const reloj = setTimeout(alTerminar, DURACION_DEL_INICIO_MS);
    return () => clearTimeout(reloj);
  }, []);

  const subir = { opacity: entrada, transform: [{ translateY: entrada.interpolate({ inputRange: [0, 1], outputRange: [10, 0] }) }] };

  return (
    <Pressable style={estilos.fondo} onPress={alTerminar} accessibilityRole="image" accessibilityLabel="HOPPIT, cargando">
      <View style={estilos.degradado} pointerEvents="none" />
      <View style={estilos.centro}>
        <Animated.View style={[{ width: ANCHO, height: ALTO }, { opacity: entrada, transform: [{ scale: entrada.interpolate({ inputRange: [0, 1], outputRange: [0.88, 1] }) }] }]}>
          <Image source={ISOTIPO} style={[estilos.conejo, estilos.gris]} resizeMode="contain" />
          {/* El conejo en color, recortado desde abajo: el alto visible crece. */}
          <Animated.View style={[estilos.recorte, { height: relleno.interpolate({ inputRange: [0, 1], outputRange: [0, ALTO] }) }]}>
            <Image source={ISOTIPO} style={[estilos.conejo, { position: 'absolute', bottom: 0 }]} resizeMode="contain" />
          </Animated.View>
        </Animated.View>
        <Animated.Text style={[estilos.titulo, subir]}>HOPPIT</Animated.Text>
        <Animated.Text style={[estilos.bajada, subir]}>Entregas de última milla</Animated.Text>
      </View>
      <Animated.Text style={[estilos.carga, subir]}>Cargando tu plan de distribución…</Animated.Text>
    </Pressable>
  );
}

const estilos = StyleSheet.create({
  fondo: { ...LLENO, zIndex: 1200, backgroundColor: COLOR.blanco, alignItems: 'center', justifyContent: 'center' },
  degradado: { position: 'absolute', left: 0, right: 0, bottom: 0, height: '45%', backgroundColor: COLOR.ambarSuave, opacity: 0.7 },
  centro: { alignItems: 'center', marginTop: -36 },
  conejo: { width: ANCHO, height: ALTO },
  gris: { tintColor: '#B9BCBF', opacity: 0.55 },
  recorte: { position: 'absolute', left: 0, right: 0, bottom: 0, overflow: 'hidden' },
  titulo: { fontFamily: FUENTE.logo, fontSize: 32, letterSpacing: 2, color: COLOR.grafito, marginTop: 24 },
  bajada: { fontFamily: FUENTE.sans, fontSize: 15, color: COLOR.secundario, marginTop: 6 },
  carga: { position: 'absolute', left: 0, right: 0, bottom: 96, textAlign: 'center', fontFamily: FUENTE.sansMedia, fontSize: 13, color: COLOR.ambarTexto },
});
