"""
pdf_compresor.py — Motor de compresión de PDF para CCpdf (sin dependencias de GUI).

    comprimir_pdfs(archivos, destino, nivel="media", motor="auto", ...) -> ResumenCompresion

Dos motores, se elige solo (motor="auto"):
  • Ghostscript (si está instalado): el mejor. Reduce la resolución de las imágenes,
    recomprime y optimiza fuentes. En Linux:  sudo apt install ghostscript
  • Interno (pypdf + Pillow): funciona sin instalar nada. Recomprime las imágenes a
    JPEG con la resolución del nivel y optimiza la estructura del PDF.

Garantías:
  - Jamás sobrescribe el original: genera "<nombre>_comprimido.pdf" en la carpeta de salida.
  - Si el resultado no es más chico que el original, no se guarda (queda "sin mejora").
  - Se verifica que el PDF resultante abra, tenga el mismo número de páginas y no esté vacío.
  - Se reabre el PDF ya guardado (post-rename) para descartar corrupciones silenciosas.
  - Un archivo con error no detiene al resto; se puede cancelar (también a mitad de
    Ghostscript y a mitad de recomprimir imágenes con el motor interno).
  - Fallback automático: si Ghostscript falla en un archivo, se reintenta con el motor interno.
  - Duplicados en la lista de entrada se detectan por realpath y se omiten.
"""
import glob
import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from pypdf import PdfReader, PdfWriter

try:
    from PIL import Image
    HAY_PIL = True
    # Pillow 10+ movió las constantes de remuestreo a Image.Resampling.
    # Pillow 11+ eliminó el alias antiguo. Cubrimos ambos:
    try:
        _RESAMPLE = Image.Resampling.LANCZOS
    except AttributeError:                                   # pragma: no cover
        _RESAMPLE = Image.LANCZOS
except ImportError:                                          # pragma: no cover
    Image = None
    HAY_PIL = False
    _RESAMPLE = None

__all__ = [
    "ErrorCompresion", "ResumenCompresion", "NIVELES",
    "comprimir_pdfs", "info_motor",
    "buscar_ghostscript", "version_ghostscript",
]


class ErrorCompresion(Exception):
    """Error de validación (sin archivos, nivel inválido, Ghostscript no encontrado...)."""


class _Cancelado(Exception):
    pass


# dpi: resolución máxima de imágenes a color/gris · dpi_mono: para blanco y negro (Ghostscript)
# calidad: JPEG 1-95 (motor interno) · gs: preset de Ghostscript
NIVELES = {
    "ligera": {"etiqueta": "Ligera", "dpi": 200, "dpi_mono": 300, "calidad": 85, "gs": "/printer",
               "descripcion": "Casi sin pérdida de calidad. Imágenes a 200 dpi (buena para imprimir)."},
    "media":  {"etiqueta": "Media", "dpi": 150, "dpi_mono": 300, "calidad": 70, "gs": "/ebook",
               "descripcion": "Recomendada. Imágenes a 150 dpi: texto nítido y buen ahorro en escaneos."},
    "fuerte": {"etiqueta": "Fuerte", "dpi": 100, "dpi_mono": 200, "calidad": 50, "gs": "/screen",
               "descripcion": "Menor tamaño. Imágenes a 100 dpi: ideal para enviar por correo o ver en pantalla."},
}

# Timeout por archivo con Ghostscript (segundos). Un PDF de 200 páginas escaneadas
# puede tardar, así que es generoso.
TIMEOUT_GS_POR_ARCHIVO = 300

# Tamaño mínimo de imagen para que valga la pena recomprimirla (bytes)
MIN_BYTES_IMAGEN = 20_000

# Cache de Ghostscript: ruta, versión y flag "ya comprobado"
_GS_CACHE: dict = {"ruta": None, "version": None, "comprobado": False}


@dataclass
class ResumenCompresion:
    total: int = 0
    comprimidos: int = 0
    bytes_antes: int = 0                                 # solo de los archivos que sí se comprimieron
    bytes_despues: int = 0
    resultados: list = field(default_factory=list)       # (nombre, antes, despues, motor)
    sin_mejora: list = field(default_factory=list)       # el resultado no era más chico
    omitidos: list = field(default_factory=list)         # ya comprimidos o duplicados
    errores: list = field(default_factory=list)          # (archivo, mensaje)
    cancelado: bool = False
    carpeta_salida: str = ""
    motor_usado: str = ""
    nivel: str = ""

    @property
    def ahorro_pct(self) -> float:
        return 100.0 * (1 - self.bytes_despues / self.bytes_antes) if self.bytes_antes else 0.0

    def a_texto(self) -> str:
        l = []
        if self.cancelado:
            l.append("⚠ Proceso cancelado por el usuario.")
        l.append(f"Archivos procesados: {self.total}  ·  Comprimidos: {self.comprimidos}")
        l.append(f"Motor: {self.motor_usado}  ·  Nivel: {self.nivel}")
        if self.comprimidos:
            l.append(f"Antes: {_fmt(self.bytes_antes)}  →  Después: {_fmt(self.bytes_despues)}  "
                     f"(ahorro {self.ahorro_pct:.0f}%, {_fmt(self.bytes_antes - self.bytes_despues)})")
            for entrada in self.resultados[:15]:
                nombre, a, d = entrada[0], entrada[1], entrada[2]
                motor = entrada[3] if len(entrada) > 3 else ""
                sufijo = f" [{motor}]" if motor else ""
                l.append(f"  • {nombre}: {_fmt(a)} → {_fmt(d)} (-{100 * (1 - d / a):.0f}%){sufijo}")
            if len(self.resultados) > 15:
                l.append(f"  ... y {len(self.resultados) - 15} más")
        if self.sin_mejora:
            l.append(f"Sin mejora, no se generó archivo ({len(self.sin_mejora)}): "
                     + ", ".join(self.sin_mejora[:10]) + ("..." if len(self.sin_mejora) > 10 else ""))
        if self.omitidos:
            l.append(f"Omitidos ({len(self.omitidos)}): "
                     + ", ".join(self.omitidos[:10]) + ("..." if len(self.omitidos) > 10 else ""))
        for archivo, msg in self.errores:
            l.append(f"ERROR {archivo}: {msg}")
        if self.carpeta_salida:
            l.append(f"Salida: {self.carpeta_salida}")
        return "\n".join(l)


def _fmt(n: float) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024


# ----------------------------------------------------------------------------
# Ghostscript
# ----------------------------------------------------------------------------
def buscar_ghostscript() -> Optional[str]:
    """Ruta del ejecutable de Ghostscript, o None si no está instalado."""
    if _GS_CACHE["comprobado"]:
        return _GS_CACHE["ruta"]

    ruta = None
    for nombre in ("gs", "gswin64c", "gswin32c"):
        r = shutil.which(nombre)
        if r:
            ruta = r
            break
    if ruta is None and sys.platform.startswith("win"):
        for patron in (r"C:\Program Files\gs\gs*\bin\gswin64c.exe",
                       r"C:\Program Files (x86)\gs\gs*\bin\gswin32c.exe"):
            hallados = sorted(glob.glob(patron))
            if hallados:
                ruta = hallados[-1]
                break

    _GS_CACHE["ruta"] = ruta
    _GS_CACHE["comprobado"] = True
    return ruta


def version_ghostscript(ruta: Optional[str] = None) -> Optional[str]:
    """Versión de Ghostscript (cacheada), o None si no se puede."""
    if _GS_CACHE["version"] is not None:
        return _GS_CACHE["version"]
    ruta = ruta or buscar_ghostscript()
    if not ruta:
        return None
    try:
        v = subprocess.run([ruta, "--version"], capture_output=True, text=True,
                           timeout=10).stdout.strip()
        _GS_CACHE["version"] = v or None
    except Exception:
        _GS_CACHE["version"] = None
    return _GS_CACHE["version"]


def info_motor() -> str:
    """Texto corto para mostrar en la interfaz."""
    gs = buscar_ghostscript()
    if gs:
        v = version_ghostscript(gs)
        return f"Motor: Ghostscript {v} ✓" if v else "Motor: Ghostscript ✓"
    extra = " (sudo apt install ghostscript)" if sys.platform.startswith("linux") else ""
    return f"Motor: interno (pypdf+Pillow){extra}" if HAY_PIL else \
           f"Motor: interno (solo pypdf, sin Pillow){extra}"


def _matar_proceso(proc) -> None:
    """Termina un subprocess: primero terminate() (limpio), luego kill() si
    no muere en 1 s. En Windows, terminate() mata el árbol completo con
    /T /F cuando se lanza a través del shell — aquí lo llamamos directamente,
    así que terminate() basta para un proceso simple."""
    try:
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            pass
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait()
    except Exception:
        pass


def _comprimir_gs(gs: str, ruta_in: str, ruta_out: str, cfg: dict, cancelar) -> None:
    cmd = [
        gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.5", f"-dPDFSETTINGS={cfg['gs']}",
        "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER",
        "-dDetectDuplicateImages=true", "-dSubsetFonts=true", "-dCompressFonts=true",
        "-dDownsampleColorImages=true", "-dDownsampleGrayImages=true", "-dDownsampleMonoImages=true",
        "-dColorImageDownsampleType=/Bicubic", "-dGrayImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={cfg['dpi']}", f"-dGrayImageResolution={cfg['dpi']}",
        f"-dMonoImageResolution={cfg['dpi_mono']}",
        f"-sOutputFile={ruta_out}", "-f", ruta_in,
    ]
    flags = 0x08000000 if sys.platform.startswith("win") else 0      # sin ventana de consola
    with tempfile.TemporaryFile() as err:                            # archivo, no PIPE: evita bloqueos
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err, creationflags=flags)
        t0 = time.monotonic()
        while True:
            try:
                proc.wait(timeout=0.3)
                break
            except subprocess.TimeoutExpired:
                if cancelar is not None and cancelar.is_set():
                    _matar_proceso(proc)
                    raise _Cancelado()
                if time.monotonic() - t0 > TIMEOUT_GS_POR_ARCHIVO:
                    _matar_proceso(proc)
                    raise RuntimeError(
                        f"Ghostscript excedió {TIMEOUT_GS_POR_ARCHIVO}s sin terminar; "
                        "se descartó el resultado.")
        if proc.returncode != 0 or not os.path.exists(ruta_out):
            err.seek(0)
            msg = err.read().decode("utf-8", "replace").strip()
            raise RuntimeError(msg[:300] or f"Ghostscript terminó con código {proc.returncode}")


# ----------------------------------------------------------------------------
# Motor interno (pypdf + Pillow)
# ----------------------------------------------------------------------------
def _recomprimir_imagenes(page, cfg: dict, vistos: set, cancelar=None) -> int:
    """Recomprime a JPEG las imágenes de la página. Regresa cuántas cambió.
    Solo toca imágenes RGB/gris sin transparencia, y solo si el resultado pesa menos.
    Respeta `cancelar` entre imágenes."""
    if not HAY_PIL:
        return 0
    ancho = float(page.mediabox.width) / 72.0
    alto = float(page.mediabox.height) / 72.0
    lado_pulg = max(ancho, alto) or 8.5
    cambiadas = 0
    try:
        imagenes = list(page.images)
    except Exception:
        return 0
    for img in imagenes:
        if cancelar is not None and cancelar.is_set():
            raise _Cancelado()
        try:
            ref = img.indirect_reference
            if ref is None or ref.idnum in vistos:       # inline o ya procesada en otra página
                continue
            vistos.add(ref.idnum)
            obj = ref.get_object()
            if obj.get("/ImageMask") or "/SMask" in obj or "/Mask" in obj:
                continue                                  # transparencias: no se tocan
            pil = img.image
            if pil is None or pil.mode not in ("RGB", "L"):
                continue                                  # 1 bit, paleta, CMYK, alfa: se dejan igual
            actual = len(getattr(obj, "_data", b"") or b"")
            if actual < MIN_BYTES_IMAGEN:
                continue                                  # imagen chica: no vale la pena
            w, h = pil.size
            dpi_est = max(w, h) / lado_pulg              # supone que la imagen cubre la página
            nueva = pil
            if dpi_est > cfg["dpi"] * 1.1:
                e = cfg["dpi"] / dpi_est
                nueva = pil.resize((max(1, round(w * e)), max(1, round(h * e))), _RESAMPLE)
            buf = io.BytesIO()
            nueva.save(buf, "JPEG", quality=cfg["calidad"], optimize=True)
            if buf.tell() >= actual * 0.9:
                continue                                  # no ahorra lo suficiente
            img.replace(nueva, quality=cfg["calidad"], optimize=True)
            cambiadas += 1
        except _Cancelado:
            raise
        except Exception:
            continue                                      # una imagen rara no debe tumbar el archivo
    return cambiadas


def _comprimir_interno(ruta_in: str, ruta_out: str, cfg: dict, cancelar) -> int:
    """Escribe a `ruta_out` (que ya tiene la extensión `.tmp` del caller).
    Devuelve el número de imágenes recomprimidas (o 0 si no se hizo ninguna)."""
    reader = PdfReader(ruta_in, strict=False)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("PDF protegido con contraseña")

    # Preservar metadata del original
    metadata = None
    try:
        metadata = reader.metadata
    except Exception:
        pass

    writer = PdfWriter(clone_from=reader)
    if metadata:
        try:
            writer.add_metadata({k: v for k, v in metadata.items() if v})
        except Exception:
            pass

    vistos, imagenes = set(), 0
    for page in writer.pages:
        if cancelar is not None and cancelar.is_set():
            raise _Cancelado()
        imagenes += _recomprimir_imagenes(page, cfg, vistos, cancelar)
        try:
            page.compress_content_streams(level=9)        # sin pérdida
        except Exception:
            pass
    try:
        writer.compress_identical_objects(remove_identicals=True, remove_orphans=True)
    except Exception:
        pass

    # Escritura directa al .tmp (el caller ya envuelve con try/finally)
    with open(ruta_out, "wb") as f:
        writer.write(f)
    return imagenes


# ----------------------------------------------------------------------------
# Función principal
# ----------------------------------------------------------------------------
def _ruta_unica(ruta: str, usadas: set) -> str:
    """Evita pisar otra salida de la misma corrida o uno de los archivos de entrada."""
    base, ext = os.path.splitext(ruta)
    candidata, n = ruta, 2
    while os.path.normcase(os.path.abspath(candidata)) in usadas:
        candidata = f"{base} ({n}){ext}"
        n += 1
    usadas.add(os.path.normcase(os.path.abspath(candidata)))
    return candidata


def _num_paginas(ruta: str) -> int:
    r = PdfReader(ruta, strict=False)
    if r.is_encrypted and not r.decrypt(""):
        raise ValueError("PDF protegido con contraseña")
    return len(r.pages)


def comprimir_pdfs(
    archivos: list,
    destino: str = "",
    nivel: str = "media",
    motor: str = "auto",
    sufijo: str = "_comprimido",
    omitir_ya_comprimidos: bool = True,
    fallback_si_gs_falla: bool = True,
    verificar_post_guardado: bool = True,
    callback_progreso: Optional[Callable[[int, int, str], None]] = None,
    callback_log: Optional[Callable[[str], None]] = None,
    cancelar: Optional[threading.Event] = None,
) -> ResumenCompresion:
    """motor: "auto" | "ghostscript" | "interno".

    omitir_ya_comprimidos:  salta archivos cuyo nombre termina en `sufijo`.
    fallback_si_gs_falla:   si GS falla en un archivo, reintenta con el motor interno.
    verificar_post_guardado: reabre el PDF ya guardado para descartar corrupciones silenciosas.
    """
    log = callback_log or (lambda m: None)
    archivos = [a for a in archivos if a]
    if not archivos:
        raise ErrorCompresion("Agrega al menos un PDF para comprimir.")
    if nivel not in NIVELES:
        raise ErrorCompresion(f"Nivel desconocido: {nivel}")
    if motor not in ("auto", "ghostscript", "interno"):
        raise ErrorCompresion(f"Motor desconocido: {motor}")
    cfg = NIVELES[nivel]

    gs = buscar_ghostscript() if motor in ("auto", "ghostscript") else None
    if motor == "ghostscript" and not gs:
        raise ErrorCompresion("No se encontró Ghostscript. Instálalo (sudo apt install ghostscript) "
                              "o usa el motor automático.")
    if gs is None and not HAY_PIL:
        log("[AVISO] Sin Pillow ni Ghostscript: solo se optimizará la estructura, sin tocar imágenes.")

    destino = (destino or "").strip() or os.path.join(
        os.path.dirname(os.path.abspath(archivos[0])), "PDFs_Comprimidos")
    try:
        os.makedirs(destino, exist_ok=True)
    except OSError as e:
        raise ErrorCompresion(f"No se pudo crear la carpeta de salida: {e}")

    if gs:
        nombre_motor = f"Ghostscript {version_ghostscript(gs) or ''}".strip()
    elif HAY_PIL:
        nombre_motor = "interno (pypdf+Pillow)"
    else:
        nombre_motor = "interno (solo pypdf)"

    res = ResumenCompresion(total=len(archivos), carpeta_salida=destino,
                            nivel=cfg["etiqueta"], motor_usado=nombre_motor)
    usadas = {os.path.normcase(os.path.abspath(a)) for a in archivos}   # nunca pisar un original
    ya_vistos = set()                                                    # evitar duplicados entre entradas
    log(f"{nombre_motor} · nivel {cfg['etiqueta']} · {len(archivos)} archivo(s)")

    for i, ruta in enumerate(archivos, start=1):
        if cancelar is not None and cancelar.is_set():
            res.cancelado = True
            break
        nombre = os.path.basename(ruta)
        tmp = None
        try:
            if not os.path.isfile(ruta):
                raise FileNotFoundError("el archivo ya no existe")

            real = os.path.realpath(ruta)
            if real in ya_vistos:
                res.omitidos.append(nombre)
                log(f"[DUPLICADO] {nombre}: ya estaba en la lista")
                continue
            ya_vistos.add(real)

            stem = os.path.splitext(nombre)[0]
            if omitir_ya_comprimidos and stem.endswith(sufijo):
                res.omitidos.append(nombre)
                log(f"[YA COMPRIMIDO] {nombre}")
                continue

            antes = os.path.getsize(ruta)
            salida = _ruta_unica(os.path.join(destino, f"{stem}{sufijo}.pdf"), usadas)
            tmp = salida + ".tmp"

            motor_archivo = ""
            imagenes = None

            if gs:
                try:
                    _comprimir_gs(gs, ruta, tmp, cfg, cancelar)
                    motor_archivo = "gs"
                except _Cancelado:
                    raise
                except Exception as e_gs:
                    if fallback_si_gs_falla and HAY_PIL:
                        log(f"[FALLBACK] {nombre}: GS falló ({e_gs}); reintentando interno…")
                        if tmp and os.path.exists(tmp):
                            try: os.remove(tmp)
                            except OSError: pass
                        imagenes = _comprimir_interno(ruta, tmp, cfg, cancelar)
                        motor_archivo = "interno-fallback"
                    else:
                        raise
            else:
                imagenes = _comprimir_interno(ruta, tmp, cfg, cancelar)
                motor_archivo = "interno"

            # Verificación pre-rename: páginas y tamaño
            try:
                paginas_orig = _num_paginas(ruta)
            except Exception:
                paginas_orig = None
            paginas_out = _num_paginas(tmp)
            if paginas_orig is not None and paginas_out != paginas_orig:
                raise RuntimeError("el PDF resultante no conserva todas las páginas; se descartó")

            despues = os.path.getsize(tmp)
            if despues == 0:
                raise RuntimeError("el PDF resultante quedó vacío; se descartó")

            if despues < antes:
                os.replace(tmp, salida)

                # Verificación post-rename: reabrir el archivo final
                if verificar_post_guardado:
                    try:
                        _num_paginas(salida)   # si no abre, lanza
                    except Exception as e:
                        try: os.remove(salida)
                        except OSError: pass
                        raise RuntimeError(
                            f"el PDF guardado no se pudo reabrir ({e}); se descartó")

                res.comprimidos += 1
                res.bytes_antes += antes
                res.bytes_despues += despues
                res.resultados.append((nombre, antes, despues, motor_archivo))
                extra = f", {imagenes} imagen(es) recomprimida(s)" if imagenes else ""
                log(f"[OK] {nombre}: {_fmt(antes)} → {_fmt(despues)} "
                    f"(-{100 * (1 - despues / antes):.0f}%) [{motor_archivo}]{extra}")
            else:
                res.sin_mejora.append(nombre)
                log(f"[SIN MEJORA] {nombre}: ya estaba optimizado ({_fmt(antes)})")

            if HAY_PIL and motor_archivo.startswith("interno") and imagenes == 0:
                log(f"[AVISO] {nombre}: no se encontraron imágenes recomprimibles "
                    "(posiblemente vectorial o todas muy chicas).")

        except _Cancelado:
            res.cancelado = True
            break
        except Exception as e:
            res.errores.append((nombre, str(e)))
            log(f"[ERROR] {nombre}: {e}")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if callback_progreso:
                try:
                    callback_progreso(i, res.total, nombre)
                except Exception:
                    pass
    return res