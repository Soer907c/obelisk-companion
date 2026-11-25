"""One foreground capture requested by the user."""

from time import monotonic, sleep

from core.windows import Desktop


def capture_current_result(wait_seconds=8, *, desktop=None):
    if desktop is None:
        desktop = Desktop()
        desktop.find()
    deadline = monotonic() + wait_seconds
    while not desktop.foreground():
        if monotonic() >= deadline:
            raise RuntimeError('Bring the game to the foreground and try again.')
        sleep(0.05)

    bounds = desktop.bounds()
    frame = desktop.capture()
    if not desktop.foreground() or desktop.bounds() != bounds:
        raise RuntimeError('The game window changed during capture. Try again.')
    return frame
