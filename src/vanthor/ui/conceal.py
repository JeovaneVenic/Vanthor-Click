"""Tira as janelas do Vanthor da frente enquanto o usuário escolhe algo na tela.

Por que opacidade 0 e não ``hide()``: esconder um diálogo aberto com ``exec()`` ENCERRA o
diálogo no Qt (o editor fecharia no meio da escolha do ponto). Com opacidade 0 a janela continua
aberta, mas fica invisível aos olhos e à captura (o Windows não a desenha).
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QApplication, QWidget


def conceal_app_windows() -> Callable[[], None]:
    """Deixa invisíveis as janelas do app abertas agora. Devolve a função que as traz de volta."""
    concealed: list[tuple[QWidget, float]] = []
    for widget in QApplication.topLevelWidgets():
        if widget.isVisible() and widget.windowOpacity() > 0:
            concealed.append((widget, widget.windowOpacity()))
            widget.setWindowOpacity(0.0)

    def restore() -> None:
        for widget, opacity in concealed:
            try:
                widget.setWindowOpacity(opacity)
            except RuntimeError:  # janela já destruída enquanto estava oculta
                continue
        if concealed:
            last = concealed[-1][0]
            try:
                last.raise_()
                last.activateWindow()
            except RuntimeError:
                pass

    return restore
