# Soru işleyicileri ve davranış uyumluluğu

`src/questions.py` içindeki `run_question`, analizleri altı ayrı özel işleve yönlendirir:

| analysis | İşleyici |
|---|---|
| summary | `_handle_summary` |
| segment_performance | `_handle_segment_performance` |
| period_comparison | `_handle_period_comparison` |
| monthly_trend | `_handle_monthly_trend` |
| loss_concentration | `_handle_loss_concentration` |
| discount_comparison | `_handle_discount_comparison` |

İşleyiciler aynı modülde mevcut hesaplama yardımcılarını kullanır. `_ANALYSIS_HANDLERS` yönlendirme tablosudur; `_HandlerOptions` ortak girişte doğrulanmış parametreleri taşır. Dönem işleyicisi mevcut `_comparison` hesabını kullanır.

Ortak girişte kalan sorumluluklar: parametre/tür doğrulaması, gerekli alan kontrolü, para birimi ayrımı, filtreleme, kapsam/birim/tanım bilgilerinin oluşturulması, ortak uyarılar, boş veya kullanılamayan veri davranışı ve genel sonuç durumunun belirlenmesi. Boş/kullanılamayan seçimler işleyicilere gönderilmez. İşleyiciler bir para birimine ait, boş olmayan seçimin analiz metriklerini ve analize özel kayıt/uyarılarını üretir.

## Refaktörün kontrolü

Kaynak değiştirilmeden önce 74 sentetik senaryonun tam çıktısı `tests/fixtures/question_outputs_before_handlers.json` dosyasına kaydedildi. Dosya ayrıca önceki kaynak dosyasının SHA-256 değerini ve kullanılan pandas sürümünü içerir.

Senaryolar altı analiz türünü, filtreleri, para birimlerini, boş/eksik veriyi, isteğe bağlı kâr/sipariş alanlarını, sıralama eşitliklerini, tanımsız marjı, ürün anahtarlarını, eksik dönemleri, sıfır/negatif bazları ve geçersiz istekleri kapsar. Karşılaştırma sayısal değerler, kayıt sırası, durumlar, uyarı metinleri/sırası ve hata türü/mesajı üzerinde yapılır; yaklaşık eşitlik veya sayısal yuvarlama kullanılmaz. Sözlük alan sırası karşılaştırmanın parçası değildir.

`tests/test_question_handler_compatibility.py` ayrıca kaynak veri/isteklerin değiştirilmediğini ve ortak filtreleme/para birimi ayrımının işleyiciden önce uygulandığını kontrol eder. İşleyici seçimi ve boş/geçersiz veride işleyicilerin çağrılmaması da test edilir.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_question_handler_compatibility.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools/export_question_schemas.py --check
```

Bu refaktörün kabulünde 74 senaryo önceki sonuçlarla eşleşti; tüm proje testleri 171/171 geçti ve v1 JSON şemaları aynı kaldı. Bu sonuçlar mevcut Windows/Python 3.13 ortamında alındı. Referans JSON dosyası refaktörden sonra yeniden üretilmedi. Gelecekte bilinçli bir metrik değişikliği yapılırsa bunun ayrı değişiklik olarak değerlendirilmesi gerekir.
