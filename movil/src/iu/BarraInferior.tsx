/**
 * La barra de navegacion de abajo (2026-11-02): cuatro
 * pestañas fijas para pasar entre el mapa, las entregas, los viajes y el
 * perfil. La pestaña elegida va en grafito con su marca ambar arriba; el
 * resto, en gris. Entregas lleva la cuenta de las que faltan.
 */
import type { ComponentType } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import { COLOR, FUENTE } from '../tema';
import { IconoLista, IconoMapa, IconoPerfil, IconoViajes } from './Iconos';

export type Seccion = 'mapa' | 'entregas' | 'viajes' | 'perfil';

const SECCIONES: { clave: Seccion; texto: string; Icono: ComponentType<{ tam?: number; color: string }> }[] = [
  { clave: 'mapa', texto: 'Mapa', Icono: IconoMapa },
  { clave: 'entregas', texto: 'Entregas', Icono: IconoLista },
  { clave: 'viajes', texto: 'Viajes', Icono: IconoViajes },
  { clave: 'perfil', texto: 'Perfil', Icono: IconoPerfil },
];

/* El espacio del indicador de inicio del iPhone, debajo de la barra. */
const ABAJO = Platform.OS === 'ios' ? 22 : 6;

export default function BarraInferior({ actual, pendientes, alElegir }: {
  actual: Seccion;
  pendientes: number;
  alElegir: (seccion: Seccion) => void;
}) {
  return (
    <View style={estilos.barra} accessibilityRole="tablist">
      {SECCIONES.map(({ clave, texto, Icono }) => {
        const elegida = clave === actual;
        const color = elegida ? COLOR.grafito : COLOR.secundario;
        return (
          <Pressable
            key={clave}
            style={estilos.pestana}
            onPress={() => alElegir(clave)}
            accessibilityRole="tab"
            accessibilityState={{ selected: elegida }}
            accessibilityLabel={clave === 'entregas' && pendientes ? `${texto}, ${pendientes} pendientes` : texto}
          >
            <View style={[estilos.marca, elegida && estilos.marcaElegida]} />
            <View>
              <Icono tam={24} color={color} />
              {clave === 'entregas' && pendientes > 0 ? (
                <View style={estilos.cuenta}><Text style={estilos.cuentaTexto}>{pendientes}</Text></View>
              ) : null}
            </View>
            <Text style={[estilos.texto, { color }, elegida && { fontFamily: FUENTE.sansSemi }]}>{texto}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const estilos = StyleSheet.create({
  barra: {
    flexDirection: 'row', backgroundColor: COLOR.blanco, paddingBottom: ABAJO,
    borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: COLOR.separador, zIndex: 800,
  },
  pestana: { flex: 1, alignItems: 'center', paddingTop: 6, paddingBottom: 6, gap: 3 },
  marca: { position: 'absolute', top: 0, width: 28, height: 3, borderBottomLeftRadius: 3, borderBottomRightRadius: 3 },
  marcaElegida: { backgroundColor: COLOR.ambar },
  texto: { fontFamily: FUENTE.sansMedia, fontSize: 11.5 },
  cuenta: {
    position: 'absolute', top: -4, right: -10, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 5,
    backgroundColor: COLOR.ambar, alignItems: 'center', justifyContent: 'center',
  },
  cuentaTexto: { fontFamily: FUENTE.monoSemi, fontSize: 10.5, color: COLOR.tinta },
});
