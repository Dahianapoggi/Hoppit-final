/**
 * El mapa en la version web: la pagina del mapa (`mapaPagina`) en un iframe,
 * servida por el mismo borde que la aplicacion. Los datos y las ordenes
 * entran con `postMessage`; lo que la pagina avisa vuelve por el mismo canal,
 * y se filtra por la ventana y el origen del iframe.
 */
import { createElement, useEffect, useMemo, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { origenDelMapa, urlDelMapa } from './mapaPagina';
import type { MensajeDelMapa, PropsDelMapa } from './mapaTipos';

export default function Mapa({ servidor, datos, orden, alMensaje }: PropsDelMapa) {
  const marco = useRef<HTMLIFrameElement | null>(null);
  const [listo, setListo] = useState(false);
  const direccion = useMemo(() => urlDelMapa(servidor), [servidor]);
  const origen = useMemo(() => origenDelMapa(servidor, window.location.origin), [servidor]);
  const alMensajeActual = useRef(alMensaje);
  alMensajeActual.current = alMensaje;

  const mandar = (mensaje: object) => marco.current?.contentWindow?.postMessage(JSON.stringify(mensaje), origen);

  useEffect(() => {
    const recibir = (ev: MessageEvent) => {
      if (ev.source !== marco.current?.contentWindow || ev.origin !== origen) return;
      try {
        const mensaje = JSON.parse(String(ev.data)) as MensajeDelMapa;
        if (mensaje.tipo === 'listo') setListo(true);
        alMensajeActual.current(mensaje);
      } catch {
        /* un mensaje que no es del mapa */
      }
    };
    window.addEventListener('message', recibir);
    return () => window.removeEventListener('message', recibir);
  }, [origen]);

  useEffect(() => {
    if (listo) mandar({ tipo: 'datos', ...datos });
  }, [listo, datos]);

  useEffect(() => {
    if (listo && orden) mandar(orden);
  }, [listo, orden]);

  return (
    <View style={StyleSheet.absoluteFill}>
      {createElement('iframe', {
        ref: marco,
        src: direccion,
        title: 'Mapa de la Ruta',
        style: { border: 0, width: '100%', height: '100%', display: 'block' },
      })}
    </View>
  );
}
