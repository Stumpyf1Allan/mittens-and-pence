#!/usr/bin/env python3
"""Write the little JSON file that tells everybody else's copy a new version exists.

    python tools/publish.py --version 1.1.0 \
        --notes "Mortgages, and you can type a transaction in by hand." \
        "dist/send/Mittens and Pence.exe"

It hashes each file you give it, builds the manifest, and writes ``latest.json`` in the
project root. You then drag **both** the build and ``latest.json`` onto a new GitHub
release — the manifest is an attachment like any other, and
``/releases/latest/download/latest.json`` always points at the newest release's copy, so
the address baked into the app never changes.

The hash is the entire point. Without it a download is just bytes off the internet;
with it, the app can prove the file it received is byte-for-byte the file you built.
Which is also why this script refuses to guess: it hashes the real file on your disk,
and if the file you upload is not that file, the family's copies will reject it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Which platform a built file is for, taken from how it was named.
SUFFIX_PLATFORM = {".exe": "windows", ".dmg": "macos", ".pkg": "macos",
                   ".appimage": "linux", ".deb": "linux"}

PLATFORMS = ("windows", "macos", "linux")
#: A manifest key may name a processor as well as a platform, because a Mac is two
#: machines wearing one name and the wrong one of the two will not start at all.
ARCHES = ("arm64", "x86_64")
#: What people actually write in a filename, and what it means.
ARCH_IN_NAME = {"arm64": "arm64", "apple-silicon": "arm64", "aarch64": "arm64",
                "intel": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "amd64": "x86_64"}


def arch_of(name: str) -> str:
    """'-arm64', '-x86_64' or '' — read off the filename."""
    low = name.lower()
    for word, arch in ARCH_IN_NAME.items():
        if f"-{word}" in low or f"_{word}" in low or f".{word}." in low:
            return f"-{arch}"
    return ""


def is_key(key: str) -> bool:
    base, _, arch = key.partition("-")
    return base in PLATFORMS and (arch == "" or arch in ARCHES)


def platform_of(path: pathlib.Path) -> str:
    name = path.name.lower()
    arch = arch_of(name)
    if name.endswith(".app.zip") or "-macos" in name or "-mac." in name:
        return "macos" + arch
    if name.endswith(".zip"):
        # A bare .zip is ambiguous, and guessing wrong sends a Mac build to a PC.
        raise SystemExit(
            f"Can't tell what {path.name} is for. Name it ...-macos.zip, ...-windows.zip "
            f"or ...-linux.zip, or pass it as windows=path.")
    return SUFFIX_PLATFORM.get(path.suffix.lower(), "linux") + arch


def sha256_of(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def github_asset_url(repo: str, tag: str, filename: str) -> str:
    # GitHub replaces spaces in an uploaded asset's name with full stops, so
    # "Mittens and Pence.exe" is served as "Mittens.and.Pence.exe". Getting this
    # wrong produces a manifest whose download 404s, which is the single most
    # likely way for a release to go out broken.
    asset = re.sub(r"\s+", ".", filename.strip())
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def repo_from_manifest_url(url: str) -> str | None:
    """'https://github.com/me/app/releases/latest/download/latest.json' -> 'me/app'."""
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/releases/", (url or "").strip())
    return f"{m.group(1)}/{m.group(2)}" if m else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build latest.json for a new release.")
    ap.add_argument("files", nargs="+",
                    help="the built files, e.g. 'dist/send/Mittens and Pence.exe'. "
                         "Prefix with 'windows=' / 'macos=' / 'linux=' to be explicit.")
    ap.add_argument("--version", required=True, help="e.g. 1.1.0")
    ap.add_argument("--repo", help="owner/name on GitHub. Worked out from "
                                   "UPDATE_MANIFEST_URL if you leave it off.")
    ap.add_argument("--tag", help="release tag (default: v<version>)")
    ap.add_argument("--notes", default="", help="what changed, in plain English")
    ap.add_argument("--notes-file", help="read the notes from a file instead")
    ap.add_argument("--page", help="a web page about this release")
    ap.add_argument("--out", default=str(ROOT / "latest.json"))
    a = ap.parse_args(argv)

    from kestrel import updates  # noqa: E402  (needs ROOT on the path first)

    if updates.parse_version(a.version) is None:
        raise SystemExit(f"'{a.version}' isn't a version number. Try something like 1.1.0.")

    from kestrel import config
    if not a.repo:
        # The app already knows which repository it checks. Making somebody retype it
        # here is one more chance to typo it into a release whose download 404s.
        a.repo = repo_from_manifest_url(config.UPDATE_MANIFEST_URL)
        if a.repo:
            print(f"  repository: {a.repo}  (from UPDATE_MANIFEST_URL)")
    # Equal, not newer. This read the other way round for a long time and had it
    # exactly backwards, because the intuition — "a release should be newer than the
    # last one" — is about the wrong pair of numbers.
    #
    # The number in the manifest is compared, by every copy out there, against the
    # APP_VERSION baked into the build it is running. The file being hashed here IS
    # that build. So they have to match:
    #
    #   manifest 1.2.0, binary 1.1.0  ->  everyone who installs it is told 1.2.0 is
    #                                     available, downloads it, installs it, and is
    #                                     told again. For ever.
    #   manifest 1.0.0, binary 1.1.0  ->  nobody is ever offered it at all.
    #
    # Bumping APP_VERSION is a thing you do *before* building, not after publishing.
    # is_newer in both directions, not ==: 1.1 and 1.1.0 are the same version,
    # and only the comparison the app itself uses knows that.
    if (updates.is_newer(a.version, config.APP_VERSION)
            or updates.is_newer(config.APP_VERSION, a.version)):
        print(f"  ! You are publishing {a.version}, but kestrel/config.py says this "
              f"build is {config.APP_VERSION}.\n"
              f"    Every copy compares the manifest against the version baked into "
              f"the file it is running,\n"
              f"    so these two have to be the same number. Fix APP_VERSION, rebuild, "
              f"and publish again.", file=sys.stderr)

    tag = a.tag or f"v{a.version}"
    notes = a.notes
    if a.notes_file:
        notes = pathlib.Path(a.notes_file).read_text(encoding="utf-8").strip()

    downloads = {}
    for item in a.files:
        plat, _, raw = item.partition("=")
        if not raw:
            raw, plat = plat, ""
        path = pathlib.Path(raw).expanduser()
        if not path.is_file():
            raise SystemExit(f"No such file: {path}")
        key = plat.strip().lower() or platform_of(path)
        if not is_key(key):
            raise SystemExit(
                f"'{key}' isn't one of {', '.join(PLATFORMS)}, optionally followed by "
                f"-{ ' or -'.join(ARCHES) }.")
        if key in downloads:
            raise SystemExit(f"Two files given for {key}.")
        url = github_asset_url(a.repo, tag, path.name) if a.repo else f"PUT-THE-{key.upper()}-URL-HERE"
        downloads[key] = {"url": url, "filename": path.name,
                          "sha256": sha256_of(path), "bytes": path.stat().st_size}
        print(f"  {key:<8} {path.name}  ({path.stat().st_size / 1e6:.1f} MB)")

    # Half a platform is the quiet failure here. A release carrying macos-arm64 and
    # nothing else leaves every Intel Mac looking for a key that is not there, finding
    # no fallback, and reporting "you have the newest version" forever — which looks
    # exactly like nothing being wrong.
    for base in PLATFORMS:
        if base in downloads:
            continue
        have = {k.partition("-")[2] for k in downloads if k.startswith(base + "-")}
        if have and have != set(ARCHES):
            missing = ", ".join(sorted(set(ARCHES) - have))
            print(f"  ! {base}: only the {', '.join(sorted(have))} build is here.\n"
                  f"    A {base} machine on {missing} will find nothing and never be "
                  f"offered this.\n"
                  f"    Add it, or pass one of them as plain '{base}=' so it is the "
                  f"fallback.", file=sys.stderr)

    manifest = {
        "version": a.version,
        "published": dt.date.today().isoformat(),
        "notes": notes,
        "downloads": downloads,
    }
    if a.page:
        manifest["page"] = a.page
    elif a.repo:
        manifest["page"] = f"https://github.com/{a.repo}/releases/tag/{tag}"

    out = pathlib.Path(a.out)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\n  Written: {out}")
    if not a.repo:
        print("  Now replace every PUT-THE-...-URL-HERE with the real download link.")

    names = "\n           ".join(
        [str(pathlib.Path(f.partition("=")[2] or f)) for f in a.files] + [str(out)])
    print(f"""
  NEXT — on github.com, in your repository:

    1. Releases  ->  Draft a new release
    2. Choose a tag  ->  type {tag}  ->  "Create new tag on publish"
    3. Release title: {a.version}
    4. Drag BOTH of these onto the "Attach binaries" box:

           {names}

    5. Publish release

  latest.json goes up as an attachment like the .exe does. That is the whole
  update mechanism: this address always points at the newest release's copy of
  it, and never changes.""")
    if a.repo:
        print(f"\n      https://github.com/{a.repo}/releases/latest/download/latest.json")
    print("""
  Everybody else's copy will notice within a day, or straight away if they
  press "Check now" in Settings.""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
