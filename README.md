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

## Neler tam otomatik, neler değil

**Otomatik:** hisse/BIST/altın/Bitcoin fiyatları, hareketli ortalamalar ve "Güçlü"
durumu, piyasa değeri, gelir/FCF büyümesi, net marj, F/K, borç/özkaynak, cari oran,
faiz karşılama, net borç, varlık sınıfı dağılımı, net varlık yüzdeleri.

**Manuel kalanlar (piyasa verisinden mekanik üretilemiyor):**
- Gayrimenkul değeri (`config.json` → `netWorth.realEstateUSD`) — periyodik olarak
  sen güncellersin.
- Bitcoin Futures pozisyon değeri (`netWorth.bitcoinFuturesUSD`) — kaldıraçlı/marjin
  pozisyon büyüklüğü borsanın kendi hesabında, istersen ileride Bybit/Binance API'siyle
  otomatikleştirilebilir.
- Hedef Fiyat (Ayı/Baz/Boğa senaryoları) — `config.json` → `targetPriceOverrides`
  içine elle girersen kartta gösterilir; girmezsen o bölüm atlanır.
- "Beklenti & Öne Çıkanlar" maddeleri — haber/kazanç çağrısı özeti olduğu için
  `config.json` → `guidanceNotes` içine elle eklenir.

## Bilinmesi gerekenler

- **TradingView bağlanamaz** — resmi bir veri API'leri yok, bu yüzden Yahoo Finance
  (yfinance) ve Financial Modeling Prep kullanıldı.
- FMP ücretsiz katmanın günlük çağrı limiti var; çok sayıda pozisyon eklersen
  workflow bazı şirket kartlarını atlayabilir (script hata vermez, sadece o alanlar
  "—" görünür). Gerekirse FMP'de ücretli bir plana geçilebilir.
- BIST hisseleri yfinance'te `.IS` uzantısıyla aranır (`GARAN.IS`, `THYAO.IS` gibi).
- FMP'nin döndürdüğü alan adları zaman zaman değişebiliyor; bir metrik sürekli "—"
  görünüyorsa `scripts/fetch_data.py` içindeki `pick(...)` çağrılarını güncel FMP
  dokümantasyonuna göre güncellemek gerekebilir.
- Repo **public** olduğu için URL'ini bilen herkes verilerini görebilir (paylaşmazsan
  pratikte kimse bulamaz). Daha sıkı gizlilik istersen private repo + GitHub Pro
  ($/ay) ya da tamamen yerel kullanım alternatif.
