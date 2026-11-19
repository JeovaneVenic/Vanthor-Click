"""De quem as sobreposições interativas são "filhas".

Por que importa: enquanto um diálogo modal está aberto (o editor de automação usa ``exec()``), o Qt
bloqueia mouse e teclado em TODAS as outras janelas do app. Uma sobreposição sem dono ficaria
surda: o clique de escolha não chegava e o Esc ia para o diálogo (invisível), fechando o editor.
Fazendo a sobreposição pertencer ao diálogo modal ativo, ela entra na "cadeia" dele e recebe
os eventos normalmente.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QWidget


def modal_host() -> QWidget | None:
    return QApplication.activeModalWidget()
