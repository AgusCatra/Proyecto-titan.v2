# api/deps.py
# Proyecto Titán — Dependencias de la capa REST.
#
# Punto ÚNICO de resolución de la ruta de la base de datos. Los endpoints que
# tocan la BD declaran ``db_path: str = Depends(get_db_path)`` y abren la
# conexión DENTRO del manejador (ver la nota de threading en ``api/main.py``).
# Las pruebas sustituyen esta dependencia con
# ``app.dependency_overrides[get_db_path]`` para apuntar a una BD temporal.
#
# Este módulo solo hace imports ligeros (``core.db_manager`` no arrastra numpy,
# cv2 ni ninguna biblioteca de UI), de modo que importar ``api.deps`` es barato y
# seguro incluso si el resto de dependencias pesadas no están instaladas.

from __future__ import annotations

import logging
from contextlib import ExitStack, contextmanager
from typing import Iterator

from fastapi import HTTPException

from core import db_manager
from core.db_manager import get_db_connection

logger = logging.getLogger(__name__)


def get_db_path() -> str:
    """Devuelve la ruta activa de la base de datos SQLite del proyecto.

    Se lee el atributo en tiempo de llamada (no en el import) para que cualquier
    ``monkeypatch`` sobre ``core.db_manager.DB_PATH`` o un ``dependency_overrides``
    de FastAPI tengan efecto inmediato. Es el único punto donde la capa API decide
    qué base de datos usa: jamás se escribe SQL ni rutas de BD en los routers.
    """
    return db_manager.DB_PATH


@contextmanager
def conexion_bd_o_503(db_path: str) -> Iterator["db_manager.sqlite3.Connection"]:
    """Abre una conexión a la BD separando la ADQUISICIÓN del USO (fix K-W1).

    Dos fases deliberadamente distintas:

    1. ADQUISICIÓN (``get_db_connection``): si no se puede abrir la conexión, se
       registra la excepción con ``logger.exception`` y se lanza HTTP 503
       ``base de datos no disponible``. Es el ÚNICO caso que se traduce a 503.
    2. USO (cuerpo del ``with``): las excepciones que provoque la lógica de
       negocio (``core``: TypeError/KeyError/errores de numpy, etc.) NO se capturan
       aquí; propagan al manejador centralizado de 500 de ``api.main`` (que no
       filtra internos). Así un bug real de ``core`` ya no se enmascara como un 503
       de "BD no disponible" sin traza en el log.

    Las ``HTTPException`` que lance el cuerpo (p.ej. 404 por sesión inexistente)
    también propagan intactas: el ``finally`` solo cierra la conexión, no la
    interpreta.
    """
    stack = ExitStack()
    try:
        conn = stack.enter_context(get_db_connection(db_path))
    except Exception:
        stack.close()
        logger.exception(
            "No se pudo abrir la conexión a la base de datos (ruta=%s)", db_path
        )
        raise HTTPException(
            status_code=503, detail="base de datos no disponible"
        ) from None
    try:
        yield conn
    finally:
        stack.close()
