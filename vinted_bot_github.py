#!/usr/bin/env python3
"""
[VERSION GITHUB ACTIONS : un passage par exécution]
Bot Vinted "bonnes affaires" : te prévient (Discord et/ou Telegram) quand une
nouvelle annonce correspond à tes critères ET est nettement moins chère que
le prix habituel pour cette recherche.

Installation :  pip install requests
Lancement    :  python vinted_bot.py
"""

import json
import os
import random
import statistics
import time

import subprocess
import sys

import requests

try:
    from curl_cffi import requests as cffi
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "curl_cffi"], check=False)
    try:
        from curl_cffi import requests as cffi
    except ImportError:
        cffi = None

# ====================== CONFIGURATION ======================

DOMAIN = "www.vinted.fr"  # www.vinted.de, www.vinted.es, etc.

EXCLUDE = ["fake", "copie", "réplique", "replica", "lot", "enfant", "junior"]


SHOES = EXCLUDE + ["chaussures", "baskets", "sneakers", "pegasus", "vaporfly",
                   "air zoom", "invincible", "paire", "semelle"]


def S(name, search, pmin, pmax, ratio=0.70, exclude=None):
    return {
        "name": name, "search": search,
        "price_min": pmin, "price_max": pmax, "deal_ratio": ratio,
        "sizes": [], "brands": [], "catalogs": [], "exclude": exclude or EXCLUDE,
    }


SEARCHES = [
    # --- RUNNING NIKE (vêtements uniquement) ---
    S("Nike AeroSwift", "nike aeroswift", 15, 60, exclude=SHOES),
    S("Nike Dri-FIT ADV", "nike dri-fit adv", 15, 60, exclude=SHOES),
    S("Nike Running Division", "nike running division", 15, 70, exclude=SHOES),
    S("Nike running veste", "nike running veste", 15, 70, exclude=SHOES),
    S("Nike running short", "nike running short", 10, 40, exclude=SHOES),
    S("Nike running t-shirt", "nike running t-shirt", 10, 40, exclude=SHOES),
    S("Nike running sweat", "nike running sweat", 15, 60, exclude=SHOES),
    S("On Running veste", "on running veste", 25, 120, exclude=SHOES + ["cloud"]),
    S("On Running t-shirt", "on running t-shirt", 15, 60, exclude=SHOES + ["cloud"]),
    S("On Running short", "on running short", 15, 60, exclude=SHOES + ["cloud"]),
    # --- CHAUSSURES RUNNING ---
    S("Nike Pegasus", "nike pegasus", 20, 90),
    S("Nike Vaporfly / Alphafly", "nike vaporfly alphafly", 40, 200),
    S("Nike Invincible / Zoom Fly", "nike invincible zoom fly", 25, 120),
    S("Nike running chaussures", "nike running chaussures", 20, 90),
    S("On Cloudmonster", "on cloudmonster", 30, 130),
    S("On Cloudsurfer / Cloudstratus", "on cloudsurfer cloudstratus", 30, 130),
    S("On Cloud chaussures", "on cloud running chaussures", 30, 130),
    # --- TECH ---
    S("Nike Tech Fleece", "nike tech fleece", 25, 80),
    S("Nike Tech Pack", "nike tech pack", 25, 90),
    S("Nike ACG", "nike acg", 25, 120),
    # --- NICHE / LUXE ---
    S("Arc'teryx", "arc'teryx", 50, 300),
    S("Stone Island", "stone island", 50, 300),
    S("CP Company", "cp company", 40, 250),
    S("Moncler", "moncler", 80, 500),
    S("Represent", "represent clo", 40, 200),
    S("Gallery Dept", "gallery dept", 60, 400),
    S("Rick Owens", "rick owens", 60, 500),
    S("Fear of God", "fear of god essentials", 30, 200),
    S("Palm Angels", "palm angels", 50, 300),
    S("Off-White", "off-white", 50, 300),
    S("Aimé Leon Dore", "aime leon dore", 50, 300),
    S("Corteiz", "corteiz", 30, 250),
]

# Au moins un des deux :
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")   # https://discord.com/api/webhooks/...
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")    # token donné par @BotFather
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # ton chat id

POLL_SECONDS = 120         # ne descends pas trop bas pour éviter d'être bloqué
PER_PAGE = 20
BASELINE_PAGES = 3         # nb de pages utilisées pour estimer le prix médian
MIN_SAMPLES = 15           # nb minimum d'annonces pour fiabiliser la médiane
BASELINE_REFRESH = 30 * 60 # recalcul de la médiane toutes les 30 min

# ===========================================================

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

if cffi:
    session = cffi.Session(impersonate="chrome")
    print("[mode] curl_cffi (empreinte Chrome)")
else:
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    print("[mode] requests classique")
session.headers.update({
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Referer": f"https://{DOMAIN}/catalog",
})


def refresh_cookies():
    """Vinted exige un cookie de session : on le récupère via la page d'accueil."""
    session.cookies.clear()
    r = session.get(f"https://{DOMAIN}/", timeout=15)
    r2 = session.get(f"https://{DOMAIN}/catalog?search_text=nike", timeout=15)
    print(f"[cookies] accueil HTTP {r.status_code}, catalogue HTTP {r2.status_code}, cookies : {sorted(session.cookies.keys())}")


def build_params(s, page=1, for_baseline=False):
    p = {
        "search_text": s["search"],
        "order": "newest_first",
        "per_page": PER_PAGE,
        "page": page,
    }
    # Pour la médiane, on ignore les bornes de prix afin de ne pas la fausser.
    if not for_baseline:
        if s.get("price_min") is not None:
            p["price_from"] = s["price_min"]
        if s.get("price_max") is not None:
            p["price_to"] = s["price_max"]
    if s.get("sizes"):
        p["size_ids[]"] = s["sizes"]
    if s.get("brands"):
        p["brand_ids[]"] = s["brands"]
    if s.get("catalogs"):
        p["catalog_ids[]"] = s["catalogs"]
    return p


def fetch_items(s, page=1, for_baseline=False):
    url = f"https://{DOMAIN}/api/v2/catalog/items"
    params = build_params(s, page, for_baseline)
    r = session.get(url, params=params, timeout=15)
    if r.status_code in (401, 403):
        refresh_cookies()
        r = session.get(url, params=params, timeout=15)
    ctype = r.headers.get("content-type", "")
    if r.status_code >= 400 or "json" not in ctype:
        extrait = r.text[:150].replace("\n", " ")
        raise RuntimeError(f"HTTP {r.status_code}, type {ctype or 'inconnu'}, début : {extrait!r}")
    return r.json().get("items", [])


def price_value(item):
    p = item.get("price")
    try:
        return float(p["amount"] if isinstance(p, dict) else p)
    except (TypeError, ValueError, KeyError):
        return None


def price_label(item):
    p = item.get("price")
    cur = p.get("currency_code", "€") if isinstance(p, dict) else item.get("currency", "€")
    return f"{price_value(item)} {cur}"


def compute_median(s):
    prices = []
    for page in range(1, BASELINE_PAGES + 1):
        for it in fetch_items(s, page, for_baseline=True):
            v = price_value(it)
            if v:
                prices.append(v)
        time.sleep(random.uniform(1, 3))
    return statistics.median(prices) if len(prices) >= MIN_SAMPLES else None


def notify(s, item, median):
    title = item.get("title", "?")
    url = item.get("url") or f"https://{DOMAIN}/items/{item['id']}"
    photo = (item.get("photo") or {}).get("url")
    price = price_value(item)

    deal = ""
    if median and price:
        deal = f"📉 -{round((1 - price / median) * 100)} % vs prix médian (~{median:.0f})"

    lines = [
        f"🔥 [{s['name']}] {title}",
        f"💶 {price_label(item)}",
        deal,
        f"🏷️ {item.get('brand_title') or '-'} · taille {item.get('size_title') or '-'}",
        url,
    ]
    lines = [l for l in lines if l]
    text = "\n".join(lines)

    if DISCORD_WEBHOOK:
        embed = {"title": title, "url": url, "description": "\n".join(lines[1:-1])}
        if photo:
            embed["thumbnail"] = {"url": photo}
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=10)

    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )

    print(text, end="\n\n")


STATE_FILE = "state.json"
MAX_SEEN = 500


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"seen": {}, "medians": {}}


def save_state(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f)


def main():
    if not (DISCORD_WEBHOOK or (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)):
        raise SystemExit("Secret DISCORD_WEBHOOK manquant.")

    st = load_state()
    seen = st.setdefault("seen", {})
    meds = st.setdefault("medians", {})

    if not seen and DISCORD_WEBHOOK:
        requests.post(DISCORD_WEBHOOK, json={"content": "✅ Bot Vinted actif. Les bonnes affaires arriveront ici."}, timeout=10)

    refresh_cookies()
    ok = 0
    fails = 0

    for s in SEARCHES:
        name = s["name"]
        time.sleep(random.uniform(1, 2))
        first = name not in seen
        order = list(seen.get(name, []))
        known = set(order)

        try:
            median = None
            if s.get("deal_ratio"):
                m = meds.get(name)
                if m and time.time() - m["ts"] < BASELINE_REFRESH:
                    median = m["value"]
                else:
                    median = compute_median(s)
                    meds[name] = {"value": median, "ts": time.time()}
            items = fetch_items(s)
            ok += 1
        except Exception as e:
            print(f"[{name}] erreur : {e}")
            fails += 1
            if fails >= 3 and ok == 0:
                print("Vinted refuse les requêtes : arrêt anticipé.")
                break
            continue

        for item in items:
            if item["id"] in known:
                continue
            known.add(item["id"])
            order.append(item["id"])
            if first:
                continue  # 1er passage : on mémorise sans notifier

            title = item.get("title", "").lower()
            if any(w.lower() in title for w in s.get("exclude", [])):
                continue

            price = price_value(item)
            ratio = s.get("deal_ratio")
            if ratio and not (price and median and price <= median * ratio):
                continue
            notify(s, item, median)

        seen[name] = order[-MAX_SEEN:]

    save_state(st)
    if ok == 0:
        raise SystemExit("Aucune recherche n'a abouti (Vinted bloque peut-être GitHub).")


if __name__ == "__main__":
    main()
