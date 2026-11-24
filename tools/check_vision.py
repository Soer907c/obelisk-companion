import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from obelisk.vision import Vision
from PIL import Image
v = Vision()
for path in sorted((bootstrap.ROOT / 'tests/fixtures').glob('*.png')):
    obs, _ = v.observe(Image.open(path))
    print(path.name, obs)

