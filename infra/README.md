# Despliegue de HOPPIT en Azure

Diseño: ARQ-47 a ARQ-61 (Chat A), aceptado en B-240; secciones 1.1 a 1.11 de `ARQ_Pedido2`. Escrito y validado en la sesión 2026-10-27B, **sin aplicar**. Nada de esta carpeta creó recursos ni ingresó a ninguna cuenta: lo que sigue se hace con el usuario presente.

## Qué hay

| Archivo | Qué es |
|---|---|
| `../backend/Dockerfile` | Imagen `hoppit-backend`: una imagen, dos órdenes de arranque (núcleo y telemetría), Python 3.13, usuario sin privilegios, sin secretos, con el grafo de CABA adentro. |
| `caddy/Dockerfile` | Imagen `hoppit-borde`: Caddy de versión fija, el `Caddyfile` y la interfaz estática. |
| `caddy/Caddyfile` | Borde: `/v1/telemetria/*` a telemetría, `/v1/*` y `/estado` al núcleo, `/interno/*` cerrado, el resto la interfaz; HTTPS automático con el dominio de `HOPPIT_DOMINIO`. |
| `compose.yaml` | Los tres servicios en la máquina, con montajes en `/srv/hoppit` y las claves como secretos de Compose. |
| `compose.local.yaml` | Superposición para el equipo: sólo el 8080 y HTTP sin TLS. |
| `entorno.ejemplo`, `entorno.local.ejemplo` | Plantillas del `.env` (ninguna variable es secreta). |
| `terraform/` | Recursos de la sección 1.4 y el cloud-init. `terraform.tfvars.example` con marcadores `<completar>`. |
| `../.github/workflows/` | `bateria.yml`, la batería de pruebas (sección 1.5). Corre en cada push. |
| `../backend/tools/` | `desplegar.py`, `apagar.py`, `encender.py`, `respaldar_bases.py` y `actualizar_duckdns.py`, todas con `--simular`. |

Costos: no se escriben cifras acá. Las partidas son las de la sección 1.12 de `ARQ_Pedido2` y ARQ-50, con las cotizaciones que se registren.

## Procedimiento de despliegue

Todo desde el equipo del usuario, en PowerShell o en Git Bash, salvo donde dice "en la máquina".

1. **Instalar `az` y Terraform.**
   - Azure CLI con su instalador oficial (pide permisos de administrador).
   - Terraform ya está como binario portable en `%LOCALAPPDATA%\hoppit\herramientas\terraform-1.17.0\terraform.exe`, verificado contra el `SHA256SUMS` publicado. Se puede usar con su ruta completa o sumando esa carpeta al PATH de la consola.
   - Conviene `CHECKPOINT_DISABLE=1`, para que Terraform no consulte por versiones nuevas.
2. **Ingresar en Azure y elegir la suscripción.**
   - `az login`.
   - `az account list -o table` y `az account set --subscription <id>`.
   - Exportar el identificador sólo en la consola: `$env:ARM_SUBSCRIPTION_ID = "<id>"`. Nunca en un archivo del repositorio.
3. **Región e imagen, según ARQ-51 (sección 1.11).**
   - Leer en el portal las regiones que admite la política de la suscripción.
   - Entre las de Sudamérica admitidas, `az vm list-skus --location <región> --size Standard_B2 -o table`: tienen que ofrecer `Standard_B2ats_v2` y `Standard_B2als_v2`.
   - Si califican las dos (Brazil South y Chile Central), elegir la de menor latencia medida desde Buenos Aires, y registrar la cifra con su fecha. Si no califica ninguna, la región de EE. UU. con menor latencia.
   - Versión fija de la imagen: `az vm image list --publisher Canonical --offer ubuntu-24_04-lts --sku server --all --location <región> -o table`, y elegir la más reciente. Nunca `latest`.
4. **Presupuesto y HCP Terraform.**
   - Ingresar en HCP Terraform (`terraform login`). Crear el espacio de trabajo en modo de ejecución **local**.
   - Exportar `TF_CLOUD_ORGANIZATION` y `TF_WORKSPACE` en la consola.
   - Copiar `terraform/terraform.tfvars.example` a `terraform/terraform.tfvars` y completar cada `<completar>`: región, IP pública del equipo (`/32`), clave pública SSH, correo de la alerta, inicio del presupuesto y versión de la imagen. Ese archivo no entra a git (`terraform/.gitignore`).
5. **`init`, `plan` y `apply`.**
   - `cd infra/terraform` y `terraform init`. Ahora con el bloque `cloud {}`: en la sesión sólo se corrió `init -backend=false`.
   - `terraform plan -out plan.tfplan`. Revisar que sean los 12 recursos de la sección 1.4 y nada más.
   - `terraform apply plan.tfplan`.
   - `terraform output ip_publica`.
   - **Proveedores:** el archivo `.terraform.lock.hcl` versionado trae los hashes de Windows. Si otro integrante corre Terraform en Linux o en macOS: `terraform providers lock -platform=linux_amd64 -platform=windows_amd64 -platform=darwin_arm64`.
6. **DuckDNS.**
   - Exportar en la consola `HOPPIT_DUCKDNS_SUBDOMINIO` y `HOPPIT_DUCKDNS_TOKEN`; el token sale del gestor de contraseñas.
   - `python backend/tools/actualizar_duckdns.py --ip <ip_publica> --simular` muestra el pedido con el token oculto; sin `--simular`, lo hace.
7. **Acceso de la máquina a GHCR.**
   - Si los paquetes son privados (D9, B-235), en la máquina: `docker login ghcr.io` con un token personal de GitHub con sólo `read:packages`.
   - Si son públicos, no hace falta, pero la imagen publica el código.
8. **Primer despliegue.**
   - **Imágenes:** con la batería en verde, se construyen y se publican desde el equipo, desde la raíz del repositorio, con el SHA del commit como etiqueta (antes, `docker login ghcr.io` con un token de `write:packages`):
     ```
     docker build -t ghcr.io/<cuenta>/hoppit-backend:<sha> backend
     docker build -t ghcr.io/<cuenta>/hoppit-borde:<sha> -f infra/caddy/Dockerfile .
     docker push ghcr.io/<cuenta>/hoppit-backend:<sha>
     docker push ghcr.io/<cuenta>/hoppit-borde:<sha>
     ```
   - **En la máquina** (`ssh <usuario>@<ip>`), las claves de firma y el `.env`:
     - generar las claves con la imagen ya descargada, para que la privada no salga de la máquina:
       ```
       docker run --rm -u 0 -v /srv/hoppit/secretos:/claves ghcr.io/<cuenta>/hoppit-backend:<sha> python tools/generar_claves_de_firma.py --directorio /claves
       sudo chown 10001:10001 /srv/hoppit/secretos/*
       sudo chmod 600 /srv/hoppit/secretos/firma_ed25519_privada.pem
       ```
     - crear `/srv/hoppit/compose/.env` desde `entorno.ejemplo`, con el dominio y la cuenta del registro.
   - **Desde el equipo:** `python backend/tools/desplegar.py --version <sha> --destino <usuario>@<ip> --dominio <dominio> --simular`, revisar las órdenes y repetir sin `--simular`.
   - **Usuarios, en la máquina:** `docker compose exec nucleo python tools/crear_usuario.py --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3 …` o `tools/crear_invitacion.py` con la misma `--base-de-datos`. **La base se indica siempre:** la omisión de las herramientas es `/app/datos/hoppit.sqlite3`, que no es la del núcleo en la imagen (2026-10-27C, DK). La contraseña se tipea, nunca va en un archivo. La base la crea el núcleo al arrancar; para rehacerla, con el núcleo detenido: `docker compose run --rm --no-deps nucleo python tools/recrear_instancia.py --grafo caba --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3`.
9. **Prueba de humo.**
   - **Por HTTP, desde el equipo:** con un operador `operador-ensayo` y un transportista `transportista-ensayo` creados con la misma contraseña, en la consola `$env:HOPPIT_CONTRASENA_DEL_ENSAYO = "<contraseña>"` y `python backend/tools/ensayo_guion_demo.py --base https://<dominio> --version-esperada <versión>`. Son 16 comprobaciones; termina con código 1 si alguna falla. **Crea datos de prueba**: después, recrear la base.
   - Ingresar desde el navegador en `https://<dominio>`, y la aplicación del transportista en `https://<dominio>/app/`.
   - Dar de alta una Orden en CABA.
   - Planificar.
   - Revisar que `/estado` publique la versión esperada y el grafo de CABA verificado.
10. **Medición** (criterio de la sección 1.9, fijado antes de medir): `free -m`, `docker stats --no-stream` en reposo y diez planificaciones de CABA con `vmstat 1`. Se pasa a `Standard_B2als_v2` si el núcleo termina por memoria, si hay lectura desde swap durante la planificación o si la mediana supera 3 s. Va en fila aparte de NFR-09.
11. **Apagado.**
    - `python backend/tools/apagar.py --destino <usuario>@<ip> --simular`, y después sin `--simular`. Respalda las dos bases, detiene los servicios y desasigna la máquina.
    - Para volver: `python backend/tools/encender.py --dominio <dominio>`.

## Probar en el equipo, sin la nube

Con Docker Desktop abierto, desde la raíz del árbol de trabajo (en Git Bash, con `MSYS_NO_PATHCONV=1` para que no convierta las rutas que empiezan con `/`):

```
python backend/tools/generar_claves_de_firma.py --directorio backend/datos/tmp/despliegue_local/secretos
C="docker compose -f infra/compose.yaml -f infra/compose.local.yaml --env-file infra/entorno.local.ejemplo"
$C build
$C run --rm --no-deps nucleo python tools/recrear_instancia.py --grafo caba --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3
$C run --rm --no-deps -e HOPPIT_CONTRASENA_DEL_ENSAYO nucleo python tools/crear_usuario.py --usuario operador-ensayo --rol operador --nombre "Operador del ensayo" --contrasena-desde-variable HOPPIT_CONTRASENA_DEL_ENSAYO --base-de-datos /var/lib/hoppit/nucleo/nucleo.sqlite3
$C up -d --wait
python backend/tools/ensayo_guion_demo.py --base http://localhost:8080
$C down -v
```

(el transportista, igual que el operador con `--rol transportista`). `-e NOMBRE` sin valor pasa al contenedor la variable de la consola, sin escribirla en la orden. La interfaz del operador queda en `http://localhost:8080/` y la aplicación del transportista en `http://localhost:8080/app/`.

**Datos en volúmenes con nombre** (`compose.local.yaml`): en el equipo, las bases del núcleo y de telemetría y el estado de Caddy van en volúmenes de Docker, no en carpetas de Windows montadas, porque SQLite en modo WAL sobre un montaje desde el sistema de archivos de Windows puede fallar por los bloqueos. `down -v` los borra. En la máquina de Azure siguen los montajes de directorio sobre el disco de datos (sección 1.3). Las claves llegan igual en los dos casos, como secretos de Compose.

**Primera construcción y arranque (2026-10-27C, DK),** en Docker Desktop 4.96.2 sobre WSL 2: las dos imágenes se construyeron (`python:3.13.15-slim` y `caddy:2.10.2-alpine` existen con esas etiquetas), los tres servicios quedaron sanos y `tools/ensayo_guion_demo.py` dio 16 de 16 por el borde. Lo que el arranque real mostró y se corrigió:

- La imagen no tenía `/var/lib/hoppit/nucleo` ni `/var/lib/hoppit/telemetria`: un volumen nuevo los habría creado de root. Ahora existen con dueño `hoppit`.
- `recrear_instancia.py` no corría en la imagen: importaba el cliente de pruebas de FastAPI, que necesita un paquete de desarrollo. Ahora ese cliente se importa sólo donde se usa.
- La herramienta de ensayo de 27B esperaba 200 en el ingreso, que responde 201.
- Tras construir, la máquina virtual de WSL retuvo unos 4,7 GB por unos minutos y después los devolvió.

## Decisiones de esta carpeta

- **Grafo explícito (B-220, punto 4; 2026-10-27C, F1):** ni el servidor ni `recrear_instancia.py` tienen grafo por omisión. Sin `--grafo` ni `HOPPIT_GRAFO` terminan con código 1. El Compose declara `HOPPIT_GRAFO: caba` en `nucleo`, así que `docker compose run nucleo …` también lo hereda.
- **Respaldo en la máquina:** corre con el Python de la máquina, no con `docker compose exec`, porque `/srv/hoppit/respaldos` no está montado en ningún contenedor.
- **Huella en CI:** se corre dentro de la imagen recién construida, en un contenedor descartable que instala `httpx`, que la imagen no trae.
- **Interfaz:** en el puerto de desarrollo 5173 usa la API del 8000. En cualquier otro origen (el 8080 local o el dominio) usa el mismo origen.
