#!/usr/bin/env python3
"""PlayStation INR Gift Card stock watcher (GitHub Actions version)."""
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

STATE_FILE = "state.json"
WATCH_FILE = "watch.txt"
MAX_PRODUCTS_PER_SITE = 12

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
QUERY = "playstation gift card"

LINK_RE_PS = re.compile(r"playstation|psn|ps[\s-]?store|ps[\s-]?plus", re.I)
LINK_RE_CARD = re.compile(r"gift|card|wallet|voucher|top[\s-]?up", re.I)
OUT_RE = re.compile(r"out of stock|sold out|currently unavailable|notify me|temporarily unavailable", re.I)
IN_RE = re.compile(r"add to cart|add to bag|buy now|add to basket", re.I)

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


def fetch(url, timeout=20):
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        if r.status_code == 200:
            return r.text
        log(f"  HTTP {r.status_code}: {url}")
    except requests.RequestException as e:
        log(f"  error {type(e).__name__}: {url}")
    return None


def discover(base):
    found = {}
    host = urlparse(base).netloc.replace("www.", "")
    for pat in SEARCH_PATTERNS:
        html = fetch(base + pat.format(q=quote_plus(QUERY)))
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True) + " " + a["href"]
            if LINK_RE_PS.search(text) and LINK_RE_CARD.search(text):
                url = urljoin(base, a["href"]).split("#")[0]
                if urlparse(url).netloc.endswith(host):
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


def check_product(url):
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else None) or (
        soup.title.string.strip() if soup.title and soup.title.string else url
    )
    price, in_stock = None, None

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

    return {"title": title[:120], "price": price, "in_stock": in_stock}


def notify(text):
    log("ALERT: " + text.replace("\n", " | "))
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        log("  Telegram secrets set nahi hain")
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
        info = check_product(u)
        if info:
            results.append((name, u, info))
        time.sleep(random.uniform(1, 2))
    if not urls:
        log(f"{name}: koi PlayStation gift card link nahi mila")
    else:
        log(f"{name}: {len(urls)} products mile")
    return results


def manual_job(url):
    info = check_product(url)
    return [(urlparse(url).netloc, url, info)] if info else []


def main():
    state = load_state()
    log("Check shuru...")
    all_results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(site_job, it) for it in SITES.items()]
        if os.path.exists(WATCH_FILE):
            with open(WATCH_FILE) as f:
                manual = [l.strip() for l in f if l.strip().startswith("http")]
            futures += [ex.submit(manual_job, u) for u in manual]
        for fut in futures:
            try:
                all_results += fut.result()
            except Exception as e:
                log(f"job failed: {e}")

    in_stock_now = 0
    for name, url, info in all_results:
        prev = state.get(url, {}).get("in_stock")
        cur = info["in_stock"]
        if cur:
            in_stock_now += 1
        if cur is True and prev is not True:
            price = f" @ Rs {info['price']}" if info["price"] else ""
            notify(f"IN STOCK - {name}\n{info['title']}{price}\n{url}")
        state[url] = {"in_stock": cur, "title": info["title"]}
    save_state(state)
    log(f"Done. {len(all_results)} products checked, {in_stock_now} in stock.")


if __name__ == "__main__":
    main()
