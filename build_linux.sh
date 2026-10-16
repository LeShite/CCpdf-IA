#!/usr/bin/env bash
# Genera un ejecutable único para Linux.
set -e

cd "$(dirname "$0")"

# Usar el venv del proyecto si existe (ya trae reportlab, pytesseract,
# pdf2image, llama-cpp-python, etc.); si no, caer al python3 del sistema.
if [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
    PIP_FLAGS="--upgrade"
else
    PYTHON="python3"
    PIP_FLAGS="--user --upgrade"
fi

echo "[1/4] Instalando dependencias de build..."
"$PYTHON" -m pip install $PIP_FLAGS pyinstaller

echo "[2/4] Limpiando builds previos..."
rm -rf build dist

echo "[3/4] Empaquetando con PyInstaller..."
"$PYTHON" -m PyInstaller ccpdf_ia.spec --noconfirm --clean

echo "[4/4] Listo."
echo "Ejecutable en: dist/CCpdf IA"
echo "Para lanzar:  ./dist/CCpdf\ IA"
