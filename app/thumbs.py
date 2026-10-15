"""Miniaturas de fotos y videos.

* Se generan UNA vez y se guardan como JPEG pequeño en DATA_DIR/.thumbs/<usuario>/<misma ruta>,
  como archivo oculto ".<nombre>.jpg". Es un caché: si se borra, se regenera solo.
* Un único trabajador en segundo plano, con la prioridad de CPU más baja, que además cede el
  paso mientras haya subidas o descargas en curso.
* La API nunca espera a que se genere: responde "pendiente" y el navegador reintenta.

Por qué los archivos del espejo empiezan con "." : los nombres de usuario nunca pueden empezar
con punto (ver storage.check_name), así que una miniatura jamás choca con un archivo o
carpeta real, y mantener el espejo sincronizado al borrar/renombrar/mover es trivial.
"""
from __future__ import annotations

import io
import logging
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

from .config import DATA_DIR, THUMB_QUALITY, THUMB_SIZE, THUMB_VIDEO_SECOND, THUMBS_DIR

log = logging.getLogger("nube.thumbs")

try:  # HEIC/HEIF (fotos de iPhone). Opcional: sin esta librería simplemente no hay miniatura.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_OK = True
except Exception:  # pragma: no cover
    HEIF_OK = False

FFMPEG = shutil.which("ffmpeg")

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
if HEIF_OK:
    IMAGE_EXT |= {".heic", ".heif"}
VIDEO_EXT = (
    {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp", ".mpg", ".mpeg", ".wmv", ".flv", ".mts", ".m2ts"}
    if FFMPEG
    else set()
)

MAX_IMAGE_BYTES = 150 * 1024 * 1024   # archivos mayores: se ignoran (cuidar la RAM de la Pi)
MAX_PIXELS = 120_000_000
VIDEO_TIMEOUT = 40                    # segundos máximos para extraer un frame
YIELD_MAX = 20                        # segundos máximos cediendo el paso a subidas/descargas
SWEEP_EVERY = 24 * 3600               # limpieza de huérfanas cuando la cola lleva 24 h inactiva

_lock = threading.Lock()
_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
_pending: set[tuple[str, str]] = set()
_failed: dict[tuple[str, str], int] = {}   # (usuario, ruta) -> mtime_ns del origen cuando falló
_transfers = 0
_started = False


def kind_of(name: str) -> str | None:
    """'image', 'video' o None si no se puede hacer miniatura de ese archivo."""
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ------------------------------------------------------------------ rutas del caché
def _source(user: str, rel: str) -> Path:
    return DATA_DIR / "users" / user / rel


def _thumb_file(user: str, rel: str) -> Path:
    p = Path(rel)
    return THUMBS_DIR / user / p.parent / f".{p.name}.jpg"


def _mirror_dir(user: str, rel: str) -> Path:
    return THUMBS_DIR / user / rel


# ------------------------------------------------------------------ contador de transferencias
def begin_transfer() -> None:
    global _transfers
    with _lock:
        _transfers += 1


def end_transfer() -> None:
    global _transfers
    with _lock:
        _transfers = max(0, _transfers - 1)


def _wait_for_quiet() -> None:
    """Cede el paso: no genera miniaturas mientras haya subidas/descargas (máx. YIELD_MAX s)."""
    waited = 0.0
    while waited < YIELD_MAX:
        with _lock:
            busy = _transfers > 0
        if not busy:
            return
        time.sleep(0.5)
        waited += 0.5


# ------------------------------------------------------------------ API para main.py
def cached(user: str, rel: str, st: os.stat_result) -> Path | None:
    """Ruta de la miniatura si existe y corresponde a la versión actual del archivo.
    La miniatura guarda como mtime el mtime del original: si no coinciden, está vencida."""
    f = _thumb_file(user, rel)
    try:
        t = f.stat()
    except OSError:
        return None
    return f if abs(t.st_mtime_ns - st.st_mtime_ns) < 20_000_000 else None


def request(user: str, rel: str, st: os.stat_result) -> str:
    """Pide una miniatura sin esperar. Devuelve 'pending' o 'failed'."""
    key = (user, rel)
    with _lock:
        if _failed.get(key) == st.st_mtime_ns:
            return "failed"
        _failed.pop(key, None)
        if key not in _pending:
            _pending.add(key)
            _queue.put(key)
    return "pending"


def enqueue(user: str, rel: str) -> None:
    """Adelanta el trabajo (p. ej. justo después de una subida)."""
    if kind_of(rel) is None:
        return
    key = (user, rel)
    with _lock:
        _failed.pop(key, None)
        if key not in _pending:
            _pending.add(key)
            _queue.put(key)


def discard(user: str, rel: str) -> None:
    """Borra la(s) miniatura(s) de un archivo o de toda una carpeta."""
    if not rel:
        return
    try:
        _thumb_file(user, rel).unlink(missing_ok=True)
        shutil.rmtree(_mirror_dir(user, rel), ignore_errors=True)
        _forget(user, rel)
    except Exception:
        log.exception("no se pudo borrar la miniatura de %s/%s", user, rel)


def relocate(user: str, old: str, new: str) -> None:
    """Acompaña un renombrado o movimiento: la miniatura se mueve con el original."""
    if not old or not new:
        return
    try:
        fa, fb = _thumb_file(user, old), _thumb_file(user, new)
        if fa.is_file():
            fb.parent.mkdir(parents=True, exist_ok=True)
            os.replace(fa, fb)
        da, db = _mirror_dir(user, old), _mirror_dir(user, new)
        if da.is_dir() and not db.exists():
            db.parent.mkdir(parents=True, exist_ok=True)
            os.rename(da, db)
        _forget(user, old)
    except Exception:
        log.exception("no se pudo mover la miniatura de %s/%s", user, old)


def _forget(user: str, rel: str) -> None:
    pre = rel + "/"
    with _lock:
        for k in [k for k in _failed if k[0] == user and (k[1] == rel or k[1].startswith(pre))]:
            del _failed[k]


# ------------------------------------------------------------------ generación
def _image_from(src: Path, st: os.stat_result) -> Image.Image:
    if st.st_size > MAX_IMAGE_BYTES:
        raise ValueError("imagen demasiado grande")
    with Image.open(src) as im:
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("imagen con demasiados píxeles")
        if im.format == "JPEG":
            # Decodifica el JPEG ya reducido (mucho más rápido y con menos RAM en la Pi)
            im.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
        out = ImageOps.exif_transpose(im)   # respeta la rotación de las fotos de celular
        out.load()
        return out


def _video_frame(src: Path) -> Image.Image:
    vf = f"scale={THUMB_SIZE}:{THUMB_SIZE}:force_original_aspect_ratio=decrease"
    seconds = [THUMB_VIDEO_SECOND, 0] if THUMB_VIDEO_SECOND > 0 else [0]   # 0 = respaldo: primer frame
    for ss in seconds:
        cmd = [
            FFMPEG, "-nostdin", "-v", "error", "-threads", "1",
            "-ss", str(ss), "-i", str(src),
            "-frames:v", "1", "-an", "-sn", "-vf", vf,
            "-f", "image2pipe", "-c:v", "bmp", "-",
        ]
        r = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=VIDEO_TIMEOUT)
        if r.returncode == 0 and r.stdout:
            img = Image.open(io.BytesIO(r.stdout))
            img.load()
            return img
    raise RuntimeError("ffmpeg no pudo extraer un frame")


def _save_jpeg(img: Image.Image, path: Path) -> None:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.BILINEAR, reducing_gap=2.0)
    # La calidad se reduce aquí, para fotos y videos por igual (THUMB_QUALITY)
    img.save(str(path), "JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True)


def _process(user: str, rel: str) -> None:
    src = _source(user, rel)
    try:
        st = src.stat()
    except OSError:
        return   # el archivo ya no existe
    kind = kind_of(src.name)
    if kind is None or not src.is_file() or cached(user, rel, st):
        return
    out = _thumb_file(user, rel)
    tmp = out.with_name(f".~{secrets.token_hex(4)}.part")
    try:
        img = _image_from(src, st) if kind == "image" else _video_frame(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        _save_jpeg(img, tmp)
        os.utime(tmp, ns=(st.st_mtime_ns, st.st_mtime_ns))   # vincula la miniatura a esta versión
        if src.stat().st_mtime_ns != st.st_mtime_ns:          # el original cambió mientras tanto
            tmp.unlink(missing_ok=True)
            return
        os.replace(tmp, out)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        with _lock:
            _failed[(user, rel)] = st.st_mtime_ns
        log.warning("sin miniatura para %s/%s: %s", user, rel, e)


# ------------------------------------------------------------------ limpieza y trabajador
def sweep() -> int:
    """Borra miniaturas huérfanas (original borrado/cambiado fuera de la app) y parciales viejos."""
    removed = 0
    if not THUMBS_DIR.is_dir():
        return 0

    def rm(path: str) -> None:
        nonlocal removed
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass

    for udir in THUMBS_DIR.iterdir():
        if not udir.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(udir, topdown=False):
            rel_dir = os.path.relpath(dirpath, udir)
            src_dir = DATA_DIR / "users" / udir.name / ("" if rel_dir == "." else rel_dir)
            for fn in files:
                p = os.path.join(dirpath, fn)
                if fn.startswith(".~"):
                    rm(p)
                elif fn.startswith(".") and fn.endswith(".jpg"):
                    name = fn[1:-4]
                    if kind_of(name) is None or not (src_dir / name).is_file():
                        rm(p)
            try:
                os.rmdir(dirpath)   # solo se borra si quedó vacía
            except OSError:
                pass
    return removed


def _sweep_safe() -> None:
    try:
        n = sweep()
        if n:
            log.info("limpieza de miniaturas: %d archivos huérfanos borrados", n)
    except Exception:
        log.exception("falló la limpieza de miniaturas")


def _worker() -> None:
    try:   # prioridad mínima SOLO para este hilo (y los procesos ffmpeg que lance)
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except Exception:
        pass
    _sweep_safe()
    while True:
        try:
            key = _queue.get(timeout=SWEEP_EVERY)
        except queue.Empty:
            _sweep_safe()
            continue
        try:
            _wait_for_quiet()
            _process(*key)
        except Exception:
            log.exception("error inesperado generando miniatura %s", key)
        finally:
            with _lock:
                _pending.discard(key)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, name="thumbs", daemon=True).start()
