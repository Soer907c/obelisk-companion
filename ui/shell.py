import tkinter as tk
import customtkinter as ctk
from features import FEATURES
from .theme import BG, PANEL, ACCENT, MUTED, label

class Companion(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Obelisk Companion')
        self.geometry('1240x880')
        self.minsize(1080, 780)
        self.configure(fg_color=BG)
        self.panels = {}
        self.buttons = {}
        self.active = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar()
        self.content = ctk.CTkFrame(self, fg_color='transparent')
        self.content.grid(row=0, column=1, sticky='nsew', padx=26, pady=22)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<F8>', lambda _: self.stop_all())
        self.show_feature(FEATURES[0])

    def sidebar(self):
        rail = ctk.CTkFrame(self, width=184, corner_radius=0, fg_color='#101620')
        rail.grid(row=0, column=0, sticky='ns')
        rail.grid_propagate(False)
        rail.grid_rowconfigure(6, weight=1)
        logo = tk.Canvas(rail, width=80, height=90, bg='#101620', highlightthickness=0)
        logo.grid(row=0, column=0, padx=45, pady=(26, 4))
        logo.create_polygon(40, 7, 58, 24, 58, 70, 40, 82, 22, 70, 22, 24,
                            fill='#183e3a', outline=ACCENT, width=2)
        logo.create_line(40, 7, 40, 82, fill=ACCENT, width=2)
        logo.create_line(22, 24, 58, 24, fill=ACCENT)
        label(rail, 'OBELISK', 20, bold=True).grid(row=1, column=0)
        label(rail, 'C O M P A N I O N', 10, MUTED).grid(row=2, column=0, pady=(0, 30))
        menu = ctk.CTkFrame(rail, fg_color='transparent')
        menu.grid(row=3, column=0, padx=16, sticky='ew')
        label(menu, 'AUTOMATION', 10, MUTED, True).pack(anchor='w', pady=(0, 10))
        for feature in FEATURES:
            button = ctk.CTkButton(menu, text='◈  '+feature.title, height=42, width=152,
                                  anchor='w', command=lambda f=feature: self.show_feature(f))
            button.pack(fill='x', pady=4)
            self.buttons[feature.id] = button
        label(rail, 'Built to grow with you', 11, MUTED).grid(row=4, column=0, pady=(14, 24))
        label(rail, 'ON YOUR COMPUTER\n\nLocal screen recognition\nNo cloud or account', 11, MUTED, justify='left').grid(row=5, column=0)
        box = ctk.CTkFrame(rail, fg_color=PANEL, corner_radius=10)
        box.grid(row=7, column=0, padx=16, pady=18, sticky='ew')
        label(box, 'F8', 23, ACCENT, True).pack(pady=(10, 0))
        label(box, 'Stop & release mouse', 11, MUTED).pack(padx=10, pady=(0, 12))
        label(rail, 'v0.1 · Windows', 10, MUTED).grid(row=8, column=0, pady=(0, 16))

    def show_feature(self, feature):
        if self.active == feature.id:
            return
        if any(panel.running for panel in self.panels.values()):
            return
        for panel in self.panels.values():
            panel.grid_remove()
        if feature.id not in self.panels:
            self.panels[feature.id] = feature.create(self.content, self)
        self.panels[feature.id].grid(row=0, column=0, sticky='nsew')
        self.active = feature.id
        for key, button in self.buttons.items():
            button.configure(fg_color='#193b38' if key == feature.id else '#202c3e',
                             text_color=ACCENT if key == feature.id else '#edf3fa',
                             hover_color='#214c46')

    def stop_all(self):
        for panel in self.panels.values():
            panel.stop()

    def close(self):
        self.stop_all()
        self.destroy()

