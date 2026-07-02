#!/usr/bin/env python3
"""
CH Value Dashboard - veri çekme scripti
=========================================
GitHub Actions tarafından otomatik çalıştırılır. data/config.json'u okur,
yfinance (fiyat/hareketli ortalama) ve Financial Modeling Prep (şirket
finansalları) üzerinden veri çeker, data/data.json'a yazar.

Yerelde test etmek istersen:
    pip install -r requirements.txt
    export FMP_API_KEY=senin_key
    python scripts/fetch_data.py
"""
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "config.json"
OUTPUT_PATH = ROOT / "data" / "data.json"
AI_CACHE_PATH = ROOT / "data" / "ai_cache.json"

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"
GRAMS_PER_TROY_OUNCE = 31.1034768


def log(msg):
    print(f"[fetch_data] {msg}", flush=True)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ai_cache():
    if not AI_CACHE_PATH.exists():
        return {}
    try:
        with open(AI_CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log(f"ai_cache.json okunamadı, boş kabul ediliyor: {e}")
        return {}


# ---------------------------------------------------------------------------
# Fiyat + hareketli ortalama (yfinance)
# ---------------------------------------------------------------------------

def pct_distance(price, ma):
    if price is None or ma is None or ma == 0:
        return None
    return ((price / ma) - 1) * 100


def fetch_price_and_moving_averages(ticker):
    """Günlük 50/200 ve haftalık 50/100/200 hareketli ortalamadan fiyatın
    yüzde uzaklığını + 'Güçlü' durumunu hesaplar. Yetersiz geçmiş varsa
    (örn. yeni listelenmiş ETF'ler) ilgili alan None döner -> arayüzde '—'."""
    t = yf.Ticker(ticker)

    daily = t.history(period="2y", interval="1d", auto_adjust=False)
    weekly = t.history(period="5y", interval="1wk", auto_adjust=False)

    if daily.empty:
        raise ValueError(f"{ticker}: günlük fiyat verisi boş döndü")

    price = float(daily["Close"].iloc[-1])

    ma50d = daily["Close"].rolling(50).mean().iloc[-1]
    ma200d = daily["Close"].rolling(200).mean().iloc[-1]
    ma50d = float(ma50d) if ma50d == ma50d else None      # NaN kontrolü
    ma200d = float(ma200d) if ma200d == ma200d else None

    ma50w = ma100w = ma200w = None
    if not weekly.empty:
        s = weekly["Close"]
        v = s.rolling(50).mean().iloc[-1]
        ma50w = float(v) if v == v else None
        v = s.rolling(100).mean().iloc[-1]
        ma100w = float(v) if v == v else None
        v = s.rolling(200).mean().iloc[-1]
        ma200w = float(v) if v == v else None

    d50 = pct_distance(price, ma50d)
    d200 = pct_distance(price, ma200d)
    w50 = pct_distance(price, ma50w)
    w100 = pct_distance(price, ma100w)
    w200 = pct_distance(price, ma200w)

    strong = all(x is not None and x > 0 for x in (d50, d200, w50))

    return {
        "price": round(price, 4),
        "ma": {
            "d50": d50, "d200": d200,
            "w50": w50, "w100": w100, "w200": w200,
        },
        "status": "Güçlü" if strong else "—",
    }


# ---------------------------------------------------------------------------
# Şirket finansalları (Financial Modeling Prep)
# ---------------------------------------------------------------------------

def fmp_get(path, ticker, **params):
    if not FMP_API_KEY:
        return None
    url = f"{FMP_BASE}/{path}"
    params["symbol"] = ticker
    params["apikey"] = FMP_API_KEY
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        return data if data else None
    except Exception as e:
        log(f"FMP {path}?symbol={ticker} hata: {e}")
        return None


def first(lst):
    return lst[0] if isinstance(lst, list) and lst else (lst if isinstance(lst, dict) else None)


def pick(d, *keys):
    """Birden fazla olası alan adını dener (FMP şema zamanla değişebiliyor)."""
    if not d:
        return None
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def fetch_yfinance_fundamentals(ticker):
    """FMP'nin ücretsiz katmanda artık vermediği rasyoları (F/K, net marj, büyüme,
    borç/özkaynak, cari oran, piyasa değeri) yfinance'in .info verisinden tamamlar.
    yfinance alan bulamazsa ilgili anahtarlar None kalır, kart yine de oluşur."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as e:
        log(f"yfinance fundamentals {ticker} hata: {e}")
        return {}

    debt_to_equity = info.get("debtToEquity")
    if debt_to_equity is not None:
        debt_to_equity = debt_to_equity / 100  # yfinance bunu yüzde olarak döner (örn. 45.2 -> 0.452)

    net_debt = None
    total_debt, total_cash = info.get("totalDebt"), info.get("totalCash")
    if total_debt is not None and total_cash is not None:
        net_debt = total_debt - total_cash

    summary = info.get("longBusinessSummary")
    one_liner = (summary[:140].rsplit(" ", 1)[0] + "…") if summary and len(summary) > 140 else summary

    return {
        "marketCap": info.get("marketCap"),
        "oneLiner": one_liner,
        "peTTM": info.get("trailingPE"),
        "netMargin": info.get("profitMargins"),
        "revenueGrowthYoY": info.get("revenueGrowth"),
        "debtToEquity": debt_to_equity,
        "currentRatio": info.get("currentRatio"),
        "netDebt": net_debt,
    }


def fetch_company_financials(ticker, try_fmp=True):
    """Önce FMP'yi (varsa key ve erişimi), sonra eksik kalan alanları yfinance ile
    tamamlayarak şirket finansallarını üretir. Hiçbir kaynaktan veri gelmezse None
    döner ve o kart hiç oluşturulmaz."""
    fin = {}
    if try_fmp and FMP_API_KEY:
        quote = first(fmp_get("quote", ticker))
        profile = first(fmp_get("profile", ticker))
        ratios = first(fmp_get("ratios-ttm", ticker))
        growth = first(fmp_get("income-statement-growth", ticker, limit=1))
        cf_growth = first(fmp_get("cash-flow-statement-growth", ticker, limit=1))
        key_metrics = first(fmp_get("key-metrics-ttm", ticker))

        description = pick(profile, "description")
        one_liner = (description[:140].rsplit(" ", 1)[0] + "…") if description and len(description) > 140 else description

        fin = {
            "marketCap": pick(quote, "marketCap") or pick(profile, "mktCap"),
            "oneLiner": one_liner,
            "logoUrl": pick(profile, "image", "logo", "companyLogo"),
            "revenueGrowthYoY": pick(growth, "growthRevenue"),
            "netMargin": pick(ratios, "netProfitMarginTTM"),
            "fcfGrowthYoY": pick(cf_growth, "growthFreeCashFlow"),
            "peTTM": pick(ratios, "peRatioTTM") or pick(quote, "pe"),
            "debtToEquity": pick(ratios, "debtEquityRatioTTM"),
            "currentRatio": pick(ratios, "currentRatioTTM"),
            "interestCoverage": pick(ratios, "interestCoverageTTM") or pick(key_metrics, "interestCoverageTTM"),
            "netDebt": pick(key_metrics, "netDebtTTM", "netDebt"),
        }

    yf_fin = fetch_yfinance_fundamentals(ticker)
    for key in ("marketCap", "oneLiner", "peTTM", "netMargin", "revenueGrowthYoY", "debtToEquity", "currentRatio", "netDebt"):
        if fin.get(key) is None and yf_fin.get(key) is not None:
            fin[key] = yf_fin[key]

    has_any = any(v is not None for k, v in fin.items() if k != "logoUrl")
    return fin if has_any else None



# ---------------------------------------------------------------------------
# Net varlık hesaplamaları
# ---------------------------------------------------------------------------

def fetch_spot_price(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d")
        if h.empty:
            return None
        return float(h["Close"].iloc[-1])
    except Exception as e:
        log(f"{ticker} spot fiyat hatası: {e}")
        return None


# USDT/USDC gibi dolar stablecoinleri 1:1 kabul edilir (küçük depeg farkları ihmal
# edilir, kişisel portföy takibi için yeterli hassasiyet).
STABLECOIN_1_TO_1 = {"USDT", "USDC", "DAI", "BUSD"}

_usdtry_rate_cache = None  # Aynı çalıştırma içinde tek seferlik çekilir; tutarlılık + hız için


def get_usdtry_rate():
    """USD/TRY kurunu bir kez çekip önbelleğe alır. Sonraki tüm TRY çevirimleri
    (nakit, BIST) aynı çalıştırma içinde bu tek kuru kullanır, böylece dakikalar
    içinde ufak kur oynamalarından dolayı tutarsız sonuçlar oluşmaz."""
    global _usdtry_rate_cache
    if _usdtry_rate_cache is None:
        _usdtry_rate_cache = fetch_spot_price("USDTRY=X")
        if _usdtry_rate_cache:
            log(f"USD/TRY kuru: {_usdtry_rate_cache:.4f}")
    return _usdtry_rate_cache


def convert_to_usd(amount, currency):
    """amount: o para biriminden tutar, currency: 'USD', 'TRY', 'USDT' gibi 3 harfli kod."""
    if not amount:
        return 0.0
    currency = (currency or "USD").upper()
    if currency == "USD" or currency in STABLECOIN_1_TO_1:
        return amount
    if currency == "TRY":
        usdtry = get_usdtry_rate()
        return (amount / usdtry) if usdtry else 0.0
    # Diğer döviz cinsleri için genel deneme (çoğu "USDXXX=X" formatında, 1 USD = X yabancı para)
    rate = fetch_spot_price(f"USD{currency}=X")
    if rate:
        return amount / rate
    log(f"{currency} için kur bulunamadı, bu tutar 0 olarak sayıldı")
    return 0.0


def build_dataset(config):
    holdings_out = []
    momentum_rows = []
    company_cards = []
    ai_cache = load_ai_cache()
    get_usdtry_rate()  # her zaman dolu olsun diye erkenden çekilir (arayüzde USD->TRY gösterimi için)

    stock_total = 0.0

    for h in config["stockPortfolio"]["holdings"]:
        ticker = h["ticker"]
        fetch_ticker = h.get("fetchTicker", ticker)  # yfinance/FMP'nin gerçekte tanıdığı sembol farklıysa
        shares = h.get("shares", 0)
        if not shares:
            continue
        try:
            pm = fetch_price_and_moving_averages(fetch_ticker)
        except Exception as e:
            log(f"{ticker} fiyat hatası, atlanıyor: {e}")
            continue

        asset_class = h.get("assetClass", "Tek Hisse")
        currency = h.get("currency", "USD")
        native_value = pm["price"] * shares
        value = convert_to_usd(native_value, currency) if currency != "USD" else native_value
        stock_total += value

        # FMP yalnızca ABD/global borsalarda listeli şirketleri tanıyor — BIST ve
        # kripto için boşuna istek atıp günlük kotayı tüketmemek üzere atlanır.
        # ETF'ler için de çağrılır (logo + açıklama için), ama kart oluşturulmaz.
        # Not: assetClass tam string eşleşmesi yerine anahtar kelimeyle kontrol
        # edilir, böylece "Tek Hisse", "ABD Hisse" gibi serbest etiketler de çalışır.
        ac_norm = asset_class.strip().lower()
        is_crypto = "kripto" in ac_norm or "crypto" in ac_norm
        is_bist = "bist" in ac_norm
        is_etf = "etf" in ac_norm
        is_cash_like = "nakit" in ac_norm or "cash" in ac_norm

        fin = None
        if not is_crypto:
            fin = fetch_company_financials(fetch_ticker, try_fmp=not is_bist)
            time.sleep(0.1 if is_bist else 0.3)
        else:
            time.sleep(0.1)

        holdings_out.append({
            "ticker": ticker, "name": h.get("name", ticker),
            "assetClass": asset_class,
            "shares": shares, "price": pm["price"], "currency": currency, "value": value,
            "logoUrl": (fin or {}).get("logoUrl"),
        })
        momentum_rows.append({
            "ticker": ticker, "name": h.get("name", ticker),
            "group": "portfolio", **pm["ma"], "status": pm["status"],
        })

        # Şirket Finansalları kartları tekil hisseler için gösterilir (ABD hisseleri,
        # BIST, ya da başka herhangi bir tekil hisse etiketi). ETF/Kripto/Nakit hariç
        # tutulur — etiket tam olarak ne yazarsan yaz ("Tek Hisse", "ABD Hisse" vb.).
        if is_etf or is_crypto or is_cash_like:
            continue

        override = config.get("targetPriceOverrides", {}).get(ticker)
        ai_entry = ai_cache.get(ticker)

        target_price = None
        target_source = None
        if override:
            target_price = _build_target_price(override, pm["price"])
            target_source = "manuel"
        elif ai_entry and all(ai_entry.get(k) is not None for k in ("bear", "base", "bull")):
            target_price = _build_target_price(ai_entry, pm["price"])
            target_source = "ai"

        manual_guidance = config.get("guidanceNotes", {}).get(ticker)
        guidance = manual_guidance if isinstance(manual_guidance, list) else (
            ai_entry.get("guidance") if ai_entry and isinstance(ai_entry.get("guidance"), list) else None
        )

        # BIST için fin=None olur (FMP'de veri yok) — kart yine de oluşturulur,
        # finansal metrikler arayüzde otomatik "—" gösterilir.
        empty_fin = {"marketCap": None, "oneLiner": None, "logoUrl": None, "revenueGrowthYoY": None,
                     "netMargin": None, "fcfGrowthYoY": None, "peTTM": None, "debtToEquity": None,
                     "currentRatio": None, "interestCoverage": None, "netDebt": None}

        company_cards.append({
            "ticker": ticker, "name": h.get("name", ticker),
            "currentPrice": pm["price"],
            **(fin or empty_fin),
            "targetPrice": target_price,
            "targetPriceSource": target_source,
            "guidance": guidance,
        })


    cash = config["stockPortfolio"]["cash"]
    cash_amounts = cash.get("amounts", {})
    cash_value = sum(convert_to_usd(amt, cur) for cur, amt in cash_amounts.items())
    stock_total_with_cash = stock_total + cash_value
    if cash_value:
        holdings_out.append({
            "ticker": "CASH", "name": cash.get("label", "Nakit"),
            "subtitle": cash.get("subtitle", ""),
            "assetClass": "Nakit", "shares": None, "price": None, "value": cash_value,
        })

    for row in holdings_out:
        row["weightPct"] = round((row["value"] / stock_total_with_cash) * 100, 1) if stock_total_with_cash else 0

    # Endeks referansları (SPX / QQQ) momentum tablosunun en üstüne
    index_rows = []
    for ticker, name in (("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100")):
        try:
            pm = fetch_price_and_moving_averages(ticker)
            index_rows.append({"ticker": ticker.replace("^", ""), "name": name,
                                "group": "index", **pm["ma"], "status": pm["status"]})
        except Exception as e:
            log(f"{ticker} endeks hatası: {e}")

    # --- Net varlık ---
    nw = config["netWorth"]

    # BIST ve Kripto (spot) pozisyonlar stockPortfolio.holdings içinde tutuluyor
    # ("assetClass": "BIST" / "Kripto"), Hisse Portföyü sekmesinde kendi satırlarıyla
    # görünüyorlar. Net Varlık'taki ilgili toplamlar da aynı listeden (zaten USD'ye
    # çevrilmiş) hesaplanır. Eşleşme anahtar kelimeyle yapılır (serbest etiketler
    # de çalışsın diye), tam string eşleşmesi değil.
    bist_total = sum(row["value"] for row in holdings_out if "bist" in row["assetClass"].strip().lower())
    crypto_spot_total = sum(row["value"] for row in holdings_out
                             if "kripto" in row["assetClass"].strip().lower() or "crypto" in row["assetClass"].strip().lower())

    gold_grams = nw.get("goldGrams", 0)
    gold_value = 0.0
    if gold_grams:
        gold_oz_price = fetch_spot_price("GC=F")
        if gold_oz_price:
            gold_value = (gold_oz_price / GRAMS_PER_TROY_OUNCE) * gold_grams

    real_estate = nw.get("realEstateUSD", 0)
    btc_futures = nw.get("bitcoinFuturesUSD", 0)
    # BIST, Kripto ve Nakit ayrı kategorilerde sayıldığı için ABD Hisse toplamından düşülür
    us_stock_total = stock_total_with_cash - bist_total - crypto_spot_total - cash_value

    net_worth_categories = [
        {"id": "us-stocks", "name": "ABD Hisse Portföyü", "subtitle": nw.get("usStocksSubtitle", "ABD Hisse"), "value": us_stock_total},
        {"id": "real-estate", "name": nw.get("realEstateLabel", "Gayrimenkul"), "subtitle": nw.get("realEstateSubtitle", ""), "value": real_estate},
        {"id": "bist", "name": "Borsa İstanbul", "subtitle": nw.get("bistSubtitle", ""), "value": bist_total},
        {"id": "crypto-spot", "name": "Kripto SPOT", "subtitle": nw.get("cryptoSpotSubtitle", "Spot kripto varlıklar"), "value": crypto_spot_total},
        {"id": "cash", "name": nw.get("cashLabel", "Nakit"), "subtitle": nw.get("cashSubtitle", "USD / USDT / TRY"), "value": cash_value},
        {"id": "gold", "name": "Altın", "subtitle": nw.get("goldSubtitle", ""), "value": gold_value},
        {"id": "btc-futures", "name": "Bitcoin Futures", "subtitle": nw.get("bitcoinFuturesSubtitle", ""), "value": btc_futures},
    ]
    net_worth_total = sum(c["value"] for c in net_worth_categories)
    for c in net_worth_categories:
        c["weightPct"] = round((c["value"] / net_worth_total) * 100, 1) if net_worth_total else 0

    # --- Varlık sınıfı dağılımı (hisse portföyü içinde) ---
    asset_class_totals = {}
    for row in holdings_out:
        ac = row["assetClass"]
        asset_class_totals[ac] = asset_class_totals.get(ac, 0) + row["value"]
    asset_classes = [
        {"name": k, "value": v, "weightPct": round((v / stock_total_with_cash) * 100, 1) if stock_total_with_cash else 0}
        for k, v in sorted(asset_class_totals.items(), key=lambda kv: -kv[1])
    ]

    # AI içgörülerinin en son ne zaman güncellendiğini (tüm ticker'lar arasında en
    # yenisi) ekrana göstermek için hesaplanır — kullanıcı hangi hedef fiyat/öne
    # çıkanlar verisinin ne kadar taze olduğunu görebilsin diye.
    ai_updated_dates = [v.get("updatedAt") for v in ai_cache.values() if v.get("updatedAt")]
    ai_insights_updated_at = max(ai_updated_dates) if ai_updated_dates else None

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "aiInsightsUpdatedAt": ai_insights_updated_at,
        "usdTryRate": _usdtry_rate_cache,
        "netWorth": {"total": net_worth_total, "categories": net_worth_categories},
        "stockPortfolio": {
            "total": stock_total_with_cash,
            "positionCount": len(holdings_out),
            "holdings": sorted(holdings_out, key=lambda r: -r["weightPct"]),
        },
        "assetClasses": asset_classes,
        "momentum": {"index": index_rows, "portfolio": momentum_rows},
        "companyCards": sorted(company_cards, key=lambda c: -(c.get("marketCap") or 0)),
    }


def _build_target_price(override, current_price):
    bear, base, bull = override["bear"], override["base"], override["bull"]
    expected = bear * 0.25 + base * 0.50 + bull * 0.25
    upside = ((expected / current_price) - 1) * 100 if current_price else None
    return {
        "confidence": override.get("confidence", "düşük"),
        "bear": bear, "base": base, "bull": bull,
        "expected": round(expected, 2),
        "upsidePct": round(upside, 1) if upside is not None else None,
    }


def main():
    config = load_config()
    try:
        dataset = build_dataset(config)
    except Exception:
        log("KRİTİK HATA - veri üretilemedi:")
        traceback.print_exc()
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    holdings = dataset["stockPortfolio"]["holdings"]
    with_logo = sum(1 for h in holdings if h.get("logoUrl"))
    log(f"Yazıldı: {OUTPUT_PATH} ({dataset['stockPortfolio']['positionCount']} pozisyon, "
        f"{len(dataset['companyCards'])} şirket kartı, {with_logo}/{len(holdings)} pozisyonda FMP logosu bulundu)")


if __name__ == "__main__":
    main()
