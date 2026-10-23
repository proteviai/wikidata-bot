"""Write statements to one Wikidata item through the Action API.

Written 2026-10-22 because toolforge QuickStatements was down when the book
item Q141805965 was created by hand (labels/descriptions only). This is the
first code in the repo that writes to Wikidata -- only on the user's
explicit order, one item per call, nothing in bulk. Logs in as KnutuxBot
(.env WIKIDATA_BOT_USERNAME = `KnutuxBot@KnutuxBot`, a bot password -- the
plain account name fails; WIKIDATA_BOT_PASSWORD). The pilot (887 edits,
2026-10-22) ran under Knutux@OCR.

    python -m av.wikidata_write whoami                # login check, no edits

    python -m av.wikidata_write book                  # dry run: print the JSON
    python -m av.wikidata_write book --upload         # one wbeditentity call
    python -m av.wikidata_write qs dataset/qs_X.txt [--upload] [--pause 9] [--max N]
        # the P1343 lines of a wikidata_qs.py file, one wbeditentity per item,
        # `maxlag=5` on every edit and a pause between them (Wikidata's own
        # throttling rules: respect maxlag, stay well under ~1 edit/s)

Only claims whose property the item does not have yet are sent (so a rerun
after a partial failure is safe); the sitelink likewise.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://www.wikidata.org/w/api.php"
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
# optional contact (e-mail) for the User-Agent, kept out of the public code
_contact = os.environ.get("WIKIDATA_BOT_CONTACT")
UA = {"User-Agent": "KnutuxBot/0.1 (https://www.wikidata.org/wiki/User:KnutuxBot"
                    + (f"; {_contact})" if _contact else ")")}

BOOK = "Q141805965"


def item(qid: str) -> dict:
    return {"value": {"entity-type": "item", "numeric-id": int(qid[1:])}, "type": "wikibase-entityid"}


def string(s: str) -> dict:
    return {"value": s, "type": "string"}


def monoling(lang: str, s: str) -> dict:
    return {"value": {"text": s, "language": lang}, "type": "monolingualtext"}


def year(y: int) -> dict:
    return {"value": {"time": f"+{y}-00-00T00:00:00Z", "timezone": 0, "before": 0, "after": 0,
                      "precision": 9, "calendarmodel": "http://www.wikidata.org/entity/Q1985727"},
            "type": "time"}


def claim(prop: str, datavalue: dict) -> dict:
    return {"mainsnak": {"snaktype": "value", "property": prop, "datavalue": datavalue},
            "type": "statement", "rank": "normal"}


# Same content as dataset/qs_book.txt (av/wikidata_qs.py `book`).
BOOK_CLAIMS = [
    claim("P31", item("Q571")),            # book
    claim("P1476", monoling("lt", "Lietuvos apgyventos vietos: pirmojo visuotinojo Lietuvos "
                                   "gyventojų 1923 m. surašymo duomenys")),
    claim("P577", year(1925)),
    claim("P407", item("Q9083")),          # Lithuanian
    claim("P407", item("Q150")),           # French
    claim("P291", item("Q4115712")),       # Kaunas
    claim("P123", item("Q12663462")),      # State Data Agency (successor of the 1925 bureau)
    claim("P361", item("Q6648449")),       # 1923 census
    claim("P953", string("https://lt.wikisource.org/wiki/Index:Lietuvos_apgyventos_vietos_1923.pdf")),
    claim("P973", string("https://www.epaveldas.lt/preview?id=C1B0002365127")),
]
BOOK_SITELINKS = {"ltwikisource": {"site": "ltwikisource", "title": "Lietuvos apgyventos vietos (1923)"}}


def qualifier(prop: str, datavalue: dict) -> dict:
    return {"snaktype": "value", "property": prop, "datavalue": datavalue}


def qs_value(prop: str, raw: str) -> dict:
    """One QuickStatements value -> API datavalue (the few types wikidata_qs emits)."""
    if raw.startswith("Q"):
        return item(raw)
    if raw.startswith('"'):
        return string(raw.strip('"'))
    if raw.startswith("+"):
        t, prec = raw.split("/")
        return {"value": {"time": t, "timezone": 0, "before": 0, "after": 0, "precision": int(prec),
                          "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}, "type": "time"}
    raise ValueError(f"{prop}: cannot parse {raw!r}")


def parse_qs(path: Path) -> list[tuple[str, dict]]:
    """(qid, claim) per P1343 line: `Q P1343 Qbook P.. v .. S.. v ..` tab-separated."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        f = line.split("	")
        if len(f) < 3 or not f[0].startswith("Q") or f[1] != "P1343":
            continue
        c = claim("P1343", item(f[2]))
        quals: dict[str, list] = {}
        ref: dict[str, list] = {}
        for p, v in zip(f[3::2], f[4::2]):
            if p.startswith("S"):
                ref.setdefault("P" + p[1:], []).append(qualifier("P" + p[1:], qs_value(p, v)))
            else:
                quals.setdefault(p, []).append(qualifier(p, qs_value(p, v)))
        if quals:
            c["qualifiers"] = quals
        if ref:
            c["references"] = [{"snaks": ref}]
        out.append((f[0], c))
    return out


def has_same(cur: dict, c: dict) -> bool:
    """Item already describes-by-source the same book with the same page qualifier."""
    book = c["mainsnak"]["datavalue"]["value"]["numeric-id"]
    page = c.get("qualifiers", {}).get("P304", [{}])[0].get("datavalue", {}).get("value")
    for x in cur.get("claims", {}).get("P1343", []):
        if x["mainsnak"].get("datavalue", {}).get("value", {}).get("numeric-id") != book:
            continue
        pages = [q["datavalue"]["value"] for q in x.get("qualifiers", {}).get("P304", [])]
        if page is None or page in pages or not pages:
            return True
    return False


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=6, status_forcelist=[429, 502, 503], backoff_factor=5,
        respect_retry_after_header=True, allowed_methods=frozenset({"GET", "POST"}))))
    return s


def login(s: requests.Session) -> None:
    # KnutuxBot's bot password (2026-10-23); Wikisource uploads keep Knutux@OCR
    tok = s.get(API, params={"action": "query", "meta": "tokens", "type": "login",
                             "format": "json"}).json()["query"]["tokens"]["logintoken"]
    r = s.post(API, data={"action": "login", "lgname": os.environ["WIKIDATA_BOT_USERNAME"],
                          "lgpassword": os.environ["WIKIDATA_BOT_PASSWORD"],
                          "lgtoken": tok, "format": "json"}).json()
    if r["login"]["result"] != "Success":
        sys.exit(f"login failed: {r}")


def live(s: requests.Session, qid: str) -> dict:
    return s.get(API, params={"action": "wbgetentities", "ids": qid, "props": "claims|sitelinks",
                              "format": "json"}).json()["entities"][qid]


def edit(s: requests.Session, csrf: str, qid: str, data: dict, summary: str) -> int:
    r = s.post(API, data={"action": "wbeditentity", "id": qid, "data": json.dumps(data),
                          "summary": summary, "token": csrf, "maxlag": 5, "bot": 1,
                          "format": "json"}).json()
    if "error" in r:
        if r["error"].get("code") == "maxlag":  # servers lagging: wait and retry
            time.sleep(float(r["error"].get("lag", 5)) + 5)
            return edit(s, csrf, qid, data, summary)
        if "actionthrottledtext" in json.dumps(r["error"]):
            # Account rate limit (a "newbie" on wikidata.org: 8 edits / 60 s until
            # autoconfirmed = 4 days + 50 edits; then 90 / 60 s). Seen 2026-10-22.
            print(f"{qid}: throttled, waiting 70 s", flush=True)
            time.sleep(70)
            return edit(s, csrf, qid, data, summary)
        if r["error"].get("code") == "permissiondenied":
            # semi-protected item (edit=autoconfirmed; Q5765678 Pašiaušės km.,
            # 2026-10-22): skip, log, retry once the account is autoconfirmed
            log = Path(__file__).resolve().parent / "output" / "dataset" / "wikidata_skipped.tsv"
            if not log.exists() or f"{qid}\t" not in log.read_text(encoding="utf-8"):
                with log.open("a", encoding="utf-8") as f:
                    f.write(f"{qid}\tpermissiondenied\t{summary}\n")
            print(f"{qid}: permission denied (protected item), skipped", flush=True)
            return None
        sys.exit(f"{qid}: edit failed: {r['error']}")
    return r["entity"].get("lastrevid")


def run_qs(a: argparse.Namespace) -> None:
    rows = parse_qs(Path(a.path))
    s = session()
    todo = []
    for qid, c in rows:
        if has_same(live(s, qid), c):
            print(f"skip {qid}: already has P1343 -> book")
        else:
            todo.append((qid, c))
        time.sleep(0.5)
    print(f"{len(rows)} lines, {len(todo)} to add")
    if a.max and len(todo) > a.max:
        todo = todo[:a.max]
        print(f"capped at --max {a.max}")
    if not todo:
        return
    if not a.upload:
        print(json.dumps(todo[0][1], ensure_ascii=False, indent=1))
        return
    login(s)
    csrf = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"}
                 ).json()["query"]["tokens"]["csrftoken"]
    for i, (qid, c) in enumerate(todo):
        name = c["qualifiers"]["P1810"][0]["datavalue"]["value"]
        rev = edit(s, csrf, qid, {"claims": [c]},
                   "aprašyta šaltinyje: Lietuvos apgyventos vietos (1925), lt.wikisource")
        print(f"{i + 1}/{len(todo)} {qid} {name} rev {rev}", flush=True)
        if i + 1 < len(todo):
            time.sleep(a.pause)


def main() -> None:
    # piped stdout is cp1252 on Windows; item names have ė/š (crashed 2026-10-23)
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["book", "qs", "whoami"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--pause", type=float, default=9.0,
                    help="seconds between edits (newbie limit 8/min -> >= 7.5 s)")
    ap.add_argument("--max", type=int, default=0, help="stop after this many edits (0 = all)")
    a = ap.parse_args()
    if a.what == "whoami":  # login check, no edits
        s = session()
        login(s)
        print(json.dumps(s.get(API, params={"action": "query", "meta": "userinfo",
                                            "uiprop": "groups|editcount|ratelimits",
                                            "format": "json"}).json()["query"]["userinfo"],
                         indent=1))
        return
    if a.what == "qs":
        run_qs(a)
        return
    qid, claims, sitelinks = BOOK, BOOK_CLAIMS, BOOK_SITELINKS

    s = session()
    cur = live(s, qid)
    have = set(cur.get("claims", {}))
    todo = [c for c in claims if c["mainsnak"]["property"] not in have]
    links = {k: v for k, v in sitelinks.items() if k not in cur.get("sitelinks", {})}
    skipped = [c["mainsnak"]["property"] for c in claims if c["mainsnak"]["property"] in have]
    data: dict = {}
    if todo:
        data["claims"] = todo
    if links:
        data["sitelinks"] = links
    print(f"{qid}: {len(todo)} claims to add, {len(links)} sitelinks; "
          f"already present: {sorted(set(skipped)) or '-'}")
    if not data:
        return
    if not a.upload:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return
    login(s)
    csrf = s.get(API, params={"action": "query", "meta": "tokens", "format": "json"}
                 ).json()["query"]["tokens"]["csrftoken"]
    rev = edit(s, csrf, qid, data, "1925 m. knygos aprašas (lt.wikisource transkripcija)")
    print(f"ok, revision {rev}: https://www.wikidata.org/wiki/{qid}")


if __name__ == "__main__":
    main()
