import queue
from tkinter import BooleanVar, StringVar
import customtkinter as ctk
from obelisk.model import Settings, duration
from obelisk.runner import Runner
from core.storage import load_settings, save_settings
from ui.theme import *

class FightPanel(ctk.CTkFrame):
    def __init__(self, parent, app):
        super().__init__(parent, fg_color='transparent')
        self.app = app
        self.main = self
        self.events = queue.Queue()
        self.runner = Runner(self.events)
        self.preview_image = None
        self.metrics = {}
        self.grid_columnconfigure(0, weight=1)
        self.build_overview()
        self.saved = saved = load_settings('obelisk')
        if saved.get('hours') in self.hours.cget('values'):
            self.hours.set(saved['hours'])
        if isinstance(saved.get('attempts'), int) and saved['attempts'] >= 0:
            self.attempts_var.set(str(saved['attempts']))
        if saved.get('stop_on_victory') is False:
            self.victory.deselect()
        for variable in (self.hours_var, self.attempts_var, self.victory_var):
            variable.trace_add('write', self.save_preferences)
        self.after(100, self.poll)

    def save_preferences(self, *_):
        updated = dict(self.saved, stop_on_victory=self.victory_var.get())
        if self.hours_var.get() in self.hours.cget('values'):
            updated['hours'] = self.hours_var.get()
        try:
            limit = int(self.attempts_var.get())
            if limit >= 0:
                updated['attempts'] = limit
        except ValueError:
            pass
        if updated != self.saved:
            try:
                save_settings('obelisk', updated)
            except OSError:
                return
            self.saved = updated

    label = staticmethod(label)

    @property
    def running(self):
        return self.runner.running

    def stop(self):
        self.runner.stop()

    def build_overview(self):
        header = ctk.CTkFrame(self.main, fg_color='transparent')
        header.grid(row=0, column=0, sticky='ew')
        header.grid_columnconfigure(0, weight=1)
        self.label(header, 'Obelisk fights', 30, bold=True).grid(row=0, column=0, sticky='w')
        self.label(header, 'Repeat the fights. Track the damage. Watch your progress.', 13, MUTED).grid(row=1, column=0, sticky='w', pady=(2, 18))
        self.badge = self.label(header, '●  READY', 12, ACCENT, True)
        self.badge.grid(row=0, column=1, padx=4)

        cards = ctk.CTkFrame(self.main, fg_color='transparent')
        cards.grid(row=1, column=0, sticky='ew', pady=(0, 18))
        self.card_values = {}
        for i, (key, title, note) in enumerate([
            ('attempts', 'COMPLETED FIGHTS', 'This session'),
            ('dps', 'DAMAGE / SECOND', 'Latest confirmed reading'),
            ('fights', 'ESTIMATED FIGHTS LEFT', 'Based on recent attempts'),
            ('eta', 'ESTIMATED TIME LEFT', 'Including cooldowns')]):
            cards.grid_columnconfigure(i, weight=1, uniform='card')
            card = ctk.CTkFrame(cards, fg_color=PANEL, corner_radius=12, border_width=1, border_color=EDGE)
            card.grid(row=0, column=i, sticky='nsew', padx=(0 if i==0 else 6, 0 if i==3 else 6))
            self.label(card, title, 10, MUTED, True).pack(anchor='w', padx=16, pady=(15, 4))
            value = self.label(card, '—', 29, ACCENT if key=='dps' else TEXT, True)
            value.pack(anchor='w', padx=16)
            self.card_values[key] = value
            self.label(card, note, 10, MUTED).pack(anchor='w', padx=16, pady=(3, 15))

        center = ctk.CTkFrame(self.main, fg_color='transparent')
        center.grid(row=2, column=0, sticky='nsew')
        center.grid_columnconfigure(0, weight=1)
        center.grid_columnconfigure(1, weight=0, minsize=270)
        self.main.grid_rowconfigure(2, weight=1)
        center.grid_rowconfigure(0, weight=1)
        fight = ctk.CTkFrame(center, fg_color=PANEL, corner_radius=12, border_width=1, border_color=EDGE)
        fight.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        fight.grid_columnconfigure(0, weight=1)
        fight.grid_rowconfigure(4, weight=1)
        self.label(fight, 'Live session', 17, bold=True).grid(row=0, column=0, sticky='w', padx=20, pady=(18, 5))
        self.status_text = self.label(fight, 'Start a session, or check screen recognition first.', 12, MUTED, anchor='w', wraplength=470, justify='left')
        self.status_text.grid(row=1, column=0, sticky='ew', padx=20, pady=(0, 15))
        self.progress = ctk.CTkProgressBar(fight, progress_color=ACCENT, fg_color='#263343', height=7)
        self.progress.set(0)
        self.progress.grid(row=2, column=0, sticky='ew', padx=20)
        self.hp_label = self.label(fight, 'Remaining HP  —       Fight damage  —', 12, MUTED)
        self.hp_label.grid(row=3, column=0, sticky='w', padx=20, pady=(9, 5))
        self.preview = self.label(fight, 'A live game preview will appear here.\n\nThe companion opens the Obelisk menu for you.', 13, MUTED)
        self.preview.grid(row=4, column=0, padx=16, pady=10, sticky='nsew')
        self.timer_label = self.label(fight, 'Fight / cooldown  —       Session  00:00', 12, MUTED)
        self.timer_label.grid(row=5, column=0, sticky='w', padx=20, pady=(0, 16))

        controls = ctk.CTkFrame(center, width=246, fg_color=PANEL, corner_radius=12, border_width=1, border_color=EDGE)
        controls.grid(row=0, column=1, sticky='nsew')
        self.label(controls, 'Session', 17, bold=True).pack(anchor='w', padx=20, pady=(18, 16))
        self.label(controls, 'Maximum run time', 12, MUTED).pack(anchor='w', padx=20)
        self.hours_var = StringVar(master=self, value='6 hours')
        self.attempts_var = StringVar(master=self, value='0')
        self.victory_var = BooleanVar(master=self, value=True)
        self.hours = ctk.CTkOptionMenu(controls, values=['0.1 hours · test', '1 hour', '3 hours', '6 hours', '12 hours'],
                                    variable=self.hours_var, fg_color='#202c3e', button_color='#2c3e55', button_hover_color='#3b526e', width=224)
        self.hours.pack(padx=20, pady=(6, 12))
        self.label(controls, 'Fight limit · 0 = unlimited', 11, MUTED).pack(anchor='w', padx=20)
        self.attempt_limit = ctk.CTkEntry(controls, width=224, height=34, textvariable=self.attempts_var, fg_color=BG, border_color=EDGE)
        self.attempt_limit.pack(padx=20, pady=(6, 16))
        self.victory = ctk.CTkCheckBox(controls, text='Stop when the obelisk falls', font=('Segoe UI', 12),
                                    variable=self.victory_var, fg_color='#2fae92', hover_color='#288a75', checkmark_color=BG, checkbox_width=18, checkbox_height=18)
        self.victory.pack(anchor='w', padx=20, pady=(0, 18))
        self.start_button, self.test_button, self.stop_button = session_buttons(
            controls, 'Start fighting', self.start, 'Check screen recognition',
            lambda: self.start(True), self.stop,
        )
        self.label(controls, 'The game takes focus when you start.\nPress F8 to stop and return here.\n\nLosing focus releases the mouse.', 11, MUTED, justify='left', wraplength=224).pack(anchor='w', padx=20, pady=(0, 18))

        bottom = ctk.CTkFrame(self.main, fg_color=PANEL, corner_radius=12, border_width=1, border_color=EDGE)
        bottom.grid(row=3, column=0, sticky='ew', pady=(18, 0))
        self.label(bottom, 'Session log', 14, bold=True).pack(anchor='w', padx=18, pady=(12, 4))
        self.logbox = ctk.CTkTextbox(bottom, height=70, fg_color='transparent', text_color=MUTED,
                                   font=('Consolas', 11), activate_scrollbars=True)
        self.logbox.pack(fill='x', padx=10, pady=(0, 10))
        self.append_log('—', 'Ready. No automation is running.')
        self.label(self.main, 'Local recognition  •  Obelisk module  •  Start with a short test session', 10, MUTED).grid(row=4, column=0, sticky='w', pady=(12, 0))

    def append_log(self, timestamp, text):
        self.logbox.configure(state='normal')
        self.logbox.insert('end', f'{timestamp}   {text}\n')
        if int(self.logbox.index('end-1c').split('.')[0]) > 250:
            self.logbox.delete('1.0', '50.0')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')

    def start(self, monitor=False):
        if self.runner.running:
            return
        try:
            limit = int(self.attempt_limit.get())
            if limit < 0:
                raise ValueError()
            hours = float(self.hours.get().split()[0].replace(',', '.'))
        except ValueError:
            self.status_text.configure(text='Fight limit must be a whole number greater than or equal to 0.')
            return
        settings = Settings(hours=hours, max_attempts=limit,
                            stop_on_victory=bool(self.victory.get()), monitor_only=monitor)
        self.save_preferences()
        try:
            title = self.app.handoff_game(self.runner.desktop)
        except RuntimeError as error:
            self.status_text.configure(text=str(error))
            return
        self.start_button.configure(state='disabled')
        self.test_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.runner.start(settings, game_title=title)

    def poll(self):
        latest_preview = None
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == 'log':
                    self.append_log(*data)
                elif kind == 'status':
                    self.badge.configure(text='●  ' + data[0])
                    self.status_text.configure(text=data[1])
                elif kind == 'metrics':
                    self.metrics = data
                    for key in self.card_values:
                        value = duration(data['eta']) if key=='eta' else str(data[key]) if data[key] is not None else '—'
                        self.card_values[key].configure(text=value)
                    self.progress.set(max(0, min(1, data['progress'])))
                    self.hp_label.configure(text=f"Remaining HP  {data['hp']}       Fight damage  {data['total']}")
                    self.timer_label.configure(text=f"Fight / cooldown  {duration(data['seconds'])}       Session  {duration(data['elapsed'])}")
                elif kind == 'preview':
                    latest_preview = data
                elif kind == 'stopped':
                    self.badge.configure(text='●  STOPPED')
                    self.status_text.configure(text=data)
                    self.append_log('—', data)
                    self.start_button.configure(state='normal')
                    self.test_button.configure(state='normal')
                    self.stop_button.configure(state='disabled')
                    self.app.deiconify()
        except queue.Empty:
            pass
        if latest_preview is not None:
            width = max(180, min(420, self.preview.winfo_width()-20, int(max(120, self.preview.winfo_height()-20)*1.5)))
            self.preview_image = ctk.CTkImage(latest_preview, size=(width, round(width*2/3)))
            self.preview.configure(image=self.preview_image, text='')
        self.after(100, self.poll)

