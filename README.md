# Obelisk Companion

A Windows desktop companion that repeats Obelisk fights in Idle Obelisk Miner through Google Play Games. It reads the game window with local image matching and OCR, then uses Windows mouse input to navigate and fight.

## Install and start

You need Windows, Google Play Games with Idle Obelisk Miner installed, and 64-bit Python 3.13 with Tkinter. Add Python to PATH during installation. Download the source ZIP from the GitHub release and extract it, or clone the repository. Open PowerShell in the folder that contains `app.py` and check that `python --version` reports Python 3.13 before running the following commands.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

The first installation needs internet access for Python packages. After setup you can also double-click **Start Obelisk.cmd**. If the window fails to open, run the last PowerShell command to see the error.

1. Open Idle Obelisk Miner and leave the game visible. The recognition profile was calibrated against the v2.2.52 desktop landscape layout with a 3:2 game viewport at two window sizes.
2. In the companion, choose **Check screen recognition**. It reads the screen without clicking.
3. Set a fight limit and a maximum run time. A value of `0` disables the corresponding limit. Leave **Stop when the obelisk falls** selected to stop at the current target.
4. Choose **Start fighting**. The companion minimizes and brings the game forward. Keep the game in the foreground.
5. Press **F8** to stop. Switching focus away from the game also stops the session.

Start with a supervised one-fight session on a new setup. Game updates, modal dialogs and different layouts can affect recognition.

### Update

Close the companion. For a ZIP installation, extract the newer release into a new folder and repeat the installation commands there. For a clone on the default branch with a clean worktree, run `git pull --ff-only` and rerun the dependency installation command. Your settings are stored in `%LOCALAPPDATA%\ObeliskCompanion`, outside the source folder.

## How it works

- The fight runner confirms the menu and waits for two ready cooldown readings. During a fight it checks for increasing damage and uses two reliable zero timer readings to end the fight.
- It releases the mouse and confirms return navigation before starting another cycle. A damage stall gets one bounded retry. Focus loss, a moved game window during a hold, a stalled capture or a prolonged recognition failure stop the session.
- The interface shows the latest confirmed DPS. It estimates remaining attempts from the last ten readable completed fights and remaining time from observed start-to-start cycles. An HP value prefixed with `~` is estimated from the menu reading and the fight's damage.
- Opening the companion leaves it idle. Windows input handling, local settings and the shared UI sit outside the fight module so new features can have their own worker and recognition rules.

| Area | Implementation |
| --- | --- |
| Desktop UI and feature registration | `app.py`, `ui/`, `features.py` |
| Fight state, transitions and estimates | `obelisk/runner.py`, `obelisk/model.py` |
| Template matching and local OCR | `obelisk/vision.py`, `assets/obelisk/` |
| Window capture, input guards and local storage | `core/` |

## Verification

Run the offline checks from the project folder after installing the dependencies.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_vision.py
```

The v0.1.0 release candidate passed all 17 offline tests on Windows with Python 3.13. They cover number parsing and estimates, fight transitions with fake input, and recognition of navigation, menu, fight and return screens. The recognition fixtures keep the screen regions the detector reads. The tests use recorded data and fake input so they can run without a game session.

Previous supervised testing covered three live attempts, including two consecutive fights with cooldown and return navigation, stopping at the configured fight limit, and stopping on F8 and focus loss. A real victory and a continuous multi-hour session remain unverified. Some low-contrast fight-timer frames remain unreadable and are skipped.

## Local data and game references

OCR runs locally with bundled models from its Python package. Settings, session logs and diagnostic captures are written to `%LOCALAPPDATA%\ObeliskCompanion`. Review those files before sharing them because diagnostic captures can include game activity and session logs contain readings and timestamps.

`assets/obelisk/` contains six game UI crops required for recognition. `tests/fixtures/` contains minimized game-derived images used by the offline tests. Obelisk Companion is an independent project for Idle Obelisk Miner.
