# -*- mode: python ; coding: utf-8 -*-
"""
Spec de PyInstaller para CCpdf.
Empaqueta la app + todos los recursos de CustomTkinter + los motores.
"""
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Recursos de CustomTkinter (fuentes, temas JSON)
datas = collect_data_files("customtkinter")

# Módulos ocultos que PyInstaller no detecta solo
hiddenimports = (
    collect_submodules("customtkinter")
    + collect_submodules("pypdf")
    + collect_submodules("PIL")
)

# Añadir nuestros assets (logo)
assets_dir = os.path.join("Interfaz", "assets")
if os.path.isdir(assets_dir):
    for archivo in os.listdir(assets_dir):
        datas.append((os.path.join(assets_dir, archivo), "Interfaz/assets"))

# Nuestros módulos Python como datas (por si el empaquetado es onefile)
for carpeta in ("Motores", "Interfaz"):
    for raiz, _, archivos in os.walk(carpeta):
        for a in archivos:
            if a.endswith(".py"):
                continue  # el código va como bytecode, no como dato
        # si tienes .json u otros datos, descomenta:
        # for a in archivos:
        #     if not a.endswith(".py"):
        #         datas.append((os.path.join(raiz, a), raiz))

a = Analysis(
    ["main.py"],
    pathex=[os.path.abspath(".")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter.test", "test"],
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
    name="CCpdf",
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
