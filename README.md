# NEXUS AI – Meeting to Action

NEXUS turns meetings into work. Upload a recording or paste a transcript and Gemini produces an executive
summary, decisions, topics and action items (with owners and deadlines). You can then track tasks, see
analytics and chat with an assistant that only knows about that one meeting.

This is a rebuilt version of the original NEXUS project: same pages, same dark/purple look, but with
Google Gemini instead of OpenAI, PostgreSQL (Supabase) instead of local SQLite, a clean architecture,
stronger security and an automated test suite.

## Features

- Sign up, log in, log out (passwords hashed, CSRF protection, secure session cookies)
- Dashboard ("Command Center") with metrics and an execution chart
- New meeting from **audio** (Gemini transcription) or a **pasted transcript**
- AI analysis: summary, decisions, topics, action items with assignee (`Unassigned` when unknown) and deadline (empty when not stated)
- Meeting page with transcript, action matrix and a **meeting-specific AI chat** (answers only from that meeting)
- Task status updates (Pending / In Progress / Completed), paginated task list, analytics chart
- Optional email reminders for tasks that are due within 24 hours or overdue

## Technologies

Python 3.11+, Flask (application factory + Blueprints), Flask-SQLAlchemy, Flask-Migrate/Alembic,
Flask-Login, Flask-WTF (CSRF), Flask-Mailman, APScheduler, `google-genai` (official Google Gen AI SDK),
PostgreSQL via `psycopg` 3, Chart.js (bundled locally), pytest.

## Project structure

```
NEXUS_AI/
├── app/
│   ├── __init__.py            application factory, error handlers, security headers
│   ├── extensions.py          db, login, mail, migrate, csrf
│   ├── models/                User, Meeting, Task, ChatMessage
│   ├── routes/                auth, dashboard, meetings, tasks, analytics, chatbot (Blueprints)
│   ├── services/
│   │   ├── gemini_service.py          ALL Gemini calls (text, JSON, chat, audio)
│   │   ├── transcription_service.py   audio -> transcript (+ temp file cleanup)
│   │   ├── meeting_analysis_service.py prompt, JSON schema, validation of AI output
│   │   ├── chat_service.py            meeting-specific chatbot context
│   │   ├── task_service.py            user-scoped task queries
│   │   └── reminder_service.py        reminder emails + scheduler
│   ├── utils/                 validators, file_handler (upload checks), helpers
│   ├── templates/  static/    original UI (HTML, CSS, JS, bundled Chart.js)
├── migrations/                Alembic migrations (0001 = full schema)
├── scripts/migrate_sqlite_to_postgres.py   copy old SQLite data to PostgreSQL
├── tests/                     pytest suite (+ tests/js for optional JS tests)
├── config.py   run.py   requirements.txt   requirements-dev.txt   .env.example
```

## Prerequisites (Windows + VS Code)

1. **Python 3.11 or newer** – download from <https://www.python.org/downloads/>. During setup tick **"Add python.exe to PATH"**. Check with `python --version` in a terminal.
2. **VS Code** – <https://code.visualstudio.com/> with the *Python* extension.
3. A **Gemini API key** (free) and optionally a **Supabase** project (both explained below).

## Setup

Open the project folder in VS Code, then open a terminal (**Terminal → New Terminal**, PowerShell).

```powershell
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\Activate.ps1
# If PowerShell blocks the script, run once:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your settings file
Copy-Item .env.example .env
```

In VS Code choose the interpreter (**Ctrl+Shift+P → Python: Select Interpreter → venv**).
Now edit `.env` as described in the next sections. **Never commit or share `.env`.**

Generate a `SECRET_KEY` with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### Gemini API key

1. Open <https://aistudio.google.com/apikey> and create an API key (free tier, no card needed).
2. Put it in `.env`: `GEMINI_API_KEY=...`
3. Model names: `.env.example` uses `gemini-3.5-flash-lite` for both text and audio. Google retires and adds models
   often, so check <https://ai.google.dev/gemini-api/docs/models> and the *Deprecations* page and change
   `GEMINI_TEXT_MODEL` / `GEMINI_AUDIO_MODEL` if a model is no longer available (the app shows
   "The configured Gemini model was not found" in that case). The free tier has per-model rate limits; if you see
   a quota message, wait a minute or switch model.

How audio works: the recording is sent to Gemini (inline for files up to 15 MB, through the Gemini Files API for
bigger ones; the remote copy is deleted afterwards) with a transcription prompt. The local temporary file is deleted
right after transcription. Formats: mp3, wav, m4a, aac, ogg, flac, aiff, webm, mp4 (up to `MAX_AUDIO_MB`, default 100).
If a format is rejected by Gemini, convert it to mp3 or wav.
Optional: set `GEMINI_AUDIO_MODEL=gemini-3.5-transcribe` to use Google's dedicated speech-to-text model
(audio formats only, no mp4).

### Database

**Option A – quick local start (SQLite).** Leave `DATABASE_URL` empty. The app creates `instance/nexus.db` and applies the migrations automatically on startup.

**Option B – Supabase PostgreSQL (recommended).**

1. Create a project at <https://supabase.com>.
2. Click **Connect** → copy the **Transaction pooler** connection string (port **6543**, user looks like `postgres.<project-ref>`).
3. Put it in `.env`:
   ```
   DATABASE_URL=postgresql://postgres.xxxxx:YOUR-PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
   ```
   If the password has special characters (`@ : / ? # %`), URL-encode them (`@` → `%40`).
4. Create the tables:
   ```powershell
   $env:FLASK_APP = "run.py"
   flask db upgrade
   ```
   (Supabase documents the session pooler/direct connection for schema changes. The transaction pooler worked in testing,
   but if `flask db upgrade` ever fails there, run it once with the session-pooler URL (port 5432) in `DATABASE_URL`, then switch back.)

What the app does for the transaction pooler: uses the `psycopg` 3 driver with `prepare_threshold=None` (no server-side
prepared statements – they break behind pooled connections), `sslmode=require`, a small SQLAlchemy pool (5 + 5 overflow),
`pool_pre_ping` and 5-minute connection recycling. The app never relies on session-level state (no advisory locks, no `SET`).

### Moving data from the old SQLite file

Only needed if you want to keep the accounts/data from the original project's `instance/nexus.db`:

```powershell
flask db upgrade                      # target must have the empty tables first
python scripts/migrate_sqlite_to_postgres.py --sqlite path\to\old\nexus.db
```

The script keeps ids and password hashes, refuses to run if the target already has data, fixes the PostgreSQL id
counters and prints a row-count verification. The old file is only read. Existing users keep their passwords.

## Run

```powershell
python run.py
```

Open <http://127.0.0.1:5000>, create an account and analyze your first meeting. Reminder emails run in the
background when `MAIL_SERVER`, `MAIL_USERNAME`, `MAIL_PASSWORD` and `MAIL_DEFAULT_SENDER` are set (for Gmail use an
*app password*). You can also trigger them manually or from Windows Task Scheduler with `flask send-reminders`.

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The suite (about 230 tests) mocks Gemini, so it uses **no API quota** and needs no internet. It covers authentication,
meetings, tasks, chatbot, Gemini service and error mapping, database relationships/cascades/rollbacks, security
(CSRF, uploads, isolation between users), templates and links, and reminders.
Extra checks that run automatically when available:

- `tests/test_gemini_http.py` runs the real Google SDK against a local fake Gemini server.
- To use the whole suite against PostgreSQL: set `TEST_DATABASE_URL` (a throw-away database – tables are dropped!).
- JavaScript behaviour tests (needs Node.js): `cd tests\js; npm install; cd ..\..` then `python -m pytest tests/test_frontend.py`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `Gemini is not configured` | Set `GEMINI_API_KEY` in `.env`, restart `python run.py`. |
| `configured Gemini model was not found` | Update `GEMINI_TEXT_MODEL` / `GEMINI_AUDIO_MODEL` to a current model. |
| `quota or rate limit was reached` | Free tier limit. Wait a minute, or use another model/API key. |
| `no such table` / `relation does not exist` | Run `flask db upgrade` (set `$env:FLASK_APP="run.py"` first). |
| `password authentication failed` / connection timeout | Check the pooler URL, password encoding, and that your network allows port 6543. |
| `prepared statement ... already exists` | You are not using the app's engine settings; keep `DATABASE_URL` as the only DB variable and use the `postgresql://` form. |
| Upload "too large" | Raise `MAX_AUDIO_MB` or compress the recording. |
| `SECRET_KEY must be set` | Set `SECRET_KEY` (required when `APP_ENV=production`). |
| `DLL load failed ... An Application Control policy has blocked this file` | Windows (Smart App Control / a work or school policy) blocks compiled Python packages in your folder. Try in order: (1) `pip install -r requirements.txt --force-reinstall` after moving the project to a path like `C:\Dev\NEXUS_AI` and recreating the venv there; (2) set `$env:DISABLE_SQLALCHEMY_CEXT_RUNTIME="1"` before running (SQLAlchemy then uses pure Python); (3) turn off Smart App Control (Windows Security > App & browser control) or ask your IT admin to allow the file. Other packages with compiled parts (for example `pydantic_core`) can be blocked the same way and have no pure-Python fallback. |
| PowerShell cannot run `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| Existing old `nexus.db` fails on startup | The old file was not created by migrations. Move it away (the app creates a new one) or use the copy script to PostgreSQL. |

## Security notes

- Secrets only in `.env` (ignored by Git). If a real `.env` was ever shared or uploaded anywhere, **rotate those keys/passwords**.
- CSRF tokens on every form and on chat requests; logout is POST-only; session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` when `APP_ENV=production` (serve over HTTPS then).
- Every meeting/task/chat query is scoped to the signed-in user; other users' ids return 404.
- Uploads: extension allow-list, content-signature check, random server-side file name, size limit, deleted after use.
- AI text is shown with `textContent`/Jinja escaping, never as raw HTML. Transcripts are treated as untrusted data in prompts.
- Not included (add before serving the public): login rate limiting, a production WSGI server behind HTTPS, malware scanning of uploads. The built-in scheduler should run in a single process (`python run.py`); for multiple workers use `flask send-reminders` from a scheduler instead.
- Gemini free-tier data may be used by Google to improve its products (see Google's terms); use a paid key for confidential meetings.
