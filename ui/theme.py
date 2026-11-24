import customtkinter as ctk

BG = '#0c1018'
PANEL = '#141b27'
EDGE = '#253247'
MUTED = '#8b9bb3'
TEXT = '#edf3fa'
ACCENT = '#63e0bf'

def label(parent, text, size=13, color=TEXT, bold=False, **kwargs):
    return ctk.CTkLabel(parent, text=text, text_color=color,
                       font=ctk.CTkFont(family='Segoe UI', size=size, weight='bold' if bold else 'normal'),
                       **kwargs)

