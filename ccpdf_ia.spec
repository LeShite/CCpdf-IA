# -*- mode: python ; coding: utf-8 -*-
"""
Spec de PyInstaller para CCpdf.
Empaqueta la app + todos los recursos de CustomTkinter + los motores.
"""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Recursos de CustomTkinter (fuentes, temas JSON)
datas = collect_data_files("customtkinter")

# Módulos ocultos que PyInstaller no detecta solo:
# - customtkinter/pypdf/PIL: submódulos cargados dinámicamente.
# - pytesseract/pdf2image/reportlab/thefuzz: los usan los motores de
#   OCR/Compresión, pero no aparecen en ningún import de nivel superior
#   que PyInstaller pueda seguir sin ayuda.
# - Motores.*: app.py importa cada motor con exec() (ver _try_import en
#   Interfaz/app.py) para poder arrancar aunque falte alguno; PyInstaller
#   no analiza imports dentro de un exec(), así que hay que listarlos a mano.
hiddenimports = (
    collect_submodules("customtkinter")
    + collect_submodules("pypdf")
    + collect_submodules("PIL")
    + [
        "pytesseract",
        "pdf2image",
        "reportlab",
        "thefuzz",
        "customtkinter",
    ]
    + collect_submodules("Motores")
)

# Añadir nuestros assets (logo)
assets_dir = os.path.join("Interfaz", "assets")
if os.path.isdir(assets_dir):
    for archivo in os.listdir(assets_dir):
        datas.append((os.path.join(assets_dir, archivo), "Interfaz/assets"))

# Prompts de CIPA: texto plano que se lee en tiempo de ejecución
# (CIPA._cargar_prompt), no se puede empaquetar como bytecode.
prompts_dir = os.path.join("Motores", "CIPA", "prompts")
if os.path.isdir(prompts_dir):
    for archivo in os.listdir(prompts_dir):
        if archivo.endswith(".txt"):
            datas.append((os.path.join(prompts_dir, archivo), prompts_dir))

a = Analysis(
    ["main.py"],
    # Interfaz/app.py importa config/dialogo_confirmar/visor_pdf/actualizador
    # como módulos de nivel superior (vía sys.path, ver _preparar_path en
    # app.py), no como Interfaz.config, etc. Sin "Interfaz" en pathex,
    # PyInstaller no los encuentra y el .exe falla con
    # "ModuleNotFoundError: No module named 'config'".
    pathex=[os.path.abspath("."), os.path.abspath("Interfaz")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # llama_cpp: su .so nativo no se empaqueta bien en onefile (ver
    # BUG-022 en docs/KNOWN_ISSUES.md). CIPA ya degrada con gracia sin
    # él; el ejecutable usa Ollama como backend de IA.
    excludes=["tkinter.test", "test", "llama_cpp"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CCpdf IA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # sin consola en Windows
    icon="Interfaz/assets/logo.ico",   # ← Windows usa .ico
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# En macOS generarías un .app aquí con BUNDLE(...)
