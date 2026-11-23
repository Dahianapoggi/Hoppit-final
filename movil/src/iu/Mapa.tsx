/**
 * El mapa en Android: la pagina del mapa (`mapaPagina`) en un WebView,
 * cargada desde el servidor con el que se ingreso. Los datos y las ordenes
 * entran como eventos `message` de la pagina; lo que la pagina avisa sale por
 * `ReactNativeWebView.postMessage`.
 */
import { type ComponentType, type Ref, useEffect, useMemo, useRef, useState } from 'react';
import { StyleSheet } from 'react-native';
import { WebView, type WebViewMessageEvent, type WebViewProps } from 'react-native-webview';

import { urlDelMapa } from './mapaPagina';
import type { MensajeDelMapa, PropsDelMapa } from './mapaTipos';

/* Los tipos de la clase WebView no encajan con los de React 19 (resuelven
   las props a `never`); el componente funciona igual. Se lo vuelve a tipar
   con sus props publicadas y la referencia que se usa. */
const VistaWeb = WebView as unknown as ComponentType<WebViewProps & { ref?: Ref<WebView> }>;

export default function Mapa({ servidor, datos, orden, alMensaje }: PropsDelMapa) {
  const vista = useRef<WebView>(null);
  const [listo, setListo] = useState(false);
  const fuente = useMemo(() => ({ uri: urlDelMapa(servidor) }), [servidor]);

  const mandar = (mensaje: object) => {
    const texto = JSON.stringify(JSON.stringify(mensaje));
    vista.current?.injectJavaScript(`window.dispatchEvent(new MessageEvent('message',{data:${texto}}));true;`);
  };

  useEffect(() => {
    if (listo) mandar({ tipo: 'datos', ...datos });
  }, [listo, datos]);

  useEffect(() => {
    if (listo && orden) mandar(orden);
  }, [listo, orden]);

  const recibir = (ev: WebViewMessageEvent) => {
    try {
      const mensaje = JSON.parse(ev.nativeEvent.data) as MensajeDelMapa;
      if (mensaje.tipo === 'listo') setListo(true);
      alMensaje(mensaje);
    } catch {
      /* un mensaje que no es del mapa */
    }
  };

  return (
    <VistaWeb
      ref={vista}
      style={StyleSheet.absoluteFill}
      originWhitelist={['*']}
      source={fuente}
      onMessage={recibir}
      javaScriptEnabled
      domStorageEnabled
      scrollEnabled={false}
      overScrollMode="never"
      setSupportMultipleWindows={false}
    />
  );
}
