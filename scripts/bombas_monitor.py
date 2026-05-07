#!/usr/bin/env python3
"""
Bombas Size Availability Monitor
==================================
Checks the product page HTML for availability.

Validates the correct variant is displayed by looking for the product
image alt text pattern, e.g.:
  "Women's Sunday Slipper - Dark Espresso"
which only appears when that variant's images are shown.

If the page silently falls back to another variant (e.g. Dark Camel),
the alt text will say "Dark Camel" instead, and we catch the mismatch.

Usage:  python3 bombas_monitor.py
Stop:   Ctrl+C
"""

import re
import time
import json
import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from urllib.request import urlopen, Request

load_dotenv("secrets.env")

# ─── Configuration ───────────────────────────────────────────────
TARGET_SIZE = "7"
TARGET_VARIANT = "dark-espresso"     # URL param value
PRODUCT_PATH = "womens-sunday-slippers"
CHECK_INTERVAL = 600  # seconds (10 minutes)

BASE_URL = f"https://bombas.com/products/{PRODUCT_PATH}?variant={TARGET_VARIANT}&size={TARGET_SIZE}"

# How the variant name appears in product image alt text
# e.g. alt="Women's Sunday Slipper - Dark Espresso L [6074]"
VARIANT_ALT_NAME = TARGET_VARIANT.replace("-", " ").title()  # "Dark Espresso"

# Home Assistant webhook
HA_BASE_URL = os.environ["HA_BASE_URL"]
WEBHOOK_ID = os.environ["WEBHOOK_ID"]
HA_WEBHOOK_URL = f"{HA_BASE_URL}/api/webhook/{WEBHOOK_ID}"

# Set True to fire webhook once and exit, False to keep alerting every 60s
ALERT_ONCE = True
# ─────────────────────────────────────────────────────────────────


def fire_webhook(title, message, url):
    """POST JSON to the Home Assistant webhook."""
    payload = json.dumps({
        "title": title,
        "message": message,
        "url": url,
    }).encode()

    req = Request(
        HA_WEBHOOK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as resp:
            print(f"  ✅ Webhook fired (HTTP {resp.status})")
    except Exception as e:
        print(f"  ❌ Webhook error: {e}")


def check_availability():
    """
    Fetch the product page raw HTML and check:
      1. Product image alt text contains our variant name
         e.g. alt="Women's Sunday Slipper - Dark Espresso ..."
         If it says "Dark Camel" instead, the page fell back.
      2. "Add to Bag" button present (not "Option Not Available")

    Returns: True (in stock), False (out of stock), None (error/uncertain)
    """
    req = Request(BASE_URL, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    try:
        with urlopen(req, timeout=30) as resp:
            html = resp.read().decode()
    except Exception as e:
        print(f"\n  ❌ Fetch error: {e}")
        return None

    html_lower = html.lower()

    # ── Check 1: Verify correct variant via product image alt text ──
    # Pattern in raw HTML: alt="Women's Sunday Slipper - Dark Espresso L [6074]"
    # We look for all alt attributes containing "Sunday Slipper - XXXXX"
    # and check if any match our variant.
    alt_pattern = re.compile(r'alt=["\']([^"\']*Sunday Slipper[^"\']*)["\']', re.IGNORECASE)
    alt_matches = alt_pattern.findall(html)

    if alt_matches:
        # Check if any product image alt text contains our variant name
        variant_in_alt = any(VARIANT_ALT_NAME.lower() in alt.lower() for alt in alt_matches)

        if not variant_in_alt:
            # Figure out what's actually showing
            shown_variant = "unknown"
            for alt in alt_matches:
                # Extract variant from "Women's Sunday Slipper - VARIANT_NAME ..."
                m = re.search(r'Sunday Slipper\s*-\s*(.+?)(?:\s+L\d?|\s+\[)', alt, re.IGNORECASE)
                if m:
                    shown_variant = m.group(1).strip()
                    break
            print(f"\n  ⚠ Page fell back! Showing '{shown_variant}' instead of '{VARIANT_ALT_NAME}'")
            return False
    else:
        # Fallback: if no alt tags found, check for variant slug in image src URLs
        # Product image URLs look like: /6074-darkespresso-adult-female-layflat-
        variant_slug = TARGET_VARIANT.replace("-", "")
        img_src_pattern = re.compile(r'src=["\'][^"\']*6074-(\w+)-adult', re.IGNORECASE)
        img_matches = img_src_pattern.findall(html)

        if img_matches:
            if variant_slug not in [m.lower() for m in img_matches]:
                print(f"\n  ⚠ Page fell back! Image URLs show: {img_matches[0]}")
                return False
        else:
            print(f"\n  ⚠ Could not find product images to verify variant")
            # Continue anyway and rely on button text check

    # ── Check 2: Is the item purchasable? ──
    # Look for the actual button/link text, not just any occurrence on the page
    has_add_to_bag = "add to bag" in html_lower
    has_not_available = "option not available" in html_lower

    if has_add_to_bag and not has_not_available:
        return True
    elif has_not_available:
        return False
    else:
        print(f"\n  ⚠ Could not find 'Add to Bag' or 'Option Not Available'")
        return None


def main():
    print("=" * 60)
    print(f"  🧦 Bombas Availability Monitor")
    print(f"  Product: {PRODUCT_PATH}")
    print(f"  Variant: {VARIANT_ALT_NAME}")
    print(f"  Size: {TARGET_SIZE}")
    print(f"  URL: {BASE_URL}")
    print(f"  Checking every {CHECK_INTERVAL // 60} minutes")
    print(f"  Webhook: {HA_WEBHOOK_URL}")
    print(f"  Press Ctrl+C to stop")
    print("=" * 60)

    check_count = 0

    while True:
        check_count += 1
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] Check #{check_count}...", end=" ", flush=True)

        result = check_availability()

        if result is True:
            print("🚨 IN STOCK!")
            fire_webhook(
                title=f"Bombas Size {TARGET_SIZE} Available!",
                message=f"Women's Sunday Slipper - {VARIANT_ALT_NAME} Size {TARGET_SIZE} is back in stock!",
                url=BASE_URL,
            )

            if ALERT_ONCE:
                print("\n  Done! Exiting (ALERT_ONCE=True).")
                sys.exit(0)

            print("  ⏰ Re-alerting every 60s until stopped (Ctrl+C)")
            while True:
                time.sleep(60)
                fire_webhook(
                    title="Still Available!",
                    message=f"Go buy your slippers! {BASE_URL}",
                    url=BASE_URL,
                )

        elif result is False:
            print("❌ Not available.")
        else:
            print("⚠ Uncertain result — check manually.")

        next_check = datetime.fromtimestamp(time.time() + CHECK_INTERVAL).strftime("%H:%M:%S")
        print(f"  Next check at {next_check}")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped.")
        sys.exit(0)