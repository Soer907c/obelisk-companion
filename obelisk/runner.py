import json
import queue
import threading
import time
from datetime import datetime
from .model import Statistics, FightTracker, compact
from .vision import Vision
from core.windows import Desktop, StopHotkey, keep_awake
from core.storage import RUNTIME

class Runner:
    def __init__(self, events):
        self.events = events
        self.desktop = Desktop()
        self.thread = None
        self.cancel = threading.Event()
        self.vision = None
        self.stats = Statistics()
        self.heartbeat = 0
        self.stop_reason = 'Stopped'
        self.logfile = None
        self.watchdog = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def log(self, message, **data):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.events.put(('log', (timestamp, message)))
        if self.logfile:
            with self.logfile.open('a', encoding='utf-8') as file:
                file.write(json.dumps({'time': datetime.now().isoformat(), 'message': message,
                                       **data}, default=str, ensure_ascii=False) + '\n')

    def stop(self, reason='Stopped by you'):
        self.stop_reason = reason
        self.cancel.set()
        self.desktop.abort.set()
        self.desktop.release()

    def start(self, settings):
        if self.running:
            return
        self.cancel.clear()
        self.desktop.abort.clear()
        self.stats = Statistics()
        self.thread = threading.Thread(target=self._run, args=(settings,), daemon=True)
        self.thread.start()

    def _watchdog(self):
        hotkey = StopHotkey()
        try:
            while not self.cancel.wait(.03):
                if hotkey.pressed():
                    self.stop('Stopped with F8')
                    return
                if self.desktop.held and (not self.desktop.foreground() or time.monotonic()-self.heartbeat > 6):
                    self.stop('Mouse released: lost focus or screen capture stalled')
                    return
        finally:
            hotkey.close()

    def _run(self, settings):
        image = None
        tracker = None
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            self.logfile = RUNTIME / ('session-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.jsonl')
            self.events.put(('status', ('PREPARING', 'Loading local screen recognition…')))
            if self.vision is None:
                self.vision = Vision()
            title = self.desktop.find()
            self.log('Connected to the game', title=title)
            if self.cancel.is_set():
                return
            self.desktop.focus()
            if self.cancel.wait(.5):
                return
            self.watchdog = threading.Thread(target=self._watchdog, daemon=True)
            self.watchdog.start()
            keep_awake(True)
            started = time.monotonic()
            last_action = 0
            unknown_since = None
            prior_scene = None
            retries = 0
            baseline_level = None
            remaining_hp = None
            session_hp = None
            fight_start_hp = None
            last_dps = None
            last_total = None
            finished = False
            scene_started = started
            last_log = 0
            ready_frames = 0
            while not self.cancel.is_set():
                now = time.monotonic()
                if now-started >= settings.hours*3600:
                    self.stop('Maximum run time reached')
                    break
                self.heartbeat = now
                image = self.desktop.capture()
                obs, preview = self.vision.observe(image)
                now = time.monotonic()
                self.heartbeat = now
                if self.cancel.is_set():
                    break
                if obs.scene != prior_scene:
                    self.log('Screen: ' + obs.scene)
                    prior_scene, retries, scene_started = obs.scene, 0, now
                    image.save(RUNTIME / ('latest-' + obs.scene + '.png'))
                if obs.level is not None:
                    if baseline_level is None:
                        baseline_level = obs.level
                    elif obs.level > baseline_level:
                        if settings.stop_on_victory:
                            self.stop('Obelisk defeated — target reached')
                            break
                        baseline_level = obs.level
                        self.stats.damages.clear()
                        remaining_hp = session_hp = None
                if obs.hp is not None and obs.scene == 'menu':
                    remaining_hp = obs.hp
                    if session_hp is None:
                        session_hp = obs.hp
                if obs.scene == 'fight':
                    if tracker is None:
                        last_dps = last_total = None
                    if obs.dps is not None:
                        last_dps = obs.dps
                    if obs.total is not None:
                        last_total = obs.total
                if obs.scene == 'fight' and fight_start_hp is not None and obs.total is not None:
                    # The white HP text on its red bar is unreliable. Use menu HP minus
                    # this fight's damage instead, and mark the display approximate.
                    if 0 <= obs.total <= fight_start_hp:
                        remaining_hp = fight_start_hp - obs.total
                fights, eta = self.stats.estimate(remaining_hp)
                self.events.put(('metrics', {
                    'attempts': self.stats.attempts, 'hp': ('~' if obs.scene == 'fight' else '') + compact(remaining_hp),
                    'dps': compact(last_dps), 'total': compact(last_total), 'seconds': obs.seconds,
                    'fights': fights, 'eta': eta, 'elapsed': now-started,
                    'progress': float(1-remaining_hp/session_hp) if session_hp and remaining_hp is not None else 0,
                    'scene': obs.scene, 'raw': obs.raw,
                }))
                self.events.put(('preview', preview))
                if now-last_log > 5:
                    self.log('Screen reading', scene=obs.scene, raw=obs.raw, hp=obs.hp,
                             dps=obs.dps, total=obs.total, seconds=obs.seconds)
                    last_log = now
                    image.save(RUNTIME / 'latest-screen.png')
                if settings.monitor_only:
                    self.events.put(('status', ('OBSERVING', 'Reading the game. No clicks are being sent.')))
                    self.cancel.wait(.7)
                    continue
                if obs.scene == 'unknown':
                    self.desktop.release()
                    unknown_since = unknown_since or now
                    self.events.put(('status', ('WAITING FOR SCREEN', 'Unrecognized screen — no clicks are being sent.')))
                    if now-unknown_since > settings.unknown_seconds:
                        raise RuntimeError('Screen unrecognized for over 12 seconds. A diagnostic screenshot was saved.')
                    self.cancel.wait(.5)
                    continue
                unknown_since = None
                if obs.scene == 'fight':
                    if tracker is None:
                        tracker = FightTracker(now)
                        fight_start_hp = remaining_hp
                        self.stats.started(now)
                        finished = False
                        self.log('Fight started')
                    tracker.update(obs, now)
                    if tracker.zero_frames >= 2:
                        self.desktop.release()
                        if not finished:
                            self.stats.finished(tracker.peak)
                            self.log('Fight finished', damage=tracker.peak, attempts=self.stats.attempts)
                            finished = True
                        self.events.put(('status', ('FIGHT FINISHED', 'Opening the return menu.')))
                        if now-last_action > 1:
                            self.desktop.click(obs.targets['exit'], obs.viewport)
                            last_action = now
                            retries += 1
                    elif obs.seconds == 0:
                        self.desktop.release()
                    else:
                        # An unreadable timer is tolerated briefly while tracking actual damage.
                        self.desktop.hold(obs.targets['hold'], obs.viewport)
                        self.events.put(('status', ('FIGHTING', 'Holding on the obelisk and tracking damage.')))
                        if now-tracker.last_progress > settings.stall_seconds:
                            self.desktop.release()
                            if tracker.recoveries >= 1 or now-tracker.last_read > settings.unknown_seconds:
                                raise RuntimeError('No confirmed damage progress. Mouse hold stopped.')
                            tracker.recoveries += 1
                            tracker.last_progress = now
                            self.log('Damage stopped increasing. Retrying the mouse hold once.')
                            self.cancel.wait(.2)
                    if now-scene_started > 600:
                        raise RuntimeError('Fight screen did not finish within 10 minutes.')
                else:
                    self.desktop.release()
                    if obs.scene == 'return':
                        self.events.put(('status', ('RETURNING', 'Returning to the mines.')))
                        if now-last_action > 1:
                            self.desktop.click(obs.targets['return'], obs.viewport)
                            last_action = now
                            retries += 1
                    elif obs.scene == 'navigation':
                        tracker = None
                        fight_start_hp = None
                        self.events.put(('status', ('NAVIGATING', 'Opening the Obelisk menu.')))
                        if now-last_action > 1:
                            self.desktop.click(obs.targets['shortcut'], obs.viewport)
                            last_action = now
                            retries += 1
                    elif obs.scene == 'menu':
                        tracker = None
                        fight_start_hp = None
                        if settings.max_attempts and self.stats.attempts >= settings.max_attempts:
                            self.stop('Fight limit reached')
                            break
                        ready_frames = ready_frames + 1 if obs.seconds == 0 else 0
                        self.events.put(('status', ('WAITING', 'Reading the cooldown before the next fight.')))
                        if obs.seconds is None and now-scene_started > settings.unknown_seconds:
                            raise RuntimeError('Cooldown could not be read reliably.')
                        if ready_frames >= 2 and now-last_action > 1.2:
                            if baseline_level is None:
                                raise RuntimeError('Obelisk level could not be read. Unknown target not started.')
                            self.desktop.click(obs.targets['attack'], obs.viewport)
                            last_action = now
                            retries += 1
                            ready_frames = 0
                if retries >= 3:
                    raise RuntimeError('The menu did not respond after three attempts.')
                self.cancel.wait(.45)
        except Exception as exc:
            self.stop_reason = str(exc)
            self.events.put(('status', ('NEEDS ATTENTION', str(exc))))
            try:
                self.log('Stopped on error', error=str(exc))
                if image is not None:
                    image.save(RUNTIME / 'latest-error.png')
            except Exception:
                pass
        finally:
            self.cancel.set()
            self.desktop.abort.set()
            try:
                self.desktop.release()
            finally:
                keep_awake(False)
            if self.watchdog and self.watchdog is not threading.current_thread():
                self.watchdog.join(timeout=1)
            try:
                self.log('Session stopped', reason=self.stop_reason, attempts=self.stats.attempts)
            except OSError:
                pass
            self.events.put(('stopped', self.stop_reason))

