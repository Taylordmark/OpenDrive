import sys
from PIL import Image

path = sys.argv[1]
pts = [tuple(map(int, p.split(','))) for p in sys.argv[2:]]
im = Image.open(path).convert("RGB")
for p in pts:
    print(p, im.getpixel(p))
