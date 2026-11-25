"""Offline capture, recognition and dispatch for relic rerolls."""

from dataclasses import dataclass, field

from PIL.Image import Image

from relics.simulation import Action, Observation, Phase, RerollSimulation


@dataclass(frozen=True)
class CapturedFrame:
    id: int
    window: object
    image: Image
    owner: object = field(repr=False)


@dataclass(frozen=True)
class RecognizedFrame:
    capture: CapturedFrame
    observation: Observation


@dataclass(frozen=True)
class DispatchState:
    session: object
    foreground_window: object
    stopped: bool = False


class FakeClicks:
    def __init__(self):
        self.actions = []

    def record(self, action: Action):
        self.actions.append(action)


class RerollReplay:
    """Capture, accept results and dispatch on one loop. OCR may finish out of order."""

    def __init__(self, simulation: RerollSimulation, window, recognize, current_state,
                 clicks: FakeClicks):
        self.simulation = simulation
        self.window = window
        self.recognize = recognize
        self.current_state = current_state
        self.clicks = clicks
        self.session = object()
        self.last_capture = 0
        self.open_all_capture = 0
        self.pending_action = None

    def capture(self, grab) -> CapturedFrame:
        image = grab()
        self.last_capture += 1
        return CapturedFrame(self.last_capture, self.window, image.copy(), self.session)

    def read(self, frame: CapturedFrame) -> RecognizedFrame:
        # One recognition call derives both the screen and its reading from these pixels.
        return RecognizedFrame(frame, Observation(frame.id, **self.recognize(frame.image)))

    def observe(self, result: RecognizedFrame, *, now: float) -> bool:
        frame = result.capture
        if (frame.owner is not self.session or frame.window != self.window
                or frame.id != result.observation.frame_id
                or frame.id <= max(self.open_all_capture, self.simulation.last_frame_id)
                or self.pending_action is not None):
            return False
        self.pending_action = self.simulation.observe(result.observation, now=now)
        return True

    def dispatch(self) -> bool:
        action = self.pending_action
        self.pending_action = None
        if action is None or self.simulation.phase is Phase.STOPPED:
            return False
        state = self.current_state()
        if state.stopped:
            self.simulation.stop('Stopped with F8.')
        elif state.session is not self.session:
            self.simulation.stop('The active reroll session changed.')
        elif state.foreground_window != self.window or self.window is None:
            self.simulation.stop('Game lost focus.')
        else:
            if action is Action.OPEN_ALL:
                self.open_all_capture = self.last_capture
            self.clicks.record(action)
            return True
        return False
