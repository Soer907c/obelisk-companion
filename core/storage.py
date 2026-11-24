import json
import os
from pathlib import Path

RUNTIME = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ObeliskCompanion'

def load_settings(feature):
    try:
        data = json.loads((RUNTIME / (feature + '-settings.json')).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

def save_settings(feature, data):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = RUNTIME / (feature + '-settings.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)

