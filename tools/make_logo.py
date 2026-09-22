#!/usr/bin/env python3
"""Turn a short video of the cat into the sprite sheet the sidebar animates.

    python tools/make_logo.py Mittens.mp4

Needs ffmpeg on the PATH, plus numpy, scipy and Pillow.

Why a sprite sheet rather than an animated WebP or a video element: an animated image
loops forever with no way to stop it, and a <video> brings a decoder, a play/pause
lifecycle and an audio track nobody wants, for a cat in a corner. One still image,
stepped by CSS, plays exactly when asked and rests on frame 0 the rest of the time.

Three things here are easy to get wrong, and each of them has been got wrong:

1. **Key by connectivity, not by colour.** The background is a flat cream, but so are
   the shadows under the banknotes and most of the notes themselves. A threshold on
   "how close is this pixel to cream" punches holes straight through the money. What
   actually separates background from subject is that the background is *reachable
   from the edge of the frame* without crossing the subject — so label the cream-ish
   pixels, keep only the blobs that touch the border, and everything else stays.

   `binary_closing` needs `border_value=1`. Without it the close pads with zeros, eats
   the outermost ring of the background, and the entire frame border is reclassified
   as subject — which silently makes the crop box the whole frame.

2. **One crop box for every frame.** Cropping each frame to its own content makes the
   cat swim around inside its own logo as it moves. The box is the union of all 120
   frames' content, plus a margin, so the picture is complete in every one of them and
   nothing is ever sliced at an edge. The first version of this sheet was cropped
   flush and the pile of money was cut off at x=0, which read as a rendering fault.

3. **Un-multiply the key colour at soft edges.** A feathered edge carries a fraction of
   the cream with it. Composited over a light panel nobody notices; over the dark
   theme it haloes. The observed colour is a*F + (1-a)*B, so the foreground is
   (C - (1-a)B) / a.

The frame is deliberately NOT square. The picture is about 1.44:1 — a cat on a
spread-out pile — and squaring it spends two fifths of every frame on empty space,
which forces the sheet to be rendered small and scaled up in the sidebar, and an
upscaled sprite looks soft. `styles.css` derives `--fw` from `--fh` using the ratio
printed at the end of this script; if you change the artwork, change that line too.
A test checks the two agree.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "kestrel" / "web" / "static" / "logo.webp"

COLS, ROWS = 12, 10          # 120 frames; styles.css must agree
FRAME_HEIGHT = 96            # source frames; the sidebar scales from here
MARGIN = 0.03                # breathing room around the subject
TOLERANCE = 26               # per-channel distance from the background colour
QUALITY, ALPHA_QUALITY = 62, 80
DENOISE = 0.6                # the source is a rendered illustration; the grain is
                             # compression, not detail, and WebP pays real bytes for it


def extract(video: pathlib.Path, into: pathlib.Path) -> list[pathlib.Path]:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is not on the PATH.")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-an", "-vsync", "0",
                    str(into / "f%04d.png")], check=True)
    frames = sorted(into.glob("*.png"))
    if len(frames) < COLS * ROWS:
        raise SystemExit(f"{video.name} has {len(frames)} frames; {COLS * ROWS} needed.")
    step = len(frames) // (COLS * ROWS)
    return frames[::step][:COLS * ROWS]


def key(path: pathlib.Path):
    """(foreground rgb, alpha) with the background removed and its colour divided out."""
    rgb = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    base = np.median(border, axis=0)

    labelled, _ = ndimage.label(np.abs(rgb - base).sum(axis=2) < TOLERANCE * 3)
    from_edge = (set(labelled[0]) | set(labelled[-1])
                 | set(labelled[:, 0]) | set(labelled[:, -1]))
    from_edge.discard(0)
    background = ndimage.binary_closing(np.isin(labelled, list(from_edge)),
                                        np.ones((3, 3)), border_value=1)

    alpha = ndimage.gaussian_filter((~background).astype(np.float32), 0.8)
    alpha = np.clip((alpha - 0.35) / 0.4, 0, 1)
    safe = np.maximum(alpha, 1e-3)[..., None]
    return np.clip((rgb - (1 - safe) * base) / safe, 0, 255), alpha


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("video")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        chosen = extract(pathlib.Path(a.video), pathlib.Path(tmp))
        print(f"  {len(chosen)} frames")
        keyed = [key(f) for f in chosen]

    H, W = keyed[0][1].shape
    union = np.max([al for _, al in keyed], axis=0)
    ys, xs = np.where(union > 0.2)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    px, py = int((x1 - x0 + 1) * MARGIN), int((y1 - y0 + 1) * MARGIN)
    bx0, by0 = x0 - px, y0 - py
    bw, bh = (x1 - x0 + 1) + 2 * px, (y1 - y0 + 1) + 2 * py
    fw = int(round(FRAME_HEIGHT * bw / bh))
    print(f"  subject {x1-x0+1}x{y1-y0+1} of {W}x{H}; box {bw}x{bh}; frame {fw}x{FRAME_HEIGHT}")

    sheet = Image.new("RGBA", (fw * COLS, FRAME_HEIGHT * ROWS), (0, 0, 0, 0))
    sx0, sy0 = max(bx0, 0), max(by0, 0)
    sx1, sy1 = min(bx0 + bw, W), min(by0 + bh, H)
    for n, (fg, al) in enumerate(keyed):
        canvas = np.zeros((bh, bw, 4), np.uint8)
        canvas[sy0 - by0:sy1 - by0, sx0 - bx0:sx1 - bx0, :3] = fg[sy0:sy1, sx0:sx1]
        canvas[sy0 - by0:sy1 - by0, sx0 - bx0:sx1 - bx0, 3] = al[sy0:sy1, sx0:sx1] * 255
        sheet.paste(Image.fromarray(canvas, "RGBA").resize((fw, FRAME_HEIGHT), Image.LANCZOS),
                    ((n % COLS) * fw, (n // COLS) * FRAME_HEIGHT))

    arr = np.asarray(sheet)
    softened = Image.fromarray(arr[..., :3]).filter(ImageFilter.GaussianBlur(DENOISE))
    Image.fromarray(np.dstack([np.asarray(softened), arr[..., 3]]), "RGBA").save(
        a.out, "WEBP", quality=QUALITY, method=6, alpha_quality=ALPHA_QUALITY)

    size = pathlib.Path(a.out).stat().st_size / 1024
    print(f"  written: {a.out}  ({size:.0f} KB)")
    print(f"\n  styles.css must say:\n"
          f"      --cols: {COLS};  --rows: {ROWS};\n"
          f"      --fw: calc(var(--fh) * {fw} / {FRAME_HEIGHT});")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
