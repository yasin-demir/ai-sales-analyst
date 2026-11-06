# AI Sales Analyst

Analiz sonuçları: [grafikli notebook](notebooks/01_sales_eda.ipynb) ve [indirilebilir HTML raporu](docs/reports/sales-eda.html). HTML raporu dokuz grafiği gömülü içerir; indirip tarayıcıda açabilirsiniz. [Rapor kapsamı ve yeniden üretme adımları](docs/reports/README.md).

Yerel API: `python -m src.api`. Sunucunun ürettiği oturumlar, veri sahipliği, süre sonu, güvenli silme ve özel JSON rapor indirmeleri [yaşam döngüsü rehberinde](docs/OWNERSHIP_AND_LIFECYCLE.md) açıklanır. API yalnızca `127.0.0.1` üzerinde çalışır.

API hazırlığının ilk paketi: çekirdek tür hataları, altı analiz için kesin Pydantic istek/yanıt modelleri ve dosya sorgusunda çift yönlü doğrulama. Kapsam, uyumluluk ve test komutları [sözleşme rehberinde](docs/API_CONTRACT_V1.md) yer alır.

Hazırlanmış veri hizmeti; yükleme, önizleme, onay, hazırlama ve sürüme bağlı sorgulamayı ayırır. Tekrarlı sorgularda kaynak dosyayı yeniden okumaz. [Hizmet rehberi ve çalıştırılabilir örnek](docs/PREPARED_DATA_SERVICE.md) bulunur.

Kendi CSV veya Excel satış dosyasından yerel, açıklamalı analiz raporu üreten Python projesi. Sütun isimleri Superstore ile aynı olmak zorunda değildir. Satış tutarı temel alandır; diğer analizler mevcut alanlara göre etkinleşir. Hesaplamalar yereldir; temel kullanımda dil modeli veya API anahtarı gerekmez. İsteğe bağlı `ask-ai` soruyu OpenAI veya yerel Ollama ile yorumlar. Anahtarsız kullanım için [yerel model rehberi](docs/LOCAL_MODEL_SETUP.md), ilk tasarım için [AI akışı rehberi](docs/AI_QUESTION_FLOW.md) bulunur.

## Kendi dosyanızla kullanım

Kurulumdan sonra `start_app.cmd` dosyası veya aşağıdaki komut masaüstü akışını açar:

```powershell
python -m src.app
```

Dosya seçimi → tarih/sayı biçimi → sütun eşleştirme → gözden geçirme → HTML raporu. Aynı dosya yapısının ayarları kaydedilip yeniden kullanılabilir. Kâr/bölge/tarih yoksa yalnızca desteklenen analizler hazırlanır. Çoklu para birimleri birleştirilmez; sorunlu kayıtlar sessizce atılmaz.

```powershell
python -m src.cli analyze examples/synthetic_sales.csv --config examples/synthetic_mapping.json --output work/demo.html
python -m src.cli inspect kendi_dosyam.csv --template work/taslak.json
```

Örnek dosyalar tamamen sentetiktir. Ayrıntılı eşleştirme, Excel ve veri kuralları [genel veri rehberinde](docs/GENERAL_DATA_GUIDE.md), yaygın kullanıma açılma adımları [yayın planında](docs/PUBLIC_RELEASE_PLAN.md) açıklanır.

Alternatif kurulum: `python -m pip install .` çekirdek aracı kurar; `python -m pip install ".[eda]"` notebook araçlarını da ekler. Kurulumdan sonra `sales-analyst` ve `sales-analyst-desktop` komutları bulunur. Bu Python paketidir; bağımsız Windows kurulum programı değildir.

Superstore verisi için yerel analiz kütüphanesi, komut satırı raporları ve açıklamalı Jupyter EDA çalışması. İsteğe bağlı sınırlı doğal dil yorumlayıcısı CLI üzerinden kullanılabilir. Yerel API yapılandırılmış sorguları oturum sahipliğiyle çalıştırır; sohbet ekranı henüz eklenmemiştir.

## Kurulum

Python 3.10+ kullanılır. Doğrulanan mevcut ortam Windows / Python 3.13'tür.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Geniş sürüm aralıkları desteklenen bağımlılıkları belirtir. Aynı Windows/Python 3.13 ortamını kurmak için `requirements-windows.lock.txt` tüm kurulu paketlerin tam sürüm kaydını içerir. Bu kayıt mevcut çalışan geliştirme ortamından üretilmiştir. Çekirdek wheel paketi temiz bir Windows/Python 3.13 ortamına kurulmuştur; çalıştırma sırasında Windows Uygulama Denetimi yeni indirilen pandas DLL dosyasını engellemiştir. Bu nedenle temiz ortamda çalışma doğrulanmış değildir. Paket içeriği ayrıca mevcut çalışan bağımlılıklarla test edilir; tam notebook ortamı kilidinin temiz kurulumu yapılmamıştır. Diğer işletim sistemlerinde platforma özgü paketler nedeniyle `requirements.txt` kullanılır.

## Veri

CSV yerelde `data/Sample - Superstore.csv` konumundadır ve Git'e eklenmez. Kaynak, sürüm ve kullanım koşulları için [veri notları](data/README.md), kullanılan dosyanın kimliği için [parmak izi](data/source_manifest.json) bulunur. Kaynak indirme bağlantısı ve yeniden dağıtım izni henüz doğrulanmamıştır.

Tam analiz sözleşmesi: gerekli kimlikler dolu, Row ID benzersiz, Sales pozitif, Quantity pozitif tam sayı, Discount 0–1 aralığında, sayılar sonlu ve Ship Date >= Order Date. Profit negatif veya sıfır olabilir. Tarih metinleri ay/gün/yıl biçimindedir. Eksik satırlar sessizce atılmaz; geçersiz veri hata verir. CSV UTF-8, cp1252 veya Latin-1 olarak okunabilir. Postal Code metin olarak korunur.

## Çalıştırma

Komutlar proje kökünden çalıştırılır:

```powershell
python -m src.main
python -m src.main --report category
python -m src.main --report region
python -m src.main --report losses
python -m src.main --report time
python -m src.main --report diagnostics --json
python -m src.main --data "data/Sample - Superstore.csv" --report category --json
```

Varsayılan özet eski hafif CSV okuyucusunu korur: Sales zorunlu, Profit isteğe bağlıdır ve toplamlar Decimal ile hesaplanır. Ayrıntılı raporlar tam veri sözleşmesini doğrular. JSON çıktısı kapsam ve ölçü birimlerini taşır; özetin kesin parasal değerleri metin olarak aktarılır. Yerel analiz komutları verileri dış servise göndermez. `ask-ai --provider ollama` yerel modeli kullanır; `--provider openai` yalnızca soru metnini OpenAI'ya gönderir.

## Notebook

```powershell
python -m notebook
```

Ana çalışma `notebooks/01_sales_eda.ipynb` dosyasıdır. Jupyter proje kökünden veya `notebooks` klasöründen açılabilir. Hesaplama fonksiyonları `src/analysis.py`, okuma ve doğrulama `src/data.py` içindedir. Notebook açıklama, tablo, grafik ve çalıştırma sırasında üretilen sayısal bulguları birleştirir.

Çalışma; veri kalitesi, genel KPI'lar, kategori/alt kategori, bölge, indirim dağılımları, ürün zarar yoğunlaşması, kategori-bölge brüt zararı ve zaman performansını içerir. Dokuz grafik bulunur. Son tanısal bölüm ürün-bölge zarar örtüşmesini, en büyük zarar havuzlarındaki ürünleri ve 2016–2017 kategori marj değişimi ayrıştırmasını içerir.

Bu notebook, 2014–2017 Superstore örnek olayının anlatımını ve belirli segmentlerini korur. Genel hesaplama fonksiyonları farklı doğrulanmış girdileri destekler; notebook'un bütün görsel yerleşimlerinin ve örnek segmentlerinin rastgele bir veri kümesi için otomatik uygun olduğu iddia edilmez. Yeni dönem veya segmentler için anlatım kapsamı ve grafik düzeni ayrıca gözden geçirilir. Kişisel keşif notebook'u ana çalışmaya dahil edilmemiştir.

## Metrik sözleşmesi

- Net kâr: Profit toplamı. Muhasebe maliyet kapsamı bağımsız olarak doğrulanmamıştır.
- Brüt zarar: yalnızca negatif Profit satırlarının pozitif büyüklüğü; muhasebedeki brüt kâr değildir.
- Marj: toplam Profit / toplam Sales × 100. Sıfır payda için sonuç tanımsızdır.
- Ürün: Category + Sub-Category + Product ID + Product Name bileşik anahtarı. İsim çatışmaları otomatik birleştirilmez.
- Top-N payları iç içedir; toplanmaz. Kategori-bölge payı tüm brüt zararı, örtüşme tablosu ise ilgili havuzun zararını payda alır.
- İndirim özetleri satır ağırlıksızdır. Takvim ayı ortalamaları gözlenen yıllık aylık toplamlardan hesaplanır; eksik aylar sıfırla doldurulmaz.
- Marj köprüsü yüzde puan cinsinden betimsel ayrıştırmadır; nedensellik veya tasarruf tahmini değildir.

## Doğrulama

```powershell
python -m unittest discover -s tests -v
python tools/verify_notebook.py
python tools/verify_notebook.py --write --html work/sales_eda_review.html
python -m pip check
```

Testler sentetik veri kullanır ve [CI](.github/workflows/tests.yml) içinde çalışır. Gerçek CSV gerektiren notebook kontrolü yereldir. Kontrol; iki çalışma dizininden sıfır kernel ile çalıştırmayı, metrik uzlaşmalarını ve bölüm bağlantılarını doğrular.

## Devam planı

Kararlar ve aşamalar [yol haritasında](docs/ROADMAP.md) yer alır. Sınırlı ilk AI akışı, son iki tamamlanmış ay karşılaştırmasını doğrulanmış fonksiyonlarla çalıştırır; rakamlar kod şablonundan gelir. Gerçek model kabulü API anahtarı/model yapılandırması ve ayrı canlı kontrol gerektirir. Servis yayını yapılmamıştır.


## Filtreli analiz ve soru fonksiyonları

Genel rapor tarih/segment filtrelerini, dönem karşılaştırmasını ve segment kârlılığını destekler. Masaüstü ekranındaki “Analiz kapsamı / dönem karşılaştırması” seçeneği bu kapsamı belirler. Her soru sonucu tarih/kayıt kapsamını, filtreleri, para birimini, paydaları ve uyarıları taşır.

```powershell
python -m src.cli questions
python -m src.cli ask examples/synthetic_sales.csv --config examples/synthetic_mapping.json --request examples/question_lowest_margin.json --output work/answer.json
```

Fonksiyonlar, örnekler ve sonuç sözleşmesi [analitik soru rehberinde](docs/ANALYTICAL_QUESTIONS.md) açıklanır. Yerel FastAPI prototipi hazırlanmış veri üzerinde bu deterministik katmanı kullanır. İsteğe bağlı CLI LLM yorumlayıcısı yalnızca sınırlı dönem isteğini seçer; HTTP API yapılandırılmış sorgu alır.

Yerel API için dosya/veri/yanıt kotaları, sınırlı iş kuyruğu ve sonlandırılabilir hesaplama süreleri: [kaynak profili](docs/RESOURCE_LIMITS.md). Örnek yapılandırma `examples/resource-limits.json`; API başlatırken `--limits` ile seçilir.

Yükle → incele → eşleştirmeyi onayla → hazırla → aynı veride iki sorgu → sil kullanım örnekleri: [ilk API akışı](docs/FIRST_API_FLOW.md). Sunucu açıkken etkileşimli endpoint ekranı: `http://127.0.0.1:8000/docs`.

Grafik içeren HTML raporu, `/reports` isteğinde `format: "html"` ile ayrı ve sınırlı bir render işi olarak oluşturulur. Sayısal `/questions` endpoint'i grafik üretmez. [Grafik izolasyonu ve hata temizliği](docs/RENDERING.md).
