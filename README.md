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

## Collaborative deployment on Render

The repository includes a `render.yaml` Blueprint for an authenticated,
multi-user deployment. Every registered user can search, label and edit video
metadata. Every authenticated user can launch an incremental scan of one fixed
Google Drive folder. Only the Render administrator can change that folder.

The Render service uses a persistent disk for SQLite. Render persistent disks
require a paid web service plan; the Blueprint uses the `starter` plan.

### Deploy

1. Push the repository to GitHub.
2. In Render, create a new **Blueprint**.
3. Select this repository and apply `render.yaml`.
4. Enter the secret environment variables requested by Render:
   - `ADMIN_EMAIL`: initial administrator e-mail.
   - `ADMIN_PASSWORD`: initial administrator password (8 characters minimum).
   - `DRIVE_SCAN_FOLDER_ID`: URL or ID of the fixed `Dpt DIGITAL` folder.
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: complete Google service account JSON.
   - `EMAIL_FROM`: verified sender address used for account confirmation.
   - `SMTP_HOST`: SMTP server supplied by the e-mail provider.
   - `SMTP_USERNAME`: SMTP account name.
   - `SMTP_PASSWORD`: SMTP password or API key.
5. Open the generated `onrender.com` URL after the first deploy completes.

### Verify new users by e-mail

The online Blueprint enables `EMAIL_VERIFICATION_REQUIRED=true`. Registration
works as follows:

1. The user enters an e-mail address and password.
2. The application sends a six-digit code through the configured SMTP service.
3. The code expires after 15 minutes and permits at most six attempts.
4. The account can sign in only after the correct code is entered.

Use an SMTP delivery provider such as Brevo, Resend or another transactional
e-mail service. Verify the sender address or domain with that provider, then
copy its SMTP values into the Render environment variables. The default
configuration uses port `587` with STARTTLS.

Codes are stored as hashes, deleted after successful verification and replaced
when a new code is requested. A new code can be requested after 60 seconds.

Render runs:

```bash
pip install -r requirements.txt
uvicorn web.server:app --host 0.0.0.0 --port $PORT
```

The health check is available at:

```text
/health
```

### Give the application access to selected Drive folders

1. In Google Cloud, enable the Google Drive API.
2. Create a service account and download its JSON key.
3. In Google Drive, share only the folders the application may index with the
   service account e-mail (`client_email` in the JSON key).
4. Paste the complete JSON key into Render as
   `GOOGLE_SERVICE_ACCOUNT_JSON`.
5. Paste the shared folder URL or ID into `DRIVE_SCAN_FOLDER_ID`.

Sharing a folder with the service account exposes that folder and its
subfolders to the application. It does not expose the user's entire Drive.
Registered users can run the scan, but they cannot change the configured folder
from the application.

The Render deployment disables demo seeding and removes existing records whose
Drive ID starts with `demo-`. Incremental rescans add new videos and update
changed Drive metadata while preserving labels, workflow stages and manually
edited Christian metadata.

### Test authenticated mode locally

```bash
PUBLIC_DEMO=false \
READ_ONLY=false \
AUTO_SEED_DEMO=false \
PURGE_DEMO_DATA=true \
AUTH_REQUIRED=true \
ALLOW_REGISTRATION=true \
ADMIN_EMAIL=admin@example.com \
ADMIN_PASSWORD='change-this-password' \
DRIVE_SCAN_FOLDER_ID='https://drive.google.com/drive/folders/your-folder-id' \
SESSION_COOKIE_SECURE=false \
DB_PATH=/tmp/cmfi-video-indexer/demo.sqlite3 \
python -m uvicorn web.server:app --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`, then sign in with the administrator account or
create another account.

For a local test without an SMTP provider, keep
`EMAIL_VERIFICATION_REQUIRED=false`. The Render configuration requires
verification by default.

### Important security action

If OAuth credentials or tokens were ever committed, removing them from the
latest revision is not enough. Revoke the exposed Google token and rotate the
OAuth client credentials before using Google Drive again.
