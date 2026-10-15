# Nube: tu drive casero en la Raspberry Pi

FastAPI + frontend estático (sin build), Docker, archivos en la SSD, acceso por Tailscale.

## Estado actual (Fases 0-2)

- Login con usuarios (argon2 + sesión por cookie httpOnly), cada usuario con su carpeta aislada
- Carpetas: crear, renombrar, mover (arrastrando sobre una carpeta), borrar
- Subida múltiple con progreso, cola de 2 en paralelo, reintentos y arrastrar/soltar
- Descarga con `Range` (reanudable), vista previa de imágenes/video/audio/PDF
- Miniaturas en la cuadrícula: fotos (incluye HEIC) y un frame de los videos, generadas en segundo plano
- 3 temas claros (Papel, Niebla, Menta) + 1 oscuro (Noche), responsive

## 1. Preparar la SSD (exFAT)

```bash
sudo apt update && sudo apt install -y exfatprogs
lsblk -f                      # anota el UUID de la SSD (ej. 1A2B-3C4D)
id                            # anota tu uid y gid (normalmente 1000)
sudo mkdir -p /mnt/ssd
```

Añade a `/etc/fstab` (una sola línea):

```
UUID=1A2B-3C4D  /mnt/ssd  exfat  uid=1000,gid=1000,umask=022,nofail,x-systemd.device-timeout=10  0  0
```

```bash
sudo mount -a
mkdir -p /mnt/ssd/drive
touch /mnt/ssd/drive/.nube-ok     # marcador: la app no arranca sin él (evita escribir en la SD si la SSD no está montada)
```

> **exFAT**: funciona, pero no tiene journaling; un apagón o desconexión en plena escritura puede corromperla.
> Si la SSD está vacía, formatearla a **ext4** (`sudo mkfs.ext4 -L nube /dev/sdX1`) es más seguro. El código funciona igual con ambos.
> Pase lo que pase, no pongas la base de datos en la SSD: por eso va en `./config` (SD).

## 2. Arrancar

```bash
cd nube
cp .env.example .env && nano .env      # DATA_PATH, PUID/PGID, ADMIN_USER/ADMIN_PASSWORD
mkdir -p config
docker compose up -d --build
docker compose logs -f
```

Segundo usuario:

```bash
docker compose exec -it drive python -m app.cli adduser nombre
docker compose exec drive python -m app.cli list
docker compose exec -it drive python -m app.cli passwd nombre
```

Después de crear tu usuario puedes borrar `ADMIN_PASSWORD` del `.env`.

## 3. Acceso con Tailscale (HTTPS, sin exponer nada a internet)

```bash
sudo tailscale serve --bg 8080
tailscale serve status      # te muestra https://<pi>.<tailnet>.ts.net
```

Con HTTPS pon `COOKIE_SECURE=true` en `.env` y `docker compose up -d`.
La segunda persona debe estar en tu tailnet (o compartirle la Pi desde la consola de Tailscale).

## Miniaturas (vista cuadrícula)

- Se generan **una sola vez** y se guardan como JPEG pequeño (~320 px, calidad 55) en `DATA_PATH/.thumbs/<usuario>/…`, con la
  misma estructura de carpetas que tus archivos (`.nombre.jpg`). Es un caché oculto: **puedes borrar `.thumbs` entero sin riesgo**, se regenera solo.
- Al borrar, renombrar o mover un archivo o carpeta, su miniatura lo acompaña. Si borras o cambias archivos directamente en la SSD,
  una limpieza automática (al arrancar y cada 24 h) elimina las miniaturas huérfanas.
- Para videos se usa `ffmpeg` (frame del segundo 1; si el video es más corto, el primero). Se ejecuta de a uno, con la prioridad
  de CPU más baja, y **espera mientras haya subidas o descargas en curso**. La primera vez que abras una carpeta nueva con muchos
  videos verás los iconos y las miniaturas irán apareciendo.
- Ajustes en `.env`: `THUMB_SIZE`, `THUMB_QUALITY` (menos = más liviano), `THUMB_VIDEO_SECOND` (0 = primer frame literal).
  Tras cambiar el tamaño o la calidad, borra `DATA_PATH/.thumbs` para regenerarlas.
- La primera construcción de la imagen en la Pi tarda más porque instala `ffmpeg`. Si `pillow-heif` diera problemas al instalar,
  quita esa línea de `requirements.txt` (solo se pierden las miniaturas de fotos HEIC).

## Estructura

```
app/        backend (config, db, security, storage, thumbs, main, cli)
static/     frontend (index.html, css/style.css, js/app.js)
config/     SQLite (se crea sola, fuera de la SSD)
```

Los archivos viven tal cual en `DATA_PATH/users/<usuario>/…`: puedes verlos y copiarlos aunque la app esté caída.

## Próximas fases

1. **Subida realmente reanudable** (protocolo tus o troceado propio): hoy un reintento reinicia el archivo
2. Subir carpetas completas, descargar carpetas en ZIP
3. Papelera de reciclaje, enlaces compartidos con expiración
4. Backups (restic/rsync a un segundo disco)
