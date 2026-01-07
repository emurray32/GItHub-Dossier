# Lead Machine - Deep-Dive Research Engine

## Overview
A Flask application for analyzing GitHub organizations to detect localization signals. The app scans GitHub repositories, commits, and PRs to find internationalization (i18n) indicators and provides AI-powered analysis using Google Gemini.

## Project Structure
```
├── app.py              # Main Flask application
├── config.py           # Configuration settings
├── database.py         # SQLite database module
├── ai_summary.py       # AI-powered analysis using Gemini
├── monitors/           # GitHub scanning modules
│   ├── discovery.py    # GitHub org discovery
│   └── scanner.py      # Deep scan functionality
├── static/             # Static assets
│   ├── css/style.css   # Styles
│   └── js/stream.js    # SSE streaming client
├── templates/          # Jinja2 templates
│   ├── base.html       # Base template
│   ├── index.html      # Homepage
│   ├── console.html    # Scan console view
│   ├── report.html     # Report view
│   ├── history.html    # Scan history
│   └── error.html      # Error page
└── data/               # SQLite database storage
```

## Running the Application
The Flask server runs on port 5000:
```bash
python app.py
```

## Environment Variables
- `FLASK_SECRET_KEY` - Flask secret key (optional, has default)
- `FLASK_DEBUG` - Enable debug mode (optional)
- `GITHUB_TOKEN` - GitHub API token for scanning
- `GEMINI_API_KEY` - Google Gemini API key for AI analysis

## Technology Stack
- **Backend**: Python 3.11, Flask
- **Database**: SQLite (local file-based)
- **Frontend**: HTML, CSS, JavaScript with Server-Sent Events
- **AI**: Google Gemini API

## Recent Changes
- 2026-01-07: Initial Replit environment setup
