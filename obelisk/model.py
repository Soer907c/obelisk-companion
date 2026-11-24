from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections import deque
import re

SUFFIXES = ['', 'k', 'm', 'b', 't', 'qa', 'qi', 'sx', 'sp', 'oc', 'no',
            'dc', 'udc', 'ddc', 'tdc', 'qadc', 'qidc', 'sxdc', 'spdc', 'ocdc', 'nodc', 'vg']

def number(text):
    """Strict parsing: unknown suffixes must never silently become small numbers."""
    if not text:
        return None
    match = re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*([a-z]*)\s*', text.lower())
    if not match or match[2] not in SUFFIXES:
        return None
    try:
        return Decimal(match[1].replace(',', '.')) * Decimal(1000) ** SUFFIXES.index(match[2])
    except InvalidOperation:
        return None

def timer(text):
    match = re.fullmatch(r'\s*(\d{1,3}):([0-5]\d)\s*', text or '')
    return int(match[1]) * 60 + int(match[2]) if match else None

def compact(value):
    if value is None:
        return '—'
    value = Decimal(value)
    for i in range(len(SUFFIXES) - 1, -1, -1):
        base = Decimal(1000) ** i
        if abs(value) >= base:
            return f'{value / base:.2f}'.rstrip('0').rstrip('.') + SUFFIXES[i]
    return '0'

def duration(seconds):
    if seconds is None:
        return '—'
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f'{hours}h {minutes:02}m' if hours else f'{minutes:02}:{seconds:02}'

@dataclass
class Observation:
    scene: str = 'unknown'
    seconds: int | None = None
    hp: Decimal | None = None
    total: Decimal | None = None
    dps: Decimal | None = None
    level: int | None = None
    targets: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
    viewport: tuple = ()

@dataclass
class Settings:
    hours: float = 6
    max_attempts: int = 0
    stop_on_victory: bool = True
    monitor_only: bool = False
    stall_seconds: float = 8
    unknown_seconds: float = 12

class Statistics:
    def __init__(self):
        self.damages = deque(maxlen=10)
        self.cycles = deque(maxlen=10)
        self.attempts = 0
        self.last_start = None
        self.damage_sum = Decimal(0)

    def started(self, now):
        if self.last_start is not None:
            self.cycles.append(now - self.last_start)
        self.last_start = now

    def finished(self, damage):
        self.attempts += 1
        if damage is not None and damage > 0:
            self.damages.append(damage)
            self.damage_sum += damage

    def estimate(self, hp):
        if hp is None or hp < 0 or not self.damages:
            return None, None
        mean = sum(self.damages) / len(self.damages)
        fights = int((hp / mean).to_integral_value(rounding=ROUND_CEILING))
        eta = fights * sum(self.cycles) / len(self.cycles) if self.cycles else None
        return fights, eta

class FightTracker:
    """Damage must increase; a stale positive DPS display is not proof of input."""
    def __init__(self, now):
        self.peak = None
        self.last_progress = now
        self.last_read = now
        self.recoveries = 0
        self.zero_frames = 0

    def update(self, obs, now):
        self.zero_frames = self.zero_frames + 1 if obs.seconds == 0 else 0
        if obs.total is not None:
            self.last_read = now
            if self.peak is None or obs.total > self.peak:
                self.peak = obs.total
                self.last_progress = now

