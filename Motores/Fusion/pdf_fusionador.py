"""
pdf_fusionador.py — Motor de fusión de PDF para CCpdf (sin dependencias de GUI).

    fusionar_pdfs(archivos, salida, marcadores=True, ...) -> ResumenFusion

Une los PDFs en el orden recibido en un solo archivo. Un PDF dañado o con
contraseña se omite y se reporta, sin detener a los demás. Si el usuario cancela,
no se escribe nada. Desde la GUI, llámalo en un hilo (los callbacks se ejecutan
en ese hilo; pásalos a la interfaz con una cola).

Multiplataforma (Windows / Linux / macOS):
  - Escritura atómica con tempfile.mkstemp en la MISMA carpeta de salida.
  - Reintentos en os.replace por si Windows lo bloquea (AV, OneDrive, visor).
  - Soporte de rutas largas (>260) en Windows con prefijo \\\\?\\.
  - Comparación de rutas con realpath+normcase (symlinks, case del SO).

Garantías:
  - La salida nunca es una de las entradas (se comprueba antes de tocar nada).
  - Verificación: el PDF fusionado se reabre y se comprueba el nº total de páginas
    antes de sustituir el destino.
  - Un PDF dañado no detiene la fusión: se omite y se sigue con los demás.
  - Si todos fallan o el usuario cancela, no se escribe ningún archivo.
"""
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pypdf import PdfReader, PdfWriter

__all__ = [
    "ErrorFusion", "ResumenFusion",
    "fusionar_pdfs", "listar_pdfs_carpeta", "ordenar_natural",
]


class ErrorFusion(Exception):
    """Error de validación o de escritura (mensaje listo para mostrar al usuario)."""


@dataclass
class ResumenFusion:
    total_archivos: int = 0
    fusionados: int = 0
    paginas: int = 0
    omitidos_duplicados: list = field(default_factory=list)  # entradas repetidas
    errores: list = field(default_factory=list)              # (archivo, mensaje)
    cancelado: bool = False
    ruta_salida: str = ""                                    # vacío si no se generó archivo

    def a_texto(self) -> str:
        if self.cancelado:
            return "⚠ Proceso cancelado por el usuario. No se generó ningún archivo."
        l = []
        if self.ruta_salida:
            l.append(f"Fusionados {self.fusionados} de {self.total_archivos} PDFs "
                     f"({self.paginas} páginas)")
            l.append(f"Salida: {self.ruta_salida}")
        else:
            l.append("No se generó ningún archivo.")
        if self.omitidos_duplicados:
            l.append(f"Duplicados omitidos ({len(self.omitidos_duplicados)}): "
                     + ", ".join(self.omitidos_duplicados[:10])
                     + ("..." if len(self.omitidos_duplicados) > 10 else ""))
        for archivo, msg in self.errores:
            l.append(f"OMITIDO {archivo}: {msg}")
        return "\n".join(l)


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------
def _clave_natural(ruta: str):
    # "doc2.pdf" antes que "doc10.pdf"
    nombre = os.path.basename(ruta).lower()
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", nombre)]


def _clave_fichero(p: str) -> str:
    """Clave robusta para comparar rutas (resuelve symlinks y case del SO)."""
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _carpeta_de(p: str) -> str:
    d = os.path.dirname(os.path.abspath(p))
    return d or os.getcwd()


_CARACTERES_INVALIDOS_WIN = set('<>:"|?*')


def _validar_nombre_salida(salida: str) -> None:
    if os.name == "nt":
        base = os.path.basename(salida)
        if any(c in _CARACTERES_INVALIDOS_WIN for c in base):
            raise ErrorFusion(
                "El nombre de salida contiene caracteres no permitidos en Windows "
                "(< > : \" | ? *)."
            )


def _abrir_para_escribir(ruta: str):
    """En Windows, si la ruta supera MAX_PATH, la prefija con \\\\?\\."""
    if os.name == "nt" and len(ruta) >= 260 and not ruta.startswith("\\\\?\\"):
        ruta = "\\\\?\\" + os.path.abspath(ruta)
    return open(ruta, "wb")


def _reemplazar_con_reintentos(origen: str, destino: str,
                               intentos: int = 5, espera: float = 0.2) -> None:
    """os.replace puede fallar en Windows si el destino está bloqueado por
    antivirus, OneDrive/Dropbox o el visor de PDFs. Reintentamos un poco."""
    ultimo = None
    for i in range(intentos):
        try:
            os.replace(origen, destino)
            return
        except PermissionError as e:
            ultimo = e
            if i < intentos - 1:
                time.sleep(espera)
    assert ultimo is not None
    raise ultimo


def _num_paginas(ruta: str) -> int:
    r = PdfReader(ruta, strict=False)
    if r.is_encrypted and not r.decrypt(""):
        raise ValueError("PDF protegido con contraseña")
    return len(r.pages)


def _deduplicar(archivos: list):
    """Devuelve (lista_sin_repetidos, lista_de_repetidos). Usa realpath, así que
    'x.pdf' y './x.pdf' se cuentan como el mismo archivo."""
    vistos, unicos, repetidos = set(), [], []
    for a in archivos:
        try:
            k = _clave_fichero(a)
        except OSError:
            unicos.append(a)
            continue
        if k in vistos:
            repetidos.append(os.path.basename(a))
        else:
            vistos.add(k)
            unicos.append(a)
    return unicos, repetidos


# ---------------------------------------------------------------------------
# Utilidades públicas
# ---------------------------------------------------------------------------
def ordenar_natural(archivos) -> list:
    return sorted(archivos, key=_clave_natural)


def listar_pdfs_carpeta(carpeta: str) -> list:
    """PDFs de una carpeta (sin subcarpetas) en orden natural."""
    if not os.path.isdir(carpeta):
        raise ErrorFusion("La carpeta no existe.")
    rutas = []
    for f in os.listdir(carpeta):
        if not f.lower().endswith(".pdf"):
            continue
        ruta = os.path.join(carpeta, f)
        if os.path.isfile(ruta):
            rutas.append(ruta)
    return ordenar_natural(rutas)


# ---------------------------------------------------------------------------
# Fusión
# ---------------------------------------------------------------------------
def fusionar_pdfs(
    archivos: list,
    salida: str,
    marcadores: bool = True,
    permitir_duplicados: bool = False,
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
) -> ResumenFusion:
    """Une `archivos` (en ese orden) en `salida`. Con `marcadores=True` agrega un
    marcador por archivo (panel de marcadores del visor) con el nombre original.

    permitir_duplicados:
      - False (por defecto): si el mismo PDF aparece dos veces, se usa la primera
        aparición y se avisa en el resumen.
      - True: se fusióna tantas veces como aparezca (útil si quieres duplicar
        páginas a propósito).
    """
    log = callback_log or (lambda m: None)
    archivos = list(archivos)
    if len(archivos) < 2:
        raise ErrorFusion("Agrega al menos 2 PDFs para fusionar.")
    salida = (salida or "").strip()
    if not salida:
        raise ErrorFusion("Indica el archivo de salida.")
    if not salida.lower().endswith(".pdf"):
        salida += ".pdf"

    _validar_nombre_salida(salida)

    # La salida no puede ser una de las entradas (se perdería el original).
    destino_abs = _clave_fichero(salida)
    for a in archivos:
        try:
            if _clave_fichero(a) == destino_abs:
                raise ErrorFusion(
                    "El archivo de salida no puede ser uno de los PDFs que estás fusionando."
                )
        except OSError:
            continue

    # Duplicados
    repetidos = []
    if not permitir_duplicados:
        archivos, repetidos = _deduplicar(archivos)
        if len(archivos) < 2:
            raise ErrorFusion(
                "Después de quitar los duplicados queda menos de 2 PDFs. "
                "Activa 'permitir duplicados' si quieres fusionar el mismo archivo varias veces."
            )

    carpeta_salida = _carpeta_de(salida)
    try:
        os.makedirs(carpeta_salida, exist_ok=True)
    except OSError as e:
        raise ErrorFusion(f"No se pudo crear la carpeta de salida: {e}")

    res = ResumenFusion(total_archivos=len(archivos),
                        omitidos_duplicados=repetidos)
    if repetidos:
        log(f"[AVISO] {len(repetidos)} archivo(s) duplicado(s) en la lista; "
            "se usó la primera aparición: " + ", ".join(repetidos[:5])
            + ("..." if len(repetidos) > 5 else ""))

    writer = PdfWriter()

    for i, ruta in enumerate(archivos, start=1):
        if cancelar is not None and cancelar.is_set():
            res.cancelado = True
            break
        nombre = os.path.basename(ruta)
        try:
            if not os.path.isfile(ruta):
                raise FileNotFoundError("el archivo ya no existe")
            reader = PdfReader(ruta, strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                raise ValueError("PDF protegido con contraseña")
            n = len(reader.pages)
            if n == 0:
                raise ValueError("el PDF no tiene páginas")
            writer.append(reader,
                          outline_item=os.path.splitext(nombre)[0] if marcadores else None)
            res.fusionados += 1
            res.paginas += n
            log(f"[OK] {nombre} ({n} pág.)")
        except Exception as e:
            res.errores.append((nombre, str(e)))
            log(f"[OMITIDO] {nombre}: {e}")
        finally:
            if callback_progreso:
                try:
                    callback_progreso(i, res.total_archivos, nombre)
                except Exception:
                    pass

    if res.cancelado or res.fusionados == 0:
        return res

    # Cancelación justo antes de escribir (por si el usuario pulsó Detener
    # mientras terminaba el último PDF)
    if cancelar is not None and cancelar.is_set():
        res.cancelado = True
        return res

    log("Guardando archivo...")

    # Escritura atómica: temp en la MISMA carpeta y rename con reintentos.
    try:
        fd, tmp = tempfile.mkstemp(dir=carpeta_salida, prefix=".fusion_", suffix=".pdf")
        os.close(fd)
    except OSError as e:
        raise ErrorFusion(f"No se pudo crear el archivo temporal: {e}")

    try:
        try:
            with _abrir_para_escribir(tmp) as f:
                writer.write(f)
        except Exception as e:
            raise ErrorFusion(f"No se pudo generar el PDF: {e}")

        # Verificación: reabrir el resultado y comprobar el nº total de páginas.
        try:
            paginas_out = _num_paginas(tmp)
        except Exception as e:
            raise ErrorFusion(f"El PDF generado no se pudo verificar: {e}")
        if paginas_out != res.paginas:
            raise ErrorFusion(
                f"Verificación fallida: el PDF resultante tiene {paginas_out} páginas "
                f"pero se esperaban {res.paginas}. Se descartó para no dejar un archivo corrupto."
            )

        log(f"Verificado: {paginas_out} páginas.")

        try:
            _reemplazar_con_reintentos(tmp, salida)
        except PermissionError:
            raise ErrorFusion(
                "No se pudo guardar el archivo. Puede estar abierto en otro programa "
                "o bloqueado por un sincronizador de archivos."
            )
        except OSError as e:
            raise ErrorFusion(f"No se pudo guardar el archivo: {e}")
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    res.ruta_salida = salida
    return res