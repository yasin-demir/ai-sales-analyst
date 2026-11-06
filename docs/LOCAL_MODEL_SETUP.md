# Anahtarsız yerel AI kullanımı

## Kurulan sistem ve kontrol sonucu

6 Kasım 2026 tarihinde bu bilgisayara Ollama 0.40.15 ve `qwen3:4b` kuruldu. Modelin kimliği `359d7dd4bcda`, indirme boyutu yaklaşık 2,5 GB. Yerel servis `127.0.0.1:11434` adresinde çalışıyor. Model soruyu yorumlar; tarihleri ve satış/kâr rakamlarını mevcut Python analiz kodu hesaplar. OpenAI anahtarı veya bulut modeline geçiş yoktur.

Sıra: kurulum ve model dosyası doğrulandı → bağımsız soru yorumlama kontrolü yapıldı → kontroller geçtikten sonra analiz bağlantısı eklendi → gerçek model ve sentetik CSV ile tüm akış çalıştırıldı.

İlk kontrol 7/8 geçti: açık bir kısmi ay isteğinde model kapsam dışı cevap yerine açıklama istedi. Türkçe karar kuralları netleştirildi; farklı ifadeler eklenince 11/12 geçti. Test cümlelerinden farklı dört örnekle tam ay/kısmi ay/belirsiz dönem ayrımı gösterildi; son kontrol **12/12** geçti. İlk iki denemede analiz bağlantısı açılmadı. Bu örnek kümesi bütün olası sorular için doğruluk garantisi değildir; yeni ifadelerle genişletilmelidir.

Yerel bağlantı ve CLI için altı çevrimdışı test bulunuyor. Tüm proje testleri bu çalıştırmada **128/128** geçti. Gerçek model + örnek CSV kontrolünde Eylül/Ekim satış değişimi %20, kâr değişimi %25 olarak doğrulandı.

## Örnek dosyayla kullan

Proje kökünden:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m src.cli ask-ai examples/completed_months_sales.csv --config examples/completed_months_mapping.json --reference-date 2026-11-06 --provider ollama --model qwen3:4b --question "Son iki tamamlanmış ayda satış ve kâr nasıl değişti?" --output work/local_ai_answer.json
```

Örnek veriler sentetiktir. Beklenen: Eylül satış 100.000 TRY, kâr 12.000 TRY; Ekim satış 120.000 TRY, kâr 15.000 TRY. Kasım kaydı bu iki tamamlanmış ayın dışında kalır.

`--provider ollama` yerel modeli seçer. Bu seçenek olmadan eski OpenAI varsayılanı korunur. `--model` verilmezse `OLLAMA_MODEL`, o da yoksa `qwen3:4b` kullanılır. `.env` otomatik okunmaz. `--reference-date` işletmenin referans takvim tarihidir; dosyada bulunmayan aylar sıfır kabul edilmez.

## Kontrolleri yeniden çalıştır

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/verify_ai_intents.py --provider ollama --model qwen3:4b
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

İlk komut dosya analizi yapmadan 12 sentetik soruyu gerçek yerel modele gönderir. İkinci komut ağ gerektirmeyen otomatik testleri çalıştırır. Model tag'i değişirse veya prompt düzenlenirse gerçek yorumlama kontrolünü tekrarlamak gerekir.

## Çalıştırma notları

Ollama Windows uygulaması arka planda açık olmalıdır. Başlat menüsünden Ollama açılabilir. Yeni terminalde `ollama list` kurulu, `ollama ps` yüklü modelleri gösterir. İlk model açılışı daha uzun sürebilir. Kontrol sırasında RTX 3050'de modelin bir kısmı CPU/RAM, bir kısmı GPU üzerinde çalıştı; tamamen GPU'ya sığdığı varsayılmıyor.

İsteklerde 4096 token bağlam sınırı, 512 çıkış tokenı sınırı ve sıcaklık 0 kullanılır. Bu görev için düşünme modu kapalıdır. Yanıt kesilirse/geçersizse analiz çalışmaz. Bağlantı zaman aşımı 120 saniyedir. Soru ve sentetik öğretici örnekler yerel modele gider; satış dosyası ve hesaplama sonuçları modele gönderilmez.

Bu sürüm tek komutluk soru-cevap akışıdır; masaüstü uygulamasına sohbet alanı henüz eklenmemiştir.

Resmi kaynaklar: [Windows kurulumu](https://docs.ollama.com/windows), [Qwen3 4B](https://ollama.com/library/qwen3:4b), [yapılandırılmış çıktı](https://docs.ollama.com/capabilities/structured-outputs).
