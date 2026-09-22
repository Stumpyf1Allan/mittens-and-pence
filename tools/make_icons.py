"""Build the app icon set from one square source image.

    python tools/make_icons.py path/to/artwork.png

Windows reads PNG-compressed entries inside an .ico, but every icon editor and every
Microsoft tool writes the small sizes as uncompressed BMP and only the 256px entry as
PNG. Pillow writes PNG for all of them. That is legal but unconventional, and this
build cannot be tested here — the executable is made on Windows — so the icon is
assembled by hand in the layout Windows has always been given.

A BMP entry inside an .ico is not a .bmp file: no file header, the height in the info
header is **doubled** (colour rows plus the AND mask), and the rows run bottom-up.
Getting any of that wrong produces a file that opens fine in a viewer and shows as a
blank square in the taskbar.
"""

from __future__ import annotations

import io
import pathlib
import struct
import sys

from PIL import Image

#: BMP below this, PNG at and above it — what Windows tooling produces.
PNG_FROM = 256
SIZES = (16, 24, 32, 48, 64, 128, 256)


def square(src: Image.Image, faint: int = 8) -> Image.Image:
    """Crop to the real artwork, wipe near-transparent haze, pad to a square."""
    import numpy as np
    im = src.convert("RGBA")
    a = np.array(im)[:, :, 3]
    w = im.width
    rows = np.where((a > 40).sum(axis=1) > w * 0.15)[0]
    cols = np.where((a > 40).sum(axis=0) > w * 0.15)[0]
    if len(rows) and len(cols):
        im = im.crop((int(cols.min()), int(rows.min()),
                      int(cols.max()) + 1, int(rows.max()) + 1))
    arr = np.array(im)
    # Faint speckle is invisible at full size and grey dirt at 16px.
    arr[:, :, 3][arr[:, :, 3] < faint] = 0
    im = Image.fromarray(arr, "RGBA")
    side = max(im.size)
    out = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    out.paste(im, ((side - im.width) // 2, (side - im.height) // 2), im)
    return out


def _bmp_entry(im: Image.Image) -> bytes:
    """One BMP image as an .ico stores it: doubled height, bottom-up, AND mask after."""
    w, h = im.size
    px = im.convert("RGBA").load()
    colour = bytearray()
    for y in range(h - 1, -1, -1):                 # bottom-up
        for x in range(w):
            r, g, b, a = px[x, y]
            colour += bytes((b, g, r, a))          # BGRA

    # The 1-bit AND mask is ignored for 32bpp images but must still be present and
    # 4-byte aligned, or the entry is malformed.
    row_bytes = ((w + 31) // 32) * 4
    mask = bytearray(row_bytes * h)

    header = struct.pack("<IiiHHIIiiII",
                         40,          # header size
                         w, h * 2,    # doubled: colour rows + mask rows
                         1, 32,       # planes, bits per pixel
                         0,           # BI_RGB, uncompressed
                         len(colour) + len(mask),
                         0, 0, 0, 0)
    return bytes(header + colour + mask)


def write_ico(square_img: Image.Image, path: pathlib.Path, sizes=SIZES) -> None:
    entries, blobs = [], []
    for n in sizes:
        im = square_img.resize((n, n), Image.LANCZOS)
        if n >= PNG_FROM:
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            blobs.append(buf.getvalue())
        else:
            blobs.append(_bmp_entry(im))
        entries.append(n)

    offset = 6 + 16 * len(entries)
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    for n, blob in zip(entries, blobs):
        dim = 0 if n >= 256 else n                 # 0 means 256 in an icon directory
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    for blob in blobs:
        out += blob
    path.write_bytes(bytes(out))


#: The macOS tiers worth carrying, and the pixel size each one holds. Pillow's ICNS
#: writer emits the whole ladder at full quality whatever you feed it — 3 MB for one
#: icon file — so the chunks are written here instead, with optimised PNGs.
ICNS_TIERS = (
    (b"icp4", 16), (b"icp5", 32), (b"ic11", 32), (b"ic12", 64),
    (b"ic07", 128), (b"ic13", 256), (b"ic08", 256), (b"ic14", 512), (b"ic09", 512),
)


def write_icns(square_img: Image.Image, path: pathlib.Path) -> None:
    """An ICNS is a magic word, a length, then typed chunks each holding a PNG."""
    chunks = bytearray()
    for tag, n in ICNS_TIERS:
        buf = io.BytesIO()
        square_img.resize((n, n), Image.LANCZOS).save(buf, format="PNG", optimize=True)
        blob = buf.getvalue()
        chunks += tag + struct.pack(">I", len(blob) + 8) + blob
    path.write_bytes(b"icns" + struct.pack(">I", len(chunks) + 8) + bytes(chunks))


def main(source: str) -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    res = root / "kestrel" / "resources"
    img = square(Image.open(source))
    print(f"square artwork: {img.size[0]}px")

    img.resize((512, 512), Image.LANCZOS).save(res / "kestrel-512.png")
    img.resize((256, 256), Image.LANCZOS).save(res / "kestrel.png")
    img.resize((64, 64), Image.LANCZOS).save(root / "kestrel" / "web" / "static" / "favicon.png")
    write_icns(img, res / "kestrel.icns")
    write_ico(img, res / "kestrel.ico")
    print("wrote kestrel.ico, .icns, .png, -512.png and web/static/favicon.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(
        pathlib.Path(__file__).resolve().parent.parent / "kestrel" / "resources" / "kestrel-512.png"))
