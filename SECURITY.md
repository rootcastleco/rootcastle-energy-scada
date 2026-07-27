# Güvenlik Politikası

## Kapsam

Rootcastle Energy SCADA bir OT/enerji telemetri görüntüleyicisidir. Public sürüm bilinçli olarak salt okunurdur: Modbus katmanı yalnızca Function 03 ve 04'e izin verir. Cihaz ayarı yazma, sayaç sıfırlama ve röle kontrolü proje kapsamı dışındadır.

## Dağıtım temeli

- Sayaçları ve ağ geçitlerini izole OT/VLAN segmentinde tutun.
- Ham Modbus portlarını internete açmayın.
- TLS ve kimlik doğrulamayı reverse proxy veya özel erişim ağ geçidinde sonlandırın.
- Süreci root olmayan kullanıcı ve salt okunur dosya sistemiyle çalıştırın.
- Yükseltme öncesi SQLite veri tabanını ve cihaz profillerini yedekleyin.
- Üçüncü taraf register haritalarını saha doğrulamasına kadar güvenilmeyen girdi kabul edin.

## Bildirim

Güvenlik açığını düzeltme öncesi public olarak paylaşmak yerine Rootcastle Engineering & Innovation'a özel kanaldan bildirin. Etkilenen commit, yapılandırma, yeniden üretim adımları ve etkiyi ekleyin.

Web sitesi: https://rootcastle.com
Geliştirici: https://batuhanayribas.com
