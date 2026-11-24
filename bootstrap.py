"""Keep this project's libraries isolated from the user's Python installation."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
if (ROOT / '.deps').is_dir():
    sys.path.insert(0, str(ROOT / '.deps'))

