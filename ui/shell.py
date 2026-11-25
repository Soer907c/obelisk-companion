import tkinter as tk
import customtkinter as ctk
import json
import os
from datetime import datetime
from base64 import b64decode
from pathlib import Path
from tempfile import TemporaryDirectory
from queue import Empty, SimpleQueue
from threading import Event, Thread
from bootstrap import ROOT
from core.windows import StopHotkey, user32, center_window
from core.storage import RUNTIME, load_settings, save_settings
from features import FEATURES
from .theme import BG, PANEL, ACCENT, MUTED, label

class Companion(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Obelisk Companion')
        icon = ROOT / 'assets' / 'obelisk-companion.ico'
        if icon.is_file():
            self.iconbitmap(str(icon))
        else:
            with TemporaryDirectory() as directory:
                temporary_icon = Path(directory) / icon.name
                temporary_icon.write_bytes(b64decode(icon.with_suffix('.ico.b64').read_bytes()))
                self.iconbitmap(str(temporary_icon))
        self.geometry('1240x880')
        self.positioned = False
        self.bind('<Map>', self.position_on_start, add='+')
        self.configure(fg_color=BG)
        self.panels = {}
        self.buttons = {}
        self.active = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar()
        self.build_viewport()
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.verify_handoff = os.environ.get('OBELISK_VERIFY_GAME_HANDOFF', '')
        self.verify_relic_start = os.environ.get('OBELISK_VERIFY_RELIC_START') == '1'
        trial_mode = os.environ.get('OBELISK_SUPERVISED_RELIC_TRIAL')
        self.supervised_relic_trial = int(trial_mode) if trial_mode in ('1', '2') else 0
        if (ROOT / 'runtime' / 'relic-verification-4000.enabled').is_file():
            self.supervised_relic_trial = 20
        preferred = load_settings('companion').get('selected_feature')
        self.show_feature(next(feature for feature in FEATURES if feature.id == 'relics')
                          if self.verify_handoff or self.supervised_relic_trial or self.verify_relic_start
                          else next((feature for feature in FEATURES if feature.id == preferred), FEATURES[0]),
                          remember=False)
        if self.supervised_relic_trial in (2, 20):
            self.panels['relics'].prepare_two_roll_trial()
        if self.verify_relic_start:
            self.panels['relics'].prepare_goal_verification()
        if self.verify_handoff:
            self.record_handoff('verification ready', mode=self.verify_handoff)
        self.hotkey_events = SimpleQueue()
        self.hotkey_closed = Event()
        Thread(target=self.listen_for_f8, daemon=True).start()
        self.after(20, self.poll_f8)

    def position_on_start(self, event):
        if event.widget is self and not self.positioned:
            self.positioned = True
            self.after_idle(self.center_on_monitor)

    def center_on_monitor(self):
        size = center_window(user32.GetAncestor(self.winfo_id(), 2),
                             (self.winfo_width(), self.winfo_height()), (1240, 880))
        if size:
            self.minsize(max(1, min(1080, size[0])), max(1, min(560, size[1])))

    def build_viewport(self):
        viewport = ctk.CTkFrame(self, fg_color='transparent')
        viewport.grid(row=0, column=1, sticky='nsew', padx=(18, 8), pady=14)
        viewport.grid_rowconfigure(0, weight=1)
        viewport.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(viewport, bg=BG, borderwidth=0, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical = ctk.CTkScrollbar(viewport, command=self.canvas.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        self.horizontal = ctk.CTkScrollbar(viewport, orientation='horizontal',
                                            command=self.canvas.xview)
        self.horizontal.grid(row=1, column=0, sticky='ew')
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=self.horizontal.set)
        self.content = ctk.CTkFrame(self.canvas, fg_color=BG)
        self.content_id = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self.canvas.bind('<Configure>', self.fit_viewport)
        self.content.bind('<Configure>', self.fit_viewport)
        self.bind('<MouseWheel>', self.scroll_viewport, add='+')

    def fit_viewport(self, _=None):
        visible_width = self.canvas.winfo_width()
        width = max(visible_width, round(830 * self.content._get_widget_scaling()))
        height = max(self.canvas.winfo_height(), self.content.winfo_reqheight())
        self.canvas.itemconfigure(self.content_id, width=width, height=height)
        self.canvas.configure(scrollregion=(0, 0, width, height))
        if width > visible_width:
            self.horizontal.grid()
        else:
            self.horizontal.grid_remove()

    def scroll_viewport(self, event):
        widget = event.widget
        while widget is not None and widget is not self.content:
            if isinstance(widget, (ctk.CTkScrollableFrame, tk.Text)):
                return
            widget = widget.master
        if widget is self.content or event.widget is self.canvas:
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')

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
        self.hotkey_label = label(box, 'Start / stop', 11, MUTED)
        self.hotkey_label.pack(padx=10, pady=(0, 12))
        label(rail, 'v0.2.0 · Windows', 10, MUTED).grid(row=8, column=0, pady=(0, 16))

    def show_feature(self, feature, *, remember=True):
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
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.after_idle(self.fit_viewport)
        if remember:
            try:
                save_settings('companion', {'selected_feature': feature.id})
            except OSError:
                pass
        for key, button in self.buttons.items():
            button.configure(fg_color='#193b38' if key == feature.id else '#202c3e',
                             text_color=ACCENT if key == feature.id else '#edf3fa',
                             hover_color='#214c46')

    def stop_all(self):
        for panel in self.panels.values():
            panel.stop()

    def listen_for_f8(self):
        hotkey = StopHotkey()
        try:
            if not hotkey.registered:
                self.hotkey_events.put(None)
            while not self.hotkey_closed.wait(.01):
                if hotkey.pressed():
                    self.hotkey_events.put(user32.GetForegroundWindow())
        finally:
            hotkey.close()

    def poll_f8(self):
        while True:
            try:
                foreground = self.hotkey_events.get_nowait()
            except Empty:
                break
            if foreground is None:
                self.hotkey_label.configure(text='Start / stop (polled)')
            else:
                companion = user32.GetAncestor(self.winfo_id(), 2)
                self.handle_f8(foreground == companion == user32.GetForegroundWindow())
        self.after(20, self.poll_f8)

    def handle_f8(self, companion_focused):
        panel = self.panels[self.active]
        self.record_handoff('F8', companion_focused=companion_focused, running=panel.running)
        if panel.running:
            panel.stop()
        elif companion_focused:
            panel.start()

    def handoff_game(self, desktop):
        # The UI still owns foreground activation rights when it requests game focus.
        before = user32.GetForegroundWindow()
        title = desktop.find()
        accepted = desktop.focus()
        after_request = user32.GetForegroundWindow()
        self.iconify()
        self.record_handoff('handoff requested', before=before, game=desktop.hwnd,
                            companion=user32.GetAncestor(self.winfo_id(), 2),
                            game_title=title, accepted=accepted, after_request=after_request,
                            after_minimize=user32.GetForegroundWindow())
        if self.verify_handoff:
            self.after(200, lambda: self.record_handoff(
                'handoff settled', game=desktop.hwnd, game_focused=desktop.foreground()))
        return title

    def record_handoff(self, event, **fields):
        if not (self.verify_handoff or self.supervised_relic_trial or self.verify_relic_start):
            return
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with (RUNTIME / 'handoff-verification.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(dict(time=datetime.now().isoformat(), pid=os.getpid(),
                                          event=event, feature=self.active,
                                          foreground=user32.GetForegroundWindow(), **fields)) + '\n')
        except OSError:
            pass

    def close(self):
        self.hotkey_closed.set()
        self.stop_all()
        self.destroy()

