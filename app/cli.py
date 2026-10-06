"""Administración de usuarios.

  docker compose exec -it drive python -m app.cli adduser maria
  docker compose exec -it drive python -m app.cli passwd maria
  docker compose exec drive python -m app.cli list
"""
import argparse
import getpass
import sqlite3
import sys

from . import db, security


def _ask_password(given: str | None) -> str:
    if given:
        return given
    p1 = getpass.getpass("Contraseña (mín. 8 caracteres): ")
    if p1 != getpass.getpass("Repite la contraseña: "):
        sys.exit("Las contraseñas no coinciden")
    return p1


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("adduser", help="crear usuario")
    a.add_argument("username")
    a.add_argument("--password")
    p = sub.add_parser("passwd", help="cambiar contraseña")
    p.add_argument("username")
    p.add_argument("--password")
    sub.add_parser("list", help="listar usuarios")
    args = ap.parse_args()

    db.init()
    try:
        if args.cmd == "adduser":
            security.create_user(args.username, _ask_password(args.password))
            print(f"Usuario '{args.username.lower()}' creado")
        elif args.cmd == "passwd":
            ok = security.set_password(args.username, _ask_password(args.password))
            print("Contraseña actualizada (sesiones cerradas)" if ok else "Ese usuario no existe")
        else:
            print("\n".join(security.list_users()) or "(sin usuarios)")
    except sqlite3.IntegrityError:
        sys.exit("Ese usuario ya existe")
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
