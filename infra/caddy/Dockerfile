# Imagen del borde (ARQ-47, seccion 1.2; 2026-10-27B, D; 2026-10-27C, W):
# Caddy de version fija, su configuracion, la interfaz estatica del operador y
# la version web de la aplicacion del transportista. Se construye con la raiz
# del repositorio como contexto, para alcanzar `frontend/` y `movil/`; el
# archivo Dockerfile.dockerignore de esta carpeta deja entrar solo eso.

# Etapa 1: la version web de la aplicacion (`movil/`), con la misma version de
# Node.js que el binario portable con que se desarrolla (24.22.1). Expo la
# exporta para la subruta /app (experiments.baseUrl en app.config.ts).
FROM node:24.22.1-slim AS aplicacion
ENV EXPO_NO_TELEMETRY=1 \
    CI=1 \
    npm_config_audit=false \
    npm_config_fund=false
WORKDIR /movil
COPY movil/package.json movil/package-lock.json ./
RUN npm ci
COPY movil/ ./
RUN npx expo export --platform web --output-dir dist

# Etapa 2: el borde.
FROM caddy:2.10.2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY frontend /srv/interfaz
COPY --from=aplicacion /movil/dist /srv/aplicacion
