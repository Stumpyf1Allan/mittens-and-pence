"""The institution registry that backs the dropdown.

Everything the UI needs to answer two questions:
  1. Which bank / broker is this?
  2. What is the next step for it — link it, or upload a file?
"""

from __future__ import annotations

import functools
import json
import pathlib
import re
import unicodedata

from .. import config

DATA = pathlib.Path(__file__).resolve().parent / "data"


def _data_dir() -> pathlib.Path:
    """Bundled data survives PyInstaller by riding along in the resource dir."""
    frozen = config.resource_dir() / "institutions" / "data"
    if frozen.exists():
        return frozen
    return DATA


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    d = _data_dir()
    out = {"countries": {}, "by_id": {}, "providers": {}}
    for code, fname in (("GB", "institutions_gb.json"), ("ZA", "institutions_za.json")):
        payload = json.loads((d / fname).read_text(encoding="utf-8"))
        out["countries"][code] = payload
        for inst in payload["institutions"]:
            out["by_id"][inst["id"]] = inst
    out["providers"] = json.loads((d / "providers.json").read_text(encoding="utf-8"))
    return out


def countries() -> list[dict]:
    data = _load()
    return [
        {"code": c, "name": data["countries"][c]["country_name"],
         "currency": data["countries"][c]["currency"],
         "count": len(data["countries"][c]["institutions"])}
        for c in ("GB", "ZA")
    ]


def all_institutions(country: str | None = None) -> list[dict]:
    data = _load()
    if country:
        return list(data["countries"][country.upper()]["institutions"])
    out = []
    for c in ("GB", "ZA"):
        out.extend(data["countries"][c]["institutions"])
    return out


def get(institution_id: str) -> dict | None:
    return _load()["by_id"].get(institution_id)


def providers() -> dict:
    return _load()["providers"]


def provider(pid: str) -> dict | None:
    return _load()["providers"].get(pid)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def search(query: str, country: str | None = None, kinds: list[str] | None = None,
           limit: int = 40) -> list[dict]:
    """Fuzzy-ish search: exact prefix beats word-prefix beats substring."""
    q = _norm(query)
    pool = all_institutions(country)
    if kinds:
        pool = [i for i in pool if i["kind"] in kinds]
    if not q:
        return pool[:limit] if limit else pool

    scored = []
    for inst in pool:
        hay = _norm(inst["search"])
        name = _norm(inst["name"])
        aliases = {_norm(a) for a in inst.get("aka", [])}
        score = None
        if name == q:
            score = 0
        elif q in aliases:
            # "FNB" is First National Bank's own abbreviation, and should beat
            # "FNB Share Investing" merely starting with those letters.
            score = 0.5
        elif name.startswith(q):
            score = 1
        elif any(w.startswith(q) for w in hay.split()):
            score = 2
        elif q in hay:
            score = 3
        else:
            # initials: "fnb" -> "first national bank"
            initials = "".join(w[0] for w in name.split() if w)
            if initials.startswith(q):
                score = 2
        if score is not None:
            # Someone typing "fnb" or "absa" almost always wants the bank, not the
            # broker that shares its name, so banks break ties.
            kind_rank = 0 if inst["kind"] in ("bank", "building_society") else 1
            scored.append((score, kind_rank, len(inst["name"]), inst))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    out = [s[3] for s in scored]
    return out[:limit] if limit else out


#: injected by providers.load_all() — which provider ids have a client class
_built_check = None


def set_built_providers(fn):
    """Tell the registry which providers Mittens & Pence can actually drive.

    The catalogue lists providers Mittens & Pence *knows about* — including ones that are only
    documented, like Enable Banking. Offering one of those as the route and then failing
    to find a client for it is how you end up telling someone their bank supports Open
    Banking and handing them an Upload button in the same breath. `providers.load_all()`
    calls this, so selection and recommendation only ever land on something real.
    """
    global _built_check
    _built_check = fn


def _ensure_built_check():
    """Load the provider modules on first use, so a caller that never touched
    the providers package still gets truthful answers."""
    global _built_check
    if _built_check is None:
        try:
            from ..providers import base as _providers
            _providers.load_all()
        except Exception:
            pass


def provider_is_usable(pid: str) -> bool:
    """Self-serve, not deprecated, and there is code behind it."""
    p = provider(pid) or {}
    if not p.get("self_serve") or p.get("deprecated"):
        return False
    _ensure_built_check()
    return _built_check(pid) if _built_check is not None else True


def provider_is_free(pid: str) -> bool:
    """Usable *and* free to a household.

    TrueLayer is self-serve to sign up but its free console account is sandbox only —
    a real bank link needs a paid plan. Recommending it as "connect automatically"
    sends someone to a screen that warns them not to enter their bank details. So the
    recommendation only ever lands on a route that actually works for free; the paid
    one stays listed and labelled.
    """
    if not provider_is_usable(pid):
        return False
    return bool((provider(pid) or {}).get("free"))


def effective_recommendation(inst: dict) -> str:
    """What Mittens & Pence actually suggests, as opposed to what the data file says.

    An automatic route nobody can sign up for, one Mittens & Pence has no client for, and the
    South African aggregator route all defer to file upload. The picker badge and the
    detail panel must agree, so both go through here.
    """
    rec = inst["sync"]["recommended"]
    m = next((x for x in inst["sync"]["methods"] if x["method"] == rec), None)
    if not m:
        return "csv"
    if rec in ("csv", "manual"):
        return rec
    free = any(provider_is_free(p) for p in m.get("providers", []))
    if rec == "aggregator" or not free:
        return "csv" if any(x["method"] == "csv" for x in inst["sync"]["methods"]) else "manual"
    return rec


def next_step(institution_id: str) -> dict:
    """The heart of the dropdown: what happens after you pick this institution.

    Returns the ordered list of routes with a plain-English explanation of each,
    and which one Mittens & Pence suggests.
    """
    inst = get(institution_id)
    if not inst:
        return {"error": "unknown institution"}

    methods = []
    withheld = []
    for m in inst["sync"]["methods"]:
        _ensure_built_check()
        provs = []
        for p in m.get("providers", []):
            detail = {"id": p, **{k: v for k, v in (provider(p) or {}).items()}}
            detail["built"] = _built_check(p) if _built_check is not None else True
            detail["usable"] = provider_is_usable(p)
            detail["free"] = provider_is_free(p)
            provs.append(detail)
        # `ready` is what the app may actually choose; `self_serve_providers` stays as
        # the wider "you could sign up for these" list the detail panel shows.
        ready = [p["id"] for p in provs if p["usable"]]
        # A route that works without paying — what the recommendation is allowed to use.
        free = [p["id"] for p in provs if p["free"]]
        methods.append({
            **m,
            "providers_detail": provs,
            "ready_providers": (free + [p for p in ready if p not in free]),
            "free_providers": free,
            "paid_providers": [p for p in ready if p not in free],
            "self_serve_providers": [p["id"] for p in provs
                                     if p.get("self_serve") and not p.get("deprecated")],
            "documented_only": [p["id"] for p in provs
                                if p.get("self_serve") and not p.get("deprecated")
                                and not p["built"]],
            "needs_setup": m["method"] in ("openbanking", "direct_api", "aggregator"),
            "available_now": m["method"] in ("csv", "manual") or bool(ready),
        })

    # A route nobody can take without paying is not a route. Open Banking and the
    # aggregators reach these banks only through a registered provider, and none of them
    # is free to a household — so offering the option produced a screen that looked like
    # it could connect and then asked for a subscription. They are withheld rather than
    # deleted: the panel still says the bank supports it, so nobody is left wondering why
    # their bank appears not to.
    keep = []
    for m in methods:
        paid_only = (m["method"] in ("openbanking", "aggregator")
                     and not m["free_providers"])
        (withheld if paid_only else keep).append(m)
    methods = keep

    recommended = effective_recommendation(inst)

    from . import websites as _websites

    return {
        "institution": {k: inst[k] for k in
                        ("id", "name", "country", "kind", "kind_label", "group",
                         "currency", "account_types")},
        # Only ever a URL that was checked against the site itself; see websites.py.
        "website": _websites.website(inst["id"]),
        "withheld": [{"method": m["method"], "label": m["label"]} for m in withheld],
        "methods": methods,
        "recommended": recommended,
        "summary": _summary(inst, methods, recommended),
    }


def _summary(inst, methods, recommended) -> str:
    name = inst["name"]
    m = next((x for x in methods if x["method"] == recommended), None)
    if not m:
        return f"Add {name} manually."
    if m["method"] == "direct_api":
        p = (m.get("providers_detail") or [{}])[0]
        what = ("holdings, dividends and cash" if inst["kind"] in ("broker", "platform")
                else "balances and transactions")
        return (f"{name} publishes its own API for connecting to your own account — free, "
                f"no aggregator and no middleman. Set up a key at "
                f"{p.get('signup','their developer site')}, paste it in once, and Mittens & Pence "
                f"pulls {what} on its own from then on.")
    if m["method"] == "openbanking":
        return (f"{name} supports Open Banking. Mittens & Pence opens {name}'s own login page, you approve "
                f"read-only access, and balances and transactions flow in automatically. "
                f"You re-approve about every 90 days — the app reminds you.")
    if m["method"] == "aggregator":
        return (f"South Africa has no mandated open banking yet. {name} can be reached through a "
                f"data aggregator, but those onboard businesses rather than individuals — so the "
                f"practical route is a monthly statement upload, which takes under a minute.")
    if m["method"] == "csv":
        where = m.get("where") or "your online account's statements section"
        agg = next((x for x in methods if x["method"] == "aggregator"), None)
        why = ("A data aggregator could reach it, but South African aggregators onboard "
               "businesses rather than households — so the practical route is your own "
               "statement file. " if agg else
               f"{name} has no self-serve API, so Mittens & Pence uses your own statement file. ")
        return (why + f"Download it from {where}, drop it in, and Mittens & Pence maps the columns "
                f"once and remembers them. Re-uploading an overlapping file is safe — "
                f"duplicates are ignored.")
    return f"Enter {name} balances by hand; Mittens & Pence keeps the monthly history for you."


def stats() -> dict:
    out = {}
    for c in ("GB", "ZA"):
        items = all_institutions(c)
        kinds, routes = {}, {}
        for i in items:
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
            r = next_step(i["id"])["recommended"]
            routes[r] = routes.get(r, 0) + 1
        out[c] = {"total": len(items), "kinds": kinds, "routes": routes}
    return out
