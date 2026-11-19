# -*- mode: python ; coding: utf-8 -*-
# Empacotamento do Vanthor Bot Click (PyInstaller, modo pasta).
#
# Escolhas e motivos:
# - onedir (pasta) em vez de onefile: abre bem mais rápido (não descompacta a cada execução) e
#   gera menos alarmes falsos de antivírus.
# - sem UPX: binários comprimidos com UPX são um gatilho clássico de falso positivo.
# - console=False: aplicativo de janela, sem terminal preto ao abrir.
# - módulos Qt que o app não usa ficam de fora para reduzir o tamanho do instalador.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent  # noqa: F821 (SPECPATH é definido pelo PyInstaller)
SRC = ROOT / "src"
VERSION = re.search(r'__version__ = "([^"]+)"', (SRC / "vanthor" / "__init__.py").read_text("utf-8")).group(1)
NUMBERS = tuple(int(p) for p in VERSION.split(".")) + (0,)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMBERS, prodvers=NUMBERS),
    kids=[
        StringFileInfo([
            StringTable("041604B0", [
                StringStruct("CompanyName", "Vanthor"),
                StringStruct("FileDescription", "Vanthor Bot Click"),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "VanthorBotClick"),
                StringStruct("LegalCopyright", "© 2026 Vanthor"),
                StringStruct("OriginalFilename", "VanthorBotClick.exe"),
                StringStruct("ProductName", "Vanthor Bot Click"),
                StringStruct("ProductVersion", VERSION),
            ])
        ]),
        VarFileInfo([VarStruct("Translation", [0x0416, 1200])]),  # português (Brasil), Unicode
    ],
)

EXCLUDES = [
    "tkinter", "unittest", "pydoc", "pytest", "_pytest", "pytestqt",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtNetwork",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuick3D",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtNfc", "PySide6.QtHttpServer",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "vanthor" / "resources"), "vanthor/resources")],
    hiddenimports=[],
    excludes=EXCLUDES,
    noarchive=False,
)

# Arquivos que plugins do Qt arrastam mas o app nunca usa (medido: ~60 MB):
# OpenGL por software, Qt Quick/QML (só o teclado virtual usa), leitor de PDF, rede e SSL
# (o app não acessa a internet) e formatos de imagem/plataformas que não carregamos.
DROP = re.compile(
    r"^(opengl32sw\.dll"
    r"|Qt6(Quick|Qml|Pdf|Network|OpenGL|VirtualKeyboard)\w*\.dll"
    r"|qpdf\.dll|qtvirtualkeyboardplugin\.dll|qdirect2d\.dll|qminimal\.dll|qoffscreen\.dll"
    r"|qtuiotouchplugin\.dll|qicns\.dll|qtiff\.dll|qwebp\.dll|qtga\.dll|qwbmp\.dll|qgif\.dll|qjpeg\.dll"
    r"|libssl-3\w*\.dll|libcrypto-3\w*\.dll|_ssl\.pyd)$",
    re.IGNORECASE,
)


def _keep(entry: tuple) -> bool:
    name = Path(entry[0]).name
    if DROP.match(name):
        return False
    # Traduções do Qt: só português do Brasil (botões padrão como "OK" e "Cancelar").
    if "translations" in Path(entry[0]).parts and name.endswith(".qm"):
        return name.startswith("qtbase_pt_BR")
    return True


a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VanthorBotClick",
    icon=str(SRC / "vanthor" / "resources" / "brand" / "app.ico"),
    version=version_info,
    console=False,
    upx=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="VanthorBotClick",
)
