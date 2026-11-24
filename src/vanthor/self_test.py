"""Autoteste do aplicativo empacotado (``VanthorBotClick.exe --self-test``).

Confere, no executável de verdade, o que mais quebra em empacotamento: recursos embutidos,
banco e migrações, monitores, captura de tela e montagem da janela. Grava o resultado em JSON
na pasta de dados (o executável não tem console) e sai com código 0 (ok) ou 1 (falha).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from vanthor import __version__
from vanthor.winsys.paths import data_dir, resource_path

log = logging.getLogger(__name__)

REPORT_NAME = "self-test.json"


def run_checks(build_window: Callable[[], Any]) -> dict[str, Any]:
    results: dict[str, Any] = {"version": __version__, "checks": {}}
    checks: dict[str, Any] = results["checks"]

    def check(name: str, func: Callable[[], Any]) -> None:
        started = time.perf_counter()
        try:
            detail = func()
            checks[name] = {"ok": True, "detail": detail, "ms": round((time.perf_counter() - started) * 1000, 1)}
        except Exception as exc:  # o autoteste registra QUALQUER falha em vez de abortar no meio
            log.exception("Autoteste falhou em %s", name)
            checks[name] = {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def resources() -> str:
        icons = list(resource_path("icons").glob("*.svg"))
        brand = [resource_path("brand", n) for n in ("app.ico", "mark.png", "logo.png")]
        missing = [p.name for p in brand if not p.exists()]
        if len(icons) < 30 or missing:
            raise RuntimeError(f"recursos faltando: ícones={len(icons)}, marca={missing}")
        return f"{len(icons)} ícones, marca completa"

    def monitors() -> str:
        from vanthor.winsys.monitors import enumerate_monitors

        found = enumerate_monitors()
        if not found:
            raise RuntimeError("nenhum monitor encontrado")
        return ", ".join(f"{m.rect.width}x{m.rect.height}@{m.scale:g}" for m in found)

    def capture() -> str:
        from vanthor.capture.capturer import RegionCapturer
        from vanthor.capture.geometry import ScreenRect
        from vanthor.winsys.monitors import enumerate_monitors

        primary = next(m for m in enumerate_monitors() if m.primary)
        capturer = RegionCapturer()
        try:
            image = capturer.grab(ScreenRect(primary.rect.x, primary.rect.y, 64, 64))
        finally:
            capturer.close()
        if image.shape != (64, 64, 3):
            raise RuntimeError(f"formato inesperado {image.shape}")
        return "64x64 capturado"

    def window() -> str:
        win = build_window()
        return f"janela {win.width()}x{win.height()}"

    def linux_desktop() -> str:
        from Xlib import display

        from vanthor.winsys.x11 import compositor_running

        d = display.Display()
        try:
            missing = [ext for ext in ("XTEST", "RANDR") if not d.has_extension(ext)]
        finally:
            d.close()
        if missing:
            raise RuntimeError(f"extensões do X11 ausentes: {', '.join(missing)}")
        return "X11 com XTEST e RANDR; compositor " + ("ligado" if compositor_running() else "DESLIGADO")

    def linux_emergency_key() -> str:
        from vanthor.winsys.hotkeys import create_global_hotkey, parse_hotkey

        hotkey = create_global_hotkey(on_trigger=lambda: None)
        if not hotkey.register(0, parse_hotkey("Esc")):
            raise RuntimeError("outro programa já usa a tecla Esc como atalho global")
        hotkey.unregister()
        return "Esc pode ser capturada como tecla de emergência"

    import sys

    if sys.platform.startswith("linux"):
        check("linux_x11", linux_desktop)
        check("tecla_emergencia", linux_emergency_key)
    check("recursos", resources)
    check("monitores", monitors)
    check("captura", capture)
    check("janela", window)
    results["ok"] = all(c["ok"] for c in checks.values())
    return results


def write_report(results: dict[str, Any]) -> None:
    path = data_dir() / REPORT_NAME
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Autoteste %s: %s", "ok" if results["ok"] else "FALHOU", path)
