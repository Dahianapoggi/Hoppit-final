/**
 * "Avisar problema" de la maqueta: primero que pasa, despues donde. Lo que se
 * elige se declara como Incidente sobre la Ruta del transportista; el lugar
 * viaja en la descripcion, porque el contrato no tiene un punto del Incidente.
 */
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { PROBLEMAS } from '../logica/viaje';
import { COLOR, FUENTE, LLENO } from '../tema';

export type PasoDelProblema = 'que' | 'donde';

export default function Problema(props: {
  paso: PasoDelProblema;
  elegido: string;
  alElegir: (texto: string) => void;
  alEnMiCamino: () => void;
  alMarcarEnElMapa: () => void;
  alVolver: () => void;
  alCerrar: () => void;
}) {
  return (
    <>
      <Pressable style={estilos.velo} onPress={props.alCerrar} accessibilityLabel="Cerrar" />
      <View style={estilos.hoja} accessibilityViewIsModal>
        <View style={estilos.cabeza}>
          <Text style={estilos.titulo}>{props.paso === 'que' ? '¿Qué pasa?' : `${props.elegido}: ¿dónde?`}</Text>
          <Pressable style={estilos.cerrar} onPress={props.alCerrar} accessibilityRole="button" accessibilityLabel="Cerrar">
            <Text style={estilos.cerrarTexto}>✕</Text>
          </Pressable>
        </View>
        {props.paso === 'que' ? (
          <View style={estilos.grilla}>
            {PROBLEMAS.map((p) => (
              <Pressable key={p.texto} style={({ pressed }) => [estilos.grande, pressed && { backgroundColor: '#E5E5EA' }]}
                onPress={() => props.alElegir(p.texto)} accessibilityRole="button">
                <Text style={estilos.grandeTexto}>{p.texto}</Text>
              </Pressable>
            ))}
          </View>
        ) : (
          <>
            <Pressable style={[estilos.cta, estilos.ctaRojo]} onPress={props.alEnMiCamino} accessibilityRole="button">
              <Text style={[estilos.ctaTexto, { color: COLOR.blanco }]}>Está en mi camino</Text>
            </Pressable>
            <Pressable style={[estilos.cta, estilos.ctaSuave]} onPress={props.alMarcarEnElMapa} accessibilityRole="button">
              <Text style={[estilos.ctaTexto, { color: COLOR.ambarTexto }]}>Lo marco en el mapa</Text>
            </Pressable>
            <Pressable onPress={props.alVolver} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
              <Text style={estilos.enlace}>‹ Cambiar el problema</Text>
            </Pressable>
          </>
        )}
      </View>
    </>
  );
}

const estilos = StyleSheet.create({
  velo: { ...LLENO, zIndex: 1090, backgroundColor: 'rgba(0,0,0,0.28)' },
  hoja: {
    position: 'absolute', left: 8, right: 8, bottom: 8, zIndex: 1100, backgroundColor: 'rgba(255,255,255,0.98)',
    borderRadius: 46, paddingTop: 20, paddingHorizontal: 18, paddingBottom: 30,
    shadowColor: '#000', shadowOpacity: 0.28, shadowRadius: 40, shadowOffset: { width: 0, height: 10 }, elevation: 16,
  },
  cabeza: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 },
  titulo: { flex: 1, fontFamily: FUENTE.sansNegrita, fontSize: 20, letterSpacing: -0.3, color: COLOR.tinta },
  cerrar: { width: 30, height: 30, borderRadius: 15, backgroundColor: COLOR.relleno, alignItems: 'center', justifyContent: 'center' },
  cerrarTexto: { fontFamily: FUENTE.sansNegrita, fontSize: 14, color: COLOR.secundario },
  grilla: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  grande: { width: '48.8%', flexGrow: 1, flexBasis: '45%', height: 54, borderRadius: 14, backgroundColor: COLOR.agrupado, justifyContent: 'center', paddingHorizontal: 12 },
  grandeTexto: { fontFamily: FUENTE.sansMedia, fontSize: 16, color: COLOR.tinta, textAlign: 'center' },
  cta: { height: 52, borderRadius: 14, marginTop: 14, alignItems: 'center', justifyContent: 'center', borderWidth: 1 },
  ctaRojo: { backgroundColor: COLOR.rojo, borderColor: COLOR.rojo, marginTop: 0 },
  ctaSuave: { backgroundColor: COLOR.ambarSuave, borderColor: COLOR.ambarLinea },
  ctaTexto: { fontFamily: FUENTE.sansSemi, fontSize: 17 },
  enlace: { fontFamily: FUENTE.sansMedia, fontSize: 15.5, color: COLOR.ambarTexto, paddingVertical: 11, paddingHorizontal: 2 },
});
