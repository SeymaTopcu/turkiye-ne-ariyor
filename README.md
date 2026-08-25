# Türkiye Ne Arıyor?

Türkiye'nin 81 ili için Google Trends **Top 25** ve **Rising Top 25** verisini günlük olarak arşivleyen statik web sitesi.

## Nasıl çalışır?

Her gün GitHub Actions:
1. Google Trends public BigQuery dataset'ini sorgular.
2. Türkiye'nin il bazlı Top 25 ve Rising Top 25 verisini çeker.
3. `data/archive/YYYY-MM-DD.json` dosyasına kaydeder.
4. `data/latest.json` dosyasını günceller.
5. GitHub Pages siteyi yeniden yayınlar.

## Gerekli GitHub secrets

- `GCP_PROJECT_ID`
- `GCP_SERVICE_ACCOUNT_JSON`

## Veri kaynağı

- `bigquery-public-data.google_trends.international_top_terms`
- `bigquery-public-data.google_trends.international_top_rising_terms`

Sorgular yakın tarih partition'larını kullanır ve `maximum_bytes_billed` sınırı vardır.