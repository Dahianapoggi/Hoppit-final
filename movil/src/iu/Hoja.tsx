/**
 * La hoja inferior de la maqueta v6: lo que hay que hacer ahora (la proxima
 * Parada, iniciar el viaje o el resumen del viaje terminado). Desde
 * 2026-11-02 las listas de Entregas y de Mis viajes son pestañas de la barra
 * inferior; los enlaces de la hoja llevan a ellas.
 */
import { useState } from 'react';
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from 'react-native';

import type { RutaPublicada } from '../api';
import {
  type EstadoDelViaje,
  MOTIVOS_DE_NO_ENTREGA,
  bultosDe,
  conteoDelViaje,
  estadoDeParada,
  llegadaEstimada,
  proximaParada,
  textoKm,
} from '../logica/viaje';
import { COLOR, FUENTE, SOMBRA_HOJA } from '../tema';
import { IconoListo, IconoPersona, IconoPin, IconoReloj } from './Iconos';

const ISOTIPO = require('../../assets/hoppit-isotipo.png');

export type Pestana = 'entregas' | 'viajes';

export interface ResultadoLocal {
  hora: string;
  motivo?: string;
}

export interface PropsDeLaHoja {
  ruta: RutaPublicada | null;
  estado: EstadoDelViaje;
  consultando: boolean;
  aviso: string | null;
  ocupado: boolean;
  /** Distancia en linea recta a la proxima Parada, si se conoce la posicion. */
  distanciaAProxima: number | null;
  resultados: Map<number, ResultadoLocal>;
  /** Lleva a la pestaña de Entregas o de Mis viajes. */
  alIrA: (pestana: Pestana) => void;
  alIniciar: () => void;
  alEntregar: () => void;
  alNoPude: (motivo: string) => void;
  /** El alto de la hoja, para ubicar "Avisar problema" justo arriba y dejar
      lugar en el mapa al encuadrar. */
  alMedirAlto: (alto: number) => void;
}

export default function Hoja(props: PropsDeLaHoja) {
  const [sinEntregar, setSinEntregar] = useState(false);
  const { ruta, estado } = props;

  const enlace = (pestana: Pestana, texto: string) => (
    <Pressable style={estilos.pie2Boton} onPress={() => props.alIrA(pestana)} accessibilityRole="button">
      <Text style={estilos.enlace}>{texto} ›</Text>
    </Pressable>
  );

  let heroe;
  if (estado === 'en_curso' && ruta) {
    const p = proximaParada(ruta);
    if (p && sinEntregar) {
      heroe = (
        <View style={estilos.heroe}>
          <Text style={estilos.clave}>No pude entregar</Text>
          <Text style={[estilos.titulo, { marginTop: 4 }]} numberOfLines={2}>{p.punto.rotulo.texto}</Text>
          <View style={estilos.motivos}>
            {MOTIVOS_DE_NO_ENTREGA.map((m) => (
              <Pressable key={m} style={({ pressed }) => [estilos.motivo, pressed && { backgroundColor: '#E5E5EA' }]}
                onPress={() => { setSinEntregar(false); props.alNoPude(m); }} accessibilityRole="button" disabled={props.ocupado}>
                <Text style={estilos.motivoTexto}>{m}</Text>
              </Pressable>
            ))}
          </View>
          <Pressable onPress={() => setSinEntregar(false)} accessibilityRole="button" style={{ alignSelf: 'flex-start' }}>
            <Text style={[estilos.enlace, { paddingVertical: 11 }]}>‹ Volver</Text>
          </Pressable>
        </View>
      );
    } else if (p) {
      const { total, hechas } = conteoDelViaje(ruta);
      const llegada = llegadaEstimada(ruta, p);
      const ordenes = p.ordenes.map((o) => o.id).join(', ');
      heroe = (
        <View style={estilos.heroe}>
          <View style={estilos.pasos}>
            {ruta.paradas.map((x) => {
              const e = estadoDeParada(x);
              return <View key={x.secuencia} style={[estilos.paso, e === 'entregada' && estilos.pasoHecho, e === 'fallida' && estilos.pasoMal, x === p && estilos.pasoActual]} />;
            })}
          </View>
          <View style={estilos.claveFila}>
            <Text style={estilos.clave}>Entrega {hechas + 1} de {total}</Text>
            <View style={estilos.punto} />
            <Text style={estilos.clave}>Próxima parada</Text>
          </View>
          <View style={estilos.heroeFila}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <Text style={estilos.titulo} numberOfLines={2}>{p.punto.rotulo.texto}</Text>
              <View style={estilos.meta}><IconoPin color={COLOR.secundario} /><Text style={estilos.metaTexto} numberOfLines={1}>Parada {p.secuencia} · {bultosDe(p)} {bultosDe(p) === 1 ? 'bulto' : 'bultos'}</Text></View>
              <View style={estilos.meta}><IconoPersona color={COLOR.secundario} /><Text style={estilos.metaTexto} numberOfLines={1}>{p.ordenes.length === 1 ? 'Orden' : 'Órdenes'} {ordenes}</Text></View>
            </View>
            <View style={estilos.llegada}>
              <Text style={estilos.llegadaClave}>LLEGADA</Text>
              <Text style={estilos.llegadaHora}>{llegada?.hora ?? '--:--'}</Text>
              <Text style={estilos.llegadaPie}>{props.distanciaAProxima != null ? textoKm(props.distanciaAProxima) : 'estimada'}</Text>
            </View>
          </View>
          {llegada ? (
            <View style={estilos.chips}>
              <View style={estilos.chip}><IconoReloj color={COLOR.chipTexto} /><Text style={estilos.chipTexto}>Entre {llegada.desde} y {llegada.hasta}</Text></View>
            </View>
          ) : null}
          <Pressable
            style={({ pressed }) => [estilos.cta, pressed && estilos.ctaApretado, props.ocupado && estilos.ctaInactivo]}
            onPress={props.alEntregar}
            disabled={props.ocupado}
            accessibilityRole="button"
          >
            {props.ocupado ? <ActivityIndicator color={COLOR.tinta} /> : (
              <View style={estilos.ctaFila}><IconoListo color={COLOR.tinta} /><Text style={estilos.ctaTexto}>Entregué</Text></View>
            )}
          </Pressable>
          <View style={estilos.pie2}>
            <Pressable style={estilos.pie2Boton} onPress={() => setSinEntregar(true)} accessibilityRole="button" disabled={props.ocupado}>
              <Text style={estilos.enlace}>No pude entregar</Text>
            </Pressable>
            <View style={estilos.divisor} />
            {enlace('entregas', 'Ver entregas')}
          </View>
        </View>
      );
    }
  }
  if (!heroe && estado === 'terminado' && ruta) {
    const { entregadas, fallidas } = conteoDelViaje(ruta);
    heroe = (
      <View style={estilos.heroe}>
        <View style={estilos.fin}>
          <View style={estilos.finIcono}><IconoListo tam={22} color={COLOR.verde} /></View>
          <View><Text style={estilos.titulo}>Viaje terminado</Text><Text style={estilos.subtitulo}>Ruta {ruta.vehiculo_id}</Text></View>
        </View>
        <View style={estilos.chips}>
          <View style={[estilos.chip, estilos.chipBien]}><View style={[estilos.chipPunto, { backgroundColor: COLOR.verde }]} /><Text style={[estilos.chipTexto, { color: COLOR.verde }]}>{entregadas} entregada{entregadas !== 1 ? 's' : ''}</Text></View>
          {fallidas ? <View style={[estilos.chip, estilos.chipMal]}><View style={[estilos.chipPunto, { backgroundColor: COLOR.rojo }]} /><Text style={[estilos.chipTexto, { color: COLOR.rojo }]}>{fallidas} sin entregar</Text></View> : null}
        </View>
        {fallidas ? <Text style={estilos.mini}>El despacho ve las entregas sin entregar en su pantalla.</Text> : null}
        <View style={estilos.pie2}>{enlace('viajes', 'Ver mis viajes')}</View>
      </View>
    );
  }
  if (!heroe) {
    const marca = (
      <View style={estilos.marca}>
        <Image source={ISOTIPO} style={estilos.marcaImagen} resizeMode="contain" />
        <View><Text style={estilos.marcaNombre}>HOPPIT</Text><Text style={estilos.marcaBajada}>Plan de Distribución · hoy</Text></View>
      </View>
    );
    if (estado === 'por_iniciar' && ruta) {
      const salida = ruta.hora_de_salida?.slice(0, 5);
      heroe = (
        <View style={estilos.heroe}>
          {marca}
          <Text style={estilos.clave}>Tu próximo viaje</Text>
          <Text style={[estilos.titulo, { marginTop: 4 }]}>Ruta {ruta.vehiculo_id}</Text>
          <View style={estilos.meta}><IconoPin color={COLOR.secundario} /><Text style={estilos.metaTexto} numberOfLines={1}>
            {ruta.paradas.length} entregas{ruta.distancia_m ? ` · ${textoKm(ruta.distancia_m)}` : ''} · {ruta.vehiculo_id}</Text></View>
          <View style={estilos.chips}>
            <View style={[estilos.chip, estilos.chipVamos]}><IconoReloj color={COLOR.ambarTexto} /><Text style={[estilos.chipTexto, { color: COLOR.ambarTexto }]}>{salida ? `Sale ${salida}` : 'Ya podés salir'}</Text></View>
          </View>
          <Pressable style={({ pressed }) => [estilos.cta, pressed && estilos.ctaApretado]} onPress={props.alIniciar} accessibilityRole="button">
            <Text style={estilos.ctaTexto}>Iniciar viaje</Text>
          </Pressable>
          <View style={estilos.pie2}>{enlace('viajes', 'Ver mis viajes')}</View>
        </View>
      );
    } else {
      heroe = (
        <View style={estilos.heroe}>
          {marca}
          <Text style={estilos.clave}>{props.consultando ? 'Consultando tu Ruta…' : 'Sin viaje asignado'}</Text>
          <Text style={[estilos.titulo, { marginTop: 4 }]}>Todavía no tenés una Ruta</Text>
          <Text style={estilos.subtitulo}>Cuando el despacho te asigne una, aparece acá.</Text>
          <View style={estilos.chips}>
            <View style={estilos.chip}><IconoReloj color={COLOR.chipTexto} /><Text style={estilos.chipTexto}>Se consulta cada 15 segundos</Text></View>
          </View>
          <Pressable style={[estilos.cta, estilos.ctaInactivo]} disabled accessibilityRole="button">
            <Text style={[estilos.ctaTexto, { color: COLOR.inactivo }]}>Esperando el despacho</Text>
          </Pressable>
        </View>
      );
    }
  }

  return (
    <View style={estilos.hoja} onLayout={(e) => props.alMedirAlto(Math.round(e.nativeEvent.layout.height))}>
      <View style={estilos.agarre}><View style={estilos.agarreBarra} /></View>
      {props.aviso ? (
        <View style={estilos.franja}><Text style={estilos.franjaTexto} numberOfLines={2}>{props.aviso}</Text></View>
      ) : null}
      {heroe}
    </View>
  );
}

const estilos = StyleSheet.create({
  hoja: {
    position: 'absolute', left: 0, right: 0, bottom: 0, zIndex: 700, maxHeight: '66%',
    backgroundColor: 'rgba(255,255,255,0.97)', borderTopLeftRadius: 28, borderTopRightRadius: 28, paddingBottom: 12, ...SOMBRA_HOJA,
  },
  agarre: { width: '100%', paddingTop: 8, paddingBottom: 6, alignItems: 'center' },
  agarreBarra: { width: 36, height: 5, borderRadius: 3, backgroundColor: '#C7C7CC' },
  heroe: { paddingHorizontal: 20, paddingBottom: 8 },
  franja: { marginHorizontal: 20, marginBottom: 8, paddingVertical: 10, paddingHorizontal: 14, borderRadius: 14, backgroundColor: COLOR.agrupado },
  franjaTexto: { fontFamily: FUENTE.sans, fontSize: 14, color: COLOR.tinta },
  marca: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 14 },
  marcaImagen: { width: 44, height: 44 },
  marcaNombre: { fontFamily: FUENTE.logo, fontSize: 17, letterSpacing: 1, color: COLOR.tinta, lineHeight: 20 },
  marcaBajada: { fontFamily: FUENTE.sans, fontSize: 12.5, color: COLOR.secundario, marginTop: 1 },
  pasos: { flexDirection: 'row', gap: 5, marginTop: 2, marginBottom: 12 },
  paso: { flex: 1, height: 4, borderRadius: 2, backgroundColor: COLOR.relleno },
  pasoHecho: { backgroundColor: COLOR.grafito },
  pasoMal: { backgroundColor: COLOR.rojo },
  pasoActual: { backgroundColor: COLOR.ambar },
  claveFila: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  clave: { fontFamily: FUENTE.sansSemi, fontSize: 13, color: COLOR.secundario },
  punto: { width: 3, height: 3, borderRadius: 2, backgroundColor: '#C7C7CC' },
  heroeFila: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'stretch', gap: 12, marginTop: 6 },
  titulo: { fontFamily: FUENTE.sansSemi, fontSize: 23, lineHeight: 27, letterSpacing: -0.3, color: COLOR.tinta },
  subtitulo: { fontFamily: FUENTE.sans, fontSize: 15, color: COLOR.secundario, marginTop: 3 },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 7, marginTop: 6, minWidth: 0 },
  metaTexto: { flex: 1, fontFamily: FUENTE.sans, fontSize: 14.5, color: COLOR.texto2 },
  llegada: {
    minWidth: 94, backgroundColor: COLOR.agrupado, borderWidth: 1, borderColor: COLOR.bordeAgrupado, borderRadius: 16,
    paddingVertical: 8, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center',
  },
  llegadaClave: { fontFamily: FUENTE.sansNegrita, fontSize: 10.5, letterSpacing: 0.7, color: COLOR.secundario },
  llegadaHora: { fontFamily: FUENTE.monoSemi, fontSize: 26, letterSpacing: -0.5, color: COLOR.tinta, lineHeight: 30 },
  llegadaPie: { fontFamily: FUENTE.sans, fontSize: 12.5, color: COLOR.secundario },
  chips: { flexDirection: 'row', gap: 8, flexWrap: 'wrap', marginTop: 12 },
  chip: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 28, paddingHorizontal: 11, borderRadius: 14, backgroundColor: COLOR.agrupado },
  chipBien: { backgroundColor: COLOR.verdeSuave },
  chipMal: { backgroundColor: COLOR.rojoSuave },
  chipVamos: { backgroundColor: COLOR.ambarSuave },
  chipPunto: { width: 7, height: 7, borderRadius: 4 },
  chipTexto: { fontFamily: FUENTE.sansSemi, fontSize: 13, color: COLOR.chipTexto },
  cta: {
    height: 52, borderRadius: 14, marginTop: 14, alignItems: 'center', justifyContent: 'center',
    backgroundColor: COLOR.ambar, borderWidth: 1, borderColor: COLOR.ambarBorde,
  },
  ctaApretado: { transform: [{ scale: 0.98 }], opacity: 0.85 },
  ctaInactivo: { backgroundColor: COLOR.relleno, borderColor: 'transparent' },
  ctaFila: { flexDirection: 'row', alignItems: 'center', gap: 7 },
  ctaTexto: { fontFamily: FUENTE.sansSemi, fontSize: 17, color: COLOR.tinta },
  pie2: { flexDirection: 'row', alignItems: 'stretch', marginTop: 14, marginHorizontal: -20, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: COLOR.separador },
  pie2Boton: { flex: 1, height: 48, alignItems: 'center', justifyContent: 'center' },
  divisor: { width: StyleSheet.hairlineWidth, backgroundColor: COLOR.separador },
  enlace: { fontFamily: FUENTE.sansMedia, fontSize: 16, color: COLOR.ambarTexto },
  motivos: { gap: 8, marginTop: 10 },
  motivo: { height: 52, borderRadius: 14, backgroundColor: COLOR.agrupado, justifyContent: 'center', paddingHorizontal: 16 },
  motivoTexto: { fontFamily: FUENTE.sansMedia, fontSize: 17, color: COLOR.tinta },
  fin: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  finIcono: { width: 42, height: 42, borderRadius: 21, backgroundColor: COLOR.verdeSuave, alignItems: 'center', justifyContent: 'center' },
  mini: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, marginTop: 10 },
});
