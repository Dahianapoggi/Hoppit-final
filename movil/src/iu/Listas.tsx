/**
 * Las pestañas Entregas y Viajes de la barra inferior (2026-11-02): las
 * mismas filas que se desplegaban en la hoja de la maqueta, en pantalla
 * completa y con el titulo grande de iOS. Tocar una entrega la muestra en el
 * mapa.
 */
import type { ReactNode } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

import type { RutaPublicada } from '../api';
import {
  type EstadoDelViaje,
  bultosDe,
  conteoDelViaje,
  estadoDeParada,
  llegadaEstimada,
  proximaParada,
  textoKm,
} from '../logica/viaje';
import { COLOR, FUENTE } from '../tema';
import type { ResultadoLocal } from './Hoja';
import { IconoListo } from './Iconos';

function Marco({ titulo, bajada, arriba, children }: { titulo: string; bajada?: string; arriba: number; children: ReactNode }) {
  return (
    <ScrollView style={estilos.fondo} contentContainerStyle={{ paddingTop: arriba + 8, paddingBottom: 24 }}>
      <Text style={estilos.titulo}>{titulo}</Text>
      {bajada ? <Text style={estilos.bajada}>{bajada}</Text> : null}
      {children}
    </ScrollView>
  );
}

export function PantallaEntregas({ ruta, resultados, arriba, alVerParada }: {
  ruta: RutaPublicada | null;
  resultados: Map<number, ResultadoLocal>;
  arriba: number;
  alVerParada: (secuencia: number) => void;
}) {
  if (!ruta) {
    return (
      <Marco titulo="Entregas" arriba={arriba}>
        <Text style={estilos.vacio}>Todavía no tenés una Ruta. Cuando el despacho te asigne una, sus entregas aparecen acá.</Text>
      </Marco>
    );
  }
  const siguiente = proximaParada(ruta);
  const { total, hechas } = conteoDelViaje(ruta);
  return (
    <Marco titulo="Entregas" bajada={`Ruta ${ruta.vehiculo_id} · ${hechas} de ${total} hechas`} arriba={arriba}>
      <View style={estilos.tarjeta}>
        {ruta.paradas.map((p, i) => {
          const e = estadoDeParada(p);
          const local = resultados.get(p.secuencia);
          const llegada = llegadaEstimada(ruta, p);
          return (
            <Pressable
              key={p.secuencia}
              style={({ pressed }) => [estilos.fila, i > 0 && estilos.filaLinea, pressed && { backgroundColor: COLOR.agrupado }]}
              onPress={() => alVerParada(p.secuencia)}
              accessibilityRole="button"
              accessibilityHint="La muestra en el mapa"
            >
              <View style={[estilos.numero, p === siguiente && estilos.numeroSiguiente, e === 'entregada' && estilos.numeroHecho, e === 'fallida' && estilos.numeroMal]}>
                {e === 'entregada'
                  ? <IconoListo tam={14} color={COLOR.blanco} />
                  : <Text style={estilos.numeroTexto}>{e === 'fallida' ? '!' : p.secuencia}</Text>}
              </View>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text style={estilos.filaTitulo} numberOfLines={1}>{p.punto.rotulo.texto}</Text>
                <Text style={estilos.filaSub} numberOfLines={1}>
                  {e === 'entregada' ? 'Entregada'
                    : e === 'fallida' ? (local?.motivo ?? 'Sin entregar')
                      : llegada ? `Entre ${llegada.desde} y ${llegada.hasta}` : `${bultosDe(p)} bultos`}
                </Text>
              </View>
              {e === 'pendiente'
                ? <Text style={estilos.filaDerecha}>{llegada?.hora ?? '--:--'}</Text>
                : <Text style={estilos.filaDerechaTenue}>{e === 'entregada' ? (local?.hora ?? 'Hecha') : 'Sin entregar'}</Text>}
              <Text style={estilos.chevron}>›</Text>
            </Pressable>
          );
        })}
      </View>
      <Text style={estilos.pie}>Tocá una entrega para verla en el mapa.</Text>
    </Marco>
  );
}

export function PantallaViajes({ ruta, estado, arriba, alVerMapa }: {
  ruta: RutaPublicada | null;
  estado: EstadoDelViaje;
  arriba: number;
  alVerMapa: () => void;
}) {
  if (!ruta) {
    return (
      <Marco titulo="Mis viajes" arriba={arriba}>
        <Text style={estilos.vacio}>Hoy no tenés viajes asignados.</Text>
      </Marco>
    );
  }
  const { total, hechas, entregadas, fallidas } = conteoDelViaje(ruta);
  const cuando = estado === 'en_curso' ? 'En curso' : estado === 'terminado' ? 'Terminado' : ruta.hora_de_salida ? `Sale ${ruta.hora_de_salida.slice(0, 5)}` : 'Hoy';
  return (
    <Marco titulo="Mis viajes" bajada="Plan de Distribución de hoy" arriba={arriba}>
      <View style={estilos.tarjeta}>
        <Pressable style={({ pressed }) => [estilos.fila, pressed && { backgroundColor: COLOR.agrupado }]} onPress={alVerMapa} accessibilityRole="button" accessibilityHint="Lo muestra en el mapa">
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={estilos.filaTitulo} numberOfLines={1}><Text style={{ color: COLOR.ambar, fontSize: 10 }}>● </Text>Ruta {ruta.vehiculo_id}</Text>
            <Text style={estilos.filaSub}>
              {hechas}/{total} entregas{ruta.distancia_m ? ` · ${textoKm(ruta.distancia_m)}` : ''}
              {fallidas ? ` · ${entregadas} entregadas, ${fallidas} sin entregar` : ''}
            </Text>
          </View>
          <Text style={estilos.filaDerechaTenue}>{cuando}</Text>
          <Text style={estilos.chevron}>›</Text>
        </Pressable>
      </View>
      <Text style={estilos.pie}>El despacho te asigna una Ruta por vez. Tocala para verla en el mapa.</Text>
    </Marco>
  );
}

const estilos = StyleSheet.create({
  fondo: { flex: 1, backgroundColor: COLOR.agrupado },
  titulo: { fontFamily: FUENTE.sansNegrita, fontSize: 34, letterSpacing: -0.6, color: COLOR.tinta, paddingHorizontal: 20 },
  bajada: { fontFamily: FUENTE.sans, fontSize: 15, color: COLOR.secundario, paddingHorizontal: 20, marginTop: 2 },
  tarjeta: { marginHorizontal: 16, marginTop: 18, backgroundColor: COLOR.blanco, borderRadius: 12, overflow: 'hidden' },
  fila: { flexDirection: 'row', gap: 14, alignItems: 'center', paddingVertical: 12, paddingHorizontal: 16 },
  filaLinea: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: COLOR.separador },
  numero: { width: 28, height: 28, borderRadius: 14, backgroundColor: COLOR.grafito, alignItems: 'center', justifyContent: 'center' },
  numeroSiguiente: { backgroundColor: COLOR.verde },
  numeroHecho: { backgroundColor: '#AEAEB2' },
  numeroMal: { backgroundColor: COLOR.rojo },
  numeroTexto: { fontFamily: FUENTE.monoSemi, fontSize: 13, color: COLOR.blanco },
  filaTitulo: { fontFamily: FUENTE.sansSemi, fontSize: 16.5, color: COLOR.tinta },
  filaSub: { fontFamily: FUENTE.sans, fontSize: 13.5, color: COLOR.secundario },
  filaDerecha: { fontFamily: FUENTE.monoSemi, fontSize: 16, color: COLOR.tinta },
  filaDerechaTenue: { fontFamily: FUENTE.sansMedia, fontSize: 14, color: COLOR.secundario },
  chevron: { fontSize: 22, color: '#C7C7CC', marginLeft: -6 },
  vacio: { fontFamily: FUENTE.sans, fontSize: 15, color: COLOR.secundario, paddingHorizontal: 20, paddingTop: 24, lineHeight: 21 },
  pie: { fontFamily: FUENTE.sans, fontSize: 13, color: COLOR.secundario, marginHorizontal: 32, marginTop: 10, lineHeight: 18 },
});
