import hashlib
import os
import re
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from . import db
from .config import SESSION_DAYS

_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("contraseña-falsa")  # para igualar tiempos si el usuario no existe
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")


# ---------- usuarios ----------
def create_user(username: str, password: str) -> None:
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Usuario inválido: 2-32 caracteres (a-z, 0-9, _ . -)")
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    with db.conn() as c:
        c.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES (?,?,?)",
            (username, _ph.hash(password), int(time.time())),
        )


def set_password(username: str, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres")
    with db.conn() as c:
        cur = c.execute(
            "UPDATE users SET password_hash=? WHERE username=?",
            (_ph.hash(password), username.strip().lower()),
        )
        if cur.rowcount:
            c.execute(
                "DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)",
                (username.strip().lower(),),
            )
        return cur.rowcount > 0


def list_users() -> list[str]:
    with db.conn() as c:
        return [r["username"] for r in c.execute("SELECT username FROM users ORDER BY id")]


def bootstrap_admin() -> None:
    """Primer arranque: crea el usuario inicial desde ADMIN_USER / ADMIN_PASSWORD."""
    user, pwd = os.getenv("ADMIN_USER", ""), os.getenv("ADMIN_PASSWORD", "")
    if user and pwd and not list_users():
        create_user(user, pwd)


def _verify(hash_: str, password: str) -> bool:
    try:
        return _ph.verify(hash_, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def authenticate(username: str, password: str):
    with db.conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        _verify(_DUMMY_HASH, password)
        return None
    return row if _verify(row["password_hash"], password) else None


# ---------- sesiones ----------
def _th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db.conn() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        c.execute(
            "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?,?,?)",
            (_th(token), user_id, now + SESSION_DAYS * 86400),
        )
    return token


def user_from_token(token: str | None):
    if not token:
        return None
    with db.conn() as c:
        return c.execute(
            "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash=? AND s.expires_at>?",
            (_th(token), int(time.time())),
        ).fetchone()


def delete_session(token: str | None) -> None:
    if token:
        with db.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_th(token),))


# ---------- freno a fuerza bruta (en memoria) ----------
_fails: dict[str, list[float]] = {}
_WINDOW, _LIMIT = 300, 8


def throttled(key: str) -> bool:
    now = time.time()
    hits = [t for t in _fails.get(key, []) if now - t < _WINDOW]
    _fails[key] = hits
    return len(hits) >= _LIMIT


def register_failure(key: str) -> None:
    _fails.setdefault(key, []).append(time.time())


def clear_failures(key: str) -> None:
    _fails.pop(key, None)
