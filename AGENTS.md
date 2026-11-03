# Repository guide

- `README.md` is the source catalog. `plugins.json` and `.agents/plugins/marketplace.json` are generated feeds.
- `scripts/generate_plugins_json.py` generates the feeds. Use `--output-dir <temporary-folder>` to keep outputs away from tracked files; `--offline` uses only README entries and performs no network access; `--check` compares files without writing.
- Local setup: Python 3.12.10 and `python3 -m pip install -r requirements-dev.txt`.
- Offline checks: `python3 -m unittest tests/test-generate-plugins-json.py` and `python3 scripts/check-alphabetical.py README.md`.
- `scripts/validate-contribution.py` and scanner jobs access GitHub and contributed repositories; run only when external reads are appropriate.
- CI only reads repository contents. It does not commit or push generated files. Keep generated feed changes reviewable in the pull request.
