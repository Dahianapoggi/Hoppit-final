/**
 * La notificacion de arriba de la maqueta: entra, queda 3,4 s y se va. Cada
 * aviso nuevo (otro `n`) la vuelve a mostrar.
 */
import { useEffect, useRef } from 'react';
import { Animated, Image, StyleSheet, Text, View } from 'react-native';

import { COLOR, FUENTE } from '../tema';

const ISOTIPO = require('../../assets/hoppit-isotipo.png');
const DURACION_MS = 3400;

export interface AvisoBreve {
  texto: string;
  n: number;
}

export default function Aviso({ aviso, arriba }: { aviso: AvisoBreve | null; arriba: number }) {
  const valor = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!aviso) return;
    Animated.timing(valor, { toValue: 1, duration: 300, useNativeDriver: true }).start();
    const reloj = setTimeout(
      () => Animated.timing(valor, { toValue: 0, duration: 300, useNativeDriver: true }).start(),
      DURACION_MS,
    );
    return () => clearTimeout(reloj);
  }, [aviso?.n]);

  if (!aviso) return null;
  return (
    <Animated.View
      pointerEvents="none"
      accessibilityLiveRegion="polite"
      style={[
        estilos.aviso,
        { top: arriba, opacity: valor, transform: [{ translateY: valor.interpolate({ inputRange: [0, 1], outputRange: [-14, 0] }) }] },
      ]}
    >
      <View style={estilos.icono}><Image source={ISOTIPO} style={estilos.imagen} resizeMode="contain" /></View>
      <View style={{ flex: 1 }}>
        <Text style={estilos.marca}>HOPPIT</Text>
        <Text style={estilos.texto}>{aviso.texto}</Text>
      </View>
    </Animated.View>
  );
}

const estilos = StyleSheet.create({
  aviso: {
    position: 'absolute', left: 10, right: 10, zIndex: 2000, flexDirection: 'row', gap: 11, alignItems: 'center',
    paddingVertical: 11, paddingHorizontal: 14, borderRadius: 24, backgroundColor: 'rgba(255,255,255,0.98)',
    shadowColor: '#000', shadowOpacity: 0.22, shadowRadius: 30, shadowOffset: { width: 0, height: 8 }, elevation: 10,
  },
  icono: { width: 38, height: 38, borderRadius: 9, backgroundColor: COLOR.ambarSuave, padding: 4 },
  imagen: { width: '100%', height: '100%' },
  marca: { fontFamily: FUENTE.sansSemi, fontSize: 14, color: COLOR.tinta },
  texto: { fontFamily: FUENTE.sans, fontSize: 14, color: '#3C3C43', lineHeight: 17.5 },
});
