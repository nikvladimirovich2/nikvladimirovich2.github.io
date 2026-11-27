"""Collects the weekly maximum fuel prices published by the Serbian Ministry of Internal and Foreign Trade
(must.gov.rs, «Обавештење о највишој малопродајној цени деривата нафте») into gorivo/prices.json.

Run by .github/workflows/gorivo.yml (Fridays every 20 min around 15:00 + daily). Only new notices are fetched.
Exit code 0 also when nothing changed; 1 when the source page could not be read or parsed (the workflow
then fails visibly).
"""
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "gorivo", "prices.json")
BASE = "https://www.must.gov.rs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
LINK = re.compile(r'href="(/vest/(\d+)/obavestenje-o-najvisoj-maloprodajnoj-ceni-derivata-nafte[^"]*)"')
MONTHS = {
    # Standard genitive forms, plus a defensive "-ара"/"-ara" alternate genitive for the four months whose
    # stem ends in "р" (септембар/октобар/новембар/децембар) — the ministry's own notices are not always
    # grammatically consistent (seen: "12. септембара" and "19. септембра" in the SAME sentence, ids 15066/15210).
    "јануара": 1, "фебруара": 2, "марта": 3, "априла": 4, "маја": 5, "јуна": 6, "јула": 7, "августа": 8,
    "септембра": 9, "септембара": 9, "октобра": 10, "октобара": 10, "новембра": 11, "новембара": 11,
    "децембра": 12, "децембара": 12,
    "januara": 1, "februara": 2, "marta": 3, "aprila": 4, "maja": 5, "juna": 6, "jula": 7, "avgusta": 8,
    "septembra": 9, "septembara": 9, "oktobra": 10, "oktobara": 10, "novembra": 11, "novembara": 11,
    "decembra": 12, "decembara": 12,
}
MONTH_RE = "|".join(MONTHS)
DATE = rf"(\d{{1,2}})\.\s*({MONTH_RE})\s*(\d{{4}})"
PERIOD = re.compile(rf"(?:од|od)\s+(?:(\d{{1,2}})\s*(?:часова|časova)\s+)?{DATE}.{{0,40}}?(?:до|do)\s+(?:(\d{{1,2}})\s*(?:часова|časova)\s+)?{DATE}", re.I | re.S)
DIESEL = re.compile(r"EVRO\s*DIZEL[^0-9]{0,40}?(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:динара|dinara)", re.I)
BMB = re.compile(r"BMB\s*95[^0-9]{0,40}?(\d{2,3}(?:[.,]\d{1,2})?)\s*(?:динара|dinara)", re.I)


def get(url):
    err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "sr,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{url}: {err}")


def text_of(page):
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s)


def stamp(day, month, year, hour):
    d = dt.datetime(int(year), MONTHS[month.lower()], int(day), int(hour) if hour else 15)
    return d.strftime("%Y-%m-%dT%H:%M")  # Serbian local time (Europe/Belgrade)


def parse(page, url, nid):
    s = text_of(page)
    i = s.find("утврђује се")
    if i < 0:
        i = s.find("utvrđuje se")
    body = s[i:i + 900] if i >= 0 else s
    p, d, b = PERIOD.search(body), DIESEL.search(body), BMB.search(body)
    if not (p and d and b):
        raise ValueError(f"cannot parse {url}: period={bool(p)} diesel={bool(d)} bmb={bool(b)}")
    h1, d1, m1, y1, h2, d2, m2, y2 = p.groups()
    num = lambda x: float(x.replace(",", "."))  # noqa: E731
    return {"id": int(nid), "from": stamp(d1, m1, y1, h1), "to": stamp(d2, m2, y2, h2),
            "diesel": num(d.group(1)), "bmb95": num(b.group(1)), "url": BASE + url}


def main():
    data = {"source": BASE + "/vesti/", "prices": []}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            data = json.load(f)
    known = {p["id"] for p in data["prices"]}
    listing = get(BASE + "/vesti/")
    links = {int(n): u for u, n in LINK.findall(listing)}
    if not links:
        print("no notices found on the listing page (layout changed or blocked)", file=sys.stderr)
        return 1
    extra = [int(x) for x in os.environ.get("GORIVO_EXTRA_IDS", "").split(",") if x.strip()]
    new = 0
    for nid in sorted(set(links) | set(extra)):
        if nid in known:
            continue
        url = links.get(nid, f"/vest/{nid}/obavestenje-o-najvisoj-maloprodajnoj-ceni-derivata-nafte-evro-dizel-i-evro-premijum-bmb-95.php")
        data["prices"].append(parse(get(BASE + url), url, nid))
        new += 1
        time.sleep(1)
    if not new:
        print("no new notices")
        return 0
    data["prices"].sort(key=lambda p: p["from"])
    # one entry per period: a corrected notice for the same period replaces the earlier one
    by_from = {}
    for p in data["prices"]:
        by_from[p["from"]] = p
    data["prices"] = sorted(by_from.values(), key=lambda p: p["from"])
    data["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["schema"] = 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    last = data["prices"][-1]
    print(f"{new} new; latest {last['from']}..{last['to']}: diesel {last['diesel']}, BMB95 {last['bmb95']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
