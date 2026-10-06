import os
from pathlib import Path

# Archivos de los usuarios (la SSD). Estructura: DATA_DIR/users/<usuario>/...
DATA_DIR = Path(os.getenv("DATA_DIR", "/data")).resolve()

# Base de datos SQLite. Va FUERA de la SSD exFAT (en la SD/volumen local):
# SQLite sobre exFAT es frágil si hay un apagón.
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config")).resolve()
DB_PATH = CONFIG_DIR / "drive.db"

SESSION_DAYS = int(os.getenv("SESSION_DAYS", "30"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"

MAX_UPLOAD_BYTES = int(float(os.getenv("MAX_UPLOAD_GB", "50")) * 1024**3)
MIN_FREE_BYTES = int(os.getenv("MIN_FREE_MB", "200")) * 1024**2

# Seguro anti-"SSD no montada": si falta el archivo marcador, la app no arranca
# (así nunca escribe por error en la tarjeta SD).
REQUIRE_MARKER = os.getenv("REQUIRE_MARKER", "true").lower() == "true"
MARKER_NAME = ".nube-ok"
