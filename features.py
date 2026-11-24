"""Register independent features here; the shell discovers their navigation entries.

A panel is a CTkFrame exposing `running: bool` and `stop()`. Each feature owns its
settings, worker and view. Only one feature can own the game input at a time.
"""
from dataclasses import dataclass
from importlib import import_module

@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    module: str
    view: str

    def create(self, parent, app):
        return getattr(import_module(self.module), self.view)(parent, app)

FEATURES = (
    Feature('obelisk', 'Obelisk fights', 'ui.fight_panel', 'FightPanel'),
)

