# İlk AI soru akışı: son iki tamamlanmış ay

API anahtarı olmadan çalışan Ollama seçeneği de eklendi ve gerçek modelle kontrol edildi. Kurulum, kullanım ve kabul sonuçları için [yerel model rehberine](LOCAL_MODEL_SETUP.md) bakın. Aşağıdaki OpenAI talimatları yalnızca bulut seçeneği içindir.

## Uygulanan adımlar

1. `src/periods.py` içindeki `last_two_complete_months(reference_date)` yalnızca takvim sınırlarını üretir. Girdi saat içermeyen bir `datetime.date` nesnesidir. İlk olarak altı bağımsız testle kontrol edilmiştir: Kasım örneği, yıl geçişi, artık yıl/normal Şubat, tüm ay uzunlukları, ayın gününden bağımsızlık ve geçersiz girdiler. Analiz bağlantısı bu kontroller geçtikten sonra eklenmiştir.
2. `src/sales_assistant.py` izin verilen isteği doğrular, bu tarihleri mevcut `answer_file_question` → `run_question` dönem karşılaştırmasına iletir. Dosya okuma, onaylanmış eşleştirme, veri kalitesi ve para birimi kuralları mevcut analiz katmanından gelir. Sekiz dosya/sonuç testiyle kontrol edilmiştir.
3. `src/intent_ai.py` OpenAI Responses API üzerinden yalnızca soru metnini sınıflandırır. Structured Outputs ve yerel doğrulama kullanılır. Üç karar vardır: `ready`, `needs_clarification`, `unsupported`. Windows Türkçe konsol kodlaması kontrolü dahil sekiz çevrimdışı bağlantı/CLI testi eklenmiştir.
4. Sayısal cevap Türkçe şablondan üretilir. Model hesap yapmaz; dosya, sütun eşleştirmesi veya analiz sonucu modele gönderilmez. Ham JSON; tarihleri, kaynağın parmak izini, kaliteyi, kayıt sayılarını ve uyarıları korur.

Tarih fonksiyonunun mantığı: referans gününü ayın ilk gününe getirip bir gün geri gideriz; bu, son tamamlanmış ayın son günüdür (`current_end`). Bunun gününü 1 yaparak başlangıcını buluruz (`current_start`). Bu başlangıçtan bir gün geri giderek önceki ayın sonunu (`baseline_end`), onun gününü 1 yaparak da önceki ayın başını (`baseline_start`) buluruz. Böylece 30/31 gün veya artık yıl için ayrı elle yazılmış kurallar gerekmez.

## İstek sözleşmesi

Modelin hazır kararı:

```json
{"status":"ready","analysis":"period_comparison","period_rule":"last_two_complete_months"}
```

Belirsiz soru:

```json
{"status":"needs_clarification","question":"Son iki tamamlanmış ayı mı karşılaştırmak istiyorsunuz?"}
```

Desteklenmeyen soru:

```json
{"status":"unsupported","message":"Bu sürüm yalnızca son iki tamamlanmış ayın genel karşılaştırmasını destekler."}
```

API şeması bu kararı bir `decision` nesnesine sarar; uygulama bu sarmalayıcıyı açıp alanları ayrıca doğrular. Modelin ilave tarih, filtre veya fonksiyon adı eklemesine izin verilmez. Açık bölge/ürün filtreleri, özel tarih aralıkları, MTD ve tahmin bu ilk AI sürümünün kapsamı dışındadır. Deterministik CLI'nin mevcut kapsamı daha geniştir.

## AI olmadan çalıştırma

Proje kökünde:

```powershell
.\.venv\Scripts\python.exe -m src.cli compare-months examples/completed_months_sales.csv --config examples/completed_months_mapping.json --reference-date 2026-11-06 --output work/completed_months_answer.json
```

Beklenen: Eylül satış 100.000 TRY, kâr 12.000 TRY; Ekim satış 120.000 TRY, kâr 15.000 TRY. Değişimler satışta 20.000 TRY / %20, kârda 3.000 TRY / %25. Dosyadaki Kasım kaydı karşılaştırmaya alınmaz. Veriler tamamen sentetiktir.

`--reference-date` zorunludur ve işletmenin yerel takvim tarihini belirtir. Uygulama dosyadaki son tarihi bugünün tarihi gibi kullanmaz. İlgili dönem dosyada bulunmuyorsa sıfır satış uydurulmaz. Sınır günlerinde kayıt bulunması da tek başına dosyanın eksiksiz olduğunu kanıtlamaz; kapsam uyarıları gösterilir.

## AI ile çalıştırma

Standart Python kütüphanesiyle HTTPS isteği kullanılır; ek SDK kurulumu gerekmez. Çalıştırılan sürecin ortamında `OPENAI_API_KEY` ve Structured Outputs destekleyen erişilebilir bir model için `OPENAI_MODEL` ayarlanmalıdır. `.env` otomatik yüklenmez. Model adı ayrıca `--model` ile verilebilir. Hesap/model erişimi ve API ücretleri kullanılan hesaba bağlıdır.

```powershell
.\.venv\Scripts\python.exe -m src.cli ask-ai examples/completed_months_sales.csv --config examples/completed_months_mapping.json --reference-date 2026-11-06 --question "Son iki tamamlanmış ayda satış ve kâr nasıl değişti?" --output work/ai_answer.json
```

Soru metni OpenAI'ya gönderilir; müşteri/satış satırları gönderilmez. `store=false` kullanılır. Bu ayar sağlayıcının tüm veri saklama politikalarına ilişkin bir garanti değildir. Yanıt eksik, reddedilmiş veya geçersizse analiz çalıştırılmaz. Bağlantı zaman aşımı 30 saniyedir; otomatik tekrar yoktur.

`needs_clarification` çıktısında soru gösterilir. Bu sürüm tek komutluk bir akıştır: kullanıcı açıklamasını sonraki komutta tam soru olarak verir. Sohbet geçmişi, masaüstü sohbet ekranı, FastAPI ve MCP bu değişikliğe dahil değildir.

CLI çıkışları: başarılı analiz 0; açıklama gereksinimi, kapsam dışı soru veya veri yetersizliği 2; geçersiz istek/ayar/bağlantı hatası 1. `--output` kaynak veri veya eşleştirme dosyasının üzerine yazamaz.

## Doğrulama ve kalan kabul adımı

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools/verify_ai_intents.py
```

İlk komut çevrimdışı testleri çalıştırır. AI testlerinde servis taklidi kullanılır: bu testler gerçek modelin Türkçe soruları doğru anladığını kanıtlamaz.

İkinci komut 12 sentetik soruyla **12 gerçek API çağrısı** yapar; açık/belirsiz soruları, MTD, özel tarih, filtre, tahmin ve sahte sonuç isteğini kontrol eder. OpenAI seçeneğinin gerçek model kabulü anahtar/model yapılandırması olmadan tamamlanamaz. Yerel seçenek `--provider ollama` ile anahtarsız çalışır. Structured Outputs biçimi sınırlar; anlamın doğru yorumlandığı bu kabul örnekleriyle ayrıca ölçülmelidir. Başarılı sonuçlar bütün olası cümleler için garanti oluşturmaz.

Resmi kaynak: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
