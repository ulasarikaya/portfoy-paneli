# Portföy Paneli

Kendi net varlık ve hisse portföyünü otomatik güncellenen, tek sayfalık bir panelde
gösteren kişisel araç. Fiyatlar ve hareketli ortalamalar yfinance, şirket finansalları
Financial Modeling Prep'ten otomatik çekilir; sen sadece pozisyon değiştiğinde
`data/config.json`'u güncellersin.

## Nasıl çalışıyor

```
GitHub Actions (her gün otomatik)
   └─ scripts/fetch_data.py
        ├─ yfinance        → fiyat, 50G/200G/50H/100H/200H hareketli ortalama
        └─ FMP API         → piyasa değeri, büyüme, marj, bilanço rasyoları
        └─ data/data.json'a yazar, commit + push eder
index.html (GitHub Pages)
   └─ data/data.json'u okuyup grafikleri/tabloyu/kartları çizer
```

Tarayıcı hiçbir zaman canlı bir piyasa API'sine bağlanmaz — sadece kendi deponun
içindeki `data.json`'u okur. Bu yüzden CORS sorunu yaşanmaz ve sayfa hem GitHub
Pages'te hem (bir HTTP sunucusu üzerinden) yerelde sorunsuz çalışır.

## Kurulum (tek seferlik)

1. **FMP API key al** — https://site.financialmodelingprep.com adresinden ücretsiz
   bir hesap aç, dashboard'dan API anahtarını kopyala. (Bu adımı senin yapman
   gerekiyor, hesap oluşturma işlemini benim için yapamıyorum.)

2. **GitHub'da yeni bir public repo oluştur** (örn. `portfoy-paneli`).

3. Bu klasördeki tüm dosyaları o repoya yükle (GitHub web arayüzünden sürükle-bırak
   ya da `git push` ile).

4. **API key'i secret olarak ekle**: repo → *Settings* → *Secrets and variables* →
   *Actions* → *New repository secret* → adı `FMP_API_KEY`, değeri az önce aldığın
   key.

5. **GitHub Pages'i aç**: repo → *Settings* → *Pages* → *Source*: "Deploy from a
   branch" → branch: `main`, klasör: `/ (root)` → Save. Birkaç dakika sonra
   `https://kullanici-adin.github.io/portfoy-paneli/` adresi aktif olur.

6. **`data/config.json`'u kendi verilerinle doldur**: hisse ticker'ları ve adet,
   nakit tutarı, gayrimenkul değeri, BIST pozisyonları, altın (gram), Bitcoin
   futures pozisyon değeri. Dosyadaki `_readme` ve `_example` alanlarını silebilirsin,
   sadece dökümantasyon amaçlı.

7. **İlk veri çekimini elle tetikle**: repo → *Actions* sekmesi → "Portföy verisini
   güncelle" workflow'u → *Run workflow*. 1-2 dakika içinde `data/data.json`
   güncellenip otomatik commit edilecek. Sayfayı yenile, veriler görünmeli.

Bundan sonra workflow hafta içi her gün otomatik çalışıp veriyi tazeler (cron
zamanını `.github/workflows/update-data.yml` içinden değiştirebilirsin).

## Pozisyon değiştirdiğinde

Sadece `data/config.json`'u güncelle ve commit et — bir sonraki otomatik çalışmada
(ya da Actions sekmesinden elle tetiklersen hemen) fiyat/oran/grafikler kendiliğinden
güncellenir. Başka hiçbir dosyaya dokunman gerekmiyor.

## Yerelde önizleme

`index.html`'i doğrudan çift tıklayıp açarsan veri **yüklenmez** — tarayıcılar
`file://` üzerinden yerel JSON okumayı güvenlik gereği engelliyor. Bunun yerine
proje klasöründe basit bir sunucu başlat:

```bash
python3 -m http.server 8000
```

ve `http://localhost:8000` adresini aç.

## Hedef Fiyat ve Öne Çıkanlar'ı AI ile otomatikleştirmek (opsiyonel)

Bu iki alan artık elle girilmek zorunda değil. `scripts/fetch_ai_insights.py`,
Anthropic API'sini web search aracıyla kullanarak her hisse için gerçek analist
konsensüs hedef fiyatlarını arar ve son çeyrek sonuçlarından kısa bir özet çıkarır.
Ayrı, **haftalık** çalışan bir workflow olarak kurulu (`update-ai-insights.yml`) —
günlük fiyat güncellemesinden bilerek ayrıldı çünkü bu veri o kadar sık değişmiyor
ve her çağrı ücretli.

Açmak için:

1. https://console.anthropic.com adresinden bir API key oluştur (bu da senin
   yapman gereken bir hesap işlemi).
2. Repo → *Settings* → *Secrets and variables* → *Actions* → yeni secret:
   `ANTHROPIC_API_KEY`.
3. Actions sekmesinden "AI içgörülerini güncelle" workflow'unu bir kez elle
   tetikle. `data/ai_cache.json` dolacak, bir sonraki `fetch_data.py`
   çalışmasında kartlara otomatik işlenecek.

Kartlarda AI'dan gelen hedef fiyat **"AI · analist konsensüsü"** etiketiyle
işaretlenir — elle girdiğin (`config.json` → `targetPriceOverrides`)
değerlerden görsel olarak ayrılsın diye. Script gerçek analist hedefi bulamazsa
o kart için hedef fiyat bloğunu hiç göstermez, sayı uydurmaz.

`config.json` içine bir ticker için elle değer girersen, o her zaman AI'ın
önüne geçer.

**Maliyet:** Her ticker başına web search içeren bir API çağrısı yapılır;
portföy büyüklüğüne göre haftalık birkaç dolar civarında bir maliyet
oluşabilir. Sıklığı `.github/workflows/update-ai-insights.yml` içindeki
cron'dan ayarlayabilirsin (örn. aylığa düşürmek için).

## Neler tam otomatik, neler değil

**Otomatik (günlük, ücretsiz):** hisse/BIST/altın/Bitcoin fiyatları, hareketli
ortalamalar ve "Güçlü" durumu, piyasa değeri, gelir/FCF büyümesi, net marj, F/K,
borç/özkaynak, cari oran, faiz karşılama, net borç, varlık sınıfı dağılımı, net
varlık yüzdeleri.

**Otomatik (haftalık, ücretli — ANTHROPIC_API_KEY gerekir):** Hedef Fiyat
(Ayı/Baz/Boğa) ve "Öne Çıkanlar" maddeleri.

**Tamamen manuel kalanlar (piyasa verisinden ya da AI'dan mekanik üretilemiyor):**
- Gayrimenkul değeri (`config.json` → `netWorth.realEstateUSD`) — periyodik olarak
  sen güncellersin.
- Bitcoin Futures pozisyon değeri (`netWorth.bitcoinFuturesUSD`) — kaldıraçlı/marjin
  pozisyon büyüklüğü borsanın kendi hesabında, istersen ileride Bybit/Binance API'siyle
  otomatikleştirilebilir.

## Bilinmesi gerekenler

- **TradingView bağlanamaz** — resmi bir veri API'leri yok, bu yüzden Yahoo Finance
  (yfinance) ve Financial Modeling Prep kullanıldı.
- FMP ücretsiz katmanın günlük çağrı limiti var; çok sayıda pozisyon eklersen
  workflow bazı şirket kartlarını atlayabilir (script hata vermez, sadece o alanlar
  "—" görünür). Gerekirse FMP'de ücretli bir plana geçilebilir.
- BIST hisseleri yfinance'te `.IS` uzantısıyla aranır (`GARAN.IS`, `THYAO.IS` gibi).
- **Nakit birden fazla para biriminde girilebilir.** `config.json` → `stockPortfolio.cash.amounts`
  içine `{"USD": 1000, "USDT": 500, "TRY": 7000}` gibi her para birimini ayrı ayrı yazarsın,
  TL'yi elle dolara çevirmen gerekmez — script güncel USD/TRY kuruyla otomatik çevirip
  hepsini tek bir "Nakit" satırında toplar (donut/listede ayrı ayrı görünmez, dağılım
  sade kalır).
- **Spot kripto pozisyonları** (BTC, ETH, altcoinler) `stockPortfolio.holdings`'e normal bir
  pozisyon gibi eklenir; ticker'ı yfinance'in spot formatıyla yaz (`BTC-USD`, `ETH-USD`,
  `SOL-USD` gibi), `shares` alanına elindeki coin adedini gir, `assetClass: "Kripto"` koy.
  Crypto'nun "şirket finansalları" olmadığı için script bu pozisyonlar için FMP'ye hiç
  istek atmaz — sadece fiyat/hareketli ortalama (yfinance) çekilir, bu yeterli zaten.
  Kaldıraçlı/marjin pozisyonların (futures, spot olmayan) hâlâ `netWorth.bitcoinFuturesUSD`
  üzerinden manuel girilir, çünkü kaldıraç oranı borsa hesabının kendi içinde.
- FMP'nin döndürdüğü alan adları zaman zaman değişebiliyor; bir metrik sürekli "—"
  görünüyorsa `scripts/fetch_data.py` içindeki `pick(...)` çağrılarını güncel FMP
  dokümantasyonuna göre güncellemek gerekebilir.
- Repo **public** olduğu için URL'ini bilen herkes verilerini görebilir (paylaşmazsan
  pratikte kimse bulamaz). Daha sıkı gizlilik istersen private repo + GitHub Pro
  ($/ay) ya da tamamen yerel kullanım alternatif.
