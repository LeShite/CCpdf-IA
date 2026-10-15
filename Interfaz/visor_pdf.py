"""
visor_pdf.py — Widget de visualización de PDF (CustomTkinter + motor propio).

Envuelve DocumentoPDF con:
  - Navegación página a página (botones + teclado ←/→/Inicio/Fin)
  - Zoom con botones y Ctrl+rueda
  - Ajustar ancho / ajustar página
  - Salto directo por número de página
  - Búsqueda de texto (resalta páginas con coincidencias)
  - Caché LRU de páginas renderizadas (no explota con PDFs grandes)
"""
import os
import threading
import tkinter as tk
from collections import OrderedDict
from tkinter import filedialog, messagebox

from PIL import ImageTk
import customtkinter as ctk

from Motores.Visualizacion import (
    DocumentoPDF, abrir_pdf, ErrorVisor,
    ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT,
)

COLOR_FONDO       = "#0F1218"
COLOR_ACENTO      = "#3B82F6"
COLOR_TEXTO_SUAVE = "#8A94A6"

ZOOM_PASO = 1.25
CACHE_MAX = 3


class VisorPDF(ctk.CTkFrame):
    """Panel completo para abrir y navegar un PDF."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=COLOR_FONDO, corner_radius=8, **kwargs)

        self._doc: DocumentoPDF | None = None
        self._pagina = 0
        self._zoom = ZOOM_DEFAULT
        self._ajuste = "pagina"         # "pagina" | "ancho" | "libre"
        self._cache: OrderedDict = OrderedDict()
        self._img_ref = None

        self._construir_ui()
        self._actualizar_estado()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _construir_ui(self):
        barra = ctk.CTkFrame(self, fg_color="transparent")
        barra.pack(fill="x", padx=8, pady=(8, 4))

        ctk.CTkButton(barra, text="📂 Abrir PDF…", width=130, height=28,
                      fg_color=COLOR_ACENTO, command=self._elegir_archivo
                      ).pack(side="left", padx=(0, 12))

        self.btn_prev = ctk.CTkButton(barra, text="◀", width=36, height=28,
                                      command=self.pagina_anterior)
        self.btn_prev.pack(side="left")

        self.entry_pagina = ctk.CTkEntry(barra, width=52, height=28, justify="center")
        self.entry_pagina.pack(side="left", padx=4)
        self.entry_pagina.bind("<Return>", lambda e: self._ir_a_entry())

        self.lbl_total = ctk.CTkLabel(barra, text="/ 0", text_color=COLOR_TEXTO_SUAVE)
        self.lbl_total.pack(side="left", padx=(0, 6))

        self.btn_next = ctk.CTkButton(barra, text="▶", width=36, height=28,
                                      command=self.pagina_siguiente)
        self.btn_next.pack(side="left")

        # Búsqueda
        self.entry_buscar = ctk.CTkEntry(barra, width=160, height=28,
                                         placeholder_text="Buscar en el PDF…")
        self.entry_buscar.pack(side="left", padx=(20, 4))
        self.entry_buscar.bind("<Return>", lambda e: self._buscar())

        self.btn_buscar = ctk.CTkButton(barra, text="🔎", width=36, height=28,
                                        fg_color=COLOR_ACENTO, command=self._buscar)
        self.btn_buscar.pack(side="left")

        # Zoom a la derecha
        ctk.CTkButton(barra, text="⚙ Página", width=90, height=28,
                      command=self.ajustar_pagina).pack(side="right")
        ctk.CTkButton(barra, text="↔ Ancho", width=90, height=28,
                      command=self.ajustar_ancho).pack(side="right", padx=(0, 6))
        ctk.CTkButton(barra, text="+", width=36, height=28,
                      command=lambda: self._zoom_x(ZOOM_PASO)
                      ).pack(side="right", padx=(6, 0))
        self.lbl_zoom = ctk.CTkLabel(barra, text="—", width=52,
                                     text_color=COLOR_TEXTO_SUAVE)
        self.lbl_zoom.pack(side="right", padx=4)
        ctk.CTkButton(barra, text="−", width=36, height=28,
                      command=lambda: self._zoom_x(1 / ZOOM_PASO)
                      ).pack(side="right")

        # Área del canvas
        area = ctk.CTkFrame(self, fg_color=COLOR_FONDO)
        area.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(area, bg=COLOR_FONDO, highlightthickness=0,
                                borderwidth=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.sb_v = tk.Scrollbar(area, orient="vertical", command=self.canvas.yview)
        self.sb_v.grid(row=0, column=1, sticky="ns")
        self.sb_h = tk.Scrollbar(area, orient="horizontal", command=self.canvas.xview)
        self.sb_h.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self.sb_v.set, xscrollcommand=self.sb_h.set)

        # Teclado
        self.canvas.configure(takefocus=True)
        self.canvas.bind("<Left>", lambda e: self.pagina_anterior())
        self.canvas.bind("<Right>", lambda e: self.pagina_siguiente())
        self.canvas.bind("<Prior>", lambda e: self.pagina_anterior())
        self.canvas.bind("<Next>", lambda e: self.pagina_siguiente())
        self.canvas.bind("<Home>", lambda e: self.ir_a(0))
        self.canvas.bind("<End>", lambda e: self.ir_a(self.num_paginas - 1))
        self.canvas.bind("<Control-plus>", lambda e: self._zoom_x(ZOOM_PASO))
        self.canvas.bind("<Control-equal>", lambda e: self._zoom_x(ZOOM_PASO))
        self.canvas.bind("<Control-minus>", lambda e: self._zoom_x(1 / ZOOM_PASO))
        self.canvas.bind("<Control-0>", lambda e: self._zoom_reset())

        # Rueda del ratón
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel_linux)
        self.canvas.bind("<Button-5>", self._on_wheel_linux)

        # Placeholder
        self._placeholder = ctk.CTkLabel(
            self.canvas,
            text="Sin PDF cargado\n\nUsa «📂 Abrir PDF…» o llama a .cargar(ruta)",
            text_color=COLOR_TEXTO_SUAVE, justify="center",
        )
        self.canvas.create_window(0, 0, window=self._placeholder, anchor="nw",
                                  tags=("placeholder",))

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    @property
    def num_paginas(self) -> int:
        return self._doc.num_paginas if self._doc else 0

    @property
    def pagina(self) -> int:
        return self._pagina

    @property
    def ruta(self) -> str:
        return self._doc.ruta if self._doc else ""

    def cargar(self, ruta: str):
        if not ruta or not os.path.isfile(ruta):
            messagebox.showwarning("Atención", "El archivo no existe.")
            return
        self.cerrar()
        try:
            self._doc = abrir_pdf(ruta)
        except ErrorVisor as e:
            messagebox.showerror("Error al abrir", str(e))
            return
        self._pagina = 0
        self._zoom = ZOOM_DEFAULT
        self._ajuste = "pagina"
        self._cache.clear()
        self.lbl_total.configure(text=f"/ {self._doc.num_paginas}")
        self._ocultar_placeholder()

        print(f"[VISOR] Abierto: {os.path.basename(ruta)}")
        print(f"[VISOR] Páginas: {self._doc.num_paginas}")
        try:
            w, h = self._doc.tamano_pagina(0)
            print(f"[VISOR] Página 1: {w:.0f}x{h:.0f} pt")
        except Exception as e:
            print(f"[VISOR] Error tamaño: {e}")

        self._ajustar_y_render()

    def cerrar(self):
        if self._doc is not None:
            try:
                self._doc.cerrar()
            except Exception:
                pass
        self._doc = None
        self._cache.clear()
        self._img_ref = None
        self.canvas.delete("all")
        self._placeholder = ctk.CTkLabel(
            self.canvas,
            text="Sin PDF cargado\n\nUsa «📂 Abrir PDF…» o llama a .cargar(ruta)",
            text_color=COLOR_TEXTO_SUAVE, justify="center",
        )
        self.canvas.create_window(0, 0, window=self._placeholder, anchor="nw",
                                  tags=("placeholder",))
        self._actualizar_estado()

    def ir_a(self, indice: int):
        if self._doc is None:
            return
        indice = max(0, min(self.num_paginas - 1, indice))
        if indice == self._pagina:
            return
        self._pagina = indice
        self._render()

    def pagina_siguiente(self):
        self.ir_a(self._pagina + 1)

    def pagina_anterior(self):
        self.ir_a(self._pagina - 1)

    def ajustar_pagina(self):
        self._ajuste = "pagina"
        self._ajustar_y_render()

    def ajustar_ancho(self):
        self._ajuste = "ancho"
        self._ajustar_y_render()

    # ------------------------------------------------------------------
    # Búsqueda
    # ------------------------------------------------------------------
    def _buscar(self):
        if self._doc is None:
            messagebox.showinfo("Sin PDF", "Abre un PDF antes de buscar.")
            return
        termino = self.entry_buscar.get().strip()
        if not termino:
            return
        try:
            hits = self._doc.buscar(termino)
        except ErrorVisor as e:
            messagebox.showwarning("Búsqueda", str(e))
            return
        if not hits:
            messagebox.showinfo("Búsqueda",
                                f"No se encontró «{termino}» en el documento.")
            return
        siguiente = next((p for p in hits if p >= self._pagina), hits[0])
        self.ir_a(siguiente)
        messagebox.showinfo(
            "Búsqueda",
            f"«{termino}» aparece en {len(hits)} página(s): "
            + ", ".join(str(p + 1) for p in hits[:30])
            + ("…" if len(hits) > 30 else ""))

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------
    def _zoom_x(self, factor: float):
        nuevo = max(ZOOM_MIN, min(ZOOM_MAX, self._zoom * factor))
        if abs(nuevo - self._zoom) < 1e-3:
            return
        self._zoom = nuevo
        self._ajuste = "libre"
        self._render()

    def _zoom_reset(self):
        self._zoom = ZOOM_DEFAULT
        self._ajuste = "libre"
        self._render()

    def _ajustar_y_render(self):
        if self._doc is None:
            return
        self.update_idletasks()
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        try:
            w_pt, h_pt = self._doc.tamano_pagina(self._pagina)
        except ErrorVisor as e:
            messagebox.showerror("Error", str(e))
            return

        if self._ajuste == "ancho":
            self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, cw / w_pt))
        elif self._ajuste == "pagina":
            self._zoom = max(ZOOM_MIN, min(ZOOM_MAX, min(cw / w_pt, ch / h_pt)))
        self._render()

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------
    def _render(self):
        if self._doc is None:
            return

        clave = (self._pagina, round(self._zoom, 3))
        pil = self._cache.get(clave)

        if pil is not None:
            self._cache.move_to_end(clave)
            self._pintar(pil)
            return

        # Render en hilo separado
        pagina_req = self._pagina
        zoom_req = self._zoom

        def tarea():
            print(f"[VISOR] tarea: renderizando pág {pagina_req} zoom {zoom_req:.3f}")
            try:
                img = self._doc.renderizar(pagina_req, zoom=zoom_req)
                print(f"[VISOR] tarea: render OK → {img.size}")
            except ErrorVisor as e:
                self.after(0, lambda: messagebox.showerror("Error de render", str(e)))
                return
            except Exception as e:
                print(f"[VISOR] tarea: EXCEPCIÓN → {e!r}")
                self.after(0, lambda: messagebox.showerror("Error de render", str(e)))
                return
            print(f"[VISOR] tarea: llamando after(0, _recibir_render)")
            self.after(0, lambda: self._recibir_render(clave, img))

        threading.Thread(target=tarea, daemon=True).start()

    def _recibir_render(self, clave, img):
        print(f"[VISOR] _recibir_render clave={clave} actual={(self._pagina, round(self._zoom, 3))}")
        if (self._pagina, round(self._zoom, 3)) != clave:
            print(f"[VISOR] _recibir_render: descartado (ya cambió)")
            return
        self._cache[clave] = img
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        print(f"[VISOR] _recibir_render: llamando _pintar({img.size})")
        self._pintar(img)

    def _pintar(self, pil):
        print(f"[VISOR] _pintar: creando PhotoImage para {pil.size}")
        try:
            self.canvas.delete("pagina")
            self._img_ref = ImageTk.PhotoImage(pil)
            print(f"[VISOR] _pintar: PhotoImage creado, size={self._img_ref.width()}x{self._img_ref.height()}")
            self.canvas.create_image(0, 0, anchor="nw", image=self._img_ref, tags=("pagina",))
            self.canvas.configure(scrollregion=(0, 0, pil.size[0], pil.size[1]))
            self.canvas.yview_moveto(0)
            self.canvas.xview_moveto(0)
            print(f"[VISOR] _pintar: OK")
        except Exception as e:
            print(f"[VISOR] _pintar: EXCEPCIÓN → {e!r}")
            import traceback
            traceback.print_exc()
        self._actualizar_estado()

    def _actualizar_estado(self):
        if self._doc is None:
            self.entry_pagina.configure(state="disabled")
            self.btn_prev.configure(state="disabled")
            self.btn_next.configure(state="disabled")
            self.lbl_total.configure(text="/ 0")
            self.lbl_zoom.configure(text="—")
            return
        self.entry_pagina.configure(state="normal")
        self.entry_pagina.delete(0, "end")
        self.entry_pagina.insert(0, str(self._pagina + 1))
        self.btn_prev.configure(state="normal" if self._pagina > 0 else "disabled")
        self.btn_next.configure(
            state="normal" if self._pagina < self.num_paginas - 1 else "disabled")
        self.lbl_zoom.configure(text=f"{int(self._zoom * 100)} %")

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------
    def _elegir_archivo(self):
        ruta = filedialog.askopenfilename(
            title="Abrir PDF",
            filetypes=[("PDF", "*.pdf"), ("Todos", "*.*")])
        if ruta:
            self.cargar(ruta)

    def _ir_a_entry(self):
        if self._doc is None:
            return
        try:
            n = int(self.entry_pagina.get().strip())
        except ValueError:
            self._actualizar_estado()
            return
        self.ir_a(n - 1)
        self.canvas.focus_set()

    def _on_wheel(self, event):
        ctrl = bool(event.state & 0x0004)
        if ctrl:
            self._zoom_x(ZOOM_PASO if event.delta > 0 else 1 / ZOOM_PASO)
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _on_wheel_linux(self, event):
        ctrl = bool(event.state & 0x0004)
        arriba = (event.num == 4)
        if ctrl:
            self._zoom_x(ZOOM_PASO if arriba else 1 / ZOOM_PASO)
        else:
            self.canvas.yview_scroll(-1 if arriba else 1, "units")

    def _ocultar_placeholder(self):
        self.canvas.delete("placeholder")
