"""Folha de estilo global gerada a partir dos tokens."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from vanthor.ui.theme.fonts import ui_font
from vanthor.ui.theme.tokens import Color as C
from vanthor.ui.theme.tokens import Size as S


def apply_theme(app: QApplication) -> None:
    app.setFont(ui_font(S.BODY))
    app.setStyleSheet(build_stylesheet())


def build_stylesheet() -> str:
    return f"""
    /* Família e tamanho base vêm de QApplication.setFont (ver apply_theme). Definir fonte em "*"
       aqui anularia o setFont dos números em Bahnschrift. */
    * {{ color: {C.TEXT}; outline: none; }}
    QMainWindow, #Root {{ background: {C.BG}; }}
    QToolTip {{
        background: {C.RAISED}; color: {C.TEXT}; border: 1px solid {C.LINE_STRONG};
        padding: 6px 8px; border-radius: {S.RADIUS_S}px;
    }}

    /* Barra lateral */
    #Sidebar {{ background: {C.SURFACE}; border-right: 1px solid {C.LINE}; }}
    #BrandName {{ color: {C.TEXT}; }}
    #BrandProduct {{ color: {C.BRASA}; }}
    #NavButton {{
        text-align: left; padding: 0 14px; border: none; border-radius: {S.RADIUS_M}px;
        min-height: {S.CONTROL_H + 4}px; color: {C.TEXT_2}; background: transparent;
    }}
    #NavButton:hover {{ background: {C.RAISED}; color: {C.TEXT}; }}
    #NavButton:checked {{ background: {C.RAISED}; color: {C.TEXT}; font-weight: 600; }}
    #NavButton:focus-visible {{ border: 2px solid {C.BRASA}; }}
    #Dock {{ border-top: 1px solid {C.LINE}; }}
    QPushButton#ProfileButton {{
        text-align: left; padding: 0 12px; min-height: 36px; border-radius: {S.RADIUS_M}px;
        background: {C.BG}; border: 1px solid {C.LINE}; color: {C.TEXT_2}; font-size: {S.SMALL}px;
    }}
    QPushButton#ProfileButton:hover {{ border-color: {C.LINE_STRONG}; color: {C.TEXT}; }}
    #Chip {{
        border-radius: 10px; padding: 1px 9px; font-size: 12px; font-weight: 600;
        color: {C.ON_BRASA}; background: {C.BRASA};
    }}
    #DockState {{ font-weight: 600; }}
    #DockHint {{ color: {C.TEXT_3}; font-size: {S.SMALL}px; }}

    /* Faixa de execução no topo do conteúdo */
    #RunStrip[state="running"] {{ background: {C.BRASA}; }}
    #RunStrip[state="paused"] {{ background: {C.STEEL}; }}
    #RunStrip[state="testing"] {{ background: {C.SIM}; }}
    #Hero[state="testing"] {{ border: 1px solid {C.SIM}; }}

    /* Tabelas do monitoramento */
    QTableWidget#MonitorTable {{ background: transparent; border: none; }}
    QTableWidget#MonitorTable::item {{ border-bottom: 1px solid {C.LINE}; padding: 0 8px; }}
    QHeaderView::section {{
        background: transparent; color: {C.TEXT_2}; border: none; border-bottom: 1px solid {C.LINE_STRONG};
        padding: 6px 8px; font-size: {S.SMALL}px; font-weight: 600;
    }}
    QTableCornerButton::section {{ background: transparent; border: none; }}
    #RunStrip[state="stopped"] {{ background: transparent; }}

    /* Páginas */
    #PageTitle {{ font-size: {S.TITLE}px; font-weight: 600; }}
    #SectionTitle {{ font-size: 15px; font-weight: 600; }}
    #Muted {{ color: {C.TEXT_2}; }}
    #Faint {{ color: {C.TEXT_3}; font-size: {S.SMALL}px; }}
    #Panel {{ background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {S.RADIUS_L}px; }}
    #Hero {{ background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {S.RADIUS_L}px; }}
    #Hero[state="running"] {{ border: 1px solid {C.BRASA}; }}
    #Divider {{ background: {C.LINE}; }}
    #StateTitle {{ font-size: {S.STATE}px; font-weight: 600; }}
    #StatLabel {{ color: {C.TEXT_2}; font-size: {S.SMALL}px; }}
    #Banner {{
        background: rgba(244, 63, 94, 0.10); border: 1px solid rgba(244, 63, 94, 0.45);
        border-radius: {S.RADIUS_M}px;
    }}
    #BannerText {{ color: {C.TEXT}; }}
    #StepNumber {{
        color: {C.BRASA}; font-weight: 700; border: 1px solid {C.LINE_STRONG};
        border-radius: 13px; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
    }}

    /* Botões */
    QPushButton {{
        min-height: {S.CONTROL_H}px; padding: 0 16px; border-radius: {S.RADIUS_M}px;
        background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; color: {C.TEXT};
    }}
    QPushButton:hover {{ background: {C.RAISED_HOVER}; }}
    QPushButton:pressed {{ background: {C.LINE}; }}
    QPushButton:disabled {{ color: {C.TEXT_3}; border-color: {C.LINE}; }}
    QPushButton:focus-visible {{ border: 2px solid {C.BRASA}; }}
    QPushButton#Primary {{
        min-height: {S.PRIMARY_H}px; padding: 0 22px; font-size: 15px; font-weight: 600;
        background: {C.BRASA}; border: none; color: {C.ON_BRASA};
    }}
    QPushButton#Primary:hover {{ background: {C.BRASA_HOVER}; }}
    QPushButton#Primary:pressed {{ background: {C.BRASA_PRESSED}; }}
    QPushButton#Primary:focus-visible {{ border: 2px solid {C.TEXT}; }}
    QPushButton#StopPrimary {{
        min-height: {S.PRIMARY_H}px; padding: 0 22px; font-size: 15px; font-weight: 600;
        background: {C.TEXT}; border: none; color: {C.BG};
    }}
    QPushButton#StopPrimary:hover {{ background: #FFFFFF; }}
    QPushButton#StopPrimary:focus-visible {{ border: 2px solid {C.BRASA}; }}
    QPushButton#Quiet {{ background: transparent; border: 1px solid transparent; color: {C.TEXT_2}; }}
    QPushButton#Quiet:hover {{ background: {C.RAISED}; color: {C.TEXT}; }}

    /* Campos */
    QKeySequenceEdit QLineEdit, QLineEdit {{
        min-height: {S.CONTROL_H - 2}px; padding: 0 12px; border-radius: {S.RADIUS_M}px;
        background: {C.BG}; border: 1px solid {C.LINE_STRONG}; color: {C.TEXT};
        selection-background-color: {C.BRASA}; selection-color: {C.ON_BRASA};
    }}
    QKeySequenceEdit QLineEdit:focus, QLineEdit:focus {{ border: 1px solid {C.BRASA}; }}
    QKeySequenceEdit QLineEdit:disabled {{ color: {C.TEXT_3}; }}
    #FieldError {{ color: {C.DANGER}; font-size: {S.SMALL}px; }}
    QLineEdit#InlineName {{
        background: transparent; border: 1px solid transparent; padding: 0 6px; margin-left: -7px;
        font-size: 16px; font-weight: 600; min-height: 32px;
    }}
    QLineEdit#InlineName:hover {{ border: 1px solid {C.LINE_STRONG}; }}
    QLineEdit#InlineName:focus {{ border: 1px solid {C.BRASA}; background: {C.BG}; }}
    QComboBox {{
        min-height: {S.CONTROL_H - 2}px; padding: 0 12px; border-radius: {S.RADIUS_M}px;
        background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; min-width: 150px;
    }}
    QComboBox:hover {{ background: {C.RAISED_HOVER}; }}
    QComboBox:focus {{ border: 1px solid {C.BRASA}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {C.RAISED}; border: 1px solid {C.LINE_STRONG}; padding: 4px;
        selection-background-color: {C.RAISED_HOVER}; selection-color: {C.TEXT}; outline: none;
    }}
    QPushButton:checked {{ background: {C.RAISED_HOVER}; border-color: {C.TEXT_3}; }}

    /* Controle segmentado */
    #Segmented {{ background: {C.BG}; border: 1px solid {C.LINE_STRONG}; border-radius: {S.RADIUS_M}px; }}
    QPushButton#Segment {{
        min-height: 32px; border: none; border-radius: 7px; background: transparent; color: {C.TEXT_2};
        padding: 0 12px;
    }}
    QPushButton#Segment:hover {{ color: {C.TEXT}; }}
    QPushButton#Segment:checked {{ background: {C.RAISED_HOVER}; color: {C.TEXT}; font-weight: 600; }}

    /* Números e deslizante */
    QSpinBox, QDoubleSpinBox {{
        min-height: {S.CONTROL_H - 2}px; padding: 0 8px; border-radius: {S.RADIUS_M}px;
        background: {C.BG}; border: 1px solid {C.LINE_STRONG};
        selection-background-color: {C.BRASA}; selection-color: {C.ON_BRASA};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {C.BRASA}; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
        width: 0; border: none;
    }}
    QSlider {{ min-height: 28px; }}
    QSlider::groove:horizontal {{ height: 4px; background: {C.LINE_STRONG}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {C.BRASA}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 18px; height: 18px; margin: -7px 0; border-radius: 9px; background: {C.TEXT};
    }}
    QSlider::handle:horizontal:hover {{ background: #FFFFFF; }}
    QSlider:focus QSlider::handle:horizontal {{ background: {C.BRASA}; }}
    QDialog {{ background: {C.BG}; }}

    /* Editor de automação */
    QToolButton#StepButton {{
        border: none; background: transparent; color: {C.TEXT_3}; font-size: 15px; padding: 6px 4px;
    }}
    QToolButton#StepButton:hover {{ color: {C.TEXT_2}; }}
    QToolButton#StepButton:checked {{ color: {C.TEXT}; font-weight: 600; }}
    QToolButton#ZoneChoice {{
        background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {S.RADIUS_M}px;
        padding: 8px; color: {C.TEXT_2};
    }}
    QToolButton#ZoneChoice:hover {{ border-color: {C.LINE_STRONG}; color: {C.TEXT}; }}
    QToolButton#ZoneChoice:checked {{ color: {C.TEXT}; font-weight: 600; background: {C.RAISED}; }}
    #OptionSelected {{
        background: {C.SURFACE}; border: 1px solid {C.BRASA}; border-radius: {S.RADIUS_L}px;
    }}
    #StatusChip {{ border-radius: 11px; padding: 2px 10px; font-size: {S.SMALL}px; }}
    QToolButton#ActionChoice {{
        background: {C.SURFACE}; border: 1px solid {C.LINE}; border-radius: {S.RADIUS_L}px;
        padding: 12px 8px; color: {C.TEXT_2}; min-height: 76px;
    }}
    QToolButton#ActionChoice:hover {{ border-color: {C.LINE_STRONG}; color: {C.TEXT}; }}
    QToolButton#ActionChoice:checked {{ border: 1px solid {C.BRASA}; background: {C.RAISED}; color: {C.TEXT}; }}
    #StepRow {{ background: {C.BG}; border: 1px solid {C.LINE}; border-radius: {S.RADIUS_M}px; }}

    /* Listas e rolagem */
    QListView {{ background: transparent; border: none; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {C.LINE_STRONG}; border-radius: 3px; min-height: 32px; }}
    QScrollBar::handle:vertical:hover {{ background: {C.TEXT_3}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; height: 0;
    }}
    QMessageBox {{ background: {C.SURFACE}; }}
    """
