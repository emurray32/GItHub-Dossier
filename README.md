# GitHub Dossier 🔍

**AI-Powered Sales Intelligence for Localization Opportunities**

GitHub Dossier is a BDR (Business Development Representative) tool that scans public GitHub organizations to identify companies that need localization solutions. It uses AI to generate actionable sales insights, including pain points, tech stack hooks, and ready-to-send email drafts.

---

## ✨ Features

- **Deep GitHub Scanning**: Analyzes commits, PRs, and file structures for i18n signals
- **AI-Powered Analysis**: Uses Google Gemini to generate actionable sales intelligence
- **Pain Point Detection**: Identifies developer frustration with translation workflows
- **Competitor Detection**: Spots existing TMS (Translation Management System) usage
- **Buying Committee Identification**: Finds key stakeholders to contact
- **Ready-to-Use Cold Emails**: Generates personalized outreach drafts
- **PDF Reports**: Export professional reports for your sales team
- **Compliance Risk Assessment**: Detects missing localized legal/privacy assets

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/emurray32/GItHub-Dossier.git
cd GItHub-Dossier
```

### 2. Set Up Environment
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure API Keys
```bash
cp .env.example .env
```

Edit `.env` and add your API keys:
- **GITHUB_TOKEN**: Get one at [GitHub Settings > Tokens](https://github.com/settings/tokens)
- **GEMINI_API_KEY**: Get one at [Google AI Studio](https://makersuite.google.com/app/apikey)

### 4. Run the Application
```bash
python app.py
```

Visit `http://localhost:5000` in your browser.

---

## 📖 How to Use

1. **Enter a Company Name**: Type any company name in the search bar
2. **Watch the Live Scan**: See real-time progress as GitHub is analyzed
3. **Review the Report**: Get AI-generated insights including:
   - Opportunity Score (1-10)
   - Pain Point Analysis
   - Tech Stack Recommendations
   - Cold Email Draft
4. **Export as PDF**: Download a professional report for your records

---

## 🔧 Configuration

Key settings in `config.py`:

| Setting | Default | Description |
|---------|---------|-------------|
| `MAX_REPOS_TO_SCAN` | 15 | Maximum repos to analyze per org |
| `COMMITS_PER_REPO` | 100 | Recent commits to scan |
| `PR_LOOKBACK_DAYS` | 90 | Days of PR history to analyze |
| `GREENFIELD_STAR_THRESHOLD` | 1000 | Stars needed for greenfield detection |

---

## 🛡️ Rate Limits

- **Without GitHub Token**: 60 requests/hour (limited)
- **With GitHub Token**: 5,000 requests/hour (recommended)

Always configure your `GITHUB_TOKEN` for best results!

---

## 📁 Project Structure

```
GitHub-Dossier/
├── app.py              # Flask application
├── config.py           # Configuration settings
├── database.py         # SQLite database operations
├── ai_summary.py       # AI analysis with Gemini
├── pdf_generator.py    # PDF report generation
├── utils.py            # Utility functions
├── monitors/
│   ├── discovery.py    # GitHub org discovery
│   └── scanner.py      # Deep scan logic
├── templates/          # HTML templates
└── static/             # CSS and assets
```

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

---

## 📄 License

MIT License - feel free to use this for your sales team!
