# Agent setup and maintenance guide

This is a Windows-first local image-processing project. Never commit real follower screenshots, generated output, OCR databases, or local virtual environments.

## Setup

Run from the repository root:

```powershell
cmd /c 00_安装环境.bat
```

Equivalent agent-friendly commands:

```powershell
python --version  # must report Python 3.11.x
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py prepare-templates
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Verification

Before publishing changes, run:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe app.py list-templates
```

The `.bat` files must remain ASCII with Windows CRLF line endings. `tests/test_launchers.py` enforces this.

## Privacy boundary

Do not stage or publish anything under:

- `.venv/`
- `inbox/`
- `output/`
- `data/`
- `archive/`
- `needs_review/`
- `assets/source/`
- `assets/temp/`

Only example template images under `templates/` are intended for publication.
