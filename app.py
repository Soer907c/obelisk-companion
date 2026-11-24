import bootstrap
from core.windows import dpi_aware, single_instance

dpi_aware()

import customtkinter as ctk
from ui.shell import Companion

if __name__ == '__main__':
    instance = single_instance()
    if instance is None:
        raise SystemExit(0)
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('dark-blue')
    Companion().mainloop()
