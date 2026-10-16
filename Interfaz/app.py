"""
app.py — Interfaz gráfica de CCpdf (CustomTkinter).

Diseño: enterprise moderno (sidebar oscura + contenido claro).
Arquitectura de hilos:
  El hilo del motor SOLO deja mensajes en self.cola.
  _procesar_cola los consume en el hilo principal.
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections import namedtuple
from dataclasses import dataclass

from tkinter import filedialog, messagebox
import customtkinter as ctk


# ═══════════════════════════════════════════════════════════════
#  RUTAS DEL PROYECTO
# ═══════════════════════════════════════════════════════════════
DIRECTORIO_ACTUAL = os.path.dirname(os.path.abspath(__file__))
DIRECTORIO_PADRE  = os.path.dirname(DIRECTORIO_ACTUAL)

DIRECTORIO_MOTOR      = os.path.join(DIRECTORIO_PADRE, "Motores", "Extraccion")
DIRECTORIO_FUSION     = os.path.join(DIRECTORIO_PADRE, "Motores", "Fusion")
DIRECTORIO_DIVISION   = os.path.join(DIRECTORIO_PADRE, "Motores", "Division")
DIRECTORIO_COMPRESION = os.path.join(DIRECTORIO_PADRE, "Motores", "Compresion")
DIRECTORIO_VISOR      = os.path.join(DIRECTORIO_PADRE, "Motores", "Visualizacion")


def _preparar_path():
    for ruta in (DIRECTORIO_ACTUAL, DIRECTORIO_PADRE,
                 DIRECTORIO_MOTOR, DIRECTORIO_FUSION,
                 DIRECTORIO_DIVISION, DIRECTORIO_COMPRESION,
                 DIRECTORIO_VISOR):
        if ruta and os.path.isdir(ruta) and ruta not in sys.path:
            sys.path.insert(0, ruta)


_preparar_path()


def _ruta_recurso(relativa: str) -> str:
    """Resuelve rutas de recursos en dev y dentro del .exe empaquetado."""
    base = getattr(sys, "_MEIPASS", DIRECTORIO_PADRE)
    return os.path.join(base, relativa)


# ═══════════════════════════════════════════════════════════════
#  IMPORTS DE MOTORES (con fallbacks)
# ═══════════════════════════════════════════════════════════════
import config as cfg_prefs
from dialogo_confirmar import mostrar_dialogo_confirmacion
from visor_pdf import VisorPDF


try:
    from Motores.CIPA import AsistenteCIPA
    _HAY_ASISTENTE = True
except Exception:
    # No solo ImportError: Motores/CIPA/config.py crea carpetas de caché al
    # importarse (RUTA_CACHE.mkdir()), y un problema de permisos lanza
    # OSError, no ImportError. Sin este except amplio, eso impedía abrir
    # la app entera.
    AsistenteCIPA = None
    _HAY_ASISTENTE = False


def _try_import(nombre_humano, opciones):
    """Prueba varias sentencias de import y devuelve los símbolos."""
    errores = []
    for sentencia, claves in opciones:
        try:
            ns = {}
            exec(sentencia, ns)
            return [ns[k] for k in claves]
        except ImportError as e:
            errores.append(f"  • {sentencia.split(' import ')[0]}  →  {e}")
    raise ImportError(f"No se pudo importar {nombre_humano}:\n" + "\n".join(errores))


(_cortar_hasta_palabra, _clasificar_por_lista, _limpiar_claves,
 _leer_claves_txt, ErrorMotor) = _try_import("motor de extracción", [
    ("from pdf_motor import cortar_hasta_palabra, clasificar_por_lista, "
     "limpiar_claves, leer_claves_txt, ErrorMotor",
     ["cortar_hasta_palabra", "clasificar_por_lista", "limpiar_claves",
      "leer_claves_txt", "ErrorMotor"]),
    ("from Motores.Extraccion.pdf_motor import cortar_hasta_palabra, "
     "clasificar_por_lista, limpiar_claves, leer_claves_txt, ErrorMotor",
     ["cortar_hasta_palabra", "clasificar_por_lista", "limpiar_claves",
      "leer_claves_txt", "ErrorMotor"]),
])

(_fusionar_pdfs, _listar_pdfs_carpeta, _ordenar_natural,
 ErrorFusion, ResumenFusion) = _try_import("motor de fusión", [
    ("from pdf_fusionador import fusionar_pdfs, listar_pdfs_carpeta, "
     "ordenar_natural, ErrorFusion, ResumenFusion",
     ["fusionar_pdfs", "listar_pdfs_carpeta", "ordenar_natural",
      "ErrorFusion", "ResumenFusion"]),
    ("from Motores.Fusion.pdf_fusionador import fusionar_pdfs, "
     "listar_pdfs_carpeta, ordenar_natural, ErrorFusion, ResumenFusion",
     ["fusionar_pdfs", "listar_pdfs_carpeta", "ordenar_natural",
      "ErrorFusion", "ResumenFusion"]),
])

(_dividir_por_unidad, _leer_unidades_txt,
 ErrorDivisor, ResumenDivision) = _try_import("motor de división", [
    ("from pdf_divisor import dividir_por_unidad, leer_unidades_txt, "
     "ErrorDivisor, ResumenDivision",
     ["dividir_por_unidad", "leer_unidades_txt", "ErrorDivisor", "ResumenDivision"]),
    ("from Motores.Division.pdf_divisor import dividir_por_unidad, "
     "leer_unidades_txt, ErrorDivisor, ResumenDivision",
     ["dividir_por_unidad", "leer_unidades_txt", "ErrorDivisor", "ResumenDivision"]),
])

(_comprimir_pdfs, ErrorCompresion, ResumenCompresion,
 _info_motor, NIVELES_COMPRESION) = _try_import("motor de compresión", [
    ("from pdf_compresor import comprimir_pdfs, ErrorCompresion, "
     "ResumenCompresion, info_motor, NIVELES",
     ["comprimir_pdfs", "ErrorCompresion", "ResumenCompresion", "info_motor", "NIVELES"]),
    ("from Motores.Compresion.pdf_compresor import comprimir_pdfs, "
     "ErrorCompresion, ResumenCompresion, info_motor, NIVELES",
     ["comprimir_pdfs", "ErrorCompresion", "ResumenCompresion", "info_motor", "NIVELES"]),
])

(_DocumentoPDF, _abrir_pdf, ErrorVisor, _InfoDocumento,
 _ZOOM_MIN, _ZOOM_MAX, _ZOOM_DEFAULT) = _try_import("motor de visor", [
    ("from visor_pdf_motor import DocumentoPDF, abrir_pdf, ErrorVisor, "
     "InfoDocumento, ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT",
     ["DocumentoPDF", "abrir_pdf", "ErrorVisor", "InfoDocumento",
      "ZOOM_MIN", "ZOOM_MAX", "ZOOM_DEFAULT"]),
    ("from Motores.Visualizacion.visor_pdf_motor import DocumentoPDF, "
     "abrir_pdf, ErrorVisor, InfoDocumento, ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT",
     ["DocumentoPDF", "abrir_pdf", "ErrorVisor", "InfoDocumento",
      "ZOOM_MIN", "ZOOM_MAX", "ZOOM_DEFAULT"]),
])

(_aplicar_ocr, _paginas_sin_texto, ErrorOCR,
 ResumenOCR, _info_motor_ocr, IDIOMAS_OCR) = _try_import("motor de OCR", [
    ("from Motores.OCR import aplicar_ocr, paginas_sin_texto, ErrorOCR, "
     "ResumenOCR, info_motor_ocr, IDIOMAS_DISPONIBLES",
     ["aplicar_ocr", "paginas_sin_texto", "ErrorOCR",
      "ResumenOCR", "info_motor_ocr", "IDIOMAS_DISPONIBLES"]),
    ("from Motores.OCR.pdf_ocr import aplicar_ocr, paginas_sin_texto, "
     "ErrorOCR, ResumenOCR, info_motor_ocr, IDIOMAS_DISPONIBLES",
     ["aplicar_ocr", "paginas_sin_texto", "ErrorOCR",
      "ResumenOCR", "info_motor_ocr", "IDIOMAS_DISPONIBLES"]),
])

# ═══════════════════════════════════════════════════════════════
#  PALETA — Enterprise
# ═══════════════════════════════════════════════════════════════
class P:
    # Sidebar oscura
    SB_BG          = "#0F172A"
    SB_HOVER       = "#1E293B"
    SB_ACTIVE      = "#1E293B"
    SB_TEXT        = "#94A3B8"
    SB_TEXT_ACTIVE = "#F8FAFC"
    SB_SECTION     = "#64748B"

    # Superficie clara
    BG             = "#F8FAFC"
    CARD           = "#FFFFFF"
    CARD_ALT       = "#F1F5F9"
    BORDER         = "#E2E8F0"
    BORDER_STRONG  = "#CBD5E1"

    # Tipografía
    TXT            = "#0F172A"
    TXT_2          = "#475569"
    TXT_3          = "#94A3B8"
    TXT_INV        = "#FFFFFF"

    # Acento
    ACCENT         = "#4F46E5"
    ACCENT_HOVER   = "#4338CA"

    # Semánticos
    SUCCESS        = "#10B981"
    SUCCESS_HOVER  = "#059669"
    WARNING        = "#F59E0B"
    DANGER         = "#EF4444"
    DANGER_HOVER   = "#DC2626"
    INFO           = "#3B82F6"

    # Radios
    R_CARD = 12
    R_INPUT = 8
    R_BTN = 8


def F(size=12, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def FM(size=11, weight="normal"):
    return ctk.CTkFont(family="Consolas", size=size, weight=weight)


def _oscurecer(hex_color: str, factor: float) -> str:
    try:
        r = int(hex_color[1:3], 16); g = int(hex_color[3:5], 16); b = int(hex_color[5:7], 16)
        r = max(0, int(r * (1 - factor))); g = max(0, int(g * (1 - factor))); b = max(0, int(b * (1 - factor)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return hex_color


# ═══════════════════════════════════════════════════════════════
#  CONFIGURACIÓN GLOBAL
# ═══════════════════════════════════════════════════════════════
ctk.set_appearance_mode("light")

APP_NOMBRE  = "CCpdf IA"
APP_VERSION = "v0.1.0-alpha"

_PREFS = cfg_prefs.cargar()
_IDIOMA_ACTIVO = _PREFS.get("idioma", "es")


def T(clave: str, defecto: str = "") -> str:
    try:
        return cfg_prefs.idioma(_IDIOMA_ACTIVO).get(clave, defecto or clave)
    except Exception:
        return defecto or clave


APP_SUBTITULO = T("app_subtitulo", "Suite local de PDF")

MAX_LINEAS_LOG = 2000
MAX_MENSAJES_POR_TICK = 200
INTERVALO_COLA_MS = 80
NOMBRE_FUSIONADO_DEFECTO = "fusionado.pdf"


# ═══════════════════════════════════════════════════════════════
#  MODELO DE MÓDULOS
# ═══════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Modulo:
    clave: str
    emoji: str
    titulo: str
    descripcion: str
    color: str = P.ACCENT
    seccion: str = "Principal"


MODULOS = (
    Modulo("inicio",     "◆", "Dashboard",  "Vista general de la suite",
           P.ACCENT, "Principal"),
    Modulo("extractor",  "◈", "Extracción", "Corta y clasifica lotes por palabra",
           "#06B6D4", "Procesamiento"),
    Modulo("fusionador", "◇", "Fusionar",   "Une varios PDFs en uno solo",
           "#10B981", "Procesamiento"),
    Modulo("divisor",    "◐", "Dividir",    "Separa un PDF por unidad",
           "#F59E0B", "Procesamiento"),
    Modulo("compresor",  "◆", "Comprimir",  "Reduce el tamaño de tus PDFs",
           "#3B82F6", "Procesamiento"),
    Modulo("ocr",        "⬢", "OCR",        "Añade capa de texto a PDFs escaneados",
           "#8B5CF6", "Procesamiento"),
    Modulo("visor",      "◉", "Visor",      "Explora cualquier PDF",
           "#EC4899", "Herramientas"),
    Modulo("firmar",     "✎", "Firmar",     "Aplica firma o sello",
           "#9C27B0", "Herramientas"),
    Modulo("asistente",  "✦", "Asistente",  "Chat de ayuda sobre CCpdf IA",
           "#F59E0B", "Inteligencia"),
)

MODULO_POR_CLAVE = {m.clave: m for m in MODULOS}


# ═══════════════════════════════════════════════════════════════
#  MENSAJES DE LA COLA
# ═══════════════════════════════════════════════════════════════
MsgProgreso = namedtuple("MsgProgreso", "actual total archivo")
MsgLog      = namedtuple("MsgLog", "texto")
MsgFin      = namedtuple("MsgFin", "resumen error")
MsgPregunta = namedtuple("MsgPregunta", "valor unidad paginas indice total respuesta listo")


# ═══════════════════════════════════════════════════════════════
#  UTILIDADES
# ═══════════════════════════════════════════════════════════════
def abrir_carpeta(ruta: str) -> None:
    if not ruta or not os.path.isdir(ruta):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", ruta],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", ruta],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        messagebox.showwarning("No se pudo abrir la carpeta", str(e))


def _attr(obj, nombre, default=""):
    return getattr(obj, nombre, default)


# ═══════════════════════════════════════════════════════════════
#  COMPONENTES REUTILIZABLES
# ═══════════════════════════════════════════════════════════════
class Card(ctk.CTkFrame):
    def __init__(self, master, **kw):
        super().__init__(master, fg_color=P.CARD, border_width=1,
                         border_color=P.BORDER, corner_radius=P.R_CARD, **kw)


class Btn(ctk.CTkButton):
    def __init__(self, master, texto, comando=None, icono="", tipo="primary",
                 ancho=120, height=36, **kw):
        colores = {
            "primary":   (P.ACCENT,   P.ACCENT_HOVER,  P.TXT_INV),
            "secondary": (P.CARD,     P.CARD_ALT,      P.TXT),
            "ghost":     (P.BG,       P.CARD_ALT,      P.TXT_2),
            "success":   (P.SUCCESS,  P.SUCCESS_HOVER, P.TXT_INV),
            "danger":    (P.DANGER,   P.DANGER_HOVER,  P.TXT_INV),
        }
        fg, hov, txt = colores.get(tipo, colores["primary"])
        etiqueta = f"{icono}  {texto}".strip() if icono else texto
        super().__init__(master, text=etiqueta, command=comando,
                         width=ancho, height=height, corner_radius=P.R_BTN,
                         fg_color=fg, hover_color=hov, text_color=txt,
                         border_width=1 if tipo == "secondary" else 0,
                         border_color=P.BORDER_STRONG if tipo == "secondary" else fg,
                         font=F(11, "bold"), **kw)


class SectionLabel(ctk.CTkLabel):
    def __init__(self, master, texto):
        super().__init__(master, text=texto.upper(), font=F(9, "bold"),
                         text_color=P.SB_SECTION, anchor="w")


class NavItem(ctk.CTkFrame):
    def __init__(self, master, icono, texto, comando=None):
        super().__init__(master, fg_color="transparent", height=38)
        self.pack_propagate(False)
        self._comando = comando
        self._activo = False
        self._habilitado = True

        self._barra = ctk.CTkFrame(self, width=3, corner_radius=2, fg_color=P.ACCENT)
        self._barra.pack(side="left", fill="y", pady=6, padx=(6, 0))
        self._barra.pack_forget()

        self._icono = ctk.CTkLabel(self, text=icono, font=F(14),
                                   text_color=P.SB_TEXT, width=20)
        self._icono.pack(side="left", padx=(12, 10))

        self._texto = ctk.CTkLabel(self, text=texto, font=F(12),
                                   text_color=P.SB_TEXT, anchor="w")
        self._texto.pack(side="left", fill="x", expand=True)

        for w in (self, self._icono, self._texto):
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            w.bind("<Button-1>", self._on_click, add="+")

    def _on_enter(self, _=None):
        if not self._activo and self._habilitado:
            self.configure(fg_color=P.SB_HOVER)
            self._icono.configure(fg_color=P.SB_HOVER)
            self._texto.configure(fg_color=P.SB_HOVER)

    def _on_leave(self, _=None):
        if not self._activo:
            self.configure(fg_color="transparent")
            self._icono.configure(fg_color="transparent")
            self._texto.configure(fg_color="transparent")

    def _on_click(self, _=None):
        if self._comando and self._habilitado:
            self._comando()

    def set_activo(self, activo: bool):
        self._activo = activo
        if activo:
            self.configure(fg_color=P.SB_ACTIVE)
            self._icono.configure(text_color=P.SB_TEXT_ACTIVE, fg_color=P.SB_ACTIVE)
            self._texto.configure(text_color=P.SB_TEXT_ACTIVE, font=F(12, "bold"),
                                  fg_color=P.SB_ACTIVE)
            self._barra.pack(side="left", fill="y", pady=6, padx=(6, 0), before=self._icono)
        else:
            self.configure(fg_color="transparent")
            self._icono.configure(text_color=P.SB_TEXT, fg_color="transparent")
            self._texto.configure(text_color=P.SB_TEXT, font=F(12), fg_color="transparent")
            self._barra.pack_forget()

    def set_habilitado(self, hab: bool):
        self._habilitado = hab
        col = P.SB_TEXT if hab else "#475569"
        self._icono.configure(text_color=col)
        self._texto.configure(text_color=col)


class StatCard(ctk.CTkFrame):
    def __init__(self, master, etiqueta, valor, delta, color, icono="●"):
        super().__init__(master, fg_color=P.CARD, border_width=1,
                         border_color=P.BORDER, corner_radius=P.R_CARD)
        self.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 0))
        ctk.CTkLabel(top, text=icono, font=F(16), text_color=color).pack(side="left")
        ctk.CTkLabel(top, text=etiqueta, font=F(11, "bold"),
                     text_color=P.TXT_3).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self, text=valor, font=F(24, "bold"),
                     text_color=P.TXT, anchor="w"
                     ).grid(row=1, column=0, sticky="ew", padx=18, pady=(6, 0))
        ctk.CTkLabel(self, text=delta, font=F(11),
                     text_color=color, anchor="w"
                     ).grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))


class SectionTitle(ctk.CTkFrame):
    def __init__(self, master, titulo, descripcion=""):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=titulo, font=F(14, "bold"),
                     text_color=P.TXT, anchor="w").pack(anchor="w")
        if descripcion:
            ctk.CTkLabel(self, text=descripcion, font=F(11),
                         text_color=P.TXT_2, anchor="w").pack(anchor="w", pady=(2, 0))


class InputField(ctk.CTkFrame):
    """Campo con etiqueta + entry + botón opcional."""
    def __init__(self, master, etiqueta, placeholder="", boton=None, **kw):
        super().__init__(master, fg_color="transparent", **kw)
        ctk.CTkLabel(self, text=etiqueta, font=F(11, "bold"),
                     text_color=P.TXT, anchor="w").pack(anchor="w", pady=(0, 4))
        fila = ctk.CTkFrame(self, fg_color="transparent")
        fila.pack(fill="x")
        self.entry = ctk.CTkEntry(
            fila, placeholder_text=placeholder, height=38,
            corner_radius=P.R_INPUT, border_width=1, border_color=P.BORDER_STRONG,
            fg_color=P.CARD, text_color=P.TXT, placeholder_text_color=P.TXT_3,
            font=F(12))
        self.entry.pack(side="left", fill="x", expand=True)
        if boton:
            Btn(fila, boton[0], comando=boton[1], icono=boton[2],
                tipo="secondary", ancho=100, height=38
                ).pack(side="left", padx=(8, 0))

    def get(self): return self.entry.get().strip()
    def set(self, v):
        self.entry.delete(0, "end")
        self.entry.insert(0, v)


# ═══════════════════════════════════════════════════════════════
#  PANEL DE PROGRESO
# ═══════════════════════════════════════════════════════════════
class PanelProgreso(ctk.CTkFrame):
    def __init__(self, master, altura_log: int = 180, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)

        card = Card(self)
        card.pack(fill="both", expand=True)

        interior = ctk.CTkFrame(card, fg_color="transparent")
        interior.pack(fill="both", expand=True, padx=18, pady=18)

        SectionTitle(interior, "Estado del proceso", "Registro de la operación"
                     ).pack(anchor="w", pady=(0, 10))

        fila = ctk.CTkFrame(interior, fg_color="transparent")
        fila.pack(fill="x", pady=(0, 6))
        self.lbl_progreso = ctk.CTkLabel(fila, text=T("listo", "Listo."),
                                         text_color=P.TXT_2, font=F(11))
        self.lbl_progreso.pack(side="left")
        self.btn_stop = ctk.CTkButton(
            fila, text=T("detener", "Detener"), width=90, height=28,
            corner_radius=P.R_BTN, state="disabled",
            fg_color=P.DANGER, hover_color=P.DANGER_HOVER,
            text_color=P.TXT_INV, font=F(11, "bold"))
        self.btn_stop.pack(side="right")

        self.barra = ctk.CTkProgressBar(interior, fg_color=P.CARD_ALT,
                                        progress_color=P.ACCENT, height=6,
                                        corner_radius=3)
        self.barra.pack(fill="x", pady=(4, 12))
        self.barra.set(0)

        self.txt_log = ctk.CTkTextbox(
            interior, height=altura_log, fg_color=P.CARD_ALT, text_color=P.TXT_2,
            border_width=1, border_color=P.BORDER, corner_radius=8, font=FM(10))
        self.txt_log.pack(fill="both", expand=True)

    def set_progreso(self, actual, total, archivo):
        self.barra.set(actual / total if total else 0)
        self.lbl_progreso.configure(text=f"Procesado {actual} de {total}: {archivo}")

    def escribir_log(self, mensaje):
        self.txt_log.insert("end", mensaje + "\n")
        try:
            total = int(self.txt_log.index("end-1c").split(".")[0])
        except Exception:
            total = 0
        if total > MAX_LINEAS_LOG:
            self.txt_log.delete("1.0", f"{total - MAX_LINEAS_LOG}.0")
        self.txt_log.see("end")

    def limpiar_log(self):
        self.txt_log.delete("1.0", "end")

    def reset(self):
        self.barra.set(0)
        self.lbl_progreso.configure(text=T("listo", "Listo."))


# ═══════════════════════════════════════════════════════════════
#  APLICACIÓN PRINCIPAL
# ═══════════════════════════════════════════════════════════════
class CCPdfApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NOMBRE} — {APP_SUBTITULO}")
        self.geometry("1280x820")
        self.minsize(1100, 700)
        self.configure(fg_color=P.BG)
        self._aplicar_icono_ventana()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._nav_items: dict[str, NavItem] = {}
        self._vistas: dict[str, ctk.CTkFrame] = {}
        self._clave_actual = "inicio"

        self.evento_cancelar = threading.Event()
        self.cola: queue.Queue = queue.Queue()
        self._procesando = False
        self._after_id = None
        self._panel_activo: PanelProgreso | None = None

        self._lista_fusion: list[str] = []
        self._lista_compresion: list[str] = []

        self._lista_ocr: list[str] = []

        # Asistente CIPA (se instancia bajo demanda, no al arrancar)
        self._asistente = None
        self._chat_ocupado = False

        self._crear_sidebar()
        self._crear_topbar()
        self._crear_area_contenido()
        self.mostrar_vista("inicio")

        self.protocol("WM_DELETE_WINDOW", self._al_cerrar)
        self._procesar_cola()

    # ─── Icono ───
    def _aplicar_icono_ventana(self):
        try:
            png = _ruta_recurso("Interfaz/assets/logo.png")
            ico = _ruta_recurso("Interfaz/assets/logo.ico")
            if sys.platform.startswith("win") and os.path.isfile(ico):
                self.iconbitmap(ico)
            elif os.path.isfile(png):
                from PIL import Image, ImageTk
                img = Image.open(png).convert("RGBA")
                fotos = [ImageTk.PhotoImage(img.resize((n, n), Image.LANCZOS))
                         for n in (16, 32, 48, 64, 128, 256)]
                self.iconphoto(True, *fotos)
                self._iconos_ref = fotos
        except Exception as e:
            print(f"[AVISO] Icono no aplicado: {e}")

    # ─── Sidebar ───
    def _crear_sidebar(self):
        sb = ctk.CTkFrame(self, width=240, corner_radius=0, fg_color=P.SB_BG)
        sb.grid(row=0, column=0, rowspan=2, sticky="nsew")
        sb.grid_propagate(False)
        self.sidebar = sb

        cab = ctk.CTkFrame(sb, fg_color="transparent", height=64)
        cab.pack(fill="x", padx=16, pady=(18, 8))
        cab.pack_propagate(False)

        logo = ctk.CTkFrame(cab, width=36, height=36, corner_radius=10, fg_color=P.ACCENT)
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="CC", font=F(14, "bold"),
                     text_color=P.TXT_INV).pack(expand=True)

        textos = ctk.CTkFrame(cab, fg_color="transparent")
        textos.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(textos, text=APP_NOMBRE, font=F(15, "bold"),
                     text_color=P.TXT_INV, anchor="w").pack(anchor="w")
        ctk.CTkLabel(textos, text=APP_VERSION, font=F(9),
                     text_color=P.SB_TEXT, anchor="w").pack(anchor="w")

        nav = ctk.CTkFrame(sb, fg_color="transparent")
        nav.pack(fill="x", padx=8, pady=(8, 0))

        seccion_actual = None
        for mod in MODULOS:
            if mod.seccion != seccion_actual:
                seccion_actual = mod.seccion
                SectionLabel(nav, seccion_actual).pack(
                    anchor="w", padx=14,
                    pady=(14 if mod.clave != "inicio" else 0, 6))
            item = NavItem(nav, mod.emoji, mod.titulo,
                           comando=lambda c=mod.clave: self.mostrar_vista(c))
            item.pack(fill="x", padx=2, pady=1)
            self._nav_items[mod.clave] = item

        pie = ctk.CTkFrame(sb, fg_color="transparent")
        pie.pack(side="bottom", fill="x", padx=8, pady=12)

        it_cfg = NavItem(pie, "⚙", T("configuracion", "Configuración"),
                         comando=self._abrir_config)
        it_cfg.pack(fill="x", padx=2, pady=1)

        user = ctk.CTkFrame(pie, fg_color=P.SB_HOVER, corner_radius=10)
        user.pack(fill="x", padx=4, pady=(10, 0))
        avatar = ctk.CTkFrame(user, width=32, height=32, corner_radius=16,
                              fg_color=P.ACCENT)
        avatar.pack(side="left", padx=10, pady=10)
        avatar.pack_propagate(False)
        ctk.CTkLabel(avatar, text="IK", font=F(11, "bold"),
                     text_color=P.TXT_INV).pack(expand=True)
        col = ctk.CTkFrame(user, fg_color="transparent")
        col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(col, text="Iker", font=F(11, "bold"),
                     text_color=P.TXT_INV, anchor="w").pack(anchor="w")
        ctk.CTkLabel(col, text="Kodesoft", font=F(9),
                     text_color=P.SB_TEXT, anchor="w").pack(anchor="w")

    # ─── Topbar ───
    def _crear_topbar(self):
        top = ctk.CTkFrame(self, height=64, corner_radius=0, fg_color=P.CARD)
        top.grid(row=0, column=1, sticky="ew")
        top.grid_propagate(False)
        self.topbar = top

        migas = ctk.CTkFrame(top, fg_color="transparent")
        migas.pack(side="left", padx=24, pady=18)
        self._bc_modulo = ctk.CTkLabel(migas, text="", font=F(13, "bold"),
                                       text_color=P.TXT)
        self._bc_modulo.pack(side="left")
        ctk.CTkLabel(migas, text="  /  ", font=F(13),
                     text_color=P.TXT_3).pack(side="left")
        ctk.CTkLabel(migas, text=APP_NOMBRE, font=F(13),
                     text_color=P.TXT_3).pack(side="left")

        ctk.CTkFrame(self, height=1, fg_color=P.BORDER
                     ).grid(row=0, column=1, sticky="ews")

    # ─── Área de contenido ───
    def _crear_area_contenido(self):
        wrap = ctk.CTkFrame(self, corner_radius=0, fg_color=P.BG)
        wrap.grid(row=1, column=1, sticky="nsew")
        wrap.grid_columnconfigure(0, weight=1)
        wrap.grid_rowconfigure(0, weight=1)
        self.content = wrap

        self._vistas["inicio"]     = self._vista_inicio()
        self._vistas["extractor"]  = self._vista_extractor()
        self._vistas["fusionador"] = self._vista_fusionador()
        self._vistas["divisor"]    = self._vista_divisor()
        self._vistas["compresor"]  = self._vista_compresor()
        self._vistas["ocr"]        = self._vista_ocr()
        self._vistas["visor"]      = self._vista_visor()
        self._vistas["firmar"]     = self._vista_placeholder(MODULO_POR_CLAVE["firmar"])
        self._vistas["asistente"]  = self._vista_asistente()

    # ═══════════════════════════════════════════════════════════
    #  VISTA: DASHBOARD
    # ═══════════════════════════════════════════════════════════
    def _vista_inicio(self):
        scroll = ctk.CTkScrollableFrame(self.content, fg_color="transparent",
                                        corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        interior = ctk.CTkFrame(scroll, fg_color="transparent")
        interior.pack(fill="both", expand=True, padx=36, pady=(28, 28))

        ctk.CTkLabel(interior, text="Bienvenido a CCpdf",
                     font=F(24, "bold"), text_color=P.TXT, anchor="w"
                     ).pack(anchor="w")
        ctk.CTkLabel(interior,
                     text="Suite local de gestión de PDF · Todo se procesa en tu equipo",
                     font=F(12), text_color=P.TXT_2, anchor="w"
                     ).pack(anchor="w", pady=(4, 22))

        # KPIs
        stats = ctk.CTkFrame(interior, fg_color="transparent")
        stats.pack(fill="x")
        for i in range(4):
            stats.grid_columnconfigure(i, weight=1, uniform="stat")

        kpis = [
            ("Modo local",  "100 %",  "sin nube",        P.SUCCESS, "◆"),
            ("Extracción",  "Listo",  "Fuzzy + exacto",  P.ACCENT,  "◈"),
            ("Fusión",      "Listo",  "con marcadores",  P.INFO,    "◇"),
            ("Módulos",     "9",      "7 activos",       P.WARNING, "◐"),
        ]
        for i, (etq, val, delta, col, ico) in enumerate(kpis):
            StatCard(stats, etq, val, delta, col, ico).grid(
                row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="nsew")

        # Acciones rápidas
        ctk.CTkLabel(interior, text="Acciones rápidas",
                     font=F(15, "bold"), text_color=P.TXT, anchor="w"
                     ).pack(anchor="w", pady=(28, 4))
        ctk.CTkLabel(interior, text="Los flujos más habituales, a un clic.",
                     font=F(11), text_color=P.TXT_2, anchor="w"
                     ).pack(anchor="w", pady=(0, 14))

        grid = ctk.CTkFrame(interior, fg_color="transparent")
        grid.pack(fill="x")
        for i in range(4):
            grid.grid_columnconfigure(i, weight=1, uniform="accion")

        acciones = [
            ("Extracción", "Corta y clasifica", "#06B6D4", "◈", "extractor"),
            ("Fusionar",   "Une varios en uno", "#10B981", "◇", "fusionador"),
            ("Dividir",    "Separa por unidad", "#F59E0B", "◐", "divisor"),
            ("Comprimir",  "Reduce el tamaño",  "#3B82F6", "◆", "compresor"),
        ]
        for i, (titulo, desc, col, ico, clave) in enumerate(acciones):
            card = self._accion_card(grid, titulo, desc, col, ico,
                                     lambda c=clave: self.mostrar_vista(c))
            card.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0), sticky="nsew")

        return scroll

    def _accion_card(self, master, titulo, desc, color, icono, comando):
        card = ctk.CTkFrame(master, fg_color=P.CARD, border_width=1,
                            border_color=P.BORDER, corner_radius=P.R_CARD, height=130)
        card.grid_propagate(False)
        franja = ctk.CTkFrame(card, height=3, corner_radius=2, fg_color=color)
        franja.pack(fill="x", padx=14, pady=(14, 0))
        ctk.CTkLabel(card, text=icono, font=F(22), text_color=color, anchor="w"
                     ).pack(anchor="w", padx=16, pady=(10, 4))
        ctk.CTkLabel(card, text=titulo, font=F(13, "bold"),
                     text_color=P.TXT, anchor="w").pack(anchor="w", padx=16)
        ctk.CTkLabel(card, text=desc, font=F(10), text_color=P.TXT_2,
                     anchor="w", wraplength=180, justify="left"
                     ).pack(anchor="w", padx=16, pady=(2, 0))

        def _h_in(_): card.configure(border_color=color)
        def _h_out(_): card.configure(border_color=P.BORDER)
        for w in (card, *card.winfo_children()):
            w.bind("<Enter>", _h_in, add="+")
            w.bind("<Leave>", _h_out, add="+")
            w.bind("<Button-1>", lambda e: comando(), add="+")
        return card

    # ═══════════════════════════════════════════════════════════
    #  VISTA: PLACEHOLDER (Firmar)
    # ═══════════════════════════════════════════════════════════
    def _vista_placeholder(self, mod: Modulo):
        frame = ctk.CTkFrame(self.content, corner_radius=P.R_CARD,
                             fg_color=P.CARD, border_width=1, border_color=P.BORDER)
        interior = ctk.CTkFrame(frame, fg_color="transparent")
        interior.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(interior, text=mod.emoji, font=F(56),
                     text_color=mod.color).pack(pady=(0, 8))
        ctk.CTkLabel(interior, text=mod.titulo, font=F(22, "bold"),
                     text_color=P.TXT).pack()
        ctk.CTkLabel(interior, text=T("en_desarrollo", "En desarrollo"),
                     font=F(12, "bold"), text_color=mod.color).pack(pady=(4, 0))
        return frame

    # ═══════════════════════════════════════════════════════════
    #  VISTA: VISOR
    # ═══════════════════════════════════════════════════════════
    def _vista_visor(self):
        frame = ctk.CTkFrame(self.content, corner_radius=P.R_CARD,
                             fg_color=P.CARD, border_width=1, border_color=P.BORDER)
        self.visor = VisorPDF(frame)
        self.visor.pack(fill="both", expand=True, padx=12, pady=12)
        return frame

    # ═══════════════════════════════════════════════════════════
    #  VISTA: OCR
    # ═══════════════════════════════════════════════════════════
    def _vista_ocr(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["ocr"])
        _, col_izq, col_der = self._layout_2col(interior)

        card = Card(col_izq)
        card.pack(fill="x")
        SectionTitle(card, "Archivos de entrada",
                     "Selecciona los PDFs escaneados a los que aplicarles OCR."
                     ).pack(anchor="w", padx=22, pady=(20, 14))

        barra = ctk.CTkFrame(card, fg_color="transparent")
        barra.pack(fill="x", padx=22, pady=(0, 10))
        Btn(barra, "Añadir PDFs", icono="+", tipo="secondary", ancho=140,
            comando=self._ocr_add_pdfs).pack(side="left", padx=(0, 6))
        Btn(barra, "Añadir carpeta", icono="📁", tipo="secondary", ancho=160,
            comando=self._ocr_add_carpeta).pack(side="left", padx=6)
        Btn(barra, "Quitar", icono="🗑", tipo="danger", ancho=100,
            comando=self._ocr_quitar).pack(side="left", padx=6)

        self._ocr_listbox = tk.Listbox(
            card, selectmode=tk.EXTENDED, activestyle="none",
            bg=P.CARD_ALT, fg=P.TXT, selectbackground=P.ACCENT,
            selectforeground=P.TXT_INV, highlightthickness=1,
            highlightbackground=P.BORDER, highlightcolor=P.ACCENT,
            borderwidth=0, relief="flat", font=("Segoe UI", 10), height=8)
        self._ocr_listbox.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        self.lbl_ocr_count = ctk.CTkLabel(card, text="0 archivos en la lista.",
                                          text_color=P.TXT_3, font=F(11))
        self.lbl_ocr_count.pack(anchor="w", padx=26, pady=(0, 16))

        card_opt = Card(col_izq)
        card_opt.pack(fill="x", pady=(14, 0))
        SectionTitle(card_opt, "Parámetros",
                     "Idioma del documento y opciones del OCR."
                     ).pack(anchor="w", padx=22, pady=(20, 10))

        ctk.CTkLabel(card_opt, text="Idioma", font=F(11, "bold"),
                     text_color=P.TXT).pack(anchor="w", padx=22, pady=(0, 4))
        self._ocr_idioma_var = tk.StringVar(value="Español")
        etiquetas_idiomas = list(IDIOMAS_OCR.values())
        self._ocr_idioma_menu = ctk.CTkOptionMenu(
            card_opt, values=etiquetas_idiomas, variable=self._ocr_idioma_var,
            fg_color=P.CARD_ALT, button_color=P.ACCENT,
            button_hover_color=P.ACCENT_HOVER, text_color=P.TXT,
            dropdown_fg_color=P.CARD, dropdown_hover_color=P.CARD_ALT,
            dropdown_text_color=P.TXT,
            font=F(11), dropdown_font=F(11),
            corner_radius=P.R_INPUT, height=36)
        self._ocr_idioma_menu.pack(fill="x", padx=22, pady=(0, 14))

        self._ocr_chk_cipa = ctk.CTkCheckBox(
            card_opt, text="Usar CIPA (limpieza con IA)",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self._ocr_chk_cipa.pack(anchor="w", padx=22, pady=3)

        self._ocr_chk_solo_escaneado = ctk.CTkCheckBox(
            card_opt, text="Solo aplicar a páginas sin texto",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self._ocr_chk_solo_escaneado.select()
        self._ocr_chk_solo_escaneado.pack(anchor="w", padx=22, pady=3)

        self._ocr_destino = InputField(card_opt, "Carpeta de salida (opcional)",
                                       "Por defecto junto a cada PDF")
        self._ocr_destino.pack(fill="x", padx=22, pady=(10, 8))
        Btn(card_opt, "Explorar", icono="📂", tipo="secondary", ancho=130, height=32,
            comando=lambda: self._sel_carpeta(self._ocr_destino)
            ).pack(anchor="w", padx=22, pady=(0, 10))

        self.lbl_ocr_motor_info = ctk.CTkLabel(
            card_opt, text="", text_color=P.TXT_3, anchor="w",
            justify="left", wraplength=520, font=F(10))
        self.lbl_ocr_motor_info.pack(fill="x", padx=22, pady=(0, 18))

        self.btn_run_ocr = Btn(col_izq, "Ejecutar OCR", icono="⬢",
                               tipo="success", ancho=200, height=44,
                               comando=self._iniciar_ocr)
        self.btn_run_ocr.pack(fill="x", pady=(14, 0))

        self.panel_ocr = PanelProgreso(col_der, altura_log=280)
        self.panel_ocr.pack(fill="both", expand=True)
        self.panel_ocr.btn_stop.configure(command=self._detener)

        try:
            self.lbl_ocr_motor_info.configure(text=_info_motor_ocr())
        except Exception:
            self.lbl_ocr_motor_info.configure(text="Motor OCR: no disponible")

        return scroll

    # ═══════════════════════════════════════════════════════════
    #  VISTA: ASISTENTE
    # ═══════════════════════════════════════════════════════════
    def _vista_asistente(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["asistente"])

        card = Card(interior)
        card.pack(fill="both", expand=True, pady=(22, 0))

        ctk.CTkLabel(card,
                     text="Pregunta a CIPA sobre cómo usar CCpdf IA",
                     font=F(12), text_color=P.TXT_2, anchor="w"
                     ).pack(anchor="w", padx=22, pady=(20, 10))

        self._chat_historial = ctk.CTkTextbox(
            card, fg_color=P.CARD_ALT, height=400, corner_radius=8,
            border_width=1, border_color=P.BORDER, text_color=P.TXT,
            font=F(11), wrap="word")
        self._chat_historial.pack(fill="both", expand=True, padx=22, pady=(0, 10))
        self._chat_historial.insert("1.0",
            "CIPA: ¡Hola! Soy el asistente de CCpdf IA. "
            "Pregúntame lo que quieras sobre cómo usar la suite.\n\n"
            "Ejemplos:\n"
            "  · ¿Cómo divido un PDF por unidad médica?\n"
            "  · ¿Qué diferencia hay entre el compresor y el OCR?\n"
            "  · ¿Es seguro usar CCpdf con documentos confidenciales?\n")
        self._chat_historial.configure(state="disabled")

        fila = ctk.CTkFrame(card, fg_color="transparent")
        fila.pack(fill="x", padx=22, pady=(0, 20))

        self._chat_entry = ctk.CTkEntry(
            fila, placeholder_text="Escribe tu pregunta…",
            height=42, corner_radius=P.R_INPUT,
            border_color=P.BORDER_STRONG, fg_color=P.CARD,
            text_color=P.TXT, placeholder_text_color=P.TXT_3, font=F(12))
        self._chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._chat_entry.bind("<Return>", lambda e: self._chat_enviar())

        self._btn_chat_enviar = Btn(fila, "Enviar", icono="▶", tipo="primary",
                                    ancho=120, height=42,
                                    comando=self._chat_enviar)
        self._btn_chat_enviar.pack(side="right")

        return scroll

    # ═══════════════════════════════════════════════════════════
    #  HELPERS DEL OCR
    # ═══════════════════════════════════════════════════════════
    def _ocr_add_pdfs(self):
        rutas = filedialog.askopenfilenames(
            title="Selecciona los PDFs para OCR",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if rutas:
            self._ocr_add_rutas(rutas)

    def _ocr_add_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta con PDFs")
        if not carpeta:
            return
        try:
            pdfs = _listar_pdfs_carpeta(carpeta)
        except ErrorFusion as e:
            messagebox.showwarning("Atención", str(e))
            return
        if not pdfs:
            messagebox.showinfo("Sin PDFs", "Esa carpeta no contiene PDFs.")
            return
        self._ocr_add_rutas(pdfs)

    def _ocr_add_rutas(self, rutas):
        if not hasattr(self, "_lista_ocr"):
            self._lista_ocr = []
        existentes = {os.path.realpath(p) for p in self._lista_ocr}
        añadidos = 0
        for r in rutas:
            if os.path.realpath(r) in existentes:
                continue
            self._lista_ocr.append(r)
            existentes.add(os.path.realpath(r))
            añadidos += 1
        self._ocr_refrescar()
        if añadidos:
            self.panel_ocr.escribir_log(f"Añadidos {añadidos} PDF(s).")

    def _ocr_refrescar(self):
        if not hasattr(self, "_lista_ocr"):
            self._lista_ocr = []
        self._ocr_listbox.delete(0, "end")
        for i, ruta in enumerate(self._lista_ocr, 1):
            self._ocr_listbox.insert("end", f"{i:>3}.   {os.path.basename(ruta)}")
        n = len(self._lista_ocr)
        self.lbl_ocr_count.configure(
            text=f"{n} archivo{'s' if n != 1 else ''} en la lista.")

    def _ocr_quitar(self):
        sel = list(self._ocr_listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            del self._lista_ocr[i]
        self._ocr_refrescar()

    # ═══════════════════════════════════════════════════════════
    #  HELPERS DEL ASISTENTE
    # ═══════════════════════════════════════════════════════════
    def _chat_enviar(self):
        """Envía el mensaje del usuario al asistente CIPA."""
        if not _HAY_ASISTENTE:
            messagebox.showwarning(
                "Asistente",
                "El módulo CIPA no está disponible.\n"
                "Comprueba que exista Motores/CIPA y que Ollama esté activo.")
            return

        texto = self._chat_entry.get().strip()
        if not texto:
            return
        if self._chat_ocupado:
            return

        self._chat_append(f"Tú: {texto}\n\n")
        self._chat_entry.delete(0, "end")

        self._chat_ocupado = True
        self._btn_chat_enviar.configure(state="disabled", text="Pensando…")
        self._chat_append("CIPA: pensando…\n\n")

        threading.Thread(target=self._chat_hilo, args=(texto,), daemon=True).start()

    def _chat_hilo(self, texto: str):
        """Corre en hilo aparte. Llama al asistente y devuelve por after()."""
        try:
            if self._asistente is None:
                self._asistente = AsistenteCIPA()
            respuesta = self._asistente.enviar(texto)
        except Exception as e:
            respuesta = f"[Error: {e}]"
        self.after(0, lambda: self._chat_mostrar_respuesta(respuesta))

    def _chat_mostrar_respuesta(self, respuesta: str):
        """Vuelve al hilo UI. Quita el 'pensando…' y muestra la respuesta."""
        try:
            contenido = self._chat_historial.get("1.0", "end")
            pensando = "CIPA: pensando…\n\n"
            if pensando in contenido:
                contenido = contenido.replace(pensando, "")
                self._chat_historial.configure(state="normal")
                self._chat_historial.delete("1.0", "end")
                self._chat_historial.insert("1.0", contenido)
                self._chat_historial.configure(state="disabled")
        except Exception:
            pass

        self._chat_append(f"CIPA: {respuesta}\n\n")
        self._chat_ocupado = False
        self._btn_chat_enviar.configure(state="normal", text="▶  Enviar")

    def _chat_append(self, texto: str):
        """Añade texto al chat y hace scroll al final."""
        try:
            self._chat_historial.configure(state="normal")
            self._chat_historial.insert("end", texto)
            self._chat_historial.see("end")
            self._chat_historial.configure(state="disabled")
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    #  ARRANQUE OCR
    # ═══════════════════════════════════════════════════════════

    def _iniciar_ocr(self):
        """Ejecuta OCR sobre los archivos seleccionados."""
        if self._procesando:
            return
        if not hasattr(self, "_lista_ocr") or not self._lista_ocr:
            messagebox.showwarning("Atención", "Agrega al menos un PDF.")
            return

        # ─── Recoger parámetros del formulario ───
        # Idioma: convertir etiqueta a código ("Español" → "spa")
        etiqueta_idioma = self._ocr_idioma_var.get()
        codigo_idioma = "spa"
        for cod, etq in IDIOMAS_OCR.items():
            if etq == etiqueta_idioma:
                codigo_idioma = cod
                break

        usar_cipa = bool(self._ocr_chk_cipa.get())
        solo_escaneado = bool(self._ocr_chk_solo_escaneado.get())
        destino = self._ocr_destino.get()

        archivos = list(self._lista_ocr)
        kwargs = dict(
            archivos=archivos,
            destino=destino,
            idioma=codigo_idioma,
            solo_si_escaneado=solo_escaneado,
            usar_cipa=usar_cipa,
        )

        self.panel_ocr.escribir_log(f"Procesando {len(archivos)} archivo(s)…")
        self._lanzar(_aplicar_ocr, kwargs, self.panel_ocr)

    # ═══════════════════════════════════════════════════════════
    #  Helpers comunes para las vistas
    # ═══════════════════════════════════════════════════════════
    def _cabecera_modulo(self, parent, mod: Modulo):
        cab = ctk.CTkFrame(parent, fg_color="transparent")
        cab.pack(fill="x")
        izq = ctk.CTkFrame(cab, fg_color="transparent")
        izq.pack(side="left", fill="x", expand=True)
        fila = ctk.CTkFrame(izq, fg_color="transparent")
        fila.pack(anchor="w")
        ctk.CTkLabel(fila, text=mod.emoji, font=F(22),
                     text_color=mod.color).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(fila, text=mod.titulo, font=F(20, "bold"),
                     text_color=P.TXT).pack(side="left")
        ctk.CTkLabel(izq, text=mod.descripcion, font=F(12),
                     text_color=P.TXT_2, anchor="w").pack(anchor="w", pady=(4, 0))

    def _scroll_view(self):
        s = ctk.CTkScrollableFrame(self.content, fg_color="transparent",
                                   corner_radius=0)
        s.grid(row=0, column=0, sticky="nsew")
        interior = ctk.CTkFrame(s, fg_color="transparent")
        interior.pack(fill="both", expand=True, padx=36, pady=(28, 28))
        return s, interior

    def _layout_2col(self, parent):
        layout = ctk.CTkFrame(parent, fg_color="transparent")
        layout.pack(fill="both", expand=True, pady=(22, 0))
        layout.grid_columnconfigure(0, weight=3)
        layout.grid_columnconfigure(1, weight=2)
        col_izq = ctk.CTkFrame(layout, fg_color="transparent")
        col_izq.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        col_der = ctk.CTkFrame(layout, fg_color="transparent")
        col_der.grid(row=0, column=1, sticky="nsew")
        return layout, col_izq, col_der

    # ═══════════════════════════════════════════════════════════
    #  VISTA: EXTRACCIÓN
    # ═══════════════════════════════════════════════════════════
    def _vista_extractor(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["extractor"])
        _, col_izq, col_der = self._layout_2col(interior)

        # Tarjeta: origen / destino
        card = Card(col_izq)
        card.pack(fill="x")
        SectionTitle(card, "Origen y destino",
                     "Selecciona dónde buscar los PDFs y dónde guardar los resultados."
                     ).pack(anchor="w", padx=22, pady=(20, 16))

        self._ext_origen = InputField(card, "Carpeta de origen",
                                      "Ej. /home/usuario/Documentos/PDFs")
        self._ext_origen.pack(fill="x", padx=22, pady=(0, 10))
        Btn(card, "Examinar", icono="📂", tipo="secondary", ancho=110, height=30,
            comando=lambda: self._sel_carpeta(self._ext_origen)
            ).pack(anchor="w", padx=22, pady=(0, 14))

        self._ext_destino = InputField(card, "Carpeta de salida (opcional)",
                                       "Por defecto: carpeta configurada o junto al PDF")
        self._ext_destino.pack(fill="x", padx=22, pady=(0, 10))
        Btn(card, "Examinar", icono="📂", tipo="secondary", ancho=110, height=30,
            comando=lambda: self._sel_carpeta(self._ext_destino)
            ).pack(anchor="w", padx=22, pady=(0, 20))

        # Tabs
        self.tabs = ctk.CTkTabview(
            col_izq, fg_color="transparent",
            segmented_button_selected_color=P.ACCENT,
            segmented_button_selected_hover_color=P.ACCENT_HOVER,
            segmented_button_unselected_color=P.CARD_ALT,
            segmented_button_unselected_hover_color=P.BORDER,
            text_color=P.TXT)
        self.tabs.pack(fill="both", expand=True, pady=(14, 0))
        tab_firma = self.tabs.add("✂️  Corte hasta palabra")
        tab_lista = self.tabs.add("🗂️  Clasificador por lista")

        # Tab firma
        self._ext_k1 = ctk.CTkEntry(
            tab_firma, placeholder_text="Texto donde detener el corte (requerido)",
            height=38, corner_radius=P.R_INPUT, border_color=P.BORDER_STRONG,
            fg_color=P.CARD, text_color=P.TXT, placeholder_text_color=P.TXT_3,
            font=F(12))
        self._ext_k1.pack(fill="x", pady=(12, 6))
        self._ext_k2 = ctk.CTkEntry(
            tab_firma, placeholder_text="Segundo texto en la misma página (opcional)",
            height=38, corner_radius=P.R_INPUT, border_color=P.BORDER_STRONG,
            fg_color=P.CARD, text_color=P.TXT, placeholder_text_color=P.TXT_3,
            font=F(12))
        self._ext_k2.pack(fill="x", pady=6)

        self.chk_fuzzy_1 = ctk.CTkCheckBox(
            tab_firma, text="Tolerancia ortográfica (Fuzzy)",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self.chk_fuzzy_1.select()
        self.chk_fuzzy_1.pack(anchor="w", pady=(12, 4))

        self.chk_palabra_1 = ctk.CTkCheckBox(
            tab_firma, text="Solo palabra completa",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self.chk_palabra_1.pack(anchor="w", pady=4)

        self.btn_run_1 = Btn(tab_firma, "Ejecutar corte", icono="▶", tipo="success",
                             ancho=200, height=42,
                             comando=lambda: self._iniciar_extraccion("firma"))
        self.btn_run_1.pack(fill="x", pady=14)

        # Tab lista
        self._ext_lista = ctk.CTkTextbox(
            tab_lista, fg_color=P.CARD_ALT, height=100, corner_radius=8,
            border_width=1, border_color=P.BORDER, text_color=P.TXT, font=FM(11))
        self._ext_lista.pack(fill="both", expand=True, pady=(8, 6))

        Btn(tab_lista, "Cargar .txt", icono="📄", tipo="secondary", ancho=140,
            comando=self._cargar_txt).pack(anchor="e", pady=4)

        self.chk_fuzzy_2 = ctk.CTkCheckBox(
            tab_lista, text="Tolerancia ortográfica (Fuzzy)",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self.chk_fuzzy_2.pack(anchor="w", pady=(6, 4))

        self.chk_palabra_2 = ctk.CTkCheckBox(
            tab_lista, text="Solo palabra completa (recomendado)",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self.chk_palabra_2.pack(anchor="w", pady=4)

        self.btn_run_2 = Btn(tab_lista, "Ejecutar clasificación", icono="▶",
                             tipo="success", ancho=200, height=42,
                             comando=lambda: self._iniciar_extraccion("lista"))
        self.btn_run_2.pack(fill="x", pady=14)

        # Columna derecha
        self.panel_extractor = PanelProgreso(col_der, altura_log=280)
        self.panel_extractor.pack(fill="both", expand=True)
        self.panel_extractor.btn_stop.configure(command=self._detener)

        return scroll

    # ═══════════════════════════════════════════════════════════
    #  VISTA: FUSIONADOR
    # ═══════════════════════════════════════════════════════════
    def _vista_fusionador(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["fusionador"])
        _, col_izq, col_der = self._layout_2col(interior)

        card = Card(col_izq)
        card.pack(fill="both", expand=True)
        SectionTitle(card, "Lista de PDFs",
                     "Añade archivos, ordénalos y decide dónde guardar el resultado."
                     ).pack(anchor="w", padx=22, pady=(20, 14))

        barra = ctk.CTkFrame(card, fg_color="transparent")
        barra.pack(fill="x", padx=22, pady=(0, 10))
        Btn(barra, "Añadir PDFs", icono="+", tipo="secondary", ancho=140,
            comando=self._fusion_add_pdfs).pack(side="left", padx=(0, 6))
        Btn(barra, "Añadir carpeta", icono="📁", tipo="secondary", ancho=160,
            comando=self._fusion_add_carpeta).pack(side="left", padx=6)
        Btn(barra, "⬆", tipo="ghost", ancho=40,
            comando=lambda: self._fusion_mover(-1)).pack(side="left", padx=2)
        Btn(barra, "⬇", tipo="ghost", ancho=40,
            comando=lambda: self._fusion_mover(+1)).pack(side="left", padx=2)
        Btn(barra, "Quitar", icono="🗑", tipo="danger", ancho=100,
            comando=self._fusion_quitar).pack(side="left", padx=6)

        self._fusion_listbox = tk.Listbox(
            card, selectmode=tk.EXTENDED, activestyle="none",
            bg=P.CARD_ALT, fg=P.TXT, selectbackground=P.ACCENT,
            selectforeground=P.TXT_INV, highlightthickness=1,
            highlightbackground=P.BORDER, highlightcolor=P.ACCENT,
            borderwidth=0, relief="flat", font=("Segoe UI", 10), height=10)
        self._fusion_listbox.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        self.lbl_fusion_count = ctk.CTkLabel(card, text="0 archivos en la lista.",
                                             text_color=P.TXT_3, font=F(11))
        self.lbl_fusion_count.pack(anchor="w", padx=26)

        # Destino
        card_dest = Card(col_izq)
        card_dest.pack(fill="x", pady=(14, 0))
        SectionTitle(card_dest, "Destino",
                     "Deja vacío para guardar junto al primer PDF."
                     ).pack(anchor="w", padx=22, pady=(18, 10))

        self.var_salida_fusion = tk.StringVar()
        self.entry_salida_fusion = ctk.CTkEntry(
            card_dest, textvariable=self.var_salida_fusion,
            placeholder_text="fusionado.pdf", height=38,
            corner_radius=P.R_INPUT, border_color=P.BORDER_STRONG,
            fg_color=P.CARD, text_color=P.TXT, placeholder_text_color=P.TXT_3,
            font=F(12))
        self.entry_salida_fusion.pack(fill="x", padx=22)

        self.lbl_salida_info = ctk.CTkLabel(
            card_dest, text="Añade PDFs para ver la ruta de destino.",
            text_color=P.TXT_3, anchor="w", justify="left",
            wraplength=520, font=F(10))
        self.lbl_salida_info.pack(fill="x", padx=22, pady=(6, 10))

        self.var_salida_fusion.trace_add(
            "write", lambda *_: self._fusion_actualizar_info_salida())

        self.chk_marcadores = ctk.CTkCheckBox(
            card_dest, text="Añadir marcadores (uno por archivo)",
            fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
            text_color=P.TXT, border_color=P.BORDER_STRONG,
            checkmark_color=P.TXT_INV, font=F(11))
        self.chk_marcadores.select()
        self.chk_marcadores.pack(anchor="w", padx=22, pady=(0, 18))

        self.btn_run_fusion = Btn(col_izq, "Fusionar PDFs", icono="🧩",
                                  tipo="success", ancho=200, height=44,
                                  comando=self._iniciar_fusion)
        self.btn_run_fusion.pack(fill="x", pady=(14, 0))

        self.panel_fusion = PanelProgreso(col_der, altura_log=280)
        self.panel_fusion.pack(fill="both", expand=True)
        self.panel_fusion.btn_stop.configure(command=self._detener)

        return scroll

    # ═══════════════════════════════════════════════════════════
    #  VISTA: DIVISOR
    # ═══════════════════════════════════════════════════════════
    def _vista_divisor(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["divisor"])
        _, col_izq, col_der = self._layout_2col(interior)

        card = Card(col_izq)
        card.pack(fill="x")
        SectionTitle(card, "Archivo a dividir",
                     "Selecciona el PDF grande y la carpeta de salida."
                     ).pack(anchor="w", padx=22, pady=(20, 14))

        self._div_origen = InputField(card, "PDF origen",
                                      "PDF a dividir por unidad")
        self._div_origen.pack(fill="x", padx=22, pady=(0, 8))
        Btn(card, "Elegir PDF", icono="📂", tipo="secondary", ancho=130, height=32,
            comando=self._div_elegir_pdf).pack(anchor="w", padx=22, pady=(0, 14))

        self._div_destino = InputField(card, "Carpeta de salida (opcional)",
                                       "Por defecto junto al PDF")
        self._div_destino.pack(fill="x", padx=22, pady=(0, 8))
        Btn(card, "Explorar", icono="📂", tipo="secondary", ancho=130, height=32,
            comando=lambda: self._sel_carpeta(self._div_destino)
            ).pack(anchor="w", padx=22, pady=(0, 20))

        card_lst = Card(col_izq)
        card_lst.pack(fill="both", expand=True, pady=(14, 0))
        SectionTitle(card_lst, "Lista de unidades",
                     "Una por línea. Cada unidad tendrá su propio PDF."
                     ).pack(anchor="w", padx=22, pady=(20, 10))

        self.txt_unidades = ctk.CTkTextbox(
            card_lst, fg_color=P.CARD_ALT, height=180, corner_radius=8,
            border_width=1, border_color=P.BORDER, text_color=P.TXT, font=FM(11))
        self.txt_unidades.pack(fill="both", expand=True, padx=22, pady=(0, 8))
        self.txt_unidades.bind("<KeyRelease>", lambda e: self._div_actualizar_count())

        fila = ctk.CTkFrame(card_lst, fg_color="transparent")
        fila.pack(fill="x", padx=22, pady=(0, 18))
        self.lbl_div_count = ctk.CTkLabel(fila, text="0 unidades en la lista.",
                                          text_color=P.TXT_3, font=F(11))
        self.lbl_div_count.pack(side="left")
        Btn(fila, "Cargar .txt", icono="📄", tipo="secondary", ancho=130,
            comando=self._div_cargar_txt).pack(side="right")

        self.btn_run_div = Btn(col_izq, "Dividir por unidad", icono="✂️",
                               tipo="success", ancho=200, height=44,
                               comando=self._iniciar_division)
        self.btn_run_div.pack(fill="x", pady=(14, 0))

        self.panel_divisor = PanelProgreso(col_der, altura_log=280)
        self.panel_divisor.pack(fill="both", expand=True)
        self.panel_divisor.btn_stop.configure(command=self._detener)

        return scroll

    # ═══════════════════════════════════════════════════════════
    #  VISTA: COMPRESOR
    # ═══════════════════════════════════════════════════════════
    def _vista_compresor(self):
        scroll, interior = self._scroll_view()
        self._cabecera_modulo(interior, MODULO_POR_CLAVE["compresor"])
        _, col_izq, col_der = self._layout_2col(interior)

        card = Card(col_izq)
        card.pack(fill="both", expand=True)
        SectionTitle(card, "Lista de archivos",
                     "Añade los PDFs que quieres comprimir."
                     ).pack(anchor="w", padx=22, pady=(20, 14))

        barra = ctk.CTkFrame(card, fg_color="transparent")
        barra.pack(fill="x", padx=22, pady=(0, 10))
        Btn(barra, "Añadir PDFs", icono="+", tipo="secondary", ancho=140,
            comando=self._comp_add_pdfs).pack(side="left", padx=(0, 6))
        Btn(barra, "Añadir carpeta", icono="📁", tipo="secondary", ancho=160,
            comando=self._comp_add_carpeta).pack(side="left", padx=6)
        Btn(barra, "Quitar", icono="🗑", tipo="danger", ancho=100,
            comando=self._comp_quitar).pack(side="left", padx=6)

        self._comp_listbox = tk.Listbox(
            card, selectmode=tk.EXTENDED, activestyle="none",
            bg=P.CARD_ALT, fg=P.TXT, selectbackground=P.ACCENT,
            selectforeground=P.TXT_INV, highlightthickness=1,
            highlightbackground=P.BORDER, highlightcolor=P.ACCENT,
            borderwidth=0, relief="flat", font=("Segoe UI", 10), height=8)
        self._comp_listbox.pack(fill="both", expand=True, padx=22, pady=(0, 8))

        self.lbl_comp_count = ctk.CTkLabel(card, text="0 archivos en la lista.",
                                           text_color=P.TXT_3, font=F(11))
        self.lbl_comp_count.pack(anchor="w", padx=26, pady=(0, 16))

        # Opciones
        card_opt = Card(col_izq)
        card_opt.pack(fill="x", pady=(14, 0))
        SectionTitle(card_opt, "Parámetros",
                     "Elige nivel, motor y opciones de la compresión."
                     ).pack(anchor="w", padx=22, pady=(20, 10))

        self.seg_nivel_comp = ctk.CTkSegmentedButton(
            card_opt, values=["Ligera", "Media", "Fuerte"],
            selected_color=P.ACCENT, selected_hover_color=P.ACCENT_HOVER,
            unselected_color=P.CARD_ALT, unselected_hover_color=P.BORDER,
            text_color=P.TXT, font=F(11, "bold"),
            command=lambda _v: self._comp_actualizar_descripcion())
        self.seg_nivel_comp.set("Media")
        self.seg_nivel_comp.pack(fill="x", padx=22, pady=(0, 6))

        self.lbl_comp_desc = ctk.CTkLabel(card_opt, text="", text_color=P.TXT_2,
                                          anchor="w", justify="left",
                                          wraplength=520, font=F(10))
        self.lbl_comp_desc.pack(fill="x", padx=22, pady=(0, 12))

        ctk.CTkLabel(card_opt, text="Motor", font=F(11, "bold"),
                     text_color=P.TXT).pack(anchor="w", padx=22, pady=(0, 4))
        f_motor = ctk.CTkFrame(card_opt, fg_color="transparent")
        f_motor.pack(fill="x", padx=22)
        self.var_motor_comp = tk.StringVar(value="auto")
        for valor, etiqueta in (("auto", "Automático"),
                                ("ghostscript", "Ghostscript"),
                                ("interno", "Interno")):
            ctk.CTkRadioButton(f_motor, text=etiqueta, variable=self.var_motor_comp,
                               value=valor, fg_color=P.ACCENT,
                               hover_color=P.ACCENT_HOVER, text_color=P.TXT,
                               border_color=P.BORDER_STRONG, font=F(11)
                               ).pack(side="left", padx=(0, 18))

        self.lbl_comp_motor_info = ctk.CTkLabel(
            card_opt, text="", text_color=P.TXT_3, anchor="w", justify="left",
            wraplength=520, font=F(10))
        self.lbl_comp_motor_info.pack(fill="x", padx=22, pady=(6, 10))

        self.var_omitir_ya = tk.BooleanVar(value=True)
        self.var_fallback_gs = tk.BooleanVar(value=True)
        for txt, var in (("Omitir ya comprimidos", self.var_omitir_ya),
                         ("Fallback a motor interno", self.var_fallback_gs)):
            ctk.CTkCheckBox(card_opt, text=txt, variable=var, fg_color=P.ACCENT,
                            hover_color=P.ACCENT_HOVER, text_color=P.TXT,
                            border_color=P.BORDER_STRONG,
                            checkmark_color=P.TXT_INV, font=F(11)
                            ).pack(anchor="w", padx=22, pady=3)

        self._comp_destino = InputField(card_opt, "Carpeta de salida (opcional)",
                                        "Por defecto: <carpeta>/PDFs_Comprimidos")
        self._comp_destino.pack(fill="x", padx=22, pady=(10, 8))
        Btn(card_opt, "Explorar", icono="📂", tipo="secondary", ancho=130, height=32,
            comando=lambda: self._sel_carpeta(self._comp_destino)
            ).pack(anchor="w", padx=22, pady=(0, 18))

        self.btn_run_comp = Btn(col_izq, "Comprimir PDFs", icono="🗜️",
                                tipo="success", ancho=200, height=44,
                                comando=self._iniciar_compresion)
        self.btn_run_comp.pack(fill="x", pady=(14, 0))

        self.panel_compresor = PanelProgreso(col_der, altura_log=280)
        self.panel_compresor.pack(fill="both", expand=True)
        self.panel_compresor.btn_stop.configure(command=self._detener)

        self._comp_actualizar_descripcion()
        self._comp_actualizar_info_motor()
        return scroll

    # ═══════════════════════════════════════════════════════════
    #  HELPERS DE FUSIÓN
    # ═══════════════════════════════════════════════════════════
    def _fusion_refrescar(self):
        self._fusion_listbox.delete(0, "end")
        for i, ruta in enumerate(self._lista_fusion, 1):
            self._fusion_listbox.insert("end", f"{i:>3}.   {os.path.basename(ruta)}")
        n = len(self._lista_fusion)
        self.lbl_fusion_count.configure(
            text=f"{n} archivo{'s' if n != 1 else ''} en la lista.")
        self._fusion_actualizar_info_salida()

    def _fusion_add_pdfs(self):
        rutas = filedialog.askopenfilenames(
            title="Selecciona los PDFs a fusionar",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if not rutas:
            return
        existentes = {os.path.realpath(p) for p in self._lista_fusion}
        añadidos = 0
        for r in rutas:
            if os.path.realpath(r) in existentes:
                continue
            self._lista_fusion.append(r)
            existentes.add(os.path.realpath(r))
            añadidos += 1
        self._fusion_refrescar()
        if añadidos:
            self.panel_fusion.escribir_log(f"Añadidos {añadidos} PDF(s).")

    def _fusion_add_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta con PDFs")
        if not carpeta:
            return
        try:
            pdfs = _listar_pdfs_carpeta(carpeta)
        except ErrorFusion as e:
            messagebox.showwarning("Atención", str(e))
            return
        if not pdfs:
            messagebox.showinfo("Sin PDFs", "Esa carpeta no contiene PDFs.")
            return
        existentes = {os.path.realpath(p) for p in self._lista_fusion}
        añadidos = 0
        for r in pdfs:
            if os.path.realpath(r) in existentes:
                continue
            self._lista_fusion.append(r)
            existentes.add(os.path.realpath(r))
            añadidos += 1
        self._fusion_refrescar()
        self.panel_fusion.escribir_log(
            f"Añadidos {añadidos} PDF(s) desde {os.path.basename(carpeta)}.")

    def _fusion_mover(self, delta):
        sel = sorted(self._fusion_listbox.curselection())
        if not sel:
            return
        n = len(self._lista_fusion)
        # Posición final de cada fila seleccionada (por defecto, no se mueve).
        posiciones = {i: i for i in sel}
        if delta < 0:
            # Ascendente: una fila solo se queda quieta si la de arriba
            # también estaba seleccionada y ya se quedó quieta (o es el
            # borde). Si no, swap normal. Evita que un bloque seleccionado
            # pegado al borde se desordene entre sí.
            limite = -1
            for i in sel:
                if i - 1 == limite:
                    limite = i
                    continue
                self._lista_fusion[i], self._lista_fusion[i - 1] = (
                    self._lista_fusion[i - 1], self._lista_fusion[i])
                posiciones[i] = i - 1
        else:
            limite = n
            for i in reversed(sel):
                if i + 1 == limite:
                    limite = i
                    continue
                self._lista_fusion[i], self._lista_fusion[i + 1] = (
                    self._lista_fusion[i + 1], self._lista_fusion[i])
                posiciones[i] = i + 1
        self._fusion_refrescar()
        for nuevo in posiciones.values():
            self._fusion_listbox.selection_set(nuevo)

    def _fusion_quitar(self):
        sel = list(self._fusion_listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            del self._lista_fusion[i]
        self._fusion_refrescar()

    def _carpeta_base_fusion(self):
        defecto = self._carpeta_defecto("fusionador")
        if defecto:
            return defecto
        return os.path.dirname(self._lista_fusion[0]) if self._lista_fusion else os.getcwd()

    def _resolver_salida_fusion(self):
        entrada = self.var_salida_fusion.get().strip()
        carpeta = self._carpeta_base_fusion()
        if not entrada:
            return os.path.join(carpeta, NOMBRE_FUSIONADO_DEFECTO)
        if not os.path.isabs(entrada):
            entrada = os.path.join(carpeta, entrada)
        if not entrada.lower().endswith(".pdf"):
            entrada += ".pdf"
        return entrada

    def _fusion_actualizar_info_salida(self):
        if not hasattr(self, "lbl_salida_info"):
            return
        if not self._lista_fusion:
            self.lbl_salida_info.configure(
                text="Añade PDFs para ver la ruta de destino.",
                text_color=P.TXT_3)
            return
        ruta = self._resolver_salida_fusion()
        entrada = self.var_salida_fusion.get().strip()
        if entrada:
            self.lbl_salida_info.configure(
                text=f"📄  Se guardará en:  {ruta}", text_color=P.TXT_2)
        else:
            self.lbl_salida_info.configure(
                text=f"📄  Sin ruta → se guardará como:  {ruta}",
                text_color=P.ACCENT)

    # ═══════════════════════════════════════════════════════════
    #  HELPERS DEL DIVISOR
    # ═══════════════════════════════════════════════════════════
    def _div_elegir_pdf(self):
        ruta = filedialog.askopenfilename(
            title="PDF a dividir", filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if ruta:
            self._div_origen.set(ruta)

    def _div_cargar_txt(self):
        ruta = filedialog.askopenfilename(
            title="Lista de unidades", filetypes=[("Texto", "*.txt"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            unidades = _leer_unidades_txt(ruta)
        except ErrorDivisor as e:
            messagebox.showwarning("Atención", str(e))
            return
        self.txt_unidades.delete("1.0", "end")
        self.txt_unidades.insert("1.0", "\n".join(unidades))
        self._div_actualizar_count()
        self.panel_divisor.escribir_log(
            f"Cargadas {len(unidades)} unidades desde {os.path.basename(ruta)}.")

    def _div_actualizar_count(self):
        lineas = [l.strip() for l in self.txt_unidades.get("1.0", "end").splitlines()
                  if l.strip() and not l.strip().startswith("#")]
        self.lbl_div_count.configure(
            text=f"{len(lineas)} unidad{'es' if len(lineas) != 1 else ''} en la lista.")

    # ═══════════════════════════════════════════════════════════
    #  HELPERS DEL COMPRESOR
    # ═══════════════════════════════════════════════════════════
    def _comp_refrescar(self):
        self._comp_listbox.delete(0, "end")
        for i, ruta in enumerate(self._lista_compresion, 1):
            self._comp_listbox.insert("end", f"{i:>3}.   {os.path.basename(ruta)}")
        n = len(self._lista_compresion)
        self.lbl_comp_count.configure(
            text=f"{n} archivo{'s' if n != 1 else ''} en la lista.")

    def _comp_add_rutas(self, rutas):
        existentes = {os.path.realpath(p) for p in self._lista_compresion}
        añadidos = 0
        for r in rutas:
            if os.path.realpath(r) in existentes:
                continue
            self._lista_compresion.append(r)
            existentes.add(os.path.realpath(r))
            añadidos += 1
        self._comp_refrescar()
        if añadidos:
            self.panel_compresor.escribir_log(f"Añadidos {añadidos} PDF(s).")

    def _comp_add_pdfs(self):
        rutas = filedialog.askopenfilenames(
            title="Selecciona los PDFs a comprimir",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if rutas:
            self._comp_add_rutas(rutas)

    def _comp_add_carpeta(self):
        carpeta = filedialog.askdirectory(title="Carpeta con PDFs")
        if not carpeta:
            return
        try:
            pdfs = _listar_pdfs_carpeta(carpeta)
        except ErrorFusion as e:
            messagebox.showwarning("Atención", str(e))
            return
        if not pdfs:
            messagebox.showinfo("Sin PDFs", "Esa carpeta no contiene PDFs.")
            return
        self._comp_add_rutas(pdfs)

    def _comp_quitar(self):
        sel = list(self._comp_listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            del self._lista_compresion[i]
        self._comp_refrescar()

    def _comp_actualizar_descripcion(self):
        etiqueta = self.seg_nivel_comp.get().lower()
        cfg = NIVELES_COMPRESION.get(etiqueta, {})
        self.lbl_comp_desc.configure(text=cfg.get("descripcion", ""))

    def _comp_actualizar_info_motor(self):
        try:
            texto = _info_motor()
            if "Ghostscript" not in texto and sys.platform.startswith("linux"):
                texto += "  ·  Instala Ghostscript para mejor compresión"
            self.lbl_comp_motor_info.configure(text=texto)
        except Exception:
            self.lbl_comp_motor_info.configure(text="Motor: no se pudo detectar.")

    # ═══════════════════════════════════════════════════════════
    #  HELPERS GENERALES
    # ═══════════════════════════════════════════════════════════
    def _sel_carpeta(self, widget):
        folder = filedialog.askdirectory()
        if folder:
            widget.set(folder)

    def _cargar_txt(self):
        ruta = filedialog.askopenfilename(
            title="Selecciona el archivo de palabras",
            filetypes=[("Texto", "*.txt"), ("Todos", "*.*")])
        if not ruta:
            return
        try:
            claves = _leer_claves_txt(ruta)
        except ErrorMotor as e:
            messagebox.showwarning("Atención", str(e))
            return
        self._ext_lista.delete("1.0", "end")
        self._ext_lista.insert("1.0", "\n".join(claves))
        self.panel_extractor.escribir_log(
            f"Cargadas {len(claves)} palabras desde {os.path.basename(ruta)}")

    def _set_ejecutando(self, ejecutando, panel=None):
        self._procesando = ejecutando
        estado = "disabled" if ejecutando else "normal"
        for w in (self.btn_run_1, self.btn_run_2, self.btn_run_fusion,
                  self.btn_run_div, self.btn_run_comp, self.btn_run_ocr):
            try:
                w.configure(state=estado)
            except Exception:
                pass
        for p in (self.panel_extractor, self.panel_fusion,
                  self.panel_divisor, self.panel_compresor, self.panel_ocr):
            p.btn_stop.configure(state="normal" if ejecutando else "disabled")
        for item in self._nav_items.values():
            item.set_habilitado(not ejecutando)
        self._panel_activo = panel

    @staticmethod
    def _opciones(chk_fuzzy, chk_palabra):
        palabra = bool(chk_palabra.get())
        tolerar = bool(chk_fuzzy.get()) and not palabra
        return tolerar, palabra

    def _detener(self):
        self.evento_cancelar.set()
        if self._panel_activo is not None:
            self._panel_activo.btn_stop.configure(state="disabled")
            self._panel_activo.lbl_progreso.configure(
                text="Deteniendo al terminar el archivo actual...")

    # ═══════════════════════════════════════════════════════════
    #  ARRANQUES
    # ═══════════════════════════════════════════════════════════
    def _iniciar_extraccion(self, modo):
        if self._procesando:
            return
        origen = self._ext_origen.get()
        destino = self._ext_destino.get() or self._carpeta_defecto("extractor")
        if not origen or not os.path.isdir(origen):
            messagebox.showwarning("Atención",
                                   "Selecciona una carpeta de origen válida.")
            return
        if destino and os.path.abspath(destino) == os.path.abspath(origen):
            if not messagebox.askyesno(
                "Aviso",
                "Origen y destino son la misma carpeta.\n¿Continuar?"):
                return
        if modo == "firma":
            k1 = self._ext_k1.get().strip()
            if not k1:
                messagebox.showwarning("Atención", "Escribe el texto de corte.")
                return
            tolerar, palabra = self._opciones(self.chk_fuzzy_1, self.chk_palabra_1)
            funcion = _cortar_hasta_palabra
            kwargs = dict(origen=origen, destino=destino, clave1=k1,
                          clave2=self._ext_k2.get().strip(),
                          tolerar=tolerar, palabra_completa=palabra)
        else:
            claves = _limpiar_claves(self._ext_lista.get("1.0", "end").splitlines())
            if not claves:
                messagebox.showwarning("Atención", "La lista de palabras está vacía.")
                return
            tolerar, palabra = self._opciones(self.chk_fuzzy_2, self.chk_palabra_2)
            funcion = _clasificar_por_lista
            kwargs = dict(origen=origen, destino=destino, claves=claves,
                          tolerar=tolerar, palabra_completa=palabra)
        self._lanzar(funcion, kwargs, self.panel_extractor)

    def _iniciar_fusion(self):
        if self._procesando:
            return
        if len(self._lista_fusion) < 2:
            messagebox.showwarning("Atención", "Agrega al menos 2 PDFs.")
            return
        if not self.var_salida_fusion.get().strip():
            self.var_salida_fusion.set(
                os.path.join(self._carpeta_base_fusion(), NOMBRE_FUSIONADO_DEFECTO))
        salida = self._resolver_salida_fusion()
        salida_real = os.path.realpath(salida)
        for ruta_in in self._lista_fusion:
            if os.path.realpath(ruta_in) == salida_real:
                messagebox.showwarning(
                    "Ruta no válida",
                    "El archivo de salida no puede ser uno de los PDFs a fusionar.")
                return
        kwargs = dict(archivos=list(self._lista_fusion), salida=salida,
                      marcadores=bool(self.chk_marcadores.get()))
        self.panel_fusion.escribir_log(f"Destino: {salida}")
        self._lanzar(_fusionar_pdfs, kwargs, self.panel_fusion)

    def _iniciar_division(self):
        if self._procesando:
            return
        origen = self._div_origen.get()
        if not origen or not os.path.isfile(origen):
            messagebox.showwarning("Atención", "Selecciona un PDF válido.")
            return
        unidades = [l.strip() for l in self.txt_unidades.get("1.0", "end").splitlines()
                    if l.strip() and not l.strip().startswith("#")]
        if not unidades:
            messagebox.showwarning("Atención", "La lista de unidades está vacía.")
            return
        destino = self._div_destino.get()
        if not destino:
            base = os.path.splitext(os.path.basename(origen))[0]
            carpeta = self._carpeta_defecto("divisor") or os.path.dirname(origen)
            destino = os.path.join(carpeta, f"{base}_unidades")
        kwargs = dict(origen=origen, destino=destino, unidades=unidades,
                      callback_confirmar=self._confirmar_desde_hilo)
        self.panel_divisor.escribir_log(f"Salida: {destino}")
        self._lanzar(_dividir_por_unidad, kwargs, self.panel_divisor)

    def _iniciar_compresion(self):
        if self._procesando:
            return
        if not self._lista_compresion:
            messagebox.showwarning("Atención", "Agrega al menos un PDF.")
            return
        nivel = self.seg_nivel_comp.get().lower()
        motor = self.var_motor_comp.get()
        destino = self._comp_destino.get() or self._carpeta_defecto("compresor")
        kwargs = dict(archivos=list(self._lista_compresion), destino=destino,
                      nivel=nivel, motor=motor,
                      omitir_ya_comprimidos=bool(self.var_omitir_ya.get()),
                      fallback_si_gs_falla=bool(self.var_fallback_gs.get()))
        self._lanzar(_comprimir_pdfs, kwargs, self.panel_compresor)

    # ═══════════════════════════════════════════════════════════
    #  LANZADOR Y HILO
    # ═══════════════════════════════════════════════════════════
    def _lanzar(self, funcion, kwargs, panel):
        self.evento_cancelar.clear()
        self._set_ejecutando(True, panel=panel)
        panel.reset()
        panel.limpiar_log()
        panel.lbl_progreso.configure(text=T("iniciando", "Iniciando..."))
        threading.Thread(target=self._hilo_motor, args=(funcion, kwargs),
                         daemon=True).start()

    def _hilo_motor(self, funcion, kwargs):
        try:
            resumen = funcion(
                **kwargs,
                callback_progreso=lambda a, t, f: self.cola.put(MsgProgreso(a, t, f)),
                callback_log=lambda m: self.cola.put(MsgLog(m)),
                cancelar=self.evento_cancelar)
            self.cola.put(MsgFin(resumen, None))
        except (ErrorMotor, ErrorFusion, ErrorDivisor, ErrorCompresion, ErrorOCR) as e:
            self.cola.put(MsgFin(None, str(e)))
        except Exception as e:
            self.cola.put(MsgFin(None, f"Error inesperado: {e}"))

    def _confirmar_desde_hilo(self, valor, unidad, paginas, indice, total):
        respuesta, listo = {"ok": False}, threading.Event()
        self.cola.put(MsgPregunta(valor, unidad, paginas, indice, total,
                                  respuesta, listo))
        while not listo.wait(0.2):
            if self.evento_cancelar.is_set():
                return False
        return respuesta["ok"]

    def _preguntar(self, msg):
        if self._panel_activo is not None:
            self._panel_activo.lbl_progreso.configure(
                text=f"Esperando confirmación ({msg.indice} de {msg.total})...")

        def responder(si):
            msg.respuesta["ok"] = si
            msg.listo.set()

        mostrar_dialogo_confirmacion(self, msg.valor, msg.unidad, msg.paginas,
                                     msg.indice, msg.total, responder)

    # ═══════════════════════════════════════════════════════════
    #  COLA Y FIN
    # ═══════════════════════════════════════════════════════════
    def _procesar_cola(self):
        if not self.winfo_exists():
            return
        for _ in range(MAX_MENSAJES_POR_TICK):
            try:
                msg = self.cola.get_nowait()
            except queue.Empty:
                break
            panel = self._panel_activo
            if isinstance(msg, MsgProgreso) and panel is not None:
                panel.set_progreso(msg.actual, msg.total, msg.archivo)
            elif isinstance(msg, MsgLog) and panel is not None:
                panel.escribir_log(msg.texto)
            elif isinstance(msg, MsgFin):
                self._terminar(msg.resumen, msg.error)
            elif isinstance(msg, MsgPregunta):
                self._preguntar(msg)
        self._after_id = self.after(INTERVALO_COLA_MS, self._procesar_cola)

    def _terminar(self, resumen, error):
        panel = self._panel_activo
        self._set_ejecutando(False)
        if error or resumen is None:
            if panel is not None:
                panel.lbl_progreso.configure(text="Error.")
            messagebox.showerror("Error", error or "Error desconocido.")
            return
        texto = resumen.a_texto() if hasattr(resumen, "a_texto") else str(resumen)
        if panel is not None:
            panel.escribir_log("\n" + "─" * 40 + "\n" + texto)
        cancelado = bool(_attr(resumen, "cancelado", False))
        if panel is not None:
            if cancelado:
                panel.lbl_progreso.configure(text="Cancelado por el usuario.")
            else:
                panel.barra.set(1)
                panel.lbl_progreso.configure(text="¡Listo!")
        carpeta = _attr(resumen, "carpeta_salida", "")
        if not carpeta:
            ruta = _attr(resumen, "ruta_salida", "")
            if ruta:
                carpeta = os.path.dirname(ruta)
        hay_avisos = bool(
            _attr(resumen, "errores", []) or
            _attr(resumen, "sin_texto", []) or
            _attr(resumen, "unidades_sin_paginas", []) or
            _attr(resumen, "omitidos", []) or
            _attr(resumen, "paginas_huerfanas", 0) or
            _attr(resumen, "sin_mejora", []))
        if hay_avisos:
            texto += "\n\nRevisa el registro para más detalle."
        if carpeta and os.path.isdir(carpeta) and not cancelado:
            if messagebox.askyesno(
                "Proceso finalizado",
                texto + "\n\n¿Abrir la carpeta de salida?"):
                abrir_carpeta(carpeta)
        elif hay_avisos:
            messagebox.showwarning("Proceso finalizado", texto)
        else:
            messagebox.showinfo("Proceso finalizado", texto)

    # ═══════════════════════════════════════════════════════════
    #  CONFIGURACIÓN
    # ═══════════════════════════════════════════════════════════
    def _abrir_config(self):
        dlg = ctk.CTkToplevel(self)
        dlg.title(T("configuracion", "Configuración"))
        dlg.configure(fg_color=P.BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        ancho, alto = 640, 620
        try:
            x = self.winfo_rootx() + (self.winfo_width() - ancho) // 2
            y = self.winfo_rooty() + (self.winfo_height() - alto) // 2
            dlg.geometry(f"{ancho}x{alto}+{max(x, 0)}+{max(y, 0)}")
        except Exception:
            dlg.geometry(f"{ancho}x{alto}")

        cab = ctk.CTkFrame(dlg, corner_radius=0, fg_color=P.ACCENT, height=70)
        cab.pack(fill="x")
        cab.pack_propagate(False)
        ctk.CTkLabel(cab, text="⚙️   " + T("configuracion", "Configuración"),
                     font=F(20, "bold"), text_color=P.TXT_INV
                     ).pack(side="left", padx=26)

        tabs = ctk.CTkTabview(
            dlg, fg_color="transparent",
            segmented_button_selected_color=P.ACCENT,
            segmented_button_selected_hover_color=P.ACCENT_HOVER,
            segmented_button_unselected_color=P.CARD_ALT,
            segmented_button_unselected_hover_color=P.BORDER,
            text_color=P.TXT)
        tabs.pack(fill="both", expand=True, padx=20, pady=(12, 8))

        tab_carpetas = tabs.add("📁  Carpetas")
        tab_actual   = tabs.add("🔄  Actualizaciones")
        tab_acerca   = tabs.add("ℹ️  Acerca de")

        # Carpetas
        scroll = ctk.CTkScrollableFrame(tab_carpetas, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        entradas = {}
        for clave, etiqueta in (("extractor", "Extracción"),
                                ("fusionador", "Fusionador"),
                                ("divisor", "Divisor"),
                                ("compresor", "Compresor")):
            f = ctk.CTkFrame(scroll, fg_color="transparent")
            f.pack(fill="x", pady=(0, 14))
            ctk.CTkLabel(f, text=etiqueta, font=F(12, "bold"),
                         text_color=P.TXT).pack(anchor="w")
            sub = ctk.CTkFrame(f, fg_color="transparent")
            sub.pack(fill="x", pady=(4, 0))
            var = tk.StringVar(value=(_PREFS.get("carpetas") or {}).get(clave, ""))
            entradas[clave] = var
            ctk.CTkEntry(sub, textvariable=var, height=36,
                         corner_radius=P.R_INPUT, border_color=P.BORDER_STRONG,
                         fg_color=P.CARD, text_color=P.TXT, font=F(12)
                         ).pack(side="left", fill="x", expand=True, padx=(0, 8))

            def _elegir(v=var):
                r = filedialog.askdirectory()
                if r:
                    v.set(r)

            ctk.CTkButton(sub, text="📂", width=44, height=36,
                          corner_radius=P.R_INPUT, fg_color=P.ACCENT,
                          hover_color=P.ACCENT_HOVER, text_color=P.TXT_INV,
                          command=_elegir).pack(side="right")

        def _guardar():
            for k, v in entradas.items():
                _PREFS.setdefault("carpetas", {})[k] = v.get().strip()
            cfg_prefs.guardar(_PREFS)
            messagebox.showinfo("Carpetas", "Guardadas.", parent=dlg)

        ctk.CTkButton(tab_carpetas, text="💾   Guardar", height=40,
                      corner_radius=P.R_BTN, fg_color=P.SUCCESS,
                      hover_color=P.SUCCESS_HOVER, text_color=P.TXT_INV,
                      font=F(12, "bold"), command=_guardar
                      ).pack(fill="x", pady=(8, 0))

        # Actualizaciones
        ctk.CTkLabel(tab_actual, text="Versión instalada", font=F(11),
                     text_color=P.TXT_3).pack(anchor="w", pady=(6, 2))
        ctk.CTkLabel(tab_actual, text=APP_VERSION, font=F(22, "bold"),
                     text_color=P.TXT).pack(anchor="w", pady=(0, 20))
        lbl_act = ctk.CTkLabel(tab_actual,
                               text="Pulsa para comprobar novedades.",
                               font=F(11), text_color=P.TXT_3,
                               wraplength=520, justify="left")
        lbl_act.pack(anchor="w", pady=(0, 14))
        btn_act = ctk.CTkButton(tab_actual, text="🔍   Buscar actualizaciones",
                                height=44, corner_radius=P.R_BTN,
                                fg_color=P.ACCENT, hover_color=P.ACCENT_HOVER,
                                text_color=P.TXT_INV, font=F(13, "bold"))
        btn_act.pack(fill="x")

        def _buscar():
            try:
                from actualizador import comprobar
            except ImportError:
                messagebox.showwarning("Actualizaciones",
                                       "Falta actualizador.py en Interfaz.",
                                       parent=dlg)
                return
            btn_act.configure(state="disabled", text="Buscando…")
            lbl_act.configure(text="Consultando GitHub…", text_color=P.TXT_3)

            def tarea():
                info = comprobar(APP_VERSION)
                self.after(0, lambda: _mostrar(info))

            def _mostrar(info):
                btn_act.configure(state="normal", text="🔍   Buscar actualizaciones")
                if info.error:
                    lbl_act.configure(text=f"⚠  {info.error}", text_color=P.DANGER)
                    return
                if info.hay_nueva:
                    lbl_act.configure(text=f"✨  Nueva versión: {info.version_remota}",
                                      text_color=P.SUCCESS)
                    if messagebox.askyesno(
                        "Actualización",
                        f"Nueva: {info.version_remota}\nTienes: {info.version_actual}\n\n"
                        f"¿Abrir página de descarga?", parent=dlg):
                        import webbrowser
                        webbrowser.open(info.url_descarga)
                else:
                    lbl_act.configure(
                        text=f"✓  Estás en la última versión ({info.version_actual}).",
                        text_color=P.SUCCESS)

            threading.Thread(target=tarea, daemon=True).start()

        btn_act.configure(command=_buscar)

        # Acerca de
        try:
            from PIL import Image
            ruta_logo = _ruta_recurso("Interfaz/assets/logo.png")
            if os.path.isfile(ruta_logo):
                pil = Image.open(ruta_logo).convert("RGBA").resize((80, 80), Image.LANCZOS)
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(80, 80))
                lbl = ctk.CTkLabel(tab_acerca, image=img, text="")
                lbl._img_ref = img
                lbl.pack(pady=(6, 8))
        except Exception:
            pass

        ctk.CTkLabel(tab_acerca, text=APP_NOMBRE, font=F(24, "bold"),
                     text_color=P.TXT).pack()
        ctk.CTkLabel(tab_acerca, text=f"Versión {APP_VERSION}", font=F(12),
                     text_color=P.TXT_2).pack(pady=(2, 2))
        ctk.CTkLabel(tab_acerca,
                     text="Suite local de gestión de PDF · Open source",
                     font=F(11), text_color=P.TXT_2).pack(pady=(2, 2))
        ctk.CTkLabel(tab_acerca, text="Desarrollado por Kodesoft",
                     font=F(11, "bold"), text_color=P.ACCENT).pack(pady=(0, 18))

        ctk.CTkButton(dlg, text=T("config_cerrar", "Cerrar"), height=44,
                      corner_radius=P.R_BTN, fg_color=P.ACCENT,
                      hover_color=P.ACCENT_HOVER, text_color=P.TXT_INV,
                      font=F(13, "bold"), command=dlg.destroy
                      ).pack(pady=(8, 16))
        dlg.after(200, lambda: (dlg.lift(), dlg.grab_set()))

    # ═══════════════════════════════════════════════════════════
    #  HELPERS VARIOS
    # ═══════════════════════════════════════════════════════════
    def _carpeta_defecto(self, modulo):
        try:
            return (_PREFS.get("carpetas") or {}).get(modulo, "").strip()
        except Exception:
            return ""

    # ═══════════════════════════════════════════════════════════
    #  CIERRE Y NAVEGACIÓN
    # ═══════════════════════════════════════════════════════════
    def _al_cerrar(self):
        if self._procesando:
            if not messagebox.askyesno(
                "Salir", "Hay un proceso en curso. ¿Cancelarlo y salir?"):
                return
            self.evento_cancelar.set()
        if hasattr(self, "visor"):
            try:
                self.visor.cerrar()
            except Exception:
                pass
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self.destroy()

    def mostrar_vista(self, clave):
        if clave not in self._vistas:
            return
        for v in self._vistas.values():
            v.grid_forget()
        self._vistas[clave].grid(row=0, column=0, sticky="nsew")
        for c, item in self._nav_items.items():
            item.set_activo(c == clave)
        mod = MODULO_POR_CLAVE[clave]
        self._bc_modulo.configure(text=mod.titulo)
        self._clave_actual = clave


if __name__ == "__main__":
    CCPdfApp().mainloop()
