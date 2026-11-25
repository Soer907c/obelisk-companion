# Obelisk Companion

A Windows desktop app for Idle Obelisk Miner in Google Play Games. It can repeat Obelisk fights, read relic results and reroll relics toward your targets. Screen recognition and OCR run locally.

## Install and start

1. Install Google Play Games, Idle Obelisk Miner and 64-bit Python 3.13 with Tkinter. Add Python to PATH.
2. Download and extract the **Source code (zip)** from [GitHub Releases](https://github.com/Soer907c/obelisk-companion/releases/latest), or clone the repository.
3. Open PowerShell in the extracted or cloned folder containing `app.py` and run:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

After setup, double-click **Start Obelisk.cmd** to launch. To create a shortcut with the green obelisk icon, run `& '.\Start Obelisk.cmd' --shortcut` in PowerShell. Recreate the shortcut if you move the app folder.

## Update

- **ZIP install.** Close Companion, extract the latest release into a new folder and repeat step 3 there.
- **Git clone.** Close Companion, run `git pull --ff-only` on `main`, rerun `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`, then start the app.
- Companion stores settings, logs and diagnostic captures in `%LOCALAPPDATA%\ObeliskCompanion`. A new source folder uses the same local settings.

## Use

Open Idle Obelisk Miner in the desktop landscape layout. Select a feature in Companion. Press **F8** while Companion has focus to start the selected feature, or use its Start button. While a run is active, **F8** or **Stop** stops it. Companion brings the game forward and stops automation if the game loses focus. It opens in the idle state.

- **Obelisk fights.** Use **Check screen recognition** to verify the game view. Set the fight and time limits, choose whether to stop when the obelisk falls, and start with a supervised one-fight session. A limit of `0` disables that limit.
- **Relic rerolls.** Open the relic result in the game and use **Read current result** to check recognition without clicking in the game. Set the divine minimums and gem budget, then use **Start auto-reroll**. A minimum of `0` ignores that divine. All selected minimums must be met in the same result. Each reroll costs **200 gems**. A **gem budget of 0 means no spending limit**. With this setting, automation can continue until the targets are met, the in-game balance is too low, or the session is stopped. An unreadable required count or an uncertain purchase stops the run.

The relic calculator uses the chest counts, divine minimums and cap you enter. With 5.1 million relic chests, three divine chests, a Supernova target of 15 and a cap of 38, the first estimate appeared in about one second on the development PC. Budget-only edits reuse the per-roll odds.

## Offline verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/check_vision.py
```

The tests cover fight transitions and cooldown, relic odds, screen recognition, and purchase guards with fake input. They require no game session. The repository includes the game UI crops needed for recognition and minimized test fixtures. Companion is an independent project for Idle Obelisk Miner.
