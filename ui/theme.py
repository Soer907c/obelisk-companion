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


def session_buttons(parent, start_text, start, read_text, read, stop):
    primary = ctk.CTkButton(parent, text=start_text, height=44, width=224,
                            fg_color=ACCENT, hover_color='#85ebd0', text_color='#102720',
                            font=('Segoe UI', 13, 'bold'), command=start)
    secondary = ctk.CTkButton(parent, text=read_text, height=36, width=224,
                              fg_color='#253247', hover_color='#33455f', command=read)
    stop_button = ctk.CTkButton(parent, text='Stop · F8', height=36, width=224,
                                fg_color='#42272f', hover_color='#5b303b', text_color='#f7b7c3',
                                command=stop, state='disabled')
    for button, gap in ((primary, 9), (secondary, 9), (stop_button, 14)):
        button.pack(padx=20, pady=(0, gap))
    return primary, secondary, stop_button

