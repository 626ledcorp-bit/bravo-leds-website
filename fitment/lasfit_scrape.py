#!/usr/bin/env python3
"""Scrape LASFIT's per-model bulb-size guides into lasfit_guides.db.

Extracts FACTUAL data only: year range, make, model, trim, position,
bulb size, and setup (halogen vs sealed/OEM LED). No marketing copy,
no product recommendations, no prices, no images.

Polite by design: max 1 request per 3 seconds, sequential, normal
browser User-Agent. Aborts immediately on 429/403.

Output:
  ~/workspace/626leds/fitment/lasfit_guides.db   (vehicles/fitment tables,
      same shapes as fitment.db)
  ~/workspace/marketplace-autoreply/lasfit_fitment.json  ("year|make|model"
      -> setup, for the bot's classify_lasfit)
  ~/workspace/626leds/fitment/lasfit_review.json  (unmapped positions,
      compounds, conflicts vs our other sources)
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from lxml import html

BASE = os.path.expanduser("~/workspace/626leds/fitment")
DB = os.path.join(BASE, "lasfit_guides.db")
CACHE_JSON = os.path.expanduser("~/workspace/marketplace-autoreply/lasfit_fitment.json")
REVIEW_JSON = os.path.join(BASE, "lasfit_review.json")
ALL_CACHE = os.path.expanduser("~/workspace/marketplace-autoreply/all_fitment.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DELAY = 3.0
HTML_CACHE_DIR = os.path.expanduser(
    "~/workspace/626leds/fitment/lasfit_html_cache")

GUIDE_URLS = [
    "https://www.lasfit.com/blogs/news/2019-ram-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2018-nissan-murano-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2009-2014-ford-f150-complete-replacement-led-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2018-ford-f150-replacement-led-light-bulb-sizes-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2012-volkswagen-passat-light-bulb-sizes-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2013-2017-honda-accord-light-bulb-sizes-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2014-2019-chevy-silverado-light-bulb-sizes-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2016-2019-nissan-sentra-light-bulb-sizes-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2016-2019-honda-pilot-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2009-2013-infiniti-g37-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2017-2019-kia-optima-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2014-2019-nissan-rogue-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2019-hyundai-tucson-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2019-nissan-altima-sport-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2010-2020-toyota-sienna-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2014-2017-hyundai-veloster-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2018-2020-hyundai-kona-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2018-2020-hyundai-ioniq-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2017-2020-subaru-impreza-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2012-2015-mercedes-benz-ml350-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2015-2018-volkswagen-jetta-light-bulb-size-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2017-2019-toyota-highlander-light-bulb-size",
    "https://www.lasfit.com/blogs/news/volkswagen-tiguan-bulb-size",
    "https://www.lasfit.com/blogs/news/2020-jeep-gladiator-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2019-2024-ram-2500-3500-bulb-sizes",
    "https://www.lasfit.com/blogs/news/2018-jeep-wrangler-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/chevy-silverado-2500-3500-hd-headlight-bulb-size",
    "https://www.lasfit.com/blogs/news/ford-f250-f350-xl-xlt-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2016-2023-toyota-tacoma-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2017-2022-jeep-grand-cherokee-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2018-2023-ford-f-150-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2025-ford-transit-150-250-350-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2022-nissan-frontier-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2020-2022-ford-f250-f350-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2017-2019-ford-f-250-f-350-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2018-2020-vw-golf-gti-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2016-2018-ram-1500-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2020-chevrolet-suburban-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2001-2004-toyota-tacoma-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2005-2011-toyota-tacoma-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2012-2015-toyota-tacoma-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2016-2020-dodge-durango-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2022-dodge-charger-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2014-2020-toyota-4runner-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2019-chevrolet-silverado-2500-3500-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2018-ford-edge-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2015-2020-gmc-yukon-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/2016-2023-toyota-tacoma-bulb-size-fitment-guide",
    "https://www.lasfit.com/blogs/news/ford-f150-led-bulb-size-guide-by-year",
    "https://www.lasfit.com/blogs/news/ford-f150-fog-light-bulb-size-guide",
    "https://www.lasfit.com/blogs/news/ford-f-150-3157-bulb-size-guide-3157-vs-3157a-3057-3457-4057-and-4157",
    "https://www.lasfit.com/blogs/news/2017-toyota-tacoma-headlight-bulb-size-led-upgrade-guide",
    "https://www.lasfit.com/blogs/news/2019-toyota-tacoma-headlight-bulb-size-led-upgrade-guide",
]

sys.path.insert(0, BASE)
from normalize import canon_bulb  # noqa: E402

# ---- known makes (from our own scrape data; fallback list included) ----
def _load_makes():
    makes = set()
    if os.path.exists(ALL_CACHE):
        with open(ALL_CACHE) as fh:
            for k in json.load(fh):
                parts = k.split("|")
                if len(parts) == 3:
                    makes.add(parts[1])
    makes |= {"acura", "alfa romeo", "audi", "bmw", "buick", "cadillac",
              "chevrolet", "chrysler", "dodge", "fiat", "ford", "genesis",
              "gmc", "honda", "hyundai", "infiniti", "jaguar", "jeep", "kia",
              "land rover", "lexus", "lincoln", "mazda", "mercedes-benz",
              "mini", "mitsubishi", "nissan", "porsche", "ram", "subaru",
              "tesla", "toyota", "volkswagen", "volvo"}
    return makes


MAKES = _load_makes()

YEAR_RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-\u2013\u2014]\s*((?:19|20)\d{2})\b")
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def parse_years(text):
    m = YEAR_RANGE_RE.search(text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = YEAR_RE.search(text or "")
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


SLUG_TRAILING_STRIP = {"bulb", "bulbs", "size", "sizes", "guide", "guides",
                     "chart", "by", "year", "years"}
SLUG_INTERIOR_STOP = {"vs", "and", "replacement", "complete", "led", "light",
                      "lights", "headlight", "headlights", "fog", "fogs",
                      "bulb", "bulbs", "size", "sizes", "upgrade", "fitment"}
SLUG_MAKE_ALIAS = {"chevy": "chevrolet", "vw": "volkswagen"}


def norm_model(model):
    m = re.sub(r"\s+", " ", (model or "").strip().lower())
    m = re.sub(r"\bf\s*150\b", "f-150", m)
    m = re.sub(r"\bf\s*250\b", "f-250", m)
    m = re.sub(r"\bf\s*350\b", "f-350", m)
    return m or None


def parse_slug(url):
    """'.../2019-2024-ram-2500-3500-bulb-sizes' ->
    (2019, 2024, 'ram', '2500 3500'). Most reliable make/model source."""
    slug = url.rstrip("/").split("/")[-1]
    toks = slug.split("-")
    y1 = y2 = None
    yrs = []
    while toks and re.fullmatch(r"(19|20)\d{2}", toks[0]):
        yrs.append(int(toks.pop(0)))
    if yrs:
        y1, y2 = yrs[0], yrs[-1]
    while toks and toks[-1] in SLUG_TRAILING_STRIP:
        toks.pop()
    norm = lambda s: s.replace("-", " ").lower()  # noqa: E731
    makes_norm = {norm(mk): mk for mk in MAKES}
    make, idx = None, 0
    if len(toks) >= 2 and f"{toks[0]} {toks[1]}" in makes_norm:
        make, idx = makes_norm[f"{toks[0]} {toks[1]}"], 2
    elif toks and toks[0] in makes_norm:
        make, idx = makes_norm[toks[0]], 1
    elif toks and toks[0] in SLUG_MAKE_ALIAS:
        make, idx = SLUG_MAKE_ALIAS[toks[0]], 1
    if not make:
        return y1, y2, None, None
    rest = toks[idx:]
    model_toks = []
    for i, t in enumerate(rest):
        nxt = rest[i + 1] if i + 1 < len(rest) else ""
        if t in SLUG_INTERIOR_STOP or nxt in ("bulb", "bulbs"):
            break
        model_toks.append(t)
    model = norm_model(" ".join(model_toks))
    return y1, y2, make, model


def parse_title_vehicle(title):
    """Fallback when the slug yields no model ('2019-ram-bulb-size-guide')."""
    t = YEAR_RANGE_RE.sub("", title or "")
    t = YEAR_RE.sub("", t)
    t = re.split(r"\s*\|\s*", t)[0]
    t = " " + t.lower().replace("-", " ") + " "
    for mk in sorted(MAKES, key=len, reverse=True):
        pat = re.sub(r"[^a-z0-9]+", " ", mk).strip()
        mm = re.search(r"(?<![a-z0-9])" + re.escape(pat) +
                       r"(?![a-z0-9])", t)
        if not mm:
            continue
        rest = t[mm.end():].strip()
        rest = re.sub(r"(?i)\b(bulb|bulbs|size|sizes|guide|upgrade|"
                      r"replacement|complete|chart|led|light|lights)\b", " ",
                      rest)
        model = norm_model(re.sub(r"\s+", " ", rest).strip(" -|"))
        return mk, model
    return None, None


POS_KEYWORDS_TRIM_GUARD = re.compile(
    r"(?i)\b(bulb|headlight|beam|fog|signal|light|brake|marker|dome|map|cargo|"
    r"license|reverse|drl|parking|tail|trunk)\b")


def split_trim_leftover(leftover):
    """'S/SV(NO FOG) Bulb Size' -> 'S/SV(NO FOG)'; position words -> None."""
    rest = (leftover or "").strip(" -|")
    # "Headlight Bulb Size (Low Beam & High Beam)" -> position section, not trim
    m = re.search(r"(?i)\b(bulb size|light bulb)\b\s*(\(.+\))", rest)
    if m and POS_KEYWORDS_TRIM_GUARD.search(m.group(2)):
        return None
    rest = re.sub(r"(?i)\s*(bulb sizes?.*|light bulbs?.*)$", "",
                  rest).strip(" -|")
    if not rest or POS_KEYWORDS_TRIM_GUARD.search(rest):
        return None
    return rest


def strip_ymmm(heading, make, model):
    """Remove 'YYYY-YYYY Make Model' words from the front of a heading,
    return whatever is left (trim candidate or position words)."""
    rest = YEAR_RANGE_RE.sub("", heading or "", count=1)
    words = (make + " " + model).replace("-", " ").lower().split()
    # page text sometimes uses a short make form ("VW Passat ...")
    aliases = {"volkswagen": ("vw",), "chevrolet": ("chevy",),
               "mercedes-benz": ("mercedes", "benz")}
    tokens = rest.replace("-", " ").split()
    i = 0
    for w in words:
        if i < len(tokens) and (tokens[i].lower() == w or
                                tokens[i].lower() in aliases.get(w, ())):
            i += 1
        else:
            break
    return " ".join(tokens[i:])


def parse_yt_trim(cell, make, model):
    """'2019–2024 RAM 2500 /3500 Tradesman' -> 'Tradesman'."""
    d = (cell or "").strip()
    d = re.sub(r"^\s*\d{4}\s*[–—-]\s*\d{4}\s*", "", d)
    d = re.sub(r"^\s*\d{4}\s*", "", d)
    wordset = set((make + " " + model).replace("-", " ").replace("/", " ").lower().split())
    tokens = d.replace("-", " ").replace("/", " ").split()
    rest = " ".join(t for t in tokens if t.lower() not in wordset)
    return rest or None


def parse_details_trim(details, make, model):
    d = (details or "").strip().rstrip(".")
    m = re.match(r"(?i)^for\s+", d)
    if not m:
        return None
    d = d[m.end():].strip()
    words = (make + " " + model).replace("-", " ").lower().split()
    tokens = d.replace("-", " ").split()
    i = 0
    for w in words:
        if i < len(tokens) and tokens[i].lower() == w:
            i += 1
        else:
            break
    return " ".join(tokens[i:]) or None


def map_position(label):
    """Lasfit position label -> list of canonical snake_case positions."""
    t = " " + (label or "").lower().replace("&", " and ") + " "
    has = lambda *ws: any(w in t for w in ws)
    if has("low beam") and has("high beam"):
        return ["high_low_beam"]
    if has("low beam") or (has("forward") and has("low")):
        return ["low_beam"]
    if has("high beam") or (has("forward") and has("high")):
        return ["high_beam"]
    if has("headlight") or has("projector"):
        # bare "Headlight"/"Headlight Bulbs" or "Factory LED Projector"
        return ["headlight"]
    if "fog" in t:
        return ["fog_light"]
    if "turn signal" in t or "turning signal" in t:
        if "front" in t:
            return ["front_turn_signal"]
        if "rear" in t:
            return ["rear_turn_signal"]
        return ["front_turn_signal", "rear_turn_signal"]
    if "3rd brake" in t or "third brake" in t or "center high" in t:
        return ["center_high_mount_stop"]
    if "brake" in t and "tail" in t:
        return ["brake_light"]  # tail shares the housing; note raw label
    if "brake" in t:
        return ["brake_light"]
    if "tail" in t:
        return ["tail_light"]
    if has("reverse", "back up", "backup"):
        return ["reverse_light"]
    if "license plate" in t:
        return ["license_plate"]
    if "cargo" in t:
        return ["trunk_cargo"]
    if "trunk" in t:
        return ["trunk_cargo"]
    if "map" in t:
        return ["map_light"]
    if "dome" in t:
        return ["dome_light"]
    if "side marker" in t or "side makere" in t:  # "makere" = LASFIT typo
        if "front" in t and "rear" in t:
            return ["front_side_marker", "rear_side_marker"]
        if "front" in t:
            return ["front_side_marker"]
        if "rear" in t:
            return ["rear_side_marker"]
        return ["front_side_marker", "rear_side_marker"]
    if "daytime running" in t or "day time running" in t or \
            re.search(r"(?<![a-z])drl(?![a-z])", t):
        return ["drl"]
    if "parking" in t:
        return ["parking_light"]
    if "glove" in t:
        return ["glove_box"]
    if "vanity" in t:
        return ["vanity_mirror"]
    if has("courtesy", "stepwell", "footwell") or "door light" in t:
        return ["courtesy_step"]
    if "reading" in t:
        return ["reading_light"]
    return []


def setup_of(type_raw, bulb_raw):
    t = (type_raw or "").upper()
    b = (bulb_raw or "").upper()
    if re.match(r"^D\d[SR]", b):
        return "xenon"
    if "LED" in t or "LED" in b:
        return "factory_led"
    return "halogen"


def canon_size(raw):
    """-> (canonical|None, setup_hint|None, note)."""
    if raw is None:
        return None, None, "empty"
    s = raw.strip()
    # parenthetical alternate: "H13 (9008)" -> "H13/9008"
    pm = re.match(r"^([A-Za-z0-9\/.\-]+?)\s*\(([^)]+)\)\s*$", s)
    if pm and re.match(r"^[A-Za-z0-9][A-Za-z0-9\/.\-]*$", pm.group(2).strip()):
        s = pm.group(1).strip() + "/" + pm.group(2).strip()
    parts = re.split(r"\s*/\s*", s)
    if len(parts) > 1:
        cands, setups, notes = [], set(), []
        for p in parts:
            c, st, _ = canon_bulb(p)
            if c:
                cands.append(c)
            if st == "factory_led":
                setups.add("factory_led")
            notes.append(f"{p}->{c or st}")
        if setups == {"factory_led"}:
            return None, "factory_led", "compound LED: " + s
        if len(cands) == len(parts):
            kinds = {"xenon" if re.match(r"^D\d[SR]$", c) else "halogen"
                     for c in cands}
            if kinds == {"halogen"} or kinds == {"xenon"}:
                return s, None, "compound: " + s
        return None, None, "compound-unresolved: " + s
    c, st, detail = canon_bulb(s)
    if st == "factory_led":
        return None, "factory_led", detail
    if c:
        return c, None, ""
    return None, None, detail


class RateLimiter:
    def __init__(self, delay):
        self.delay = delay
        self.last = 0.0

    def wait(self):
        dt = time.time() - self.last
        if dt < self.delay:
            time.sleep(self.delay - dt)
        self.last = time.time()


def fetch(url, limiter):
    slug = url.rstrip("/").split("/")[-1]
    cache_path = os.path.join(HTML_CACHE_DIR, slug + ".html")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    limiter.wait()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code in (429, 403):
            raise SystemExit(f"HARD STOP: HTTP {e.code} from lasfit.com — "
                             f"stopping scrape, no retries.")
        raise
    os.makedirs(HTML_CACHE_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return body


def _table_ymmm_trim(tbl, title, make, model, s_y1, s_y2, review, url):
    h = tbl.xpath("preceding::*[self::h1 or self::h2 or self::h3 "
                  "or self::h4][1]")
    heading = h[0].text_content().strip() if h else title
    y1, y2 = parse_years(heading)
    if y1 is None:
        y1, y2 = parse_years(title)
    if y1 is None:
        y1, y2 = s_y1, s_y2
    if y1 is None:
        review.append({"kind": "no_years", "url": url,
                       "heading": heading[:120]})
        return None, None, None
    return y1, y2, split_trim_leftover(strip_ymmm(heading, make, model))


def _parse_2col_table(trs):
    """[(position, bulb)] from headerless 2-col tables, or []."""
    out = []
    for tr in trs:
        cells = [re.sub(r"\s+", " ", c.text_content()).strip()
                 for c in tr.xpath(".//td|.//th")]
        if len(cells) != 2:
            continue
        pos_raw = cells[0]
        bulb_raw = re.sub(r"(?i)\s*bulbs?\s*$", "", cells[1]).strip()
        if not POS_KEYWORDS_TRIM_GUARD.search(pos_raw):
            continue
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9/\-.]*$", bulb_raw):
            continue
        out.append((pos_raw, bulb_raw))
    return out


def _parse_multicol_table(trs):
    """[(trim, position, bulb)] from tables whose columns are trim
    variants, or []."""
    out = []
    grid = []
    for tr in trs:
        cells = [re.sub(r"\s+", " ", c.text_content()).strip()
                 for c in tr.xpath(".//td|.//th")]
        if any(cells):
            grid.append(cells)
    if len(grid) < 2 or len(grid[0]) < 3:
        return out
    hdr = grid[0]
    if hdr[0] and not POS_KEYWORDS_TRIM_GUARD.search(hdr[0]):
        return out
    trims = []
    for h in hdr[1:]:
        h2 = re.sub(r"(?i)\s*bulbs?\s*$", "", h).strip()
        if (not h2 or
                re.match(r"^[A-Za-z0-9][A-Za-z0-9/\-.]*$", h2)):
            return out  # header looks like bulb data, not a trim label
        trims.append(h2)
    for row in grid[1:]:
        if len(row) < len(hdr):
            continue
        pos_raw = row[0]
        if not POS_KEYWORDS_TRIM_GUARD.search(pos_raw):
            continue
        for trim, cell in zip(trims, row[1:]):
            bulb_raw = re.sub(r"(?i)\s*bulbs?\s*$", "", cell).strip()
            if (not bulb_raw or
                    re.match(r"(?i)^(no|n/?a|none|#)$", bulb_raw)):
                continue
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9/\-.]*$", bulb_raw):
                continue
            out.append((trim, pos_raw, bulb_raw))
    return out


def _unescape_literal_hex(text):
    """LASFIT's CMS double-escapes some UTF-8; decode those runs back."""

    def _dec(m):
        seq = m.group(0)
        try:
            raw = bytes(int(seq[i + 2:i + 4], 16)
                        for i in range(0, len(seq), 4))
            return raw.decode("utf-8", "ignore") or seq
        except Exception:  # noqa: BLE001 - keep original on failure
            return seq

    return re.sub(r"(?:\\x[0-9a-fA-F]{2})+", _dec, text or "")


def parse_guide(url, page_html, review):
    page_html = _unescape_literal_hex(page_html)
    doc = html.fromstring(page_html)
    art = doc.xpath("//article")
    art = art[0] if art else doc
    h1 = art.xpath(".//h1")
    title = h1[0].text_content().strip() if h1 else ""
    s_y1, s_y2, make, model = parse_slug(url)
    rows = []  # (y1, y2, make, model, trim, pos_raw, type_raw, bulb_raw)
    if not model:
        mk2, mo2 = parse_title_vehicle(title)
        make = make or mk2
        model = model or mo2
    if not make or not model:
        review.append({"kind": "no_vehicle", "url": url,
                       "title": title[:120]})
        return rows

    tables = art.xpath(".//table")
    parsed_tables = 0
    for tbl in tables:
        trs = tbl.xpath(".//tr")
        if not trs:
            continue
        hdr = [c.text_content().strip().lower()
               for c in trs[0].xpath(".//th|.//td")]
        try:
            i_pos = next(i for i, h in enumerate(hdr) if "position" in h)
            i_bulb = next(i for i, h in enumerate(hdr) if "bulb size" in h)
        except StopIteration:
            i_pos = i_bulb = None
        if i_pos is None:
            added = _parse_2col_table(trs)
            added_multi = []
            if not added:
                added_multi = _parse_multicol_table(trs)
            if added or added_multi:
                y1, y2, trim = _table_ymmm_trim(
                    tbl, title, make, model, s_y1, s_y2, review, url)
                if y1 is not None:
                    for pos_raw, bulb_raw in added:
                        rows.append((y1, y2, make, model, trim, pos_raw,
                                     "", bulb_raw))
                    for mtrim, pos_raw, bulb_raw in added_multi:
                        rows.append((y1, y2, make, model, mtrim, pos_raw,
                                     "", bulb_raw))
                    parsed_tables += 1
            continue
        i_type = next((i for i, h in enumerate(hdr)
                       if h.strip() == "type"), None)
        i_det = next((i for i, h in enumerate(hdr)
                      if "fitment detail" in h), None)
        h = tbl.xpath("preceding::*[self::h1 or self::h2 or self::h3 "
                      "or self::h4][1]")
        heading = h[0].text_content().strip() if h else title
        y1, y2 = parse_years(heading)
        if y1 is None:
            y1, y2 = parse_years(title)
        if y1 is None:
            y1, y2 = s_y1, s_y2
        if y1 is None:
            review.append({"kind": "no_years", "url": url,
                           "heading": heading[:120]})
            continue
        trim = split_trim_leftover(strip_ymmm(heading, make, model))
        parsed_tables += 1
        ncols = len(hdr)
        has_yt_col = bool(re.search(r"(?i)(year|trim)", hdr[0])) and ncols > 2
        prev_cells = None
        for tr in trs[1:]:
            cells = [c.text_content().strip()
                     for c in tr.xpath(".//td|.//th")]
            if prev_cells is not None and len(cells) < ncols:
                cells = prev_cells[:ncols - len(cells)] + cells
            prev_cells = cells
            if len(cells) <= max(i_pos, i_bulb):
                continue
            pos_raw = cells[i_pos]
            bulb_raw = cells[i_bulb]
            type_raw = cells[i_type] if i_type is not None and \
                len(cells) > i_type else ""
            det = cells[i_det] if i_det is not None and \
                len(cells) > i_det else ""
            t2 = trim
            if has_yt_col and cells[0].strip():
                yt_trim = parse_yt_trim(cells[0], make, model)
                if yt_trim:
                    t2 = yt_trim
            if not t2:
                t2 = parse_details_trim(det, make, model)
            rows.append((y1, y2, make, model, t2, pos_raw, type_raw, bulb_raw))

    if not parsed_tables:
        y1, y2 = parse_years(title)
        if y1 is None:
            y1, y2 = s_y1, s_y2
        if y1 is None:
            review.append({"kind": "no_years_pformat", "url": url,
                           "title": title[:120]})
            return rows
        BULLET = "•"
        ESC_BULLET = "\\xe2\\x80\\xa2"
        for hh in art.xpath(".//h2|.//h3|.//h4"):
            for sib in hh.itersiblings():
                if sib.tag in ("h1", "h2", "h3", "h4", "table", "ul"):
                    break
                if sib.tag not in ("p", "div"):
                    continue
                txt = sib.text_content().replace(ESC_BULLET, BULLET)
                if "uses:" in txt:
                    blob = txt.replace(ESC_BULLET, BULLET)
                    items = [i for i in
                             re.split(r"•|\r?\n", blob)
                             if i.strip()]
                    for item in items:
                        item = re.sub(r"\s+", " ", item).strip()
                        m = re.match(
                            r"^(?:\d{4}\s*[-–]\s*\d{4}\s+)?(.+?)\s+"
                            r"uses?:\s*(.+?)\s*$", item)
                        if not m:
                            continue
                        pos_raw = strip_ymmm(m.group(1), make, model)
                        bulb_raw = m.group(2)
                        bulb_raw = re.sub(r"(?i)\s*\(.+?\)\s*", " ", bulb_raw)
                        bulb_raw = re.split(r"\s+or\s+", bulb_raw,
                                             maxsplit=1)[0]
                        bulb_raw = re.sub(r"(?i)\s*bulbs?\s*$", "",
                                          bulb_raw).strip(" -#")
                        if not bulb_raw or re.match(
                                r"(?i)^(n/?a|none|#)$", bulb_raw):
                            continue
                        # "rear turn signal/ brake/ tail light" -> parts
                        for part in re.split(r"\s*/\s*", pos_raw):
                            rows.append((y1, y2, make, model, None,
                                         part.strip(), "", bulb_raw))
                    continue
                # "Position: Bulb" <p> lines
                for line in txt.splitlines():
                    line = re.sub(r"\s+", " ", line).strip()
                    m = re.match(
                        r"^(.+?)\s*:\s*([A-Za-z0-9][A-Za-z0-9/\-. ]*?)\s*"
                        r"(\(.+\))?\s*$", line)
                    if not m:
                        continue
                    pos_raw, bulb_raw = m.group(1).strip(), m.group(2).strip()
                    if not re.match(
                            r"(?i)^([a-z0-9]+[a-z0-9/\-]*|led(\s*\w+)?)$",
                            bulb_raw):
                        continue
                    rows.append((y1, y2, make, model, None, pos_raw, "",
                                 bulb_raw))
    return rows


def main():
    limiter = RateLimiter(DELAY)
    review = []
    all_rows = []
    ok, failed = 0, []
    for i, url in enumerate(GUIDE_URLS):
        try:
            page_html = fetch(url, limiter)
        except SystemExit as e:
            print(str(e), flush=True)
            break
        except Exception as e:  # noqa: BLE001 - one bad page must not kill run
            failed.append({"url": url, "error": f"{type(e).__name__}: {e}"})
            continue
        try:
            rows = parse_guide(url, page_html, review)
        except Exception as e:  # noqa: BLE001
            failed.append({"url": url, "error": f"parse: {e}"})
            continue
        for r in rows:
            all_rows.append((url,) + r)
        ok += 1
        print(f"[{i+1}/{len(GUIDE_URLS)}] {url.split('/')[-1]}: "
              f"{len(rows)} rows", flush=True)

    # ---- normalize into sqlite ----
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("CREATE TABLE vehicles(vehicle_id TEXT PRIMARY KEY, year INT,"
                " make TEXT, model TEXT, trim TEXT, source TEXT)")
    cur.execute("CREATE TABLE fitment(vehicle_id TEXT, position TEXT,"
                " position_raw TEXT, bulb_size_raw TEXT, bulb_size TEXT,"
                " flag TEXT, note TEXT, hintnote TEXT)")
    cur.execute("CREATE INDEX idx_fit_vehicle ON fitment(vehicle_id)")

    vehicles = {}   # (year, make, model, trim) -> vehicle_id
    seen_fit = {}   # (vehicle_id, position) -> (bulb_raw, setup)
    n_veh, n_fit = 0, 0
    # (year, make, model) -> setups seen on headlight positions only
    # (interior/map LEDs must not flip a halogen-headlight vehicle)
    headlight_setups = {}

    for (url, y1, y2, make, model, trim, pos_raw, type_raw, bulb_raw) in all_rows:
        positions = map_position(pos_raw)
        if not positions and not (type_raw or "").strip():
            # swapped columns, e.g. "| 9005 | Halogen |": the bulb code sits
            # in the position field and the type word in the bulb field
            mtype = re.match(r"(?i)^\s*(halogen|xenon|hid|led)\s*$",
                             bulb_raw or "")
            swapped = (pos_raw or "").strip()
            if mtype and re.match(r"^[A-Za-z0-9][A-Za-z0-9/\-.]*$", swapped):
                pos_raw, type_raw, bulb_raw = "Headlight", mtype.group(1), \
                    swapped
                positions = map_position(pos_raw)
        if not positions:
            review.append({"kind": "unmapped_position", "url": url,
                           "position": pos_raw, "bulb": bulb_raw})
            continue
        size, led_hint, note = canon_size(bulb_raw)
        setup = setup_of(type_raw, bulb_raw)
        if led_hint == "factory_led":
            setup = "factory_led"
        if size is None and setup == "halogen":
            review.append({"kind": "unresolved_bulb", "url": url,
                           "position": pos_raw, "bulb": bulb_raw,
                           "note": note})
            continue
        for year in range(y1, y2 + 1):
            vkey = (year, make, model, trim or "")
            vid = vehicles.get(vkey)
            if vid is None:
                vid = f"lasfit:{make}:{model}:{year}:{trim or 'base'}"
                vid = re.sub(r"\s+", "_", vid)[:180]
                vehicles[vkey] = vid
                cur.execute("INSERT INTO vehicles VALUES (?,?,?,?,?,?)",
                            (vid, year, make, model, trim, url))
                n_veh += 1
            for pos in positions:
                if pos in ("low_beam", "high_beam", "headlight",
                           "high_low_beam"):
                    headlight_setups.setdefault(
                        (year, make, model), set()).add(setup)
                fkey = (vid, pos)
                if fkey in seen_fit:
                    prev = seen_fit[fkey]
                    if prev != (bulb_raw, setup):
                        review.append({"kind": "within_lasfit_conflict",
                                       "url": url, "year": year, "make": make,
                                       "model": model, "trim": trim,
                                       "position": pos, "prev": prev,
                                       "new": (bulb_raw, setup)})
                    continue
                seen_fit[fkey] = (bulb_raw, setup)
                flag = "sealed_oem_led" if setup == "factory_led" else ""
                cur.execute(
                    "INSERT INTO fitment VALUES (?,?,?,?,?,?,?,?)",
                    (vid, pos, pos_raw, bulb_raw, size, flag, note, ""))
                n_fit += 1
    con.commit()

    # ---- JSON cache for the bot: "year|make|model" -> setup ----
    cache = {}
    # bot cache uses headlight positions only: interior/map LEDs must not
    # flip a halogen-headlight vehicle to "mixed"
    for (year, make, model), setups in headlight_setups.items():
        if setups == {"halogen"}:
            cache[f"{year}|{make}|{model}"] = "halogen"
        elif setups == {"xenon"}:
            cache[f"{year}|{make}|{model}"] = "xenon"
        elif setups == {"factory_led"}:
            cache[f"{year}|{make}|{model}"] = "factory_led"
        else:
            cache[f"{year}|{make}|{model}"] = "mixed"
    with open(CACHE_JSON, "w") as fh:
        json.dump(cache, fh, indent=1)

    # ---- conflicts vs our other sources (informational only) ----
    sys.path.insert(0, os.path.expanduser("~/workspace/marketplace-autoreply"))
    import csv_fitment
    conflicts = []
    local_table = csv_fitment._load_all()
    google = {}
    if os.path.exists(csv_fitment.GOOGLE_CACHE):
        with open(csv_fitment.GOOGLE_CACHE) as fh:
            google = json.load(fh)
    for key, lasfit_setup in cache.items():
        for src_name, table in (("local_scrape", local_table),
                                ("google", google)):
            other = table.get(key)
            if other is None:
                continue
            other_setup = other[0] if isinstance(other, list) else other
            if other_setup != lasfit_setup:
                conflicts.append({"key": key, "lasfit": lasfit_setup,
                                  src_name: other_setup})
    # local_table values are lists like ["halogen"] or ["halogen","xenon"]
    # -> normalize multi-class to "mixed" for comparison
    norm_conflicts = []
    for c in conflicts:
        key = c["key"]
        for src_name in ("local_scrape", "google"):
            if src_name not in c:
                continue
            v = {"local_scrape": local_table, "google": google}[src_name][key]
            v = "mixed" if isinstance(v, list) and len(v) > 1 else \
                (v[0] if isinstance(v, list) else v)
            if v != c["lasfit"]:
                norm_conflicts.append({"key": key, "lasfit": c["lasfit"],
                                       src_name: v})
    review_out = {"unmapped_or_unresolved": [r for r in review
                                             if r.get("kind") in
                                             ("unmapped_position",
                                              "unresolved_bulb")],
                  "within_lasfit_conflicts": [r for r in review if r.get("kind")
                                              == "within_lasfit_conflict"],
                  "parse_issues": [r for r in review if r.get("kind") not in
                                   ("unmapped_position", "unresolved_bulb",
                                    "within_lasfit_conflict")],
                  "failed_pages": failed,
                  "conflicts_vs_other_sources": norm_conflicts}
    with open(REVIEW_JSON, "w") as fh:
        json.dump(review_out, fh, indent=1)
    con.close()
    print(f"DONE guides_ok={ok} guides_failed={len(failed)} "
          f"vehicles={n_veh} fitment_rows={n_fit} "
          f"vmm_entries={len(cache)} conflicts={len(norm_conflicts)}")


if __name__ == "__main__":
    main()
