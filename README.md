# Google Drive Video Inventory (Production-Ready)

A production-ready Python application that connects to Google Drive (API v3), recursively maps **all folders/subfolders**, detects **all video files**, extracts **Drive + advanced video metadata**, and generates a professional multi-sheet Excel inventory:

- **Inventory**: full index with filters and frozen headers
- **Statistics**: dashboard-style totals & distributions
- **Duplicates**: potential duplicates + similarity indicators
- **Folder Mapping**: hierarchical folder summary (counts, sizes)
- **Errors**: inaccessible items, API/metadata failures

It supports **incremental rescans** via a local **SQLite index** to minimize redundant API calls and re-processing.

## Requirements

- Python **3.11+** (Windows/macOS/Linux)
- Google Drive API v3 enabled in a Google Cloud project
- OAuth2 client secret JSON (`credentials.json`)
- Optional but recommended: **ffprobe** (from FFmpeg) on your `PATH`

## 1) Setup (Google Cloud)

1. Create or select a Google Cloud project in the Google Cloud Console.
2. Enable **Google Drive API**.
3. Configure the **OAuth consent screen** (External or Internal for Workspace).
4. Create **OAuth client ID** credentials:
   - Application type: **Desktop app**
5. Download the client secret JSON and save it as:
   - `project/credentials.json`

## 2) Local installation

From the workspace root:

```bash
cd project
python -m venv .venv
.\.venv\Scripts\activate  # Windows PowerShell
pip install -r requirements.txt
```

Copy environment configuration:

```bash
copy .env.example .env
```

## 3) Install ffprobe (recommended)

- Windows: install FFmpeg and ensure `ffprobe.exe` is on `PATH`, or set `FFMPEG_BIN_DIR` in `.env` to the folder containing `ffprobe.exe`.
- macOS: `brew install ffmpeg`
- Linux: install `ffmpeg` from your package manager

## 4) Run

### Scan Google Drive (indexes videos into SQLite)

```bash
python main.py scan
```

First run opens your browser for Google OAuth. A token is saved to `cache/token.json`.

Incremental rescans (default) only update new or changed files:

```bash
python main.py scan
```

Force a full rescan:

```bash
python main.py scan --full
```

### Scan + open web dashboard

```bash
python main.py run
```

### Search dashboard (web UI)

Launch a local web interface to search, filter, and browse indexed videos:

```bash
python main.py serve
```

Then open `http://127.0.0.1:8080` in your browser.

Optional demo data (for testing the UI before your first scan):

```bash
python main.py seed-demo
python main.py serve
```

### Useful options

```bash
python main.py scan --full
python main.py run --no-serve
python main.py serve --host 0.0.0.0 --port 8080
```

Set `MAX_DOWNLOAD_MB` in `.env` to enable ffprobe metadata (downloads files up to that size). Use `0` to index Drive metadata only (faster).

## Output

- Excel report: `output/google_drive_video_inventory.xlsx`
- SQLite index: `database/index.sqlite3`
- Logs: `logs/app.log` and console output
- Cache: `cache/` (token, downloads)

## Troubleshooting

- **"access_denied" or consent issues**: ensure OAuth consent screen is configured; for Workspace Internal apps, verify user is in the Workspace org.
- **"insufficientPermissions"**: delete `cache/token.json` and rerun to re-consent; ensure Drive API is enabled.
- **Quota errors (429/403 rateLimitExceeded)**: rerun later; the app retries with exponential backoff and stores progress incrementally.
- **ffprobe not found**: install FFmpeg or set `FFMPEG_BIN_DIR` in `.env`, or run with `--no-ffprobe`.

## Security

Never commit:

- `.env`
- `credentials.json`
- `cache/token.json`

They are excluded by `.gitignore`.
