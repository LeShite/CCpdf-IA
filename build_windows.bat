@echo off
REM Genera un .exe único para Windows.
cd /d "%~dp0"

echo [1/4] Instalando dependencias de build...
python -m pip install --upgrade pyinstaller

echo [2/4] Limpiando builds previos...
if exist build rmdir /S /Q build
if exist dist  rmdir /S /Q dist

echo [3/4] Empaquetando con PyInstaller...
python -m PyInstaller ccpdf.spec --noconfirm --clean

echo [4/4] Listo.
echo Ejecutable en: dist\CCpdf.exe
pause
