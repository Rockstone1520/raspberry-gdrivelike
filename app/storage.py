"""Acceso al sistema de archivos con protección contra path traversal.

Reglas pensadas para exFAT: sin symlinks, nombres sin \\ / : * ? " < > |,
sin distinguir mayúsculas. Los archivos que empiezan por "." son internos
(temporales de subida, etc.) y nunca se listan ni se pueden pedir.
"""
import mimetypes
import os
import re
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from . import thumbs
from .config import DATA_DIR

INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME_BYTES = 180


def user_root(username: str) -> Path:
    root = DATA_DIR / "users" / username
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def check_name(name: str) -> str:
    """Valida un nombre elegido por el usuario (carpeta nueva, renombrar)."""
    if not isinstance(name, str):
        raise HTTPException(400, "Nombre inválido")
    name = name.strip()
    if not name or name in (".", ".."):
        raise HTTPException(400, "El nombre no puede estar vacío")
    if INVALID.search(name):
        raise HTTPException(400, 'El nombre no puede contener \\ / : * ? " < > |')
    if name.startswith("."):
        raise HTTPException(400, "El nombre no puede empezar con punto")
    if name.endswith("."):
        raise HTTPException(400, "El nombre no puede terminar en punto")
    if len(name.encode()) > MAX_NAME_BYTES:
        raise HTTPException(400, "El nombre es demasiado largo")
    return name


def sanitize_name(name: str) -> str:
    """Para archivos subidos: en vez de rechazar, reemplaza lo que exFAT no admite."""
    name = INVALID.sub("_", name or "").strip().lstrip(".").rstrip(". ")
    if not name:
        name = "archivo"
    while len(name.encode()) > MAX_NAME_BYTES:
        stem, ext = os.path.splitext(name)
        name = (stem[:-1] + ext) if len(stem) > 1 else name[: MAX_NAME_BYTES // 2]
    return name


def _parts(rel: str) -> list[str]:
    rel = (rel or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    for p in parts:
        if p == "..":
            raise HTTPException(400, "Ruta inválida")
        if p.startswith(".") or INVALID.search(p):
            raise HTTPException(400, "Ruta inválida")
    return parts


def resolve(root: Path, rel: str) -> Path:
    """Ruta relativa del usuario -> ruta real, garantizando que no sale de `root`."""
    p = root.joinpath(*_parts(rel)).resolve()
    if p != root and root not in p.parents:
        raise HTTPException(400, "Ruta inválida")
    return p


def rel(root: Path, p: Path) -> str:
    return "" if p == root else PurePosixPath(p.relative_to(root)).as_posix()


def unique_name(directory: Path, name: str) -> str:
    if not (directory / name).exists():
        return name
    stem, ext = os.path.splitext(name)
    n = 1
    while (directory / f"{stem} ({n}){ext}").exists():
        n += 1
    return f"{stem} ({n}){ext}"


def list_dir(directory: Path) -> list[dict]:
    items = []
    with os.scandir(directory) as it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                st = e.stat()
                is_dir = e.is_dir()
            except OSError:
                continue
            items.append(
                {
                    "name": e.name,
                    "type": "dir" if is_dir else "file",
                    "size": 0 if is_dir else st.st_size,
                    "modified": int(st.st_mtime),
                    "mime": None if is_dir else (mimetypes.guess_type(e.name)[0] or "application/octet-stream"),
                    "thumb": (not is_dir) and thumbs.kind_of(e.name) is not None,   # ¿tiene miniatura?
                }
            )
    items.sort(key=lambda i: (i["type"] != "dir", i["name"].casefold()))
    return items
