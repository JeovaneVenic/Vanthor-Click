"""Perfis: conjuntos de zonas e automações. Só o perfil em uso é vigiado."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSize, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QShowEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vanthor.capture.geometry import monitor_for_rect
from vanthor.core.engine import EngineState
from vanthor.persistence.profile_io import (
    ProfileFileError,
    duplicate_profile,
    export_profile,
    import_profile,
    parse_profile_file,
    to_json,
)
from vanthor.persistence.repositories import PROFILE_NAME_MAX, ProfileError, ProfileSummary
from vanthor.ui.context import AppContext
from vanthor.ui.screens import monitor_maps
from vanthor.ui.theme import icons
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.toast import Toast
from vanthor.ui.widgets.wrap_label import WrapLabel

log = logging.getLogger(__name__)
_FILE_FILTER = "Perfil do Vanthor (*.json)"


def _label(text: str = "", name: str | None = None) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def _counts(summary: ProfileSummary) -> str:
    zones = "1 zona" if summary.zones == 1 else f"{summary.zones} zonas"
    rules = "1 automação" if summary.rules == 1 else f"{summary.rules} automações"
    active = f", {summary.active_rules} {'ativa' if summary.active_rules == 1 else 'ativas'}" if summary.rules else ""
    return f"{zones}, {rules}{active}"


class _IconButton(QPushButton):
    def __init__(self, icon_name: str, tip: str) -> None:
        super().__init__()
        self.setObjectName("Quiet")
        self.setIcon(icons.icon(icon_name, Color.TEXT_2, 18))
        self.setIconSize(QSize(18, 18))
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(Size.CONTROL_H)


class ProfileCard(QFrame):
    def __init__(self, page: ProfilesPage, summary: ProfileSummary, in_use: bool, running: bool) -> None:
        super().__init__()
        self.setObjectName("Panel")
        self.summary = summary
        profile = summary.profile
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 14, 14, 14)
        row.setSpacing(14)

        badge = QLabel()
        badge.setPixmap(icons.pixmap("layers", Color.BRASA if in_use else Color.TEXT_3, 22))
        row.addWidget(badge)

        info = QVBoxLayout()
        info.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(10)
        self.name_edit = QLineEdit(profile.name)
        self.name_edit.setObjectName("InlineName")
        self.name_edit.setMaxLength(PROFILE_NAME_MAX)
        self.name_edit.setToolTip("Clique para renomear")
        self.name_edit.setAccessibleName("Nome do perfil")
        self.name_edit.editingFinished.connect(lambda: page.rename(self))
        head.addWidget(self.name_edit, 1)
        if in_use:
            chip = _label("Em uso", "Chip")
            chip.setFixedHeight(22)  # selo, não botão: altura fixa e centralizado na linha
            head.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        info.addLayout(head)
        info.addWidget(_label(_counts(summary), "Muted"))
        self.error = _label("", "FieldError")
        self.error.hide()
        info.addWidget(self.error)
        row.addLayout(info, 1)

        if not in_use:
            use = QPushButton("  Usar este perfil")
            use.setIcon(icons.icon("check", Color.TEXT, 16))
            use.setCursor(Qt.CursorShape.PointingHandCursor)
            use.setEnabled(not running)
            use.setToolTip("Pare as automações para trocar de perfil" if running else "")
            use.clicked.connect(lambda: page.use(profile.id))
            row.addWidget(use)
        duplicate = _IconButton("copy", "Duplicar perfil")
        duplicate.clicked.connect(lambda: page.duplicate(profile.id))
        row.addWidget(duplicate)
        export = _IconButton("download", "Exportar para arquivo")
        export.clicked.connect(lambda: page.export(profile.id, profile.name))
        row.addWidget(export)
        delete = _IconButton("trash-2", "Excluir perfil")
        delete.setEnabled(not in_use)
        if in_use:
            delete.setToolTip("Troque para outro perfil antes de excluir este")
        delete.clicked.connect(lambda: page.delete(summary))
        row.addWidget(delete)


class ProfilesPage(QWidget):
    def __init__(self, ctx: AppContext, toast: Toast, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._toast = toast
        self._pending_delete: set[int] = set()
        self._cards: list[ProfileCard] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(20)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(_label("Perfis", "PageTitle"))
        titles.addWidget(
            _label("Cada perfil guarda suas próprias zonas e automações. Só o perfil em uso é vigiado.", "Muted")
        )
        header.addLayout(titles, 1)
        importer = QPushButton("  Importar")
        importer.setIcon(icons.icon("upload", Color.TEXT, 16))
        importer.setCursor(Qt.CursorShape.PointingHandCursor)
        importer.setToolTip("Adiciona um perfil a partir de um arquivo exportado pelo Vanthor")
        importer.clicked.connect(self.import_file)
        header.addWidget(importer, 0, Qt.AlignmentFlag.AlignBottom)
        create = QPushButton("  Novo perfil")
        create.setObjectName("Primary")
        create.setIcon(icons.icon("plus", Color.ON_BRASA, 18))
        create.setCursor(Qt.CursorShape.PointingHandCursor)
        create.clicked.connect(self.create)
        header.addWidget(create, 0, Qt.AlignmentFlag.AlignBottom)
        root.addLayout(header)

        self._banner = QFrame()
        self._banner.setObjectName("Banner")
        banner_row = QHBoxLayout(self._banner)
        banner_row.setContentsMargins(14, 10, 8, 10)
        self._banner_text = WrapLabel("", "BannerText")
        banner_row.addWidget(self._banner_text, 1)
        close = QPushButton("Entendi")
        close.setObjectName("Quiet")
        close.clicked.connect(self._banner.hide)
        banner_row.addWidget(close)
        self._banner.hide()
        root.addWidget(self._banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        self._list = QVBoxLayout(host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(12)
        self._list.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        ctx.session.state_changed.connect(lambda _s: self.reload())
        ctx.session.profile_changed.connect(lambda _p: self.reload())

    # -- dados ------------------------------------------------------------------------------------
    def reload(self) -> None:
        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
        active_id = self._ctx.profiles.active().id
        running = self._ctx.session.state is not EngineState.STOPPED
        summaries = [s for s in self._ctx.profiles.summaries() if s.profile.id not in self._pending_delete]
        # Perfil em uso primeiro: é o que importa agora.
        summaries.sort(key=lambda s: (s.profile.id != active_id, s.profile.name.casefold()))
        for summary in summaries:
            card = ProfileCard(self, summary, summary.profile.id == active_id, running)
            self._list.insertWidget(self._list.count() - 1, card)
            self._cards.append(card)

    def _card(self, profile_id: int) -> ProfileCard | None:
        return next((c for c in self._cards if c.summary.profile.id == profile_id), None)

    def _show_banner(self, text: str, error: bool) -> None:
        self._banner.setObjectName("Banner" if error else "Panel")
        self._banner.style().unpolish(self._banner)
        self._banner.style().polish(self._banner)
        self._banner_text.setText(text)
        self._banner.show()

    # -- ações ------------------------------------------------------------------------------------
    def create(self) -> None:
        profile = self._ctx.profiles.create(self._ctx.profiles.unique_name("Novo perfil"))
        self.reload()
        card = self._card(profile.id)
        if card is not None:
            # Já abre o nome para edição: criar e nomear é um gesto só.
            QTimer.singleShot(0, lambda: (card.name_edit.setFocus(), card.name_edit.selectAll()))

    def rename(self, card: ProfileCard) -> None:
        profile = card.summary.profile
        new_name = card.name_edit.text()
        if " ".join(new_name.split()) == profile.name:
            card.error.hide()
            return
        try:
            renamed = self._ctx.profiles.rename(profile.id, new_name)
        except ProfileError as exc:
            card.error.setText(f"{exc}.")
            card.error.show()
            card.name_edit.setText(profile.name)
            return
        card.error.hide()
        card.summary = ProfileSummary(renamed, card.summary.zones, card.summary.rules, card.summary.active_rules)
        if profile.id == self._ctx.profiles.active().id:
            self._ctx.session.profile_changed.emit(renamed)

    def use(self, profile_id: int) -> None:
        try:
            self._ctx.session.switch_profile(profile_id)
        except ProfileError as exc:
            self._show_banner(f"{exc}.", error=True)
            return
        self._banner.hide()
        self._toast.show_message(f"Perfil em uso: {self._ctx.profiles.active().name}", "Ver automações",
                                 lambda: self.window().show_page("rules"), lambda: None)  # type: ignore[attr-defined]

    def duplicate(self, profile_id: int) -> None:
        copy = duplicate_profile(self._ctx.db, profile_id)
        self.reload()
        self._toast.show_message(f"{copy.name} criado", "Usar agora", lambda: self.use(copy.id), lambda: None)

    def delete(self, summary: ProfileSummary) -> None:
        profile = summary.profile
        self._pending_delete.add(profile.id)
        self.reload()

        def undo() -> None:
            self._pending_delete.discard(profile.id)
            self.reload()

        def commit() -> None:
            if profile.id in self._pending_delete:
                self._pending_delete.discard(profile.id)
                try:
                    self._ctx.profiles.delete(profile.id)
                except ProfileError as exc:
                    self._show_banner(f"{exc}.", error=True)
                self.reload()

        detail = f" e suas {summary.zones} zonas e {summary.rules} automações" if summary.zones or summary.rules else ""
        self._toast.show_message(f"{profile.name}{detail} excluídos", "Desfazer", undo, commit)

    def export(self, profile_id: int, name: str) -> None:
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "perfil"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar perfil", str(Path(folder) / f"{safe}.vanthor.json"), _FILE_FILTER
        )
        if not path:
            return
        self.export_to(profile_id, Path(path))

    def export_to(self, profile_id: int, path: Path) -> None:
        try:
            path.write_text(to_json(export_profile(self._ctx.db, profile_id)), encoding="utf-8")
        except OSError as exc:
            self._show_banner(f"Não foi possível salvar o arquivo: {exc}", error=True)
            return
        self._toast.show_message(
            f"Perfil exportado para {path.name}",
            "Abrir pasta",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))),
            lambda: None,
        )

    def import_file(self) -> None:
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _ = QFileDialog.getOpenFileName(self, "Importar perfil", folder, _FILE_FILTER)
        if path:
            self.import_from(Path(path))

    def import_from(self, path: Path) -> None:
        try:
            report = import_profile(self._ctx.db, parse_profile_file(path.read_bytes()))
        except ProfileFileError as exc:
            self._show_banner(f"Este arquivo não pôde ser importado: {exc}. Nada foi alterado.", error=True)
            return
        except OSError as exc:
            self._show_banner(f"Não foi possível ler o arquivo: {exc}", error=True)
            return
        maps = monitor_maps()
        offscreen = sum(1 for rect in report.zone_rects if monitor_for_rect(rect, maps) is None)
        message = (
            f"Perfil {report.profile.name} importado com {report.zones} "
            f"{'zona' if report.zones == 1 else 'zonas'} e {report.rules} "
            f"{'automação' if report.rules == 1 else 'automações'}. As automações chegaram pausadas: "
            "revise os pontos de clique e ligue as que quiser usar."
        )
        if offscreen:
            message += (
                f" {offscreen} {'zona está' if offscreen == 1 else 'zonas estão'} fora das telas deste computador; "
                "ajuste em Zonas depois de usar o perfil."
            )
        self._show_banner(message, error=False)
        self.reload()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.reload()
