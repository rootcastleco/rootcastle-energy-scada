# Katkı Rehberi

## Cihaz profilleri

Yeni sayaç profili için açılan katkı isteği şunları içermelidir:

1. `profiles/` altında bir profil JSON dosyası.
2. Üreticinin kamuya açık register haritası veya yeniden dağıtılabilir bir alıntı.
3. En az gerilim, akım, toplam aktif güç ve enerji için çözümleme testleri.
4. Byte/word order, ölçek, signedness ve function code bilgileri.
5. Beklenen değerleri kanıtlayan saha kaydı veya simülatör fixture'ı.
6. Modbus yazma fonksiyonu içermemesi.

Profil ilk olarak `experimental` durumunda açılır. Fiziksel cihaz veya kayıtlı trafik doğrulaması tamamlanınca `verified` yapılır.

## Mühendislik kontrolleri

```bash
python -m pytest -q
python -m compileall -q app
```

Değişiklikler 390 px mobil görünümü, API uyumluluğunu, gözlemlenebilirlik endpointlerini ve salt okunur haberleşme politikasını korumalıdır.
