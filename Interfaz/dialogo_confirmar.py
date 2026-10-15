"""
dialogo_confirmar.py — Cuadro "¿Son la misma unidad?" para CCpdf.

    mostrar_dialogo_confirmacion(parent, valor_pdf, unidad_lista, paginas,
                                 indice, total, al_responder)

No bloquea la interfaz: muestra una ventana modal y, al elegir, llama a
`al_responder(True | False)`. Cerrar la ventana equivale a "No" (lo seguro:
esas páginas quedan sin asignar, nunca en la unidad equivocada).
"""
import tkinter as tk
import customtkinter as ctk

COLOR_FONDO = "#0F1218"
COLOR_TARJETA = "#1C212B"
COLOR_TEXTO_SUAVE = "#8A94A6"
COLOR_OK = "#10B981"
COLOR_OK_HOVER = "#059669"
COLOR_NO = "#EF4444"
COLOR_NO_HOVER = "#DC2626"


def _caja(parent, titulo, texto):
    ctk.CTkLabel(parent, text=titulo, text_color=COLOR_TEXTO_SUAVE, anchor="w"
                 ).pack(fill="x", padx=24, pady=(14, 2))
    caja = ctk.CTkTextbox(parent, height=64, fg_color=COLOR_TARJETA, wrap="word",
                          font=ctk.CTkFont(size=15))
    caja.pack(fill="x", padx=24)
    caja.insert("1.0", texto)
    caja.configure(state="disabled")          # solo lectura


def mostrar_dialogo_confirmacion(parent, valor_pdf, unidad_lista, paginas,
                                 indice, total, al_responder):
    ancho, alto = 640, 480
    dlg = ctk.CTkToplevel(parent)
    dlg.title(f"Verificar unidad ({indice} de {total})")
    dlg.configure(fg_color=COLOR_FONDO)
    dlg.resizable(False, False)
    dlg.transient(parent)
    try:
        x = parent.winfo_rootx() + (parent.winfo_width() - ancho) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - alto) // 2
        dlg.geometry(f"{ancho}x{alto}+{max(x, 0)}+{max(y, 0)}")
    except Exception:
        dlg.geometry(f"{ancho}x{alto}")

    ya = {"respondido": False}

    def responder(valor: bool):
        if ya["respondido"]:
            return
        ya["respondido"] = True
        try:
            dlg.grab_release()
        except Exception:
            pass
        dlg.destroy()
        al_responder(valor)

    dlg.protocol("WM_DELETE_WINDOW", lambda: responder(False))

    ctk.CTkLabel(dlg, text="¿Estos dos nombres son la misma unidad médica?",
                 font=ctk.CTkFont(size=18, weight="bold")).pack(padx=24, pady=(22, 2), anchor="w")
    ctk.CTkLabel(dlg, text=f"Afecta {paginas} página{'s' if paginas != 1 else ''} del PDF.",
                 text_color=COLOR_TEXTO_SUAVE).pack(padx=24, anchor="w")

    _caja(dlg, "Dice en el PDF:", valor_pdf)
    _caja(dlg, "Está en tu lista como:", unidad_lista)

    ctk.CTkLabel(dlg, text="Si eliges «No», esas páginas no se asignan a ninguna unidad "
                           "y se guardan en _PAGINAS_SIN_ASIGNAR.pdf.",
                 text_color=COLOR_TEXTO_SUAVE, wraplength=590, justify="left"
                 ).pack(padx=24, pady=(14, 0), anchor="w")

    fila = ctk.CTkFrame(dlg, fg_color="transparent")
    fila.pack(fill="x", padx=24, pady=(18, 20), side="bottom")
    fila.grid_columnconfigure((0, 1), weight=1, uniform="b")
    ctk.CTkButton(fila, text="✅  Sí, es la misma", height=46, fg_color=COLOR_OK,
                  hover_color=COLOR_OK_HOVER, font=ctk.CTkFont(size=15, weight="bold"),
                  command=lambda: responder(True)).grid(row=0, column=0, padx=(0, 8), sticky="ew")
    ctk.CTkButton(fila, text="❌  No es la misma", height=46, fg_color=COLOR_NO,
                  hover_color=COLOR_NO_HOVER, font=ctk.CTkFont(size=15, weight="bold"),
                  command=lambda: responder(False)).grid(row=0, column=1, padx=(8, 0), sticky="ew")

    def _foco():
        try:
            dlg.lift()
            dlg.focus_force()
            dlg.grab_set()                    # modal; en Linux solo funciona ya visible
        except tk.TclError:
            pass
    dlg.after(250, _foco)
    return dlg