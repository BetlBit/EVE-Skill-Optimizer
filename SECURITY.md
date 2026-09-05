# Security / private local data

- Never commit `.env`.
- Never commit EVEMon Settings backups. They may contain ESI credentials or refresh tokens.
- Never commit `data/` runtime snapshots or local SDE caches.
- The local application binds to `127.0.0.1` only.
- Public source packages are created from an explicit whitelist by `pack_source.ps1`.
