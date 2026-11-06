# İlk hizmet paketi: tür doğrulaması ve soru sözleşmeleri

Bu rehber ilk sözleşme paketini anlatır. Daha sonra eklenen [yerel API ve oturum yaşam döngüsü](OWNERSHIP_AND_LIFECYCLE.md) bu modelleri kullanır; HTTP davranışının güncel açıklaması o rehberdedir.

## Uygulanan kapsam

Bu paket hizmet yol haritasının 1 ve 2. maddelerini uygular: çekirdek tür hataları, kesin Pydantic istek/yanıt modelleri ve dosya sorgusunda sözleşme doğrulaması. HTTP sunucusu, veri kümesi deposu, erişim/oturum sınırları, dosya ömrü, kaynak kotaları ve grafik eşzamanlılığı sonraki paketlerdir.

## Girdi doğrulaması

`src/questions.py` içindeki açık Python girişleri yanlış türleri kontrollü `ValueError` ile reddeder. `analysis=[]`, `sort_by=[]`, iç içe gruplama listeleri ve geçersiz doğrudan `segment_table` girdileri için regresyon testleri vardır. Geçerli Python çağrılarındaki tuple gruplama desteği korunur.

`src/contracts.py` altı ayrı istek modeli tanımlar:

| analysis | Model | Analize özel alanlar |
|---|---|---|
| summary | SummaryRequest | Yok |
| segment_performance | SegmentRequest | by zorunlu; sort_by, ascending, limit, min_records |
| period_comparison | ComparisonRequest | comparison zorunlu |
| monthly_trend | MonthlyRequest | Yok |
| loss_concentration | LossRequest | limit |
| discount_comparison | DiscountRequest | Yok |

Her istekte isteğe bağlı tarih/segment filtreleri vardır. Karşılaştırma isteği yalnızca segment filtrelerini kabul eder; tarih sınırları comparison alanından gelir. `analysis` ayırt edici alanıyla `QuestionRequest` birliği doğru modeli seçer.

Kurallar:

- Bilinmeyen ve analize ait olmayan alanlar reddedilir. Örneğin `summary` isteğine `limit` eklenemez; önceki dosya köprüsünde sessizce kabul edilebilen bu tür alanlar artık hatadır.
- `limit` ve `min_records` pozitif tam sayıdır. `true`, `1.0` ve `"10"` tam sayıya dönüştürülmez. Bu pakette üst kapasite sınırı eklenmemiştir; kotalar ayrı hizmet işidir.
- `ascending` yalnızca boolean veya null olabilir. Boyutlar ve sıralama ölçütleri kapalı değer kümeleridir. Gruplama boyutları tekrar edemez. API sözleşmesinde gruplama string veya JSON listesi kullanır; Python tuple kabul edilmez.
- Tarihler ISO `YYYY-MM-DD` stringidir ve takvim geçerliliği kontrol edilir. Dönemler sıralı/çakışmasız olmalıdır. Boş segment listeleri ve yalnızca boşluk içeren etiketler reddedilir. Etiketlerin anlamı veya eşleşmesi tahmin edilmez.

```python
from src.contracts import parse_question_request

request = parse_question_request({
    'analysis': 'segment_performance',
    'by': 'region',
    'sort_by': 'profit',
    'limit': 5,
})
parameters = request.model_dump(exclude_none=True)
```

## Yanıt doğrulaması

Altı analiz için ayrı yanıt ve para birimi sonucu modelleri vardır: `SummaryResponse`, `SegmentResponse`, `ComparisonResponse`, `MonthlyResponse`, `LossResponse`, `DiscountResponse`. Metrikler ve kayıtlar da analize özel modellerdir; serbest `dict[str, Any]` kullanılmaz. Dosya kaynağı, onaylanmış eşleştirme, kalite raporu ve analitik cevabı `FileQuestionResponse` birleştirir.

Doğrulama; alan türleri ve bilinmeyen alanlara ek olarak durum/sonuç tutarlılığını, dönem meta verisini, sıralama boyutlarını/sayılarını, metrik birimlerini, farklı para birimi etiketlerini ve kaynak parmak izini kontrol eder. NaN/Infinity ve sayı yerine boolean/string reddedilir. Marj değişimi yüzde puan cinsindedir; önceki değer pozitif değilse göreli değişim tanımsız kalır.

Mevcut `schema_version=1` JSON çıktısı korunur. Doğrulama yapılırken bulunmayan isteğe bağlı alanlar sonuca eklenmez; hesaplanamayan değerlerde mevcut null değerleri korunur. Modeli ayrıca serileştirirken `model_dump(mode='json', exclude_unset=True)` kullanmak bu davranışı korur. `exclude_none=True` ile yanıtın anlam taşıyan null değerlerini silmeyin.

`ok`, `partial`, `empty`, `empty_ranking`, `unavailable`, `insufficient_data` analitik durumları modellenmiştir. Dosya düzeyinde `needs_attention` durumunda analitik cevap yoktur. Boş veri sıfır satış gibi sunulmaz. Para birimleri arasında birleşik parasal toplam eklenemez.

## Bağlantı ve hata ayrımı

`answer_file_question` artık isteği **dosyayı okumadan önce** Pydantic ile doğrular. Ham sözlük veya uygun istek modeli alabilir. Üretilen dosya yanıtını döndürmeden önce de doğrular.

- Yanlış istek: Pydantic `ValidationError` (`ValueError` alt türü).
- Çekirdekte geçersiz parametre: kontrollü `ValueError`.
- İçeride üretilen yanıt sözleşmeye aykırıysa: `ContractViolation` (`RuntimeError` alt türü). Asıl doğrulama hatası exception cause olarak korunur. Bu hata yanlış kullanıcı isteği sayılmaz.

Bu aşamada HTTP durum kodları uygulanmamıştır. Mevcut CLI hatayı yakalar, 1 ile çıkar ve geçersiz istek için sonuç dosyası oluşturmaz. Dosyanın yeniden okunması/hazırlanması henüz değiştirilmemiştir.

## Şema dosyaları ve sürüm kuralı

`docs/schemas/` altında üç JSON Schema vardır:

- `question-request-v1.schema.json`
- `question-response-v1.schema.json`
- `file-question-response-v1.schema.json`

Takvim sıralaması ve durum tutarlılığı gibi alanlar arası kurallar Pydantic çalışma zamanı doğrulayıcılarında da kontrol edilir; yalnızca JSON Schema dosyasını okumak bütün iş kurallarını kapsamaz.

İlk API yayımlandıktan sonra v1'de alan kaldırma/yeniden adlandırma, tür/birim/anlam değiştirme veya yeni zorunlu alan ekleme yeni sözleşme sürümü gerektirir. Şema dosyaları otomatik testle modellere karşılaştırılır; bilinçli bir değişiklikte uyumluluk incelemesi ve uygun sürüm güncellemesi yapılmalıdır.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_request_types.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_contracts.py -v
.\.venv\Scripts\python.exe tools/export_question_schemas.py --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
```

`tools/export_question_schemas.py` parametresiz çalıştırılırsa şemaları yeniden üretir. Testler altı analiz için gerçek çekirdek/dosya/CLI çıktısını, JSON gidiş-dönüşünü, boş/eksik/kısmi durumları ve hatalı türleri kapsar. AI sağlayıcısına ağ çağrısı gerektirmez.
