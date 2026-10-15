#!/usr/bin/env python3
"""CCpdf — Lanzador. Ejecuta: python3 main.py"""
import os
import sys

DIRECTORIO_ACTUAL = os.path.dirname(os.path.abspath(__file__))
if DIRECTORIO_ACTUAL not in sys.path:
    sys.path.insert(0, DIRECTORIO_ACTUAL)

from Interfaz.app import CCPdfApp


if __name__ == "__main__":
    CCPdfApp().mainloop()
