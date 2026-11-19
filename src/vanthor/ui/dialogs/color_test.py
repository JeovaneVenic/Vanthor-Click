"""Testar cor: configurar a cor procurada numa zona e ver, ao vivo, o que é encontrado."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from vanthor.capture.capturer import CaptureError, Image, RegionCapturer
from vanthor.capture.zones import Zone
from vanthor.core.engine import EngineState
from vanthor.ui.conceal import conceal_app_windows
from vanthor.ui.context import AppContext
from vanthor.ui.copy import format_count
from vanthor.ui.imaging import highlight_matches, to_pixmap
from vanthor.ui.overlays.eyedropper import Eyedropper
from vanthor.ui.theme import icons
from vanthor.ui.theme.fonts import display_font
from vanthor.ui.theme.tokens import Color, Size
from vanthor.ui.widgets.segmented import Segmented
from vanthor.ui.widgets.toggle_switch import ToggleSwitch
from vanthor.vision.color import (
    DEFAULT_TOLERANCE,
    MAX_TOLERANCE,
    RGB,
    ColorCondition,
    ColorSpace,
    DetectionResult,
    MatchMode,
    detect,
    hex_to_rgb,
    rgb_to_hex,
    suggest_color,
)

log = logging.getLogger(__name__)

_PREVIEW = QSize(520, 300)
_REFRESH_MS = 200
_SAVE_DELAY_MS = 400


def _label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


class ColorTestDialog(QDialog):
    def __init__(
        self,
        ctx: AppContext,
        zone: Zone,
        parent: QWidget | None = None,
        initial: ColorCondition | None = None,
        save_draft: bool = True,
    ) -> None:
        """``save_draft=False``: modo editor de automação (o resultado é lido em ``condition()``)."""
        super().__init__(parent)
        self._ctx = ctx
        self._zone = zone
        self._initial = initial
        self._save_draft = save_draft
        self._capturer: RegionCapturer | None = None
        self._image: Image | None = None
        self._eyedropper: Eyedropper | None = None
        self._restore_windows: Callable[[], None] | None = None
        self._loading = True

        self.setWindowTitle(f"Testar cor: {zone.name}")
        self.setMinimumSize(1000, 640)

        root = QHBoxLayout(self)
        root.setContentsMargins(Size.PAGE_MARGIN, 28, Size.PAGE_MARGIN, Size.PAGE_MARGIN)
        root.setSpacing(24)
        root.addLayout(self._build_preview(), 1)
        root.addWidget(self._build_form())

        self._save_timer = QTimer(self, singleShot=True, interval=_SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save)
        self._timer = QTimer(self, interval=_REFRESH_MS)
        self._timer.timeout.connect(self._tick)

        self._load_initial()
        self._loading = False
        self._tick()
        self._timer.start()

    # -- montagem -------------------------------------------------------------------------
    def _build_preview(self) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(14)
        head = QHBoxLayout()
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {self._zone.color}; border-radius: 6px;")
        head.addWidget(dot)
        head.addWidget(_label(f"Testar cor em {self._zone.name}", "PageTitle"))
        head.addStretch(1)
        col.addLayout(head)
        col.addWidget(
            _label(
                "A prévia mostra a zona ao vivo. Com o destaque ligado, só os pixels encontrados mantêm a cor.",
                "Muted",
                wrap=True,
            )
        )

        self._preview = QLabel()
        self._preview.setMinimumSize(_PREVIEW)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            f"background: {Color.SURFACE}; border: 1px solid {Color.LINE}; border-radius: {Size.RADIUS_L}px;"
            f" color: {Color.TEXT_3};"
        )
        self._preview.setAccessibleName("Prévia da zona")
        col.addWidget(self._preview, 1)

        toggle_row = QHBoxLayout()
        self._highlight = ToggleSwitch()
        self._highlight.setChecked(True)
        self._highlight.setAccessibleName("Destacar pixels encontrados")
        self._highlight.toggled.connect(lambda _on: self._tick())
        toggle_row.addWidget(self._highlight)
        toggle_row.addWidget(_label("Destacar pixels encontrados"))
        toggle_row.addStretch(1)
        col.addLayout(toggle_row)

        result = QFrame()
        result.setObjectName("Panel")
        r = QHBoxLayout(result)
        r.setContentsMargins(18, 14, 18, 14)
        r.setSpacing(14)
        self._result_icon = QLabel()
        r.addWidget(self._result_icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self._result_title = _label()
        self._result_title.setFont(display_font(20, QFont.Weight.DemiBold))
        self._result_detail = _label("", "Muted", wrap=True)
        texts.addWidget(self._result_title)
        texts.addWidget(self._result_detail)
        r.addLayout(texts, 1)
        self._timing = _label("", "Faint")
        r.addWidget(self._timing, 0, Qt.AlignmentFlag.AlignBottom)
        col.addWidget(result)
        return col

    def _build_form(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedWidth(380)
        f = QVBoxLayout(panel)
        f.setContentsMargins(22, 20, 22, 20)
        f.setSpacing(8)

        # Cor procurada
        f.addWidget(_label("Cor procurada", "SectionTitle"))
        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        self._swatch = QLabel()
        self._swatch.setFixedSize(44, 44)
        color_row.addWidget(self._swatch)
        self._hex = QLineEdit()
        self._hex.setMaxLength(7)
        self._hex.setFixedWidth(110)
        self._hex.setAccessibleName("Cor em HEX")
        self._hex.setPlaceholderText("#22C55E")
        self._hex.editingFinished.connect(self._hex_edited)
        color_row.addWidget(self._hex)
        picker = QPushButton("  Conta-gotas")
        picker.setIcon(icons.icon("crosshair", Color.TEXT, 16))
        picker.setCursor(Qt.CursorShape.PointingHandCursor)
        picker.setToolTip("Clique em qualquer ponto da tela para pegar a cor")
        picker.clicked.connect(self._start_eyedropper)
        color_row.addWidget(picker, 1)
        f.addLayout(color_row)

        rgb_row = QHBoxLayout()
        rgb_row.setSpacing(8)
        self._rgb: list[QSpinBox] = []
        for channel in ("R", "G", "B"):
            rgb_row.addWidget(_label(channel, "Faint"))
            spin = QSpinBox()
            spin.setRange(0, 255)
            spin.setAccessibleName(f"Canal {channel}")
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.valueChanged.connect(self._rgb_edited)
            rgb_row.addWidget(spin, 1)
            self._rgb.append(spin)
        f.addLayout(rgb_row)
        self._color_error = _label("", "FieldError")
        self._color_error.hide()
        f.addWidget(self._color_error)
        f.addSpacing(14)

        # Comparação
        f.addWidget(_label("Comparação", "SectionTitle"))
        self._space = Segmented([(ColorSpace.RGB.value, "RGB exato"), (ColorSpace.HSV.value, "HSV tolerante à luz")])
        self._space.changed.connect(self._space_changed)
        f.addWidget(self._space)
        self._space_help = _label("", "Faint", wrap=True)
        f.addWidget(self._space_help)
        f.addSpacing(14)

        # Tolerância
        tol_head = QHBoxLayout()
        tol_head.addWidget(_label("Tolerância", "SectionTitle"))
        tol_head.addStretch(1)
        self._tol_value = _label()
        self._tol_value.setFont(display_font(18, QFont.Weight.DemiBold))
        tol_head.addWidget(self._tol_value)
        f.addLayout(tol_head)
        self._tolerance = QSlider(Qt.Orientation.Horizontal)
        self._tolerance.setAccessibleName("Tolerância")
        self._tolerance.valueChanged.connect(self._changed)
        f.addWidget(self._tolerance)
        self._tol_help = _label("", "Faint", wrap=True)
        f.addWidget(self._tol_help)
        f.addSpacing(14)

        # Critério
        f.addWidget(_label("Considerar encontrada quando houver", "SectionTitle"))
        self._mode = Segmented([(MatchMode.PIXELS.value, "Pixels"), (MatchMode.PERCENT.value, "% da zona")])
        self._mode.changed.connect(self._mode_changed)
        f.addWidget(self._mode)
        amount_row = QHBoxLayout()
        amount_row.addWidget(_label("Pelo menos", "Muted"))
        self._min_pixels = QSpinBox()
        self._min_pixels.setRange(1, self._zone.rect.area)
        self._min_pixels.setSuffix(" px")
        self._min_pixels.setAccessibleName("Quantidade mínima de pixels")
        self._min_pixels.valueChanged.connect(self._changed)
        self._min_percent = QDoubleSpinBox()
        self._min_percent.setRange(0.1, 100.0)
        self._min_percent.setDecimals(1)
        self._min_percent.setSingleStep(1.0)
        self._min_percent.setSuffix(" %")
        self._min_percent.setAccessibleName("Porcentagem mínima da zona")
        self._min_percent.valueChanged.connect(self._changed)
        amount_row.addWidget(self._min_pixels, 1)
        amount_row.addWidget(self._min_percent, 1)
        f.addLayout(amount_row)

        f.addStretch(1)
        self._saved = _label("", "Faint", wrap=True)
        f.addWidget(self._saved)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        if not self._save_draft:
            cancel = QPushButton("Cancelar")
            cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel.clicked.connect(self.reject)
            buttons.addWidget(cancel)
        close = QPushButton("Concluir" if self._save_draft else "Usar esta cor")
        close.setObjectName("Primary")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addWidget(close, 1)
        f.addLayout(buttons)
        return panel

    # -- estado -------------------------------------------------------------------------
    def _load_initial(self) -> None:
        condition = self._initial or self._ctx.zones.color_draft(self._zone.id)
        if condition is None:
            # Sem ajuste salvo: começa pela cor predominante no miolo da zona (a cor do botão,
            # não a do texto nem a do fundo em volta).
            image = self._capture()
            condition = ColorCondition(suggest_color(image) if image is not None else (255, 255, 255))
        self._apply(condition)

    def _apply(self, c: ColorCondition) -> None:
        self._loading = True
        self._set_color_widgets(c.color)
        self._space.set_value(c.space.value)
        self._tolerance.setRange(0, MAX_TOLERANCE[c.space])
        self._tolerance.setValue(c.tolerance)
        self._mode.set_value(c.mode.value)
        self._min_pixels.setValue(c.min_pixels)
        self._min_percent.setValue(c.min_percent)
        self._loading = False
        self._refresh_labels()

    def condition(self) -> ColorCondition:
        return ColorCondition(
            color=(self._rgb[0].value(), self._rgb[1].value(), self._rgb[2].value()),
            space=ColorSpace(self._space.value()),
            tolerance=self._tolerance.value(),
            mode=MatchMode(self._mode.value()),
            min_pixels=self._min_pixels.value(),
            min_percent=round(self._min_percent.value(), 1),
        )

    def set_color(self, rgb: RGB) -> None:
        self._set_color_widgets(rgb)
        self._changed()

    def _set_color_widgets(self, rgb: RGB) -> None:
        loading, self._loading = self._loading, True
        for spin, value in zip(self._rgb, rgb, strict=True):
            spin.setValue(value)
        self._hex.setText(rgb_to_hex(rgb))
        self._swatch.setStyleSheet(
            f"background: {rgb_to_hex(rgb)}; border: 1px solid {Color.LINE_STRONG}; border-radius: 10px;"
        )
        self._color_error.hide()
        self._loading = loading

    # -- reações ------------------------------------------------------------------------
    def _hex_edited(self) -> None:
        try:
            rgb = hex_to_rgb(self._hex.text())
        except ValueError:
            self._color_error.setText("Use o formato #RRGGBB, por exemplo #22C55E.")
            self._color_error.show()
            return
        self.set_color(rgb)

    def _rgb_edited(self) -> None:
        if self._loading:
            return
        self._set_color_widgets((self._rgb[0].value(), self._rgb[1].value(), self._rgb[2].value()))
        self._changed()

    def _space_changed(self, value: str) -> None:
        space = ColorSpace(value)
        self._loading = True
        self._tolerance.setRange(0, MAX_TOLERANCE[space])
        self._tolerance.setValue(DEFAULT_TOLERANCE[space])  # as escalas são diferentes: recomeça do padrão
        self._loading = False
        self._changed()

    def _mode_changed(self, _value: str) -> None:
        self._changed()

    def _changed(self) -> None:
        if self._loading:
            return
        self._refresh_labels()
        self._save_timer.start()
        self._tick()

    def _refresh_labels(self) -> None:
        space = ColorSpace(self._space.value())
        t = self._tolerance.value()
        if space is ColorSpace.RGB:
            self._space_help.setText("Compara vermelho, verde e azul separadamente. Mais preciso.")
            self._tol_value.setText(str(t))
            self._tol_help.setText(
                "Exige a cor idêntica." if t == 0 else f"Cada canal pode variar até {t} níveis (de 0 a 255)."
            )
        else:
            self._space_help.setText("Compara tom, saturação e brilho. Aceita a mesma cor mais clara ou mais escura.")
            self._tol_value.setText(f"{t}%")
            self._tol_help.setText(f"Tom até {_fmt(t * 1.8, 0)}° de diferença; saturação e brilho até {t}%.")
        percent = self._mode.value() == MatchMode.PERCENT.value
        self._min_pixels.setVisible(not percent)
        self._min_percent.setVisible(percent)

    def _save(self) -> None:
        if not self._save_draft:
            return
        try:
            self._ctx.zones.save_color_draft(self._zone.id, self.condition())
        except ValueError as exc:
            log.warning("Ajuste de cor inválido: %s", exc)
            return
        self._saved.setText(f"Ajuste salvo em {self._zone.name}. Ele é o ponto de partida ao criar uma automação.")

    # -- análise ao vivo ------------------------------------------------------------------
    def _capture(self) -> Image | None:
        if self._ctx.session.state is not EngineState.STOPPED:
            frame = self._ctx.session.zone_monitor.latest_frame(self._zone.id)
            if frame is not None:
                return frame.image
        if self._capturer is None:
            self._capturer = RegionCapturer()
        try:
            return self._capturer.grab(self._zone.rect)
        except CaptureError as exc:
            self._preview.setText(f"Não foi possível capturar a zona: {exc}")
            return None

    def _tick(self) -> None:
        image = self._capture()
        if image is None:
            return
        self._image = image
        try:
            condition = self.condition()
        except ValueError:
            return
        started = time.perf_counter()
        result = detect(image, condition, with_mask=True)
        elapsed = (time.perf_counter() - started) * 1000
        shown = highlight_matches(image, result.mask) if self._highlight.isChecked() and result.mask is not None else image
        self._preview.setPixmap(to_pixmap(shown, self._preview.size() - QSize(24, 24), self.devicePixelRatioF()))
        self._show_result(result, condition, elapsed)

    def _show_result(self, result: DetectionResult, condition: ColorCondition, elapsed_ms: float) -> None:
        if result.detected:
            self._result_icon.setPixmap(icons.pixmap("circle-check", Color.DETECT, 28))
            self._result_title.setText("Cor encontrada")
            self._result_title.setStyleSheet(f"color: {Color.DETECT};")
        else:
            self._result_icon.setPixmap(icons.pixmap("eye-off", Color.STEEL, 28))
            self._result_title.setText("Cor não encontrada")
            self._result_title.setStyleSheet(f"color: {Color.TEXT};")
        need = (
            f"pelo menos {format_count(condition.min_pixels)} px"
            if condition.mode is MatchMode.PIXELS
            else f"pelo menos {_fmt(condition.min_percent)}% da zona"
        )
        self._result_detail.setText(
            f"{format_count(result.matched_pixels)} de {format_count(result.total_pixels)} pixels "
            f"({_fmt(result.ratio * 100)}% da zona). Precisa de {need}."
        )
        self._timing.setText(f"Análise em {_fmt(elapsed_ms, 2)} ms")

    # -- conta-gotas ------------------------------------------------------------------------
    def _start_eyedropper(self) -> None:
        if self._eyedropper is not None:
            return
        self._timer.stop()
        eyedropper = Eyedropper()
        eyedropper.picked.connect(self._picked)
        eyedropper.cancelled.connect(self._restore_after_pick)
        self._eyedropper = eyedropper
        self._restore_windows = conceal_app_windows()
        # Um instante para o Windows redesenhar sem as nossas janelas antes de capturar a lupa.
        QTimer.singleShot(120, eyedropper.start)

    def _picked(self, rgb: RGB, _x: int, _y: int) -> None:
        self._restore_after_pick()
        self.set_color(rgb)

    def _restore_after_pick(self) -> None:
        self._eyedropper = None
        if self._restore_windows is not None:
            self._restore_windows()
            self._restore_windows = None
        self._timer.start()

    # -- encerramento -------------------------------------------------------------------------
    def done(self, result: int) -> None:
        if self._save_timer.isActive():
            self._save_timer.stop()
            self._save()
        self._timer.stop()
        if self._capturer is not None:
            self._capturer.close()
            self._capturer = None
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.done(QDialog.DialogCode.Rejected)
        event.accept()
