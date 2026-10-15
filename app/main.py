from __future__ import annotations

import mimetypes
import os
import re
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import aiofiles
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, security, storage, thumbs

COOKIE = "session"
CHUNK = 256 * 1024
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if config.REQUIRE_MARKER and not (config.DATA_DIR / config.MARKER_NAME).exists():
        raise RuntimeError(
            f"Falta {config.DATA_DIR / config.MARKER_NAME}. ¿Está montada la SSD? "
            f"Si es la primera vez, crea el archivo vacío en la raíz de la carpeta de datos."
        )
    db.init()
    security.bootstrap_admin()
    thumbs.start()   # trabajador de miniaturas: segundo plano, prioridad mínima
    yield


app = FastAPI(title="Nube", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


class SecurityHeaders:
    """Middleware ASGI puro (no interfiere con el streaming de subidas grandes)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_wrapped(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"same-origin"),
                    ]
                )
            await send(message)

        await self.app(scope, receive, send_wrapped)


app.add_middleware(SecurityHeaders)


# ---------------------------------------------------------------- auth
def current_user(request: Request) -> dict:
    u = security.user_from_token(request.cookies.get(COOKIE))
    if not u:
        raise HTTPException(401, "No autenticado")
    return {"id": u["id"], "username": u["username"]}


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    username = body.username.strip().lower()
    key = f"{request.client.host if request.client else '?'}|{username}"
    if security.throttled(key):
        raise HTTPException(429, "Demasiados intentos. Espera unos minutos.")
    user = security.authenticate(username, body.password)
    if not user:
        security.register_failure(key)
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    security.clear_failures(key)
    token = security.create_session(user["id"])
    response.set_cookie(
        COOKIE, token,
        max_age=config.SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=config.COOKIE_SECURE, path="/",
    )
    return {"username": user["username"]}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    security.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    response.status_code = 204
    return response


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    du = shutil.disk_usage(config.DATA_DIR)
    return {"username": user["username"], "disk": {"total": du.total, "used": du.used, "free": du.free}}


# ---------------------------------------------------------------- archivos
def _root(user: dict) -> Path:
    return storage.user_root(user["username"])


@app.get("/api/files")
def list_files(path: str = "", user: dict = Depends(current_user)):
    root = _root(user)
    d = storage.resolve(root, path)
    if not d.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")
    return {"path": storage.rel(root, d), "items": storage.list_dir(d)}


class MkdirIn(BaseModel):
    path: str = ""
    name: str


@app.post("/api/folders", status_code=201)
def make_folder(body: MkdirIn, user: dict = Depends(current_user)):
    root = _root(user)
    parent = storage.resolve(root, body.path)
    if not parent.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")
    name = storage.check_name(body.name)
    target = parent / name
    if target.exists():
        raise HTTPException(409, "Ya existe algo con ese nombre")
    target.mkdir()
    return {"name": name}


class RenameIn(BaseModel):
    path: str
    new_name: str


@app.post("/api/rename")
def rename(body: RenameIn, user: dict = Depends(current_user)):
    root = _root(user)
    src = storage.resolve(root, body.path)
    if src == root or not src.exists():
        raise HTTPException(404, "No encontrado")
    name = storage.check_name(body.new_name)
    dst = src.with_name(name)
    if dst.exists() and dst.name.casefold() != src.name.casefold():
        raise HTTPException(409, "Ya existe algo con ese nombre")
    old_rel = storage.rel(root, src)
    os.rename(src, dst)
    thumbs.relocate(user["username"], old_rel, storage.rel(root, dst))   # la miniatura se va con el archivo
    return {"name": name}


class MoveIn(BaseModel):
    path: str
    dest: str = ""


@app.post("/api/move")
def move(body: MoveIn, user: dict = Depends(current_user)):
    root = _root(user)
    src = storage.resolve(root, body.path)
    dest = storage.resolve(root, body.dest)
    if src == root or not src.exists():
        raise HTTPException(404, "No encontrado")
    if not dest.is_dir():
        raise HTTPException(404, "Carpeta de destino no encontrada")
    if src.is_dir() and (dest == src or src in dest.parents):
        raise HTTPException(400, "No puedes mover una carpeta dentro de sí misma")
    if dest == src.parent:
        return {"moved": False}
    target = dest / src.name
    if target.exists():
        raise HTTPException(409, "En el destino ya existe algo con ese nombre")
    old_rel = storage.rel(root, src)
    shutil.move(str(src), str(target))
    thumbs.relocate(user["username"], old_rel, storage.rel(root, target))
    return {"moved": True}


@app.delete("/api/files", status_code=204)
def delete(path: str, user: dict = Depends(current_user)):
    root = _root(user)
    p = storage.resolve(root, path)
    if p == root:
        raise HTTPException(400, "No se puede borrar la carpeta raíz")
    if not p.exists():
        raise HTTPException(404, "No encontrado")
    rel = storage.rel(root, p)
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    thumbs.discard(user["username"], rel)   # si se borra el original, se borra su miniatura
    return Response(status_code=204)


# ---------------------------------------------------------------- subida
@app.put("/api/upload")
async def upload(
    request: Request,
    name: str = Query(...),
    path: str = "",
    modified: int | None = None,
    user: dict = Depends(current_user),
):
    """Cuerpo = bytes crudos del archivo (sin multipart). Se escribe en streaming
    a un temporal oculto y se renombra al terminar: nunca queda un archivo a medias."""
    root = _root(user)
    directory = storage.resolve(root, path)
    if not directory.is_dir():
        raise HTTPException(404, "Carpeta no encontrada")

    declared = int(request.headers.get("content-length") or 0)
    if declared > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Archivo demasiado grande")
    if declared > shutil.disk_usage(config.DATA_DIR).free - config.MIN_FREE_BYTES:
        raise HTTPException(507, "No hay espacio suficiente en el disco")

    final = storage.unique_name(directory, storage.sanitize_name(name))
    tmp = directory / f".{secrets.token_hex(6)}.part"
    written = 0
    thumbs.begin_transfer()   # mientras haya transferencias, las miniaturas esperan
    try:
        async with aiofiles.open(tmp, "wb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Archivo demasiado grande")
                await f.write(chunk)
        if declared and written != declared:
            raise HTTPException(400, "Subida incompleta")
        os.replace(tmp, directory / final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        thumbs.end_transfer()
    if modified and 0 < modified < 4_102_444_800_000:
        os.utime(directory / final, (modified / 1000, modified / 1000))
    thumbs.enqueue(user["username"], storage.rel(root, directory / final))   # miniatura lista antes de que abras la cuadrícula
    return {"name": final, "size": written}


# ---------------------------------------------------------------- descarga
_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _parse_range(header: str, size: int):
    m = _RANGE.fullmatch(header.strip())
    if not m or size == 0:
        return None  # multi-rango u otro formato: se sirve completo
    s, e = m.groups()
    if s == "" and e == "":
        return None
    if s == "":
        n = int(e)
        start, end = max(size - n, 0), size - 1
    else:
        start = int(s)
        end = min(int(e), size - 1) if e else size - 1
    if start >= size or start > end or (s == "" and int(e) == 0):
        raise HTTPException(416, "Rango no satisfacible", headers={"Content-Range": f"bytes */{size}"})
    return start, end


def _iter_file(path: Path, start: int, length: int):
    thumbs.begin_transfer()   # mientras haya transferencias, las miniaturas esperan
    try:
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
    finally:
        thumbs.end_transfer()


def _safe_inline(mime: str) -> bool:
    # Nunca HTML/SVG inline: un archivo subido no debe ejecutar JS con tu sesión.
    if mime == "image/svg+xml":
        return False
    return mime.startswith(("image/", "video/", "audio/")) or mime in ("application/pdf", "text/plain")


@app.api_route("/api/download", methods=["GET", "HEAD"])
def download(request: Request, path: str, inline: bool = False, user: dict = Depends(current_user)):
    root = _root(user)
    p = storage.resolve(root, path)
    if not p.is_file():
        raise HTTPException(404, "Archivo no encontrado")

    st = p.stat()
    size = st.st_size
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    etag = f'"{st.st_mtime_ns:x}-{size:x}"'

    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if range_header and if_range and if_range.strip() != etag:
        range_header = None  # el archivo cambió: empezar de nuevo

    start, end, status = 0, size - 1, 200
    if range_header:
        r = _parse_range(range_header, size)
        if r:
            (start, end), status = r, 206
    length = end - start + 1 if size else 0

    disposition = "inline" if (inline and _safe_inline(mime)) else "attachment"
    fallback = p.name.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "ETag": etag,
        "Last-Modified": _http_date(st.st_mtime),
        "Cache-Control": "private, max-age=0, must-revalidate",
        "Content-Disposition": f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(p.name)}",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=mime)
    return StreamingResponse(_iter_file(p, start, length), status_code=status, media_type=mime, headers=headers)


def _http_date(ts: float) -> str:
    from email.utils import formatdate

    return formatdate(ts, usegmt=True)


# ---------------------------------------------------------------- miniaturas
@app.get("/api/thumb")
def thumb(path: str, v: str | None = None, user: dict = Depends(current_user)):
    """Miniatura en caché. NUNCA espera a generarla (así no frena nada):
    200 = lista · 202 = en cola, vuelve a preguntar · 404 = no se puede hacer.
    `v` (la fecha de modificación) solo sirve para que el navegador renueve su caché."""
    root = _root(user)
    p = storage.resolve(root, path)
    if not p.is_file() or thumbs.kind_of(p.name) is None:
        raise HTTPException(404, "Sin miniatura")
    rel, st = storage.rel(root, p), p.stat()
    ready = thumbs.cached(user["username"], rel, st)
    if ready:
        return FileResponse(ready, media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})
    if thumbs.request(user["username"], rel, st) == "failed":
        raise HTTPException(404, "No se pudo generar la miniatura", headers={"Cache-Control": "no-store"})
    return JSONResponse({"status": "pending"}, status_code=202, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- frontend
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
