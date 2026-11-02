# AI Sales Analyst

Superstore verisini analiz eden ve ileride LLM destekli soru-cevap ile genişletilebilecek başlangıç projesi.

## Klasör yapısı

```text
ai-sales-analyst/
├── data/
│   └── README.md
├── notebooks/
│   └── 01_exploration.ipynb
├── src/
│   ├── __init__.py
│   ├── main.py
│   └── analysis.py
├── tests/
│   ├── .gitkeep
│   └── README.md
├── README.md
├── requirements.txt
├── .gitignore
└── .env.example
```

## 1. Kurulum

Python 3.10 veya üzeri kullanın. ZIP'i açıp proje klasöründe terminal açın:

```powershell
cd ai-sales-analyst
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows'ta etkinleştirme engellenirse komutları doğrudan `.\.venv\Scripts\python.exe` ile çalıştırabilirsiniz. macOS/Linux için ortam oluşturma komutu `python3 -m venv .venv`, etkinleştirme komutu `source .venv/bin/activate` şeklindedir.

Temel analiz yalnızca Python standart kütüphanesini kullanır; ek paketler notebook ve sonraki geliştirmeler içindir.

## 2. Veriyi ekleyin

Superstore CSV bu pakete dahil değildir. Elinizdeki gerçek **Sample - Superstore.csv** dosyasını şu konuma kopyalayın:

```text
ai-sales-analyst/data/Sample - Superstore.csv
```

CSV dosyasını yeniden adlandırmanız gerekmez. Başka konum için `--data` kullanabilirsiniz. Başlangıç kodu virgülle ayrılmış CSV, nokta ondalık ayracı ve tam olarak `Sales` adlı sütun bekler. UTF-8 ve yaygın Windows cp1252 kodlamaları desteklenir.

## 3. Çalıştırın

Komutları proje kökünde çalıştırın:

```powershell
python -m src.main
python -m src.main --data "data/Sample - Superstore.csv"
python -m src.main --help
```

Veri yoksa program beklenen dosya yolunu gösterir ve veri üretmez. Veri varsa satır sayısını, toplam satışı ve varsa toplam kârı terminale yazar. Boş/parasal olmayan değerleri sessizce atlamak yerine hata verir. Veriler dış servise gönderilmez.

## 4. Notebook

```powershell
jupyter notebook
```

`notebooks/01_exploration.ipynb` dosyasını açın. İlk hücre CSV'yi yükler; sonraki hücreler sütunları, eksik değerleri ve kategorilere göre satışları incelemek içindir.

## 5. İlk günlük teslimat

- Gerçek CSV'yi belirtilen klasöre koyun ve başlangıç analizini çalıştırın.
- Notebook'ta sütun tiplerini ve eksik değerleri inceleyin.
- Veriden doğruladığınız üç iş bulgusunu notebook'a yazın.
- AI'dan bir analiz fonksiyonunu açıklamasını veya sınır durumlarını önermesini isteyin; önerileri gerçek veri ve hesaplamalarla kontrol edin.

## Sonraki geliştirme

Kategori/bölge bazında analiz, grafikler, testler ve ardından LLM araç çağırma katmanı eklenebilir. Bu başlangıç paketinde LLM/API entegrasyonu yoktur. Sayısal sonuçları Python ile hesaplayıp LLM'i bu sonuçları açıklamak için kullanın.

`.env.example` gelecekteki entegrasyonlar için yer tutucudur. Gerekirse `.env` olarak kopyalayın; mevcut kod bu dosyayı okumaz ve API anahtarı gerektirmez. Gerçek anahtarları kodunuza veya Git'e eklemeyin.
