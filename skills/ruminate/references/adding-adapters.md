# Adding a New App Adapter

All adapters live in `scripts/adapters/` and implement `BaseAdapter` from `scripts/adapters/base.py`.

## 1. Inspect an Unknown Format Safely

Before writing an adapter, inspect the structure (table names, column names, JSON keys) without dumping raw user secrets:

```bash
python3 scripts/history_tool.py inspect --path /path/to/app/data
```

## 2. Adapter Contract

Create `scripts/adapters/<app_name>.py` subclassing `BaseAdapter`:

- `name`: lowercase hyphenated source ID (e.g. `my-app`)
- `display_name`: human-readable name (e.g. `My App`)
- `tier`: `"local"`, `"hybrid"`, `"export"`, or `"generic"`
- `discover(self, since, until, home_dir=None, extra_paths=None) -> SourceReport`
  - Must check path existence and count in-window files/entries **without** loading full conversation bodies.
  - Catch `PermissionError` -> `status="permission_denied"`.
  - Catch `sqlite3.DatabaseError` (encrypted/corrupt DB) -> `status="encrypted_or_unreadable"`.
- `extract(self, since, until, home_dir=None, extra_paths=None) -> List[Session]`
  - Return normalized `Session` objects containing `Turn(role="user"|"assistant", text=..., timestamp=...)`.
  - Pass all extracted text through `clean_text(...)` and `redact_secrets(...)` (handled automatically by `normalize_turns(...)`).

## 3. Register and Test

1. Register the adapter instance in `scripts/adapters/__init__.py`.
2. Add synthetic fixture tests with **relative timestamps** (`datetime.now(timezone.utc)`) in `tests/test_ruminate.py` — never commit real personal chat logs.
3. Run `python3 -m unittest discover -s tests -v`.
