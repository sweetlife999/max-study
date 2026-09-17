# MAX TLS trust anchor

`russian_trusted_root_ca.pem` — корневой сертификат Russian Trusted Root CA,
которым подписана цепочка `platform-api2.max.ru` после миграции MAX в июле 2026.

- официальный источник: `https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt`;
- subject/issuer: `C=RU, O=The Ministry of Digital Development and Communications, CN=Russian Trusted Root CA`;
- SHA-256: `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`;
- срок действия: 1 марта 2022 — 27 февраля 2032.

При ротации заменять файл только из официального источника и сначала сверять
subject, issuer, fingerprint и срок действия через `openssl x509`. Адаптер
добавляет этот root к стандартным CA Node.js; `rejectUnauthorized` не отключается.
