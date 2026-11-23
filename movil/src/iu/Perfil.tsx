/**
 * El perfil del transportista, estilo Ajustes de iOS como en la maqueta: quien
 * es, como va el dia, el Vehiculo, preferencias de la pantalla, el estado de
 * la captura de ubicacion y la salida. Desde 2026-11-02 es una pestaña de la
 * barra inferior: no entra desde la derecha ni lleva "‹ Mapa".
 */
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import { COLOR, FUENTE } from '../tema';

const ISOTIPO = require('../../assets/hoppit-isotipo.png');

export interface DatosDelPerfil {
  nombre: string;
  iniciales: string;
  vehiculo: string | null;
  sesionHasta: string | null;
  entregadas: number;
  total: number;
  sinEntregar: number;
  viajesHechos: number;
  viajes: number;
  textoGrande: boolean;
  captura: string;
  enCola: number;
  /** Resultados de Parada declarados sin conexion y todavia sin enviar (CU-06, 4a). */
  resultadosPorEnviar: number;
  ultimoEnvio: string;
  descartadas: number;
  servidor: string;
  version: string;
}

export default function Perfil(props: {
  datos: DatosDelPerfil;
  /** Donde empieza el contenido, debajo de la barra de estado. */
  arriba: number;
  alTextoGrande: () => void;
  alSalir: () => void;
}) {
  const d = props.datos;

  return (
    <View style={estilos.fondo}>
      <ScrollView contentContainerStyle={{ paddingTop: props.arriba + 8, paddingBottom: 24 }}>
        <Text style={estilos.grande}>Perfil</Text>

        <View style={estilos.tarjeta}>
          <View style={estilos.quien}>
            <View style={estilos.avatar}><Text style={estilos.avatarTexto}>{d.iniciales}</Text></View>
            <View style={{ flex: 1 }}>
              <Text style={estilos.nombre}>{d.nombre}</Text>
              <Text style={estilos.rol}>Transportista{d.vehiculo ? ` · ${d.vehiculo}` : ''}</Text>
            </View>
          </View>
        </View>

        <View style={estilos.cifras}>
          <View style={estilos.cifra}><Text style={estilos.cifraValor}>{d.entregadas}/{d.total}</Text><Text style={estilos.cifraClave}>Entregas hoy</Text></View>
          <View style={[estilos.cifra, estilos.cifraLinea]}><Text style={estilos.cifraValor}>{d.sinEntregar}</Text><Text style={estilos.cifraClave}>Sin entregar</Text></View>
          <View style={[estilos.cifra, estilos.cifraLinea]}><Text style={estilos.cifraValor}>{d.viajesHechos}/{d.viajes}</Text><Text style={estilos.cifraClave}>Viajes</Text></View>
        </View>

        <View style={estilos.tarjeta}>
          <Fila clave="Vehículo" valor={d.vehiculo ?? 'Sin Ruta asignada'} />
          <Fila clave="Sesión" valor={d.sesionHasta ?? '—'} linea />
        </View>

        <View style={estilos.tarjeta}>
          <Pressable style={estilos.fila} onPress={props.alTextoGrande} accessibilityRole="switch" accessibilityState={{ checked: d.textoGrande }}>
            <Text style={estilos.filaClave}>Texto grande</Text>
            <View style={[estilos.interruptor, d.textoGrande && estilos.interruptorOn]}>
              <View style={[estilos.perilla, d.textoGrande && { left: 22 }]} />
            </View>
          </Pressable>
        </View>

        <Text style={estilos.seccion}>UBICACIÓN</Text>
        <View style={estilos.tarjeta}>
          <Fila clave="Captura" valor={d.captura} />
          <Fila clave="Por enviar" valor={`${d.enCola} posiciones`} linea />
          {d.resultadosPorEnviar > 0 ? (
            <Fila clave="Resultados por enviar" valor={`${d.resultadosPorEnviar} (guardados sin conexión)`} linea />
          ) : null}
          <Fila clave="Último envío" valor={d.ultimoEnvio} linea />
          {d.descartadas > 0 ? <Fila clave="Descartadas" valor={`${d.descartadas} viejas (memoria llena)`} linea /> : null}
        </View>
        <Text style={estilos.notaSeccion}>La ubicación se registra solo mientras tenés una Ruta en curso.</Text>

        <View style={estilos.tarjeta}>
          <Pressable style={estilos.fila} onPress={props.alSalir} accessibilityRole="button">
            <Text style={estilos.salir}>Cerrar sesión</Text>
          </Pressable>
        </View>

        <View style={estilos.acerca}>
          <Image source={ISOTIPO} style={estilos.acercaImagen} resizeMode="contain" />
          <Text style={estilos.acercaNombre}>HOPPIT</Text>
          <Text style={estilos.acercaTexto}>Versión {d.version} · {d.servidor}</Text>
        </View>
      </ScrollView>
    </View>
  );
}

function Fila({ clave, valor, linea }: { clave: string; valor: string; linea?: boolean }) {
  return (
    <View style={[estilos.fila, linea && estilos.filaLinea]}>
      <Text style={estilos.filaClave}>{clave}</Text>
      <Text style={estilos.filaValor} numberOfLines={2}>{valor}</Text>
    </View>
  );
}

const estilos = StyleSheet.create({
  fondo: { flex: 1, backgroundColor: COLOR.agrupado },
  grande: { fontFamily: FUENTE.sansNegrita, fontSize: 34, letterSpacing: -0.6, color: COLOR.tinta, paddingTop: 2, paddingHorizontal: 20, paddingBottom: 14 },
  tarjeta: { marginHorizontal: 16, marginBottom: 18, backgroundColor: COLOR.blanco, borderRadius: 12, overflow: 'hidden' },
  quien: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, paddingHorizontal: 16 },
  avatar: { width: 62, height: 62, borderRadius: 31, backgroundColor: COLOR.crema, alignItems: 'center', justifyContent: 'center' },
  avatarTexto: { fontFamily: FUENTE.sansSemi, fontSize: 24, color: COLOR.grafito },
  nombre: { fontFamily: FUENTE.sansSemi, fontSize: 20, color: COLOR.tinta },
  rol: { fontFamily: FUENTE.sans, fontSize: 14, color: COLOR.secundario },
  cifras: { flexDirection: 'row', marginHorizontal: 16, marginBottom: 18, backgroundColor: COLOR.blanco, borderRadius: 12, overflow: 'hidden' },
  cifra: { flex: 1, paddingVertical: 14, paddingHorizontal: 6, alignItems: 'center' },
  cifraLinea: { borderLeftWidth: StyleSheet.hairlineWidth, borderLeftColor: COLOR.separador },
  cifraValor: { fontFamily: FUENTE.monoSemi, fontSize: 22, letterSpacing: -0.4, color: COLOR.tinta },
  cifraClave: { fontFamily: FUENTE.sans, fontSize: 12, color: COLOR.secundario },
  fila: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, paddingVertical: 12, paddingHorizontal: 16, minHeight: 48 },
  filaLinea: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: COLOR.separador, marginLeft: 16, paddingLeft: 0 },
  filaClave: { fontFamily: FUENTE.sans, fontSize: 17, color: COLOR.tinta },
  filaValor: { flexShrink: 1, fontFamily: FUENTE.sans, fontSize: 16, color: COLOR.secundario, textAlign: 'right' },
  interruptor: { width: 51, height: 31, borderRadius: 16, backgroundColor: '#E9E9EA' },
  interruptorOn: { backgroundColor: COLOR.verde },
  perilla: {
    position: 'absolute', left: 2, top: 2, width: 27, height: 27, borderRadius: 14, backgroundColor: COLOR.blanco,
    shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 8, shadowOffset: { width: 0, height: 3 }, elevation: 2,
  },
  seccion: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, marginHorizontal: 32, marginBottom: 6 },
  notaSeccion: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, marginHorizontal: 32, marginTop: -12, marginBottom: 18 },
  salir: { flex: 1, fontFamily: FUENTE.sans, fontSize: 17, color: COLOR.rojo, textAlign: 'center' },
  acerca: { alignItems: 'center', gap: 4, paddingTop: 2, paddingHorizontal: 20, paddingBottom: 44 },
  acercaImagen: { width: 58, height: 58, marginBottom: 4 },
  acercaNombre: { fontFamily: FUENTE.logo, fontSize: 14, letterSpacing: 2, color: COLOR.tinta },
  acercaTexto: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, textAlign: 'center' },
});
