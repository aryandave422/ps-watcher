#!/usr/bin/env python3
"""PlayStation INDIA gift card stock watcher."""
import json
import os
import random
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

STATE_FILE = "state.json"
WATCH_FILE = "watch.txt"
MAX_PRODUCTS_PER_SITE = 25
STRICT_INDIA = True  # True: sirf wahi cards jinme India/INR/Rs likha ho

SITES = {
    "MD Computers": "https://mdcomputers.in",
    "PlayAsia": "https://www.play-asia.com",
    "gameprex": "https://gameprex.in",
    "Simply Gaming": "https://www.simplygaming.in",
    "Hitech Gamez": "https://hitechgamez.in",
    "Games Kart": "https://gamekart.in",
    "Digistore": "https://digistore.co.in",
    "E2Zstore": "https://e2zstore.com",
    "GAME THE SHOP": "https://www.gamestheshop.com",
    "GYFTR": "https://www.gyftr.com",
    "Blinkit": "https://blinkit.com",
    "Rooter": "https://shop.rooter.gg",
    "JioGames": "https://store.jiogames.com",
    "NI STORE": "https://www.nistore.in",
    "Sheenu Game Center": "https://sheenugamecenter.com",
    "HG WORLD": "https://hgworld.in",
    "Flipkart": "https://www.flipkart.com",
    "Amazon": "https://www.amazon.in",
    "Stan Shop": "https://www.stanshop.co",
    "KRG KART": "https://krgkart.com",
    "E MART": "https://emartgames.in",
    "UniPin": "https://www.unipin.com",
    "SWAG": "https://www.swag.gg",
    "99Gift": "https://99gift.in",
    "Woohoo": "https://www.woohoo.in",
    "Blue Vault": "https://www.bluevault.in",
    "Venue Games": "https://venusgamesandelecs.com",
}

SEARCH_PATTERNS = [
    "/search?q={q}",
    "/search?type=product&q={q}",
    "/?s={q}&post_type=product",
    "/catalogsearch/result/?q={q}",
    "/search/?q={q}",
]
QUERY = "playstation gift card india"

LINK_RE_PS = re.compile(r"playstation|psn|ps[\s-]?store", re.I)
LINK_RE_CARD = re.compile(r"gift|card|wallet|voucher|top[\s-]?up", re.I)
OUT_RE = re.compile(
    r"out of stock|sold out|currently unavailable|notify me|temporarily unavailable", re.I
)
IN_RE = re.compile(r"add to cart|add to bag|buy now|add to basket", re.I)

FOREIGN_RE = re.compile(
    r"\b(?:us|usa|america|american|united states|uk|united kingdom|uae|emirates|"
    r"saudi|ksa|canada|canadian|australia|aus|europe|eu|euro|germany|france|spain|"
    r"italy|japan|singapore|turkey|brazil|mexico|korea|hong kong|usd|gbp|eur|aed|"
    r"cad|aud|dollar|dollars)\b|[$£€]",
    re.I,
)
INDIA_RE = re.compile(r"\b(?:india|indian|inr)\b|₹|\brs\.?\s?\d", re.I)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}
session = requests.Session()
session.headers.update(HEADERS)


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fetch(url, timeout=12):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        log(f"  HTTP {r.status_code}: {url[:90]}")
    except requests.RequestException as e:
        log(f"  error {type(e).__name__}: {url[:90]}")
    return None


def url_words(url):
    path = urlparse(url).path
    return re.sub(r"[-_/]+", " ", path)


def region_ok(title, url):
    """India card hai ya nahi. Returns (ok, reason)."""
    blob = f"{title} {url_words(url)}"
    if FOREIGN_RE.search(blob):
        return False, "foreign region"
    if STRICT_INDIA and not INDIA_RE.search(blob):
        return False, "India nahi likha"
    return True, ""


def discover(base):
    found = {}
    host = urlparse(base).netloc.replace("www.", "")
    for pat in SEARCH_PATTERNS:
        html = fetch(base + pat.format(q=quote_plus(QUERY)))
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True) + " " + url_words(a["href"])
            if LINK_RE_PS.search(text) and LINK_RE_CARD.search(text):
                url = urljoin(base, a["href"]).split("#")[0]
                if urlparse(url).netloc.replace("www.", "").endswith(host):
                    found[url] = True
        if found:
            break
    return list(found)[:MAX_PRODUCTS_PER_SITE]


def _walk_offers(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk_offers(n)
    elif isinstance(node, dict):
        if "availability" in node:
            yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from _walk_offers(v)


def check_product(url, india_only=True):
    """Returns dict(title, price, in_stock) ya None (fail / filter)."""
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else None) or (
        soup.title.string.strip() if soup.title and soup.title.string else url
    )
    title = " ".join(title.split())[:120]

    if india_only:
        ok, reason = region_ok(title, url)
        if not ok:
            log(f"  skip ({reason}): {title[:70]}")
            return None

    price, currency, in_stock = None, None, None
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (ValueError, TypeError):
            continue
        for offer in _walk_offers(data):
            av = str(offer.get("availability", ""))
            if "InStock" in av or "LimitedAvailability" in av:
                in_stock = True
            elif in_stock is None and re.search(r"OutOfStock|SoldOut|Discontinued", av):
                in_stock = False
            price = price or offer.get("price")
            currency = currency or offer.get("priceCurrency")
        if in_stock is not None:
            break

    if in_stock is None:
        text = soup.get_text(" ", strip=True)[:60000]
        if OUT_RE.search(text) and not IN_RE.search(text):
            in_stock = False
        elif IN_RE.search(text) and not OUT_RE.search(text):
            in_stock = True
        elif OUT_RE.search(text):
            in_stock = False

    price_txt = f"{currency or ''} {price}".strip() if price else None
    return {"title": title, "price": price_txt, "in_stock": in_stock}


def notify(text):
    log("ALERT: " + text.replace("\n", " | "))
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("  Telegram token/chat id set nahi hai")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text},
            timeout=15,
        )
        if r.status_code != 200:
            log(f"  Telegram error {r.status_code}: {r.text[:200]}")
    except requests.RequestException as e:
        log(f"  Telegram error: {e}")


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def site_job(item):
    name, base = item
    urls = discover(base)
    results = []
    for u in urls:
        info = check_product(u, india_only=True)
        if info:
            results.append((name, u, info))
        time.sleep(random.uniform(1, 2))
    if not urls:
        log(f"{name}: koi PlayStation gift card link nahi mila")
    else:
        log(f"{name}: {len(urls)} links mile, {len(results)} India cards bache")
    return results


def manual_job(url):
    info = check_product(url, india_only=False)  # watch.txt pe filter nahi
    name = "watch.txt | " + urlparse(url).netloc.replace("www.", "")
    return [(name, url, info)] if info else []


def main():
    if "--test" in sys.argv:
        notify("✅ Test message: PlayStation watcher ka Telegram sahi chal raha hai.")
        return

    state = load_state()
    log("Check shuru...")
    all_results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(site_job, it) for it in SITES.items()]
        if os.path.exists(WATCH_FILE):
            with open(WATCH_FILE, encoding="utf-8") as f:
                manual = [l.strip() for l in f if l.strip().startswith("http")]
            futures += [ex.submit(manual_job, u) for u in manual]
            log(f"watch.txt: {len(manual)} links")
        for fut in futures:
            try:
                all_results += fut.result()
            except Exception as e:
                log(f"job failed: {e}")

    seen, in_stock_now = set(), 0
    for name, url, info in all_results:
        if url in seen:
            continue
        seen.add(url)
        prev = state.get(url, {}).get("in_stock")
        cur = info["in_stock"]
        if cur:
            in_stock_now += 1
        if cur is True and prev is not True:
            price = f" @ {info['price']}" if info["price"] else ""
            notify(f"🎮 IN STOCK - {name}\n{info['title']}{price}\n{url}")
        state[url] = {"in_stock": cur, "title": info["title"]}
    save_state(state)

    summary = Counter(n for n, _, _ in all_results)
    for site in SITES:
        log(f"SUMMARY {site}: {summary.get(site, 0)} India cards")
    log(f"Done. {len(seen)} products checked, {in_stock_now} in stock.")


if __name__ == "__main__":
    main()
