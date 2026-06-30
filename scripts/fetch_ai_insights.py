#!/usr/bin/env python3
"""
Portföy Paneli - AI içgörü scripti
====================================
Haftalık çalışır (bkz. .github/workflows/update-ai-insights.yml). Anthropic API'sini
web search aracıyla kullanarak her ticker için:
  - Bear/Baz/Boğa hedef fiyat (gerçek analist konsensüsünden türetilir, uydurulmaz)
  - 2-3 maddelik "öne çıkanlar" özeti (en son çeyrek/kazanç çağrısından)
üretir ve data/ai_cache.json'a yazar.

fetch_data.py bu cache'i okuyup şirket kartlarına otomatik ekler. config.json'daki
targetPriceOverrides / guidanceNotes hâlâ önceliklidir — orada elle bir şey
tanımlarsan AI'ı ezer.

Maliyet notu: her ticker için web search içeren bir API çağrısı yapılır; bu FMP/
yfinance'ten farklı olarak ücretli bir kaynak. Portföy büyüklüğüne göre haftalık
birkaç dolar civarında maliyet oluşabilir. Sıklığı update-ai-insights.yml
içindeki cron ile ayarlayabilirsin (örn. ayda bire düşürmek için '0 6 1 * *').
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "config.json"
CACHE_PATH = ROOT / "data" / "ai_cache.json"
DATA_PATH = ROOT / "data" / "data.json"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
API_URL = "https://api.anthropic.com/v1/messages"

PROMPT_TEMPLATE = """Sen bir hisse senedi araştırma asistanısın. {ticker} ({name}) için web'de güncel bilgi ara.

Güncel fiyat: ${price}

Şunları yap:
1. Analistlerin {ticker} için güncel konsensüs hedef fiyatlarını ara (düşük / ortalama / yüksek analist hedefi).
2. {ticker}'ın en son çeyrek sonuçlarını / kazanç çağrısını ara, 2-3 somut öne çıkan noktayı bul.

Kurallar:
- bear/base/bull değerlerini SADECE bulduğun gerçek analist hedef fiyatlarından türet (düşük hedef→bear, ortalama hedef→base, yüksek hedef→bull). Güvenilir veri bulamazsan ilgili alanı (veya hepsini) null yap — uydurma, tahmin etme.
- confidence: birden fazla kaynaktan tutarlı veri bulduysan "yüksek", tek kaynaktan bulduysan "orta", veri zayıf/eskiyse "düşük".
- guidance maddelerini kendi cümlelerinle özetle; hiçbir kaynaktan 15 kelimeyi aşan birebir alıntı yapma.
- guidance Türkçe ve kısa olsun (madde başına ~12 kelime), 2-3 madde.

SADECE aşağıdaki formatta cevap ver, başka açıklama ekleme, JSON'u <RESULT> ve </RESULT> arasına koy:

<RESULT>
{{"bear": <sayı veya null>, "base": <sayı veya null>, "bull": <sayı veya null>, "confidence": "düşük|orta|yüksek", "guidance": ["...", "..."]}}
</RESULT>
"""


def log(msg):
    print(f"[fetch_ai_insights] {msg}", flush=True)


def call_claude(ticker, name, price):
    prompt = PROMPT_TEMPLATE.format(ticker=ticker, name=name, price=price)
    body = {
        "model": MODEL,
        "max_tokens": 2000,
        "messages": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        r = requests.post(API_URL, headers=headers, json=body, timeout=90)
        if not r.ok:
            log(f"{ticker}: API hata {r.status_code}: {r.text[:600]}")
            return None
        data = r.json()
    except Exception as e:
        log(f"{ticker}: API çağrısı hatası: {e}")
        return None

    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    m = re.search(r"<RESULT>(.*?)</RESULT>", text, re.S)
    if not m:
        log(f"{ticker}: beklenen <RESULT> bloğu bulunamadı, atlanıyor")
        return None
    try:
        parsed = json.loads(m.group(1).strip())
    except Exception as e:
        log(f"{ticker}: JSON parse hatası: {e}")
        return None

    if any(parsed.get(k) is None for k in ("bear", "base", "bull")):
        log(f"{ticker}: analist hedef fiyatı bulunamadı, hedef fiyat bloğu atlanıyor")
        parsed["bear"] = parsed["base"] = parsed["bull"] = None

    return parsed


def main():
    if not ANTHROPIC_API_KEY:
        log("ANTHROPIC_API_KEY tanımlı değil, atlanıyor (bu script opsiyoneldir, "
            "secret eklemezsen sistemin geri kalanı normal çalışmaya devam eder).")
        sys.exit(0)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    prices = {}
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        for h in d.get("stockPortfolio", {}).get("holdings", []):
            if h.get("price"):
                prices[h["ticker"]] = h["price"]

    cache = {}
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)

    holdings = [h for h in config["stockPortfolio"]["holdings"] if h.get("shares")]
    for h in holdings:
        ticker, name = h["ticker"], h.get("name", h["ticker"])
        price = prices.get(ticker)
        if not price:
            log(f"{ticker}: güncel fiyat yok (önce fetch_data.py çalışmalı), atlanıyor")
            continue
        log(f"{ticker} için aranıyor…")
        result = call_claude(ticker, name, price)
        if result:
            result["updatedAt"] = datetime.now(timezone.utc).isoformat()
            cache[ticker] = result
        time.sleep(1)  # API rate limitine takılmamak için kısa bekleme

    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    log(f"Yazıldı: {CACHE_PATH} ({len(cache)} ticker)")


if __name__ == "__main__":
    main()
