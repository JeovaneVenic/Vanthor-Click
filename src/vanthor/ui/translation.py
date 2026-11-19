"""Tradução PT-BR dos textos padrão do Qt (botões "OK", "Cancelar", menus de contexto...)."""

from __future__ import annotations

import logging

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)


def install_qt_translation(app: QApplication) -> bool:
    translator = QTranslator(app)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load(QLocale(QLocale.Language.Portuguese, QLocale.Country.Brazil), "qtbase", "_", folder):
        app.installTranslator(translator)
        return True
    log.warning("Tradução PT-BR do Qt não encontrada em %s", folder)
    return False
