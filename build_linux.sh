#!/usr/bin/env bash
# Genera un ejecutable único para Linux.
set -e

cd "$(dirname "$0")"

echo "[1/4] Instalando dependencias de build..."
python3 -m pip install --user --upgrade pyinstaller

echo "[2/4] Limpiando builds previos..."
rm -rf build dist

echo "[3/4] Empaquetando con PyInstaller..."
python3 -m PyInstaller ccpdf.spec --noconfirm --clean

echo "[4/4] Listo."
echo "Ejecutable en: dist/CCpdf"
echo "Para lanzar:  ./dist/CCpdf"
