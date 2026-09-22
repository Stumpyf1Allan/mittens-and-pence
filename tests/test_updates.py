"""The update checker.

Everything here runs against a throwaway HTTP server on 127.0.0.1 and a temp data
directory. Nothing reaches the internet.

The https requirement is the one thing a local test server cannot satisfy without a
certificate authority, so the manifests here use real `https://127.0.0.1:…` addresses
— meaning every scheme check in the module runs exactly as it does in production —
and only the final socket is downgraded, in one clearly marked line of the fixture.
Relaxing the rule itself "just for tests" would be the wrong trade: it is the whole
reason a café wi-fi cannot decide what "the latest version" is.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import pathlib
import sys
import tempfile
import threading
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

os.environ.setdefault("MITTENS_DATA_DIR", tempfile.mkdtemp(prefix="mittens-upd-"))

import pytest  # noqa: E402

from kestrel import config, updates  # noqa: E402

#: Captured at import, before the fixture below blanks it for every test.
SHIPPED_MANIFEST_URL = config.UPDATE_MANIFEST_URL

PAYLOAD = b"not really an executable, but it hashes the same way" * 500
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


@pytest.fixture(autouse=True)
def never_the_real_internet(monkeypatch):
    """The shipped UPDATE_MANIFEST_URL points at a real GitHub address.

    Every test here must decide its own address, and none of them may quietly reach
    the network because a constant happened to be filled in.
    """
    monkeypatch.setattr(config, "UPDATE_MANIFEST_URL", "")
    monkeypatch.delenv("MITTENS_UPDATE_URL", raising=False)


@pytest.fixture()
def host(monkeypatch, tmp_path):
    """A server, a built file to download, and a plain-HTTP transport."""
    served = tmp_path / "served"
    served.mkdir()
    (served / "Mittens.and.Pence.exe").write_bytes(PAYLOAD)

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(served), **k)

        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # Every URL the module sees is https, so every scheme check runs for real...
    base = f"https://127.0.0.1:{httpd.server_address[1]}"
    plain = f"http://127.0.0.1:{httpd.server_address[1]}"

    def transport(url, timeout):
        # ...including this one, kept identical to the real opener's. Only the socket
        # underneath is plain, because a test server has no certificate.
        if not url.lower().startswith("https://"):
            raise updates.UpdateError("Updates are only fetched over https.")
        return urllib.request.urlopen(urllib.request.Request(
            url.replace(base, plain), headers={"User-Agent": updates.USER_AGENT}),
            timeout=timeout)

    monkeypatch.setattr(updates, "_open", transport)
    # Each test starts from a clean slate rather than inheriting the last one's verdict.
    monkeypatch.setattr(updates, "_STATE", dict(updates._STATE, checked_at=None,
                                                latest=None, download=None, file=None,
                                                status="idle", dismissed_version=None))
    monkeypatch.setattr(updates, "_persist", lambda: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    config.settings["update_url"] = f"{base}/latest.json"
    config.settings["auto_download_updates"] = False
    config.settings["check_for_updates"] = True

    class Host:
        url = base
        dir = served

        def publish(self, **over):
            m = {"version": "9.9.9", "published": "2026-09-14", "notes": "New things.",
                 "page": "https://example.invalid/releases",
                 "downloads": {updates.platform_key(): {
                     "url": f"{base}/Mittens.and.Pence.exe",
                     "filename": "Mittens and Pence.exe",
                     "sha256": DIGEST, "bytes": len(PAYLOAD)}}}
            m.update(over)
            (served / "latest.json").write_text(json.dumps(m), encoding="utf-8")
            return m

        def entry(self, **over):
            e = {"url": f"{base}/Mittens.and.Pence.exe", "filename": "Mittens and Pence.exe",
                 "sha256": DIGEST, "bytes": len(PAYLOAD)}
            e.update(over)
            return {updates.platform_key(): e}

    yield Host()
    httpd.shutdown()
    config.settings["update_url"] = ""


# ===========================================================================
# Comparing versions
# ===========================================================================

class TestVersions:
    @pytest.mark.parametrize("candidate,current,expected", [
        ("1.0.1", "1.0.0", True),
        ("1.0.0", "1.0.0", False),
        ("0.9.9", "1.0.0", False),
        # String comparison gets this one wrong, which is why it is here.
        ("1.0.10", "1.0.9", True),
        ("1.10.0", "1.9.0", True),
        ("2.0", "1.9.9", True),
        ("1.1.0", "1.1", False),         # 1.1 and 1.1.0 are the same version
        ("1.1.1", "1.1", True),
        ("v1.2.0", "1.1.0", True),       # a tag pasted in by mistake still reads
        ("1.0.0", "1.0.0-beta1", True),  # a release beats its own pre-release
        ("1.0.0-beta1", "1.0.0", False),
        ("1.0.0-beta2", "1.0.0-beta1", True),
    ])
    def test_ordering(self, candidate, current, expected):
        assert updates.is_newer(candidate, current) is expected

    @pytest.mark.parametrize("junk", ["banana", "", None, "1.0.0 (final)", "latest"])
    def test_nonsense_never_offers_an_update(self, junk):
        # A malformed manifest must not be able to push anybody to a download.
        assert updates.is_newer(junk, "1.0.0") is False
        assert updates.parse_version(junk) is None

    def test_the_shipped_version_is_readable(self):
        assert updates.parse_version(config.APP_VERSION) is not None


# ===========================================================================
# Talking to the manifest
# ===========================================================================

class TestChecking:
    def test_finds_a_newer_version(self, host):
        host.publish()
        st = updates.check(force=True)
        assert st["status"] == "available"
        assert st["latest"] == "9.9.9"
        assert st["download"]["sha256"] == DIGEST
        assert st["show_banner"] is True

    def test_an_older_manifest_offers_nothing(self, host):
        host.publish(version="0.0.1")
        st = updates.check(force=True)
        assert st["status"] == "current"
        assert st["download"] is None
        assert st["show_banner"] is False

    def test_dismissing_hides_that_version_only(self, host):
        host.publish(version="9.9.9")
        updates.check(force=True)
        updates.dismiss("9.9.9")
        assert updates.state()["show_banner"] is False
        host.publish(version="9.9.10")
        assert updates.check(force=True)["show_banner"] is True

    def test_no_address_means_the_whole_thing_is_off(self, host):
        config.settings["update_url"] = ""
        st = updates.check(force=True)
        assert st["status"] == "off"
        assert st["show_banner"] is False

    def test_an_empty_setting_falls_back_to_the_built_in_address(self, host, monkeypatch):
        # Settings says "leave it empty to use whatever this copy was built with", so
        # empty must mean *fall back*, not *off* — otherwise clearing the box on one
        # machine would silently switch that machine's updates off for good.
        monkeypatch.setattr(config, "UPDATE_MANIFEST_URL", f"{host.url}/latest.json")
        config.settings["update_url"] = ""
        host.publish()
        assert updates.check(force=True)["latest"] == "9.9.9"

    def test_a_web_page_is_not_a_manifest(self, host):
        (host.dir / "latest.json").write_text("<html>hello</html>", encoding="utf-8")
        st = updates.check(force=True)
        assert st["status"] == "error"
        assert st["download"] is None

    def test_a_missing_manifest_reads_as_not_published_yet(self, host):
        config.settings["update_url"] = f"{host.url}/nothing-here.json"
        st = updates.check(force=True)
        assert st["status"] == "error"
        assert "Nothing has been published" in st["last_error"]

    def test_a_manifest_with_no_version_is_refused(self, host):
        (host.dir / "latest.json").write_text(json.dumps({"notes": "hi"}), encoding="utf-8")
        assert updates.check(force=True)["status"] == "error"

    def test_an_enormous_response_is_not_read_as_a_manifest(self, host):
        (host.dir / "latest.json").write_bytes(b'{"version":"9.9.9","pad":"' +
                                               b"x" * (updates.MAX_MANIFEST_BYTES + 10) + b'"}')
        assert updates.check(force=True)["status"] == "error"


# ===========================================================================
# What may be downloaded
# ===========================================================================

class TestDownloadRules:
    def test_no_checksum_means_no_download(self, host):
        host.publish(downloads=host.entry(sha256=None))
        st = updates.check(force=True)
        assert st["status"] == "available"     # they are still told about it
        assert st["download"] is None          # but there is nothing to fetch

    def test_a_malformed_checksum_means_no_download(self, host):
        host.publish(downloads=host.entry(sha256="nope"))
        assert updates.check(force=True)["download"] is None

    def test_a_download_from_an_unrelated_host_is_refused(self, host):
        host.publish(downloads=host.entry(url="https://evil.example.net/thing.exe"))
        assert updates.check(force=True)["download"] is None

    def test_plain_http_downloads_are_refused(self, host):
        host.publish(downloads=host.entry(url="http://insecure.example.com/thing.exe"))
        assert updates.check(force=True)["download"] is None

    def test_the_manifest_cannot_choose_where_the_file_lands(self, host):
        # A filename is a name, not a path. Left alone, "../../autostart/x.exe" would
        # be a manifest writing wherever it liked on somebody's disk.
        host.publish(downloads=host.entry(filename="../../../../tmp/escaped.exe"))
        st = updates.check(force=True)
        assert st["download"]["filename"] == "escaped.exe"
        host.publish(version="9.9.8", downloads=host.entry(filename="C:\\Windows\\evil.exe"))
        st = updates.check(force=True)
        assert st["download"]["filename"] == "evil.exe"

    def test_a_platform_with_no_build_offers_no_download(self, host):
        host.publish(downloads={"plan9": {"url": f"{host.url}/Mittens.and.Pence.exe",
                                          "sha256": DIGEST, "filename": "x"}})
        st = updates.check(force=True)
        assert st["status"] == "available" and st["download"] is None

    def test_the_https_rule_is_real(self):
        with pytest.raises(updates.UpdateError) as e:
            updates._open("http://example.invalid/latest.json", 1)
        assert "https" in str(e.value).lower()

    def test_host_allowance_is_suffix_matched_not_substring_matched(self):
        ok = updates._host_allowed("https://github.com/a/b", "https://h.example/m.json")
        assert ok is True
        # "github.com.evil.net" must not pass for containing the allowed name.
        assert updates._host_allowed("https://github.com.evil.net/x",
                                     "https://h.example/m.json") is False
        assert updates._host_allowed("https://notgithub.com/x",
                                     "https://h.example/m.json") is False
        # The manifest's own host is always allowed.
        assert updates._host_allowed("https://h.example/x", "https://h.example/m.json") is True


# ===========================================================================
# Fetching, and the checksum that makes it worth doing
# ===========================================================================

class TestDownloading:
    def test_a_good_download_lands_verified(self, host):
        host.publish()
        updates.check(force=True)
        st = updates.download()
        assert st["status"] == "ready", st.get("last_error")
        f = pathlib.Path(st["file"])
        assert f.name == "Mittens and Pence.exe"
        assert f.read_bytes() == PAYLOAD
        assert not list(f.parent.glob("*.part"))

    def test_a_tampered_download_is_deleted_not_kept(self, host):
        # The one that matters. A file that fails its checksum must not be left on
        # disk looking like a legitimate update anybody could double-click.
        host.publish(downloads=host.entry(sha256="0" * 64))
        updates.check(force=True)
        st = updates.download()
        assert st["status"] == "error"
        assert "checksum" in st["last_error"].lower()
        assert "deleted" in st["last_error"].lower()
        assert not (updates.downloads_dir() / "Mittens and Pence.exe").exists()
        assert not list(updates.downloads_dir().glob("*.part"))

    def test_downloading_with_nothing_to_download_is_an_error_not_a_crash(self, host):
        host.publish(version="0.0.1")
        updates.check(force=True)
        assert updates.download()["status"] == "error"

    def test_an_already_verified_file_is_not_fetched_twice(self, host):
        host.publish()
        updates.check(force=True)
        first = updates.download()
        target = pathlib.Path(first["file"])
        stamp = target.stat().st_mtime_ns
        again = updates.download()
        assert again["status"] == "ready"
        assert target.stat().st_mtime_ns == stamp, "it re-downloaded a file it already had"

    def test_a_corrupted_local_copy_is_replaced(self, host):
        host.publish()
        updates.check(force=True)
        target = pathlib.Path(updates.download()["file"])
        target.write_bytes(b"half a file")
        st = updates.download()
        assert st["status"] == "ready"
        assert pathlib.Path(st["file"]).read_bytes() == PAYLOAD


# ===========================================================================
# Staying out of the way
# ===========================================================================

class TestManners:
    def test_turning_it_off_stops_the_check(self, host, monkeypatch):
        host.publish()
        config.settings["check_for_updates"] = False
        calls = []
        monkeypatch.setattr(updates, "_read_manifest",
                            lambda u: calls.append(u) or host.publish())
        updates.check()
        assert calls == []
        assert updates.state()["enabled"] is False

    def test_it_does_not_ask_again_within_the_interval(self, host, monkeypatch):
        host.publish()
        calls = []
        real = updates._read_manifest
        monkeypatch.setattr(updates, "_read_manifest",
                            lambda u: (calls.append(u), real(u))[1])
        updates.check(force=True)
        updates.check()
        updates.check()
        assert len(calls) == 1, "it asked the server more than once in 20 hours"

    def test_a_dead_server_is_an_error_state_not_an_exception(self, host):
        config.settings["update_url"] = "https://127.0.0.1:9/latest.json"
        st = updates.check(force=True)     # must not raise
        assert st["status"] == "error"
        assert st["last_error"]

    def test_a_download_never_replaces_anything(self, host):
        # Nothing in this module may write outside its own updates folder.
        src = pathlib.Path(updates.__file__).read_text(encoding="utf-8")
        for forbidden in ("sys.executable", "os.execv", "subprocess", "shutil.move"):
            assert forbidden not in src, f"{forbidden} has no business being here"

    def test_the_explanation_is_not_called_error(self, host):
        # Every API reply in this app goes through one helper that reads a top-level
        # "error" as "this request failed" and throws. A status payload carrying its
        # explanation under that key was swallowed whole: instead of "the download
        # didn't match its checksum", the screen said it couldn't read the status.
        config.settings["update_url"] = f"{host.url}/nothing-here.json"
        st = updates.check(force=True)
        assert st["status"] == "error"
        assert st["last_error"]
        assert "error" not in st, "a top-level 'error' key makes the whole reply look failed"

    def test_the_automatic_download_actually_happens(self, host):
        # It didn't. The startup check runs on a worker thread, and check() asked for
        # the download from inside it — which a single shared thread slot refused as
        # "already busy". The update then sat there waiting for a button press, which
        # is precisely what "download it as soon as it appears" is meant to avoid.
        import time
        host.publish()
        config.settings["auto_download_updates"] = True
        assert updates.start_check(force=True) is True
        for _ in range(100):
            if updates.state()["status"] == "ready":
                break
            time.sleep(0.1)
        st = updates.state()
        assert st["status"] == "ready", st.get("last_error") or st["status"]
        assert pathlib.Path(st["file"]).read_bytes() == PAYLOAD

    def test_a_check_and_a_download_do_not_block_each_other(self):
        assert updates._run("a", lambda: __import__("time").sleep(0.4)) is True
        assert updates._run("a", lambda: None) is False, "same job should not double-start"
        assert updates._run("b", lambda: None) is True, "a different job must not be blocked"

    def test_a_redirected_manifest_is_followed(self, host):
        # The address baked into the app is GitHub's permanent "newest release" link,
        # which 302s to wherever the asset actually lives. If redirects were not
        # followed, the whole update mechanism would be a 302 nothing could read.
        import http.server
        import threading
        import urllib.request

        host.publish()
        real = f"http://127.0.0.1:{host.url.rsplit(':', 1)[1]}/latest.json"
        hops = []

        class Bounce(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hops.append(self.path)
                self.send_response(302)
                self.send_header("Location", real)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        hop = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Bounce)
        threading.Thread(target=hop.serve_forever, daemon=True).start()
        bounce_url = f"http://127.0.0.1:{hop.server_address[1]}/releases/latest/download/latest.json"
        try:
            # The real code path, with only the socket left plain.
            saved = updates._open
            updates._open = lambda url, t: urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": updates.USER_AGENT}),
                timeout=t)
            try:
                data = updates._read_manifest(bounce_url)
            finally:
                updates._open = saved
        finally:
            hop.shutdown()
        assert hops, "the redirecting server was never asked"
        assert data["version"] == "9.9.9", "the redirect was not followed"

    def test_urllib_follows_cross_host_https_redirects(self):
        # The behaviour the test above relies on, asserted directly rather than assumed.
        import urllib.request
        opener = urllib.request.build_opener()
        assert any(isinstance(h, urllib.request.HTTPRedirectHandler)
                   for h in opener.handlers), "redirects are not being followed"


class TestTwoKindsOfMac:
    """A Mac is two machines wearing one name.

    An Apple Silicon build will not start at all on an Intel Mac, and an Intel build
    only runs on Apple Silicon once the person has installed Rosetta. So one "macos"
    download cannot be the right answer for everybody, and the manifest has to be able
    to say which processor a file is for — without breaking the releases already out
    there that only say "macos".
    """

    MANIFEST_URL = "https://github.com/a/b/releases/latest/download/latest.json"

    def _entry(self, name):
        return {"url": f"https://github.com/a/b/releases/download/v1/{name}",
                "filename": name, "sha256": "a" * 64, "bytes": 10}

    @pytest.mark.parametrize("machine,expected", [
        ("arm64", "arm64"), ("ARM64", "arm64"), ("aarch64", "arm64"),
        ("x86_64", "x86_64"), ("AMD64", "x86_64"), ("x64", "x86_64"),
        (" x86_64 ", "x86_64"),
        # Nothing we publish for. It must come back as "don't know" so the plain
        # platform entry is used, rather than be guessed into a name that happens
        # to have a download sitting behind it.
        ("armv7l", None), ("ppc64le", None), ("", None), ("i386", None),
    ])
    def test_what_the_machine_calls_its_processor(self, monkeypatch, machine, expected):
        from kestrel import updates
        monkeypatch.setattr(updates.platform, "machine", lambda: machine)
        assert updates.arch_key() == expected

    def test_the_specific_name_is_tried_before_the_plain_one(self, monkeypatch):
        from kestrel import updates
        monkeypatch.setattr(updates, "platform_key", lambda: "macos")
        monkeypatch.setattr(updates, "arch_key", lambda: "arm64")
        assert updates.download_keys() == ("macos-arm64", "macos")

    def test_an_unknown_processor_asks_only_for_the_plain_one(self, monkeypatch):
        from kestrel import updates
        monkeypatch.setattr(updates, "platform_key", lambda: "linux")
        monkeypatch.setattr(updates, "arch_key", lambda: None)
        assert updates.download_keys() == ("linux",)

    def test_each_mac_is_offered_its_own_build(self, monkeypatch):
        from kestrel import updates
        manifest = {"downloads": {
            "macos-arm64": self._entry("M-arm64.app.zip"),
            "macos-x86_64": self._entry("M-x86_64.app.zip"),
        }}
        monkeypatch.setattr(updates, "platform_key", lambda: "macos")

        monkeypatch.setattr(updates, "arch_key", lambda: "arm64")
        got = updates._download_entry(manifest, self.MANIFEST_URL)
        assert got["filename"] == "M-arm64.app.zip"

        monkeypatch.setattr(updates, "arch_key", lambda: "x86_64")
        got = updates._download_entry(manifest, self.MANIFEST_URL)
        assert got["filename"] == "M-x86_64.app.zip", \
            "an Intel Mac handed the Apple Silicon build gets a file that will not open"

    def test_an_older_manifest_still_works(self, monkeypatch):
        # Everything published so far says only "macos". Those releases have to keep
        # installing on a machine running this newer code, or an update breaks the
        # very mechanism that delivered it.
        from kestrel import updates
        manifest = {"downloads": {"macos": self._entry("Mittens.app.zip")}}
        monkeypatch.setattr(updates, "platform_key", lambda: "macos")
        monkeypatch.setattr(updates, "arch_key", lambda: "arm64")
        got = updates._download_entry(manifest, self.MANIFEST_URL)
        assert got and got["filename"] == "Mittens.app.zip"

    def test_a_manifest_for_the_other_processor_only_offers_nothing(self, monkeypatch):
        # Better to say "you have the newest version" than to hand somebody a file
        # that cannot run on their machine.
        from kestrel import updates
        manifest = {"downloads": {"macos-x86_64": self._entry("M-x86_64.app.zip")}}
        monkeypatch.setattr(updates, "platform_key", lambda: "macos")
        monkeypatch.setattr(updates, "arch_key", lambda: "arm64")
        assert updates._download_entry(manifest, self.MANIFEST_URL) is None

    def test_windows_is_unaffected(self, monkeypatch):
        from kestrel import updates
        manifest = {"downloads": {"windows": self._entry("Mittens.and.Pence.exe")}}
        monkeypatch.setattr(updates, "platform_key", lambda: "windows")
        monkeypatch.setattr(updates, "arch_key", lambda: "x86_64")
        got = updates._download_entry(manifest, self.MANIFEST_URL)
        assert got and got["filename"] == "Mittens.and.Pence.exe"


class TestPublishing:
    def test_the_repository_is_worked_out_from_the_address(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "publish", pathlib.Path(__file__).resolve().parent.parent / "tools" / "publish.py")
        pub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pub)
        assert pub.repo_from_manifest_url(
            "https://github.com/Stumpyf1Allan/mittens-and-pence"
            "/releases/latest/download/latest.json") == "Stumpyf1Allan/mittens-and-pence"
        assert pub.repo_from_manifest_url("") is None
        assert pub.repo_from_manifest_url("https://example.com/latest.json") is None

    def test_spaces_in_an_asset_name_become_full_stops(self):
        # GitHub does this to every uploaded asset. A hand-written URL that keeps the
        # spaces produces a release whose download 404s for everybody.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "publish2", pathlib.Path(__file__).resolve().parent.parent / "tools" / "publish.py")
        pub = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pub)
        url = pub.github_asset_url("a/b", "v1.1.0", "Mittens and Pence.exe")
        assert url.endswith("/v1.1.0/Mittens.and.Pence.exe")

    @staticmethod
    def _pub():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "publish3", pathlib.Path(__file__).resolve().parent.parent / "tools" / "publish.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize("filename,expected", [
        ("Mittens and Pence.exe", "windows"),
        ("Mittens and Pence-macos-arm64.app.zip", "macos-arm64"),
        ("Mittens and Pence-macos-x86_64.app.zip", "macos-x86_64"),
        ("Mittens-macos-apple-silicon.app.zip", "macos-arm64"),
        ("Mittens-macos-intel.app.zip", "macos-x86_64"),
        ("Mittens and Pence.app.zip", "macos"),
        ("Mittens.dmg", "macos"),
    ])
    def test_the_filename_says_which_machine_it_is_for(self, filename, expected):
        assert self._pub().platform_of(pathlib.PurePath(filename)) == expected

    @pytest.mark.parametrize("key,ok", [
        ("windows", True), ("macos", True), ("linux", True),
        ("macos-arm64", True), ("macos-x86_64", True), ("windows-arm64", True),
        ("mac", False), ("macos-m1", False), ("macos-arm", False),
        ("", False), ("macos-x86_64-v2", False),
    ])
    def test_which_keys_a_manifest_may_carry(self, key, ok):
        assert self._pub().is_key(key) is ok

    def test_half_a_platform_is_complained_about(self, tmp_path, capsys):
        """One Mac build and no fallback is the quiet failure in all this.

        Every Intel Mac would look for a key that is not there, find no plain "macos"
        to fall back to, and report "you have the newest version" — forever, and
        looking exactly like nothing being wrong.
        """
        pub = self._pub()
        f = tmp_path / "Mittens and Pence-macos-arm64.app.zip"
        f.write_bytes(b"x" * 16)
        out = tmp_path / "latest.json"
        pub.main(["--version", "9.9.9", "--repo", "a/b", "--out", str(out), str(f)])
        err = capsys.readouterr().err
        assert "x86_64" in err and "never be offered" in err

    def test_both_mac_builds_together_are_not_complained_about(self, tmp_path, capsys):
        pub = self._pub()
        out = tmp_path / "latest.json"
        args = ["--version", "9.9.9", "--repo", "a/b", "--out", str(out)]
        for arch in ("arm64", "x86_64"):
            f = tmp_path / f"Mittens and Pence-macos-{arch}.app.zip"
            f.write_bytes(b"x" * 16)
            args.append(str(f))
        pub.main(args)
        assert "never be offered" not in capsys.readouterr().err
        manifest = json.loads(out.read_text(encoding="utf-8"))
        assert set(manifest["downloads"]) == {"macos-arm64", "macos-x86_64"}
        for arch in ("arm64", "x86_64"):
            url = manifest["downloads"][f"macos-{arch}"]["url"]
            assert url.endswith(f"/Mittens.and.Pence-macos-{arch}.app.zip"), \
                "GitHub serves spaces as full stops; a manifest that keeps them 404s"

    @pytest.mark.parametrize("publishing,built,complain", [
        ("1.1.0", "1.1.0", False),
        ("1.1", "1.1.0", False),      # the same version, written two ways
        ("1.2.0", "1.1.0", True),     # manifest ahead of the file it describes
        ("1.0.0", "1.1.0", True),     # manifest behind it
    ])
    def test_the_manifest_version_must_match_the_build(self, tmp_path, capsys,
                                                       monkeypatch, publishing,
                                                       built, complain):
        """The check that was backwards, and the reason it matters.

        Every copy compares the manifest's version against the APP_VERSION baked into
        the file it is running — and the file being hashed here *is* that build. A
        manifest saying 1.2.0 over a binary built as 1.1.0 tells everybody who installs
        it that 1.2.0 is available: they download it, run it, and are told again. For
        ever. The old check passed that case silently and complained about the correct
        one.
        """
        pub = self._pub()
        from kestrel import config
        monkeypatch.setattr(config, "APP_VERSION", built)
        f = tmp_path / "Mittens and Pence.exe"
        f.write_bytes(b"x" * 16)
        pub.main(["--version", publishing, "--repo", "a/b",
                  "--out", str(tmp_path / "latest.json"), str(f)])
        said = "kestrel/config.py says this build is" in capsys.readouterr().err
        assert said is complain

    def test_the_shipped_address_is_a_manifest_not_a_page(self):
        # A release *page* would return HTML, which the checker rejects — correctly, but
        # confusingly. The address has to be the asset link.
        url = SHIPPED_MANIFEST_URL
        if not url:
            pytest.skip("no address baked in — updates are off in this build")
        assert url.startswith("https://")
        assert url.endswith(".json"), "that address returns a web page, not a manifest"
        assert "/releases/latest/download/" in url, \
            "pin the manifest to 'latest', or the address changes every release"


class TestAFailedCheckSaysWhatFailed:
    """An update check that can't reach anything has to say so, in those words.

    Two separate mistakes made it say the wrong thing. It treated 403 as "nothing has
    been published", which is what a blocked office network returns — so the person was
    told something about the publisher when the truth was about their wifi. And it never
    recorded the attempt, so Settings said "Last looked: never" underneath a message
    about a check that had just failed.
    """

    def test_a_blocked_network_is_not_an_unpublished_app(self, monkeypatch):
        import urllib.error
        from kestrel import updates

        def refuse(url, timeout):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

        monkeypatch.setattr(updates, "_open", refuse)
        with pytest.raises(updates.UpdateError) as e:
            updates._read_manifest("https://example.invalid/latest.json")
        assert "published" not in str(e.value).lower()
        assert "network" in str(e.value).lower() or "refused" in str(e.value).lower()

    def test_a_real_404_still_says_nothing_is_published(self, monkeypatch):
        import urllib.error
        from kestrel import updates

        def missing(url, timeout):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        monkeypatch.setattr(updates, "_open", missing)
        with pytest.raises(updates.UpdateError) as e:
            updates._read_manifest("https://example.invalid/latest.json")
        assert "published" in str(e.value).lower()

    def test_the_attempt_is_recorded_even_when_it_fails(self, monkeypatch):
        from kestrel import updates
        monkeypatch.setattr(updates, "_read_manifest",
                            lambda url: (_ for _ in ()).throw(updates.UpdateError("no")))
        monkeypatch.setattr(updates, "manifest_url", lambda: "https://example.invalid/l.json")
        updates._set(attempted_at=None, checked_at=None, status="idle")
        out = updates.check(force=True)
        assert out["status"] == "error"
        assert out["attempted_at"], "a failed check is still a check that happened"
        assert not out["checked_at"], "nothing answered, so there is no answer to date"

    def test_a_failure_is_retried_sooner_than_a_success(self):
        from kestrel import updates
        assert updates.RETRY_INTERVAL < updates.CHECK_INTERVAL, \
            "a laptop that was offline for five minutes should not wait a day"
