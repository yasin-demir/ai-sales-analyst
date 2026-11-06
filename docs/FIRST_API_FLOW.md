# İlk API akışı

API'yi proje kökünden başlatın:

```powershell
.\.venv\Scripts\python.exe -m src.api --limits examples/resource-limits.json
```

Etkileşimli API belgesi: `http://127.0.0.1:8000/docs`. Sunucu yalnızca yerel adreste çalışır. Önce `POST /v1/sessions` ile `{}` gönderip `access_token` alın. Sonraki çağrılarda `Authorization: Bearer <access_token>` kullanın. Swagger ekranında **Authorize** alanına tokenı girin. Sahiplik istemcinin verdiği bir kullanıcı kimliğiyle kurulmaz.

| Adım | Endpoint | Sonuç |
|---|---|---|
| Yükle | `POST /v1/datasets` | `201`, sunucunun oluşturduğu `dataset_id` |
| İncele | `GET /v1/datasets/{id}` | Durum, ilk 5 kayıt, kaynak sütunları ve hazırlanmış alanlar |
| Eşleştirmeyi onayla | `PUT /v1/datasets/{id}/mapping` | Onaylanmış ayarlar ve eşleştirme revizyonu |
| Hazırla | `POST /v1/datasets/{id}/prepare` | `preparation_id`, kalite ve analiz yetenekleri |
| Sorgula | `POST /v1/datasets/{id}/questions` | Aynı hazırlama kimliğinde yapılandırılmış analiz |
| Sil | `DELETE /v1/datasets/{id}` | `204`; çalışan sorgu varsa `202` ve ertelenmiş fiziksel silme |

## Yükleme ve onay

Yüklemede `X-Filename: sales.csv`, `Content-Type: application/octet-stream` başlıklarını ve gövdede dosyanın ham baytlarını gönderin. Sunucu dosya yolu almaz. CSV ve XLSX desteklenir. Swagger yükleme endpoint'inde dosya seçilebilir.

GET yanıtı mevcut `DatasetInfo` alanlarını korur ve şunları ekler:

- `preview`: `rows` toplam kaynak kayıt sayısı, `columns` kaynak sütun adları, `preview` ilk 5 kayıt; ayrıca okuma ayarları ve onay bekleyen eşleştirme önerileri.
- `preparation_id`: En son onaylanmış eşleştirme ve mevcut hazırlayıcı sürümü için hazırlanmış veri kimliği; henüz hazırlanmamışsa `null`.
- `available_fields`: Hazırlama başarılıysa doğrulanmış ve engellenmemiş kanonik analiz alanları. Hazırlamadan önce veya kalite düzeltme bekliyorsa `[]`.
- `quality`: Hazırlama kalite sonucu; kullanılabilir analizler `quality.capabilities` içindedir. Hazırlamadan önce `null`.

Kaynak sütun adları eşleştirme önerisidir; onay yerine geçmez. Örnek eşleştirme gövdesi:

```json
{
  "mapping": {"sales": "sales", "profit": "profit", "date": "date", "category": "category"},
  "mapping_reviewed": true,
  "currency": "TRY"
}
```

`mapping_reviewed: false` reddedilir. Dosya sütun adlarınıza göre alanları değiştirin. Gerektiğinde `delimiter`, `encoding`, `sheet`, `decimal`, `date_format` gibi okuma/hazırlama ayarlarını aynı gövdede belirtin. Onay öncesi farklı okuyucu ayarlarıyla inceleme için `POST /v1/datasets/{id}/preview` korunur. GET, eşleştirme onayından sonra onun okuyucu ayarlarını kullanır.

## Hazırlama ve iki sorgu

Prepare gövdesi `{}` olduğunda son onaylanmış eşleştirme kullanılır. Belirli revizyon için `{"mapping_revision": 1}` gönderilebilir. Onaysız hazırlama `409` döner. Aynı eşleştirmeyi tekrar hazırlamak mevcut hazırlanmış veriyi kullanır.

İlk sorgu:

```json
{
  "preparation_id": "HAZIRLAMA_YANITINDAKI_KIMLIK",
  "request": {"analysis": "summary"}
}
```

Aynı `preparation_id` ile ikinci sorgu:

```json
{
  "preparation_id": "HAZIRLAMA_YANITINDAKI_KIMLIK",
  "request": {"analysis": "segment_performance", "by": ["category"], "limit": 10}
}
```

`category` alanı eşleştirilmiş ve kullanılabilir olmalıdır. İki sorgu aynı kaynak parmak izi, eşleştirme ve hazırlama sürümüne dayanır. Sıcak sorgularda dosya yeniden okunmaz/hazırlanmaz. Alan adlarını tahmin eden bir LLM bu endpoint'e bağlı değildir; istekler kesin Pydantic modelleriyle doğrulanır.

Eşleştirme değişirse GET, yeni eşleştirme hazırlanana kadar `mapped`, `preparation_id: null`, `available_fields: []` gösterir. Böylece eski hazırlama yetenekleri yeni eşleştirmeye aitmiş gibi sunulmaz. Önceki hazırlamalar kimlikleriyle ayrıca sorgulanabilir.

## Analitik durumlar

Geçerli istekte boş seçim ve veri yetersizliği API hatası değildir; HTTP `200` ile mevcut analiz anlamları korunur:

- `empty`: Filtreye uyan kayıt yok. Metrikler boş kalır; sıfır satış varsayılmaz.
- `insufficient_data`: Dönemlerden birinde karşılaştırma için gözlem yok. Değişim yüzdesi üretilmez; uyarılar ve dönem bilgisi döner.
- `empty_ranking`: Kayıtlar mevcut, ancak gösterim için `min_records` koşulunu sağlayan grup yok; toplam seçim metrikleri korunur.
- `unavailable`: Gerekli analiz alanı bulunmuyor.
- `needs_attention`: Hazırlama kalite sorunu çözülmeli; `answer: null` ve kalite bilgisi döner.

Örnek boş seçim: `{"analysis":"summary","filters":{"start":"2027-01-01","end":"2027-01-31"}}`. Yapısal olarak geçersiz istek `422`; başka oturumun verisi veya silinen kimlik `404` döner. Kaynak/kuyruk/zaman hataları [kaynak profiline](RESOURCE_LIMITS.md) tabidir.

## Kabul kontrolü

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_api_flow.py -v
.\.venv\Scripts\python.exe tools/verify_local_api.py
```

İlk komut yükleme → inceleme → onay → hazırlama → aynı veride iki farklı sorgu → silme akışını, elle hesaplanmış değerleri ve mevcut analiz katmanıyla tam sonuç karşılaştırmasını doğrular. İkinci komut geçici bir Uvicorn sunucusunda gerçek TCP üzerinden aynı hazırlamada özet ve aylık seri, boş/yetersiz sonuçlar, oturum izolasyonu ve silmeyi kontrol eder.
