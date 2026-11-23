/**
 * Las pantallas antes del mapa: la direccion del servidor, el ingreso y la
 * aceptacion de los documentos pendientes. La maqueta v6 no las dibuja; se
 * arman con sus piezas (marca, grupos de campos, boton ambar con texto
 * grafito) para que se lean como parte de la misma aplicacion.
 */
import type { ReactNode } from 'react';
import { Image, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';

import type { DocumentoPendiente } from '../api';
import { COLOR, FUENTE } from '../tema';
import { IconoListo } from './Iconos';

const ISOTIPO = require('../../assets/hoppit-isotipo.png');

function Marco({ titulo, bajada, children, error }: { titulo: string; bajada?: string; children: ReactNode; error?: string }) {
  return (
    <KeyboardAvoidingView style={estilos.fondo} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={estilos.cuerpo} keyboardShouldPersistTaps="handled">
        <View style={estilos.marca}>
          <Image source={ISOTIPO} style={estilos.isotipo} resizeMode="contain" />
          <View>
            <Text style={estilos.marcaNombre}>HOPPIT</Text>
            <Text style={estilos.marcaBajada}>Aplicación del transportista</Text>
          </View>
        </View>
        <Text style={estilos.titulo}>{titulo}</Text>
        {bajada ? <Text style={estilos.bajada}>{bajada}</Text> : null}
        {children}
        {error ? <Text style={estilos.error} accessibilityRole="alert">{error}</Text> : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

export function Boton({ texto, activo = true, alPulsar, variante = 'ambar' }: {
  texto: string; activo?: boolean; alPulsar: () => void; variante?: 'ambar' | 'oscuro' | 'rojo' | 'suave';
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !activo }}
      disabled={!activo}
      onPress={alPulsar}
      style={({ pressed }) => [
        estilos.boton,
        variante === 'oscuro' && estilos.botonOscuro,
        variante === 'rojo' && estilos.botonRojo,
        variante === 'suave' && estilos.botonSuave,
        !activo && estilos.botonInactivo,
        pressed && activo && { transform: [{ scale: 0.98 }], opacity: 0.85 },
      ]}
    >
      <Text style={[
        estilos.botonTexto,
        (variante === 'oscuro' || variante === 'rojo') && { color: COLOR.blanco },
        variante === 'suave' && { color: COLOR.ambarTexto },
        !activo && { color: COLOR.inactivo },
      ]}>{texto}</Text>
    </Pressable>
  );
}

function Campo(props: { etiqueta: string; valor: string; alCambiar: (t: string) => void; secreto?: boolean; ayuda?: string; ultimo?: boolean }) {
  return (
    <View style={[estilos.fila, !props.ultimo && estilos.filaConLinea]}>
      <Text style={estilos.filaEtiqueta}>{props.etiqueta}</Text>
      <TextInput
        style={estilos.filaCampo}
        value={props.valor}
        onChangeText={props.alCambiar}
        placeholder={props.ayuda}
        placeholderTextColor="#A9A6A0"
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry={props.secreto}
        accessibilityLabel={props.etiqueta}
      />
    </View>
  );
}

export function PantallaServidor(props: {
  nucleo: string; telemetria: string; enLaWeb: boolean;
  alCambiarNucleo: (t: string) => void; alCambiarTelemetria: (t: string) => void; alSeguir: () => void;
}) {
  return (
    <Marco
      titulo="Servidor"
      bajada={props.enLaWeb
        ? 'Ya está cargada la dirección de esta página. Cambiala solo si tu operador te dio otra.'
        : 'Escribí la dirección que te dio tu operador. La aplicación no trae ninguna cargada.'}
    >
      <View style={estilos.grupo}>
        <Campo etiqueta="Dirección" valor={props.nucleo} alCambiar={props.alCambiarNucleo} ayuda="https://…" />
        <Campo etiqueta="Telemetría" valor={props.telemetria} alCambiar={props.alCambiarTelemetria} ayuda="Vacío: la misma" ultimo />
      </View>
      <Boton texto="Seguir" activo={/^https?:\/\/.+/.test(props.nucleo.trim())} alPulsar={props.alSeguir} />
    </Marco>
  );
}

export function PantallaIngreso(props: {
  usuario: string; contrasena: string; error: string;
  alCambiarUsuario: (t: string) => void; alCambiarContrasena: (t: string) => void; alIngresar: () => void; alCambiarServidor: () => void;
}) {
  return (
    <Marco titulo="Ingresar" bajada="Tu Ruta del día, Parada por Parada." error={props.error}>
      <View style={estilos.grupo}>
        <Campo etiqueta="Usuario" valor={props.usuario} alCambiar={props.alCambiarUsuario} ayuda="tu usuario" />
        <Campo etiqueta="Contraseña" valor={props.contrasena} alCambiar={props.alCambiarContrasena} secreto ayuda="••••••••" ultimo />
      </View>
      <Boton texto="Ingresar" activo={Boolean(props.usuario.trim() && props.contrasena)} alPulsar={props.alIngresar} />
      <Text style={estilos.nota}>¿Olvidaste la contraseña? Pedile al administrador que la restablezca.</Text>
      <Pressable onPress={props.alCambiarServidor} accessibilityRole="button" style={estilos.enlaceCaja}>
        <Text style={estilos.enlace}>Cambiar el servidor</Text>
      </Pressable>
    </Marco>
  );
}

export function PantallaAceptacion(props: {
  pendientes: DocumentoPendiente[]; aceptados: Set<string>; error: string;
  alAlternar: (documento: string) => void; alAceptar: () => void;
}) {
  const hechos = props.pendientes.filter((p) => props.aceptados.has(p.documento)).length;
  return (
    <Marco
      titulo="Antes de empezar"
      bajada="Cada documento se acepta por separado. Sin el de geolocalización, la aplicación no registra tu ubicación."
      error={props.error}
    >
      <View style={estilos.progreso}>
        <Text style={estilos.progresoCuenta}>{hechos} de {props.pendientes.length}</Text>
        <View style={estilos.progresoBarra}><View style={[estilos.progresoRelleno, { width: `${props.pendientes.length ? (hechos / props.pendientes.length) * 100 : 0}%` }]} /></View>
      </View>
      <View style={estilos.grupo}>
        {props.pendientes.map((p, i) => {
          const marcado = props.aceptados.has(p.documento);
          return (
            <Pressable
              key={p.documento}
              accessibilityRole="checkbox"
              accessibilityState={{ checked: marcado }}
              onPress={() => props.alAlternar(p.documento)}
              style={[estilos.documento, i < props.pendientes.length - 1 && estilos.filaConLinea, marcado && estilos.documentoHecho]}
            >
              <View style={[estilos.casilla, marcado && estilos.casillaHecha]}>{marcado ? <IconoListo tam={13} color={COLOR.blanco} /> : null}</View>
              <View style={{ flex: 1 }}>
                <Text style={estilos.documentoTitulo}>{p.titulo}</Text>
                <Text style={estilos.documentoMeta}>versión {p.version}</Text>
              </View>
            </Pressable>
          );
        })}
      </View>
      <Boton texto="Aceptar y continuar" activo={hechos === props.pendientes.length} alPulsar={props.alAceptar} />
    </Marco>
  );
}

const estilos = StyleSheet.create({
  fondo: { flex: 1, backgroundColor: COLOR.agrupado },
  cuerpo: { padding: 20, paddingTop: 64, gap: 14, maxWidth: 480, width: '100%', alignSelf: 'center' },
  marca: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 22 },
  isotipo: { width: 44, height: 44 },
  marcaNombre: { fontFamily: FUENTE.logo, fontSize: 17, letterSpacing: 1, color: COLOR.tinta },
  marcaBajada: { fontFamily: FUENTE.sans, fontSize: 12.5, color: COLOR.secundario, marginTop: 1 },
  titulo: { fontFamily: FUENTE.sansNegrita, fontSize: 34, letterSpacing: -0.6, color: COLOR.tinta },
  bajada: { fontFamily: FUENTE.sans, fontSize: 15, color: COLOR.secundario, lineHeight: 21, marginTop: -6 },
  grupo: { backgroundColor: COLOR.blanco, borderRadius: 12, overflow: 'hidden', marginTop: 6 },
  fila: { flexDirection: 'row', alignItems: 'center', minHeight: 48, paddingHorizontal: 16, gap: 12 },
  filaConLinea: { borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: COLOR.separador },
  filaEtiqueta: { fontFamily: FUENTE.sans, fontSize: 17, color: COLOR.tinta, width: 110 },
  filaCampo: { flex: 1, fontFamily: FUENTE.sans, fontSize: 17, color: COLOR.tinta, paddingVertical: 12 },
  boton: {
    height: 52, borderRadius: 14, alignItems: 'center', justifyContent: 'center', marginTop: 8,
    backgroundColor: COLOR.ambar, borderWidth: 1, borderColor: COLOR.ambarBorde,
  },
  botonOscuro: { backgroundColor: COLOR.grafito, borderColor: COLOR.grafito },
  botonRojo: { backgroundColor: COLOR.rojo, borderColor: COLOR.rojo },
  botonSuave: { backgroundColor: COLOR.ambarSuave, borderColor: COLOR.ambarLinea },
  botonInactivo: { backgroundColor: COLOR.relleno, borderColor: 'transparent' },
  botonTexto: { fontFamily: FUENTE.sansSemi, fontSize: 17, color: COLOR.tinta },
  nota: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, textAlign: 'center', lineHeight: 18 },
  enlaceCaja: { alignSelf: 'center', paddingVertical: 8 },
  enlace: { fontFamily: FUENTE.sansMedia, fontSize: 15.5, color: COLOR.ambarTexto },
  error: { fontFamily: FUENTE.sans, fontSize: 14, color: COLOR.rojo, backgroundColor: COLOR.rojoSuave, borderRadius: 12, padding: 12, overflow: 'hidden' },
  progreso: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  progresoCuenta: { fontFamily: FUENTE.mono, fontSize: 13, color: COLOR.secundario },
  progresoBarra: { flex: 1, height: 4, borderRadius: 2, backgroundColor: COLOR.relleno, overflow: 'hidden' },
  progresoRelleno: { height: '100%', backgroundColor: COLOR.verde, borderRadius: 2 },
  documento: { flexDirection: 'row', gap: 12, alignItems: 'center', padding: 14 },
  documentoHecho: { backgroundColor: COLOR.verdeSuave },
  casilla: { width: 22, height: 22, borderRadius: 6, borderWidth: 1.5, borderColor: '#C8C4BD', alignItems: 'center', justifyContent: 'center' },
  casillaHecha: { backgroundColor: COLOR.verde, borderColor: COLOR.verde },
  documentoTitulo: { fontFamily: FUENTE.sansSemi, fontSize: 15, color: COLOR.tinta },
  documentoMeta: { fontFamily: FUENTE.mono, fontSize: 11.5, color: COLOR.secundario, marginTop: 2 },
});
