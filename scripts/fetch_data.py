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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "data" / "config.json"
OUTPUT_PATH = ROOT / "data" / "data.json"
AI_CACHE_PATH = ROOT / "data" / "ai_cache.json"

FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"
EVDS_API_KEY = os.environ.get("EVDS_API_KEY", "")
EVDS_BASE = "https://evds2.tcmb.gov.tr/service/evds"
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

    # Yahoo bazen günün son satırını NaN kapanışla döndürüyor (özellikle BIST'te,
    # piyasa saatleri dışında). NaN satırları atılır, son GEÇERLİ kapanış kullanılır.
    daily_closes = daily["Close"].dropna()
    if daily_closes.empty:
        raise ValueError(f"{ticker}: geçerli kapanış fiyatı yok (tüm satırlar NaN)")

    price = float(daily_closes.iloc[-1])

    ma50d = daily_closes.rolling(50).mean().iloc[-1]
    ma200d = daily_closes.rolling(200).mean().iloc[-1]
    ma50d = float(ma50d) if ma50d == ma50d else None      # NaN kontrolü
    ma200d = float(ma200d) if ma200d == ma200d else None

    ma50w = ma100w = ma200w = None
    if not weekly.empty:
        s = weekly["Close"].dropna()
        if not s.empty:
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
        "series": daily_closes,  # tarihsel net varlık hesabı için (JSON'a yazılmaz)
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

_usdtry_series_cache = None  # 2 yıllık günlük USD/TRY serisi; tek seferlik çekilir


def series_value_at(series, target_date):
    """Bir pandas fiyat serisinde, verilen tarihe eşit ya da ondan önceki son
    geçerli değeri döner. Seri o tarihten sonra başlıyorsa (varlık o gün henüz
    yoktu) ilk bilinen değer kullanılır — dürüst bir yaklaşıklık, UI'da not edilir."""
    if series is None or len(series) == 0:
        return None
    ts = pd.Timestamp(target_date)
    if series.index.tz is not None:
        ts = ts.tz_localize(series.index.tz)
    s = series[series.index <= ts]
    if len(s) == 0:
        return float(series.iloc[0])
    return float(s.iloc[-1])


def get_usdtry_series():
    """USD/TRY'nin 2 yıllık günlük kapanış serisini tek seferlik çekip önbelleğe alır.
    Hem güncel kur hem geçmiş tarihli çevirimler (lot maliyeti, tarihsel net varlık)
    bu tek seriden okunur — tutarlılık ve tek API çağrısı."""
    global _usdtry_series_cache
    if _usdtry_series_cache is None:
        try:
            h = yf.Ticker("USDTRY=X").history(period="2y", interval="1d")
            _usdtry_series_cache = h["Close"].dropna() if not h.empty else pd.Series(dtype=float)
        except Exception as e:
            log(f"USD/TRY serisi çekilemedi: {e}")
            _usdtry_series_cache = pd.Series(dtype=float)
        if len(_usdtry_series_cache):
            log(f"USD/TRY kuru: {float(_usdtry_series_cache.iloc[-1]):.4f} "
                f"({len(_usdtry_series_cache)} günlük geçmiş yüklendi)")
    return _usdtry_series_cache


def get_usdtry_rate():
    s = get_usdtry_series()
    return float(s.iloc[-1]) if len(s) else None


def usdtry_at(target_date):
    return series_value_at(get_usdtry_series(), target_date)


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


# ---------------------------------------------------------------------------
# TCMB EVDS - mevduat faizi (enflasyon/alternatif getiri kıyaslaması için)
# ---------------------------------------------------------------------------

_evds_rates_cache = None  # [(date, yıllık_faiz_yüzde), ...] sıralı


def fetch_evds_deposit_rates(start_date, series_code):
    """TCMB EVDS'den TL mevduat ağırlıklı ortalama faiz serisini (haftalık, yıllık %
    cinsinden) çeker. Key yoksa ya da hata olursa None döner; enflasyon sütunu '—' kalır."""
    global _evds_rates_cache
    if _evds_rates_cache is not None:
        return _evds_rates_cache
    if not EVDS_API_KEY:
        log("EVDS_API_KEY tanımlı değil — enflasyon/mevduat kıyaslaması atlanıyor "
            "(opsiyoneldir, secret eklersen otomatik aktifleşir).")
        return None
    try:
        params = {
            "series": series_code,
            "startDate": start_date.strftime("%d-%m-%Y"),
            "endDate": date.today().strftime("%d-%m-%Y"),
            "type": "json",
        }
        r = requests.get(EVDS_BASE, params=params, headers={"key": EVDS_API_KEY}, timeout=30)
        r.raise_for_status()
        items = r.json().get("items", [])
        col = series_code.replace(".", "_")
        rates = []
        for it in items:
            raw = it.get(col)
            if raw in (None, "", "null"):
                continue
            try:
                d = datetime.strptime(it["Tarih"], "%d-%m-%Y").date()
                rates.append((d, float(str(raw).replace(",", "."))))
            except Exception:
                continue
        rates.sort(key=lambda x: x[0])
        if not rates:
            log(f"EVDS {series_code}: veri boş döndü (seri kodu doğru mu?)")
            return None
        _evds_rates_cache = rates
        log(f"EVDS mevduat faizi yüklendi: {len(rates)} kayıt, son değer %{rates[-1][1]:.2f} ({rates[-1][0]})")
        return rates
    except Exception as e:
        log(f"EVDS çekme hatası: {e}")
        return None


def deposit_growth_try(principal_try, start_date, rates):
    """Verilen TL anaparayı, start_date'ten bugüne EVDS haftalık faiz serisiyle
    günlük bileşik büyütür (her gün için geçerli yıllık oran / 365). 'Aynı parayı
    o gün mevduata yatırsaydın bugün ne olurdu' sorusunun cevabı. Stopaj/vergi
    hesaba katılmaz (brüt getiri)."""
    if not rates or principal_try <= 0:
        return None
    value = principal_try
    day = start_date
    today = date.today()
    idx = 0
    current_rate = rates[0][1]
    while day < today:
        while idx + 1 < len(rates) and rates[idx + 1][0] <= day:
            idx += 1
            current_rate = rates[idx][1]
        value *= (1 + current_rate / 100 / 365)
        day += timedelta(days=1)
    return value


def build_dataset(config):
    holdings_out = []
    momentum_rows = []
    company_cards = []
    hist_items = []  # tarihsel net varlık için: {"shares","currency","series"}
    ai_cache = load_ai_cache()
    get_usdtry_rate()  # her zaman dolu olsun diye erkenden çekilir (arayüzde USD->TRY gösterimi için)

    settings = config.get("settings", {})
    evds_series_code = settings.get("evdsSeriesCode", "TP.TRY.MT02")

    # Lot'lu pozisyonlar varsa, en eski alım tarihinden bugüne EVDS faiz serisi
    # tek seferde çekilir (her lot için ayrı istek atılmaz).
    all_lot_dates = []
    for h in config["stockPortfolio"]["holdings"]:
        for lot in h.get("lots", []):
            try:
                all_lot_dates.append(datetime.strptime(lot["date"], "%Y-%m-%d").date())
            except Exception:
                log(f"{h.get('ticker')}: lot tarihi hatalı ({lot.get('date')}), YYYY-AA-GG formatında olmalı")
    evds_rates = fetch_evds_deposit_rates(min(all_lot_dates), evds_series_code) if all_lot_dates else None

    stock_total = 0.0

    for h in config["stockPortfolio"]["holdings"]:
        ticker = h["ticker"]
        fetch_ticker = h.get("fetchTicker", ticker)  # yfinance/FMP'nin gerçekte tanıdığı sembol farklıysa
        lots = h.get("lots") or []
        shares = sum(l.get("shares", 0) for l in lots) if lots else h.get("shares", 0)
        if not shares:
            continue
        try:
            pm = fetch_price_and_moving_averages(fetch_ticker)
        except Exception as e:
            log(f"{ticker} fiyat hatası, atlanıyor: {e}")
            continue

        if pm["price"] is None or pm["price"] != pm["price"]:  # NaN kontrolü
            log(f"{ticker}: fiyat NaN/None döndü, pozisyon atlanıyor (toplamları bozmasın diye)")
            continue

        price_series = pm.pop("series", None)

        asset_class = h.get("assetClass", "Tek Hisse")
        currency = h.get("currency", "USD")
        native_value = pm["price"] * shares
        value = convert_to_usd(native_value, currency) if currency != "USD" else native_value
        stock_total += value

        hist_items.append({"shares": shares, "currency": currency, "series": price_series})

        # --- Lot bazlı maliyet / getiri / mevduat alternatifi (feature 5) ---
        # Alım fiyatı enstrümanın kendi para birimindedir (BIST için TL, diğerleri USD).
        # Alım günü kuru yfinance USD/TRY serisinden otomatik bulunur.
        avg_cost = cost_native_total = return_native = return_pct = None
        inflation_alt_try = None
        if lots:
            cost_native_total = 0.0
            cost_try_total = 0.0
            inflation_alt_try = 0.0 if evds_rates else None
            valid = True
            for lot in lots:
                try:
                    lot_date = datetime.strptime(lot["date"], "%Y-%m-%d").date()
                    lot_shares = float(lot["shares"])
                    lot_price = float(lot["price"])
                except Exception:
                    valid = False
                    break
                lot_cost_native = lot_shares * lot_price
                cost_native_total += lot_cost_native
                fx = usdtry_at(lot_date)
                if currency == "TRY":
                    lot_cost_try = lot_cost_native
                else:
                    lot_cost_try = lot_cost_native * fx if fx else None
                if lot_cost_try is not None:
                    cost_try_total += lot_cost_try
                    if evds_rates:
                        grown = deposit_growth_try(lot_cost_try, lot_date, evds_rates)
                        if grown is not None and inflation_alt_try is not None:
                            inflation_alt_try += grown
                else:
                    inflation_alt_try = None  # kur bulunamadıysa kıyas yapılamaz
            if valid and cost_native_total > 0:
                avg_cost = cost_native_total / shares
                return_native = native_value - cost_native_total
                return_pct = (native_value / cost_native_total - 1) * 100
            else:
                cost_native_total = None

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

        current_rate = get_usdtry_rate()
        holdings_out.append({
            "ticker": ticker, "name": h.get("name", ticker),
            "assetClass": asset_class,
            "shares": shares, "price": pm["price"], "currency": currency, "value": value,
            "valueTRY": (value * current_rate) if current_rate else None,
            "avgCost": avg_cost,
            "costTotalNative": cost_native_total,
            "returnNative": return_native,
            "returnPct": return_pct,
            "inflationAltTRY": inflation_alt_try,
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
    gold_series = None
    if gold_grams:
        try:
            gh = yf.Ticker("GC=F").history(period="2y", interval="1d")
            gold_series = gh["Close"].dropna() if not gh.empty else None
        except Exception as e:
            log(f"Altın serisi hatası: {e}")
        if gold_series is not None and len(gold_series):
            gold_value = (float(gold_series.iloc[-1]) / GRAMS_PER_TROY_OUNCE) * gold_grams

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

    # --- Tarihsel net varlık (feature 4) ---
    # Mevcut portföy bileşiminin (bugünkü adetlerin) geçmiş fiyatlarla değerlenmesi.
    # Not: geçmişte farklı pozisyonlar tutuyorduysan bu bir yaklaşıklıktır; nakit,
    # gayrimenkul ve BTC futures sabit kabul edilir (geçmiş değerleri bilinemez),
    # TRY nakit ise o günkü kurla USD'ye çevrilir.
    cash_usd_like = sum(amt for cur, amt in cash_amounts.items()
                        if cur.upper() == "USD" or cur.upper() in STABLECOIN_1_TO_1)
    cash_try_amount = sum(amt for cur, amt in cash_amounts.items() if cur.upper() == "TRY")
    static_usd = real_estate + btc_futures

    def net_worth_usd_at(d):
        total = static_usd + cash_usd_like
        fx = usdtry_at(d)
        if cash_try_amount and fx:
            total += cash_try_amount / fx
        for item in hist_items:
            p = series_value_at(item["series"], d)
            if p is None:
                continue
            v = p * item["shares"]
            if item["currency"] == "TRY":
                v = (v / fx) if fx else 0.0
            total += v
        if gold_series is not None and gold_grams:
            gp = series_value_at(gold_series, d)
            if gp:
                total += (gp / GRAMS_PER_TROY_OUNCE) * gold_grams
        return total

    today = date.today()
    current_rate_now = get_usdtry_rate()
    period_dates = {
        "1A": today - timedelta(days=30),
        "6A": today - timedelta(days=182),
        "12A": today - timedelta(days=365),
        "YBI": date(today.year, 1, 1),
    }
    net_worth_history = {}
    for label, d in period_dates.items():
        then_usd = net_worth_usd_at(d)
        then_fx = usdtry_at(d)
        then_try = then_usd * then_fx if then_fx else None
        now_try = net_worth_total * current_rate_now if current_rate_now else None
        net_worth_history[label] = {
            "asOf": d.isoformat(),
            "thenUSD": then_usd,
            "nowUSD": net_worth_total,
            "changeUSD": net_worth_total - then_usd,
            "changePctUSD": ((net_worth_total / then_usd) - 1) * 100 if then_usd else None,
            "thenTRY": then_try,
            "nowTRY": now_try,
            "changeTRY": (now_try - then_try) if (now_try is not None and then_try is not None) else None,
            "changePctTRY": ((now_try / then_try) - 1) * 100 if (now_try and then_try) else None,
        }

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
        "usdTryRate": get_usdtry_rate(),
        "netWorth": {"total": net_worth_total, "categories": net_worth_categories, "history": net_worth_history},
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


def sanitize_nan(obj):
    """JSON standardı NaN/Infinity kabul etmez ama Python'un json.dump'ı sessizce
    yazar ve tarayıcı tüm dosyayı reddeder. Güvenlik ağı olarak tüm veri yapısını
    dolaşıp NaN/Inf değerleri None'a çevirir — arayüz None'ı zaten '—' gösterir."""
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    if isinstance(obj, dict):
        return {k: sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_nan(v) for v in obj]
    return obj


def main():
    config = load_config()
    try:
        dataset = build_dataset(config)
    except Exception:
        log("KRİTİK HATA - veri üretilemedi:")
        traceback.print_exc()
        sys.exit(1)

    dataset = sanitize_nan(dataset)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2, allow_nan=False)
    holdings = dataset["stockPortfolio"]["holdings"]
    with_logo = sum(1 for h in holdings if h.get("logoUrl"))
    log(f"Yazıldı: {OUTPUT_PATH} ({dataset['stockPortfolio']['positionCount']} pozisyon, "
        f"{len(dataset['companyCards'])} şirket kartı, {with_logo}/{len(holdings)} pozisyonda FMP logosu bulundu)")


if __name__ == "__main__":
    main()
