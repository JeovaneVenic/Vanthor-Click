"""Log técnico em arquivo rotativo (diagnóstico), separado do histórico de atividades.

O histórico de atividades (o que o usuário vê) fica no SQLite. Este log serve ao suporte:
exceções, falhas de captura e o ciclo de vida do processo.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from types import TracebackType

from vanthor.winsys.paths import logs_dir

_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"


DEBUG_ENV = "VANTHOR_DEBUG"


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    # Diagnóstico detalhado sob demanda (VANTHOR_DEBUG=1): registra cada quadro analisado. Fica
    # desligado por padrão porque a 40 ciclos por segundo o arquivo giraria em minutos.
    if os.environ.get(DEBUG_ENV, "").strip() not in ("", "0"):
        level = logging.DEBUG
    root.setLevel(level)
    file_handler = RotatingFileHandler(
        logs_dir() / "vanthor.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)
    if sys.stderr is not None:  # no executável sem console, stderr é None
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(console)
    _install_exception_hooks()


def _install_exception_hooks() -> None:
    log = logging.getLogger("vanthor.crash")

    def handle(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Exceção não tratada", exc_info=(exc_type, exc, tb))

    def handle_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        log.critical(
            "Exceção não tratada na thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle
    threading.excepthook = handle_thread
