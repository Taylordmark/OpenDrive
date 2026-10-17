"""Inline report images as data URIs: python build_report.py <report_src.html> <img_dir> <out.html>"""
import base64, io, json, re, sys
from pathlib import Path
from PIL import Image
src, imgs, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
NAMES = {"summit_blueriver": "Blue River, CO 9 (summer)", "summit_winter": "Blue River, CO 9 (winter)",
         "summit_dillon": "Dillon, Buffalo St", "green_valley": "Green Valley, Agua Dulce",
         "oahu": "Oahu, Waimanalo", "skagit": "Skagit, Concrete (SR 20)"}

def uri(name):
    im = Image.open(imgs / name).convert("RGB")
    if im.width > 1400:
        im = im.resize((1400, round(im.height * 1400 / im.width)), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

st = json.loads((imgs / "stats.json").read_text())
rows = "".join(
    f"<tr><td>{NAMES[s]}</td>" + "".join(f'<td class="n">{v["game"]["ground"][k]:.{p}f} / {v["ref"]["ground"][k]:.{p}f}</td>'
    for k, p in (("luma", 3), ("sat", 2), ("hue", 0), ("detail", 2))) + "</tr>" for s, v in st.items())
sbs = "".join(f'<figure><img src="{{{{img:sbs_{s}.jpg}}}}" alt="{NAMES[s]}: game vs reference" loading="lazy"><figcaption>{NAMES[s]}: reference {v["ref_file"]}</figcaption></figure>' for s, v in st.items())
html = src.read_text(encoding="utf-8").replace("{{stats}}", rows).replace("{{sbs}}", sbs)
html = re.sub(r"\{\{img:([^}]+)\}\}", lambda m: uri(m.group(1)), html)
out.write_text(html, encoding="utf-8"); print(out, round(out.stat().st_size / 1e6, 2), "MB")
