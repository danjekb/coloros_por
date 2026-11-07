#!/usr/bin/env python3
"""Resolve device model queries or URLs to direct firmware download links from roms.lian86.top.

Usage:
    python3 bin/fetch_rom_url.py "rmx3301/rmx3301"
    python3 bin/fetch_rom_url.py "Oneplus 13"
    python3 bin/fetch_rom_url.py "https://example.com/rom.zip"
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from typing import List, Optional, Tuple

BASE_SITE = "https://roms.lian86.top"
KNOWN_BRANDS = [
    "OnePlus",
    "Realme",
    "OPPO",
    "oneplus-global",
    "xiaomi",
    "redmi",
    "Meizu",
    "Lenovo",
    "vivo",
    "iQOO",
]


def is_direct_url(query: str) -> bool:
    q = query.strip()
    return q.startswith("http://") or q.startswith("https://")


def fetch_html(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def get_all_brand_models() -> List[Tuple[str, str, str]]:
    """Fetch all available models across known brands: (brand, model_code, display_name)."""
    models = []
    for brand in KNOWN_BRANDS:
        url = f"{BASE_SITE}/brand/{brand}"
        try:
            html = fetch_html(url)
            cards = re.findall(
                r'<a href=[\"\'](/brand/[^\"\'/]+/model/[^\"\'/]+)[\"\'][^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )
            for href, inner in cards:
                display_name = re.sub(r"<[^>]+>", " ", inner).strip()
                display_name = re.sub(
                    r"进入后查看可用 ROM 版本|选择地区后查看版本", "", display_name
                ).strip()
                model_code = href.split("/")[-1]
                models.append((brand, model_code, display_name))
        except Exception as e:
            print(f"[fetch_rom_url] Warning: error reading brand {brand}: {e}", file=sys.stderr)
            continue
    return models


def score_model(query: str, brand: str, model_code: str, display_name: str) -> int:
    q_lower = query.lower().strip()
    raw_tokens = [t for t in re.split(r"[/_\s-]+", q_lower) if t]
    unique_tokens = list(dict.fromkeys(raw_tokens))

    m_lower = model_code.lower()
    d_lower = display_name.lower()
    b_lower = brand.lower()

    d_tokens = [t for t in re.split(r"[/_\s-]+", d_lower) if t]
    if "一加" in d_lower:
        d_tokens.append("oneplus")
    if "真我" in d_lower:
        d_tokens.append("realme")
    if "红米" in d_lower:
        d_tokens.append("redmi")
    if "小米" in d_lower:
        d_tokens.append("xiaomi")

    score = 0

    # Direct exact model match (e.g. KB2000, PJZ110, RMX3300)
    for t in unique_tokens:
        if t == m_lower:
            score += 500
        elif t.startswith("rmx") and m_lower.startswith("rmx") and t[:6] == m_lower[:6]:
            # e.g. RMX3301 -> RMX3300
            score += 450

    # Exact full name match (normalized)
    norm_display = d_lower.replace("一加", "oneplus").replace("真我", "realme").strip()
    if q_lower == d_lower or q_lower == norm_display or q_lower == f"{b_lower} {d_lower}":
        score += 300

    # Token matches in display name
    matches = 0
    for t in unique_tokens:
        if t in d_tokens:
            score += 100
            matches += 1
        elif t in d_lower:
            score += 50
            matches += 1

    if matches >= len(unique_tokens):
        score += 150

    # Project is ColorOS port tool: strongly prefer domestic ColorOS brands (OnePlus, Realme, OPPO)
    # over global / OxygenOS brands (oneplus-global) unless the user explicitly requested global or oos.
    explicit_global = any(w in q_lower for w in ("global", "oxygen", "oos", "ex01", "cph"))
    if not explicit_global:
        if brand in ("OnePlus", "Realme", "OPPO"):
            score += 200
        elif brand == "oneplus-global":
            score -= 100
    else:
        if brand == "oneplus-global":
            score += 150

    # Number token validation
    for t in unique_tokens:
        if t.isdigit():
            if t in d_tokens:
                score += 50
            for dt in d_tokens:
                if dt.startswith(t) and len(dt) > len(t):
                    score -= 40

    return score


def find_best_model(query: str) -> Optional[Tuple[str, str, str]]:
    models = get_all_brand_models()
    if not models:
        return None

    scored = [
        (score_model(query, b, m, d), b, m, d)
        for b, m, d in models
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 0:
        best = scored[0]
        return best[1], best[2], best[3]
    return None


def get_version_page_urls(brand: str, model_code: str, query: str = "") -> List[str]:
    url = f"{BASE_SITE}/brand/{brand}/model/{model_code}"
    html = fetch_html(url)
    v_links = re.findall(
        r'href=[\"\'](/brand/[^\"\'/]+/model/[^\"\'/]+(?:/region/[^\"\'/]+)?/version/[^\"\'/]+)[\"\']',
        html,
    )
    if v_links:
        urls = [BASE_SITE + v for v in v_links]
        # Sort ColorOS ahead if not explicitly requesting OxygenOS
        explicit_oos = any(w in query.lower() for w in ("oxygen", "oos", "ex01"))
        if not explicit_oos:
            urls.sort(key=lambda u: (0 if "ColorOS" in u else (1 if "Realme" in u else 2)))
        return urls

    # Handle models with region selection (e.g. oneplus-global)
    r_links = re.findall(
        r'href=[\"\'](/brand/[^\"\'/]+/model/[^\"\'/]+(?:/region/[^\"\'/]+)?/?)[\"\']',
        html,
    )
    region_links = [r for r in r_links if "/region/" in r]
    if region_links:
        gl_link = next((r for r in region_links if "/region/gl" in r), region_links[0])
        r_url = BASE_SITE + gl_link
        html2 = fetch_html(r_url)
        v_links2 = re.findall(
            r'href=[\"\'](/brand/[^\"\'/]+/model/[^\"\'/]+(?:/region/[^\"\'/]+)?/version/[^\"\'/]+)[\"\']',
            html2,
        )
        urls = [BASE_SITE + v for v in v_links2]
        explicit_oos = any(w in query.lower() for w in ("oxygen", "oos", "ex01"))
        if not explicit_oos:
            urls.sort(key=lambda u: (0 if "ColorOS" in u else (1 if "Realme" in u else 2)))
        return urls

    return []


def resolve_rom_download_link_via_playwright(version_page_url: str, timeout_sec: int = 45) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright is not installed. Please install playwright to fetch ROMs.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(version_page_url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            # Click download button
            btn = page.locator("button.download-button")
            if btn.count() == 0:
                return None
            btn.first.click()
            page.wait_for_timeout(1000)

            # Check if terms modal appeared
            terms = page.locator(".download-terms-dialog")
            if terms.count() > 0 and terms.is_visible():
                cb = page.locator(".download-terms-checkbox input")
                if cb.count() > 0 and not cb.is_checked():
                    cb.check()
                accept_btn = page.locator("button.download-terms-accept")
                if accept_btn.count() > 0:
                    accept_btn.click()
                page.wait_for_timeout(1000)

            # Wait for and solve captcha
            try:
                page.wait_for_selector("cap-widget", timeout=10000)
                cap = page.locator("cap-widget")
                if cap.count() > 0:
                    cap.first.click()
            except Exception:
                pass

            # Poll for download links
            start_time = time.time()
            while time.time() - start_time < timeout_sec:
                page.wait_for_timeout(1000)
                links = page.locator("a.download-link, .download-links a").all()
                for link in links:
                    href = link.get_attribute("href")
                    if href and (".zip" in href or "component-ota" in href):
                        return href
            return None
        finally:
            browser.close()


def fetch_rom_url(query: str) -> str:
    query = query.strip()
    if is_direct_url(query):
        return query

    print(f"[fetch_rom_url] Searching roms.lian86.top for '{query}'...", file=sys.stderr)
    best = find_best_model(query)
    if not best:
        raise ValueError(f"Could not find matching device on roms.lian86.top for query: '{query}'")

    brand, model_code, display_name = best
    print(f"[fetch_rom_url] Matched device: {brand} / {model_code} ({display_name})", file=sys.stderr)

    version_urls = get_version_page_urls(brand, model_code, query=query)
    if not version_urls:
        raise ValueError(f"No firmware versions found for {brand} {model_code} ({display_name})")

    latest_version_url = version_urls[0]
    print(f"[fetch_rom_url] Selected latest version page: {latest_version_url}", file=sys.stderr)

    download_link = resolve_rom_download_link_via_playwright(latest_version_url)
    if not download_link:
        raise RuntimeError(f"Failed to retrieve download link from {latest_version_url}")

    print(f"[fetch_rom_url] Successfully resolved direct ROM link: {download_link}", file=sys.stderr)
    return download_link


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", help="Device name/model code or direct URL")
    args = parser.parse_args()

    try:
        url = fetch_rom_url(args.query)
        print(url)
    except Exception as e:
        print(f"::error::Error fetching ROM URL for '{args.query}': {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
