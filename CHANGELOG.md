# Değişiklik Günlüğü

Bu proje Semantik Sürümleme yaklaşımını kullanır.

## [1.0.0] - 2026-07-27

### Eklendi

- Marka bağımsız cihaz profili mimarisi
- Doğrulanmış ENTES MPR-53S Modbus RTU profili
- Modbus RTU, şeffaf TCP-RTU ve Modbus TCP taşıma seçenekleri
- 16/32/64-bit signed, unsigned ve IEEE-754 veri çözümleme
- Çoklu byte/word order desteği
- FastAPI REST ve WebSocket servisleri
- SQLite WAL zaman serisi saklama ve enerji analitiği
- Saatlik/günlük tüketim, haftalık ısı haritası ve peak/minimum analizi
- Mobil öncelikli Rootcastle SCADA arayüzü
- Prometheus, liveness, readiness ve health endpointleri
- Docker Compose, systemd ve GitHub Actions CI
- Güvenlik, profil şeması, uyumluluk ve devreye alma dokümantasyonu
- Türkçe README ve teknik dokümantasyon

### Güvenlik

- Modbus yazma fonksiyonları engellendi
- Register blokları, timeout ve poll değerleri sınırlandırıldı
- Cihaz profili doğrulama kontrolleri eklendi
- Enerji sayaç reset ve anormal sıçrama koruması eklendi
