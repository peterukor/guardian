# Guardian

**Evidence-based pre-merge risk intelligence for software changes.**

Guardian analyzes repository changes using deterministic code, dependency, Git-history, and risk evidence, then gives an AI Agent tools to investigate that evidence and produce a **Change Passport**.

## How it works

```text
Repository
    ↓
Dependency + Git Analysis
    ↓
Deterministic Risk Scoring
    ↓
SQLite Evidence Store
    ↓
AI Agent
    ↓
Change Passport
```

> **Evidence first. AI reasons over evidence; it does not invent it.**

## Features

- **Dependency analysis** — builds a repository dependency graph using Python AST and NetworkX.
- **Blast-radius analysis** — identifies files that may be affected by a change.
- **Git history analysis** — examines file history, bug-fix patterns, and ownership concentration.
- **Deterministic risk scoring** — produces risk scores and LOW/MEDIUM/HIGH levels from repository evidence.
- **AI Agent** — investigates evidence and produces important findings and recommended checks.
- **Change Passport** — presents the resulting risk assessment and findings.
- **Prediction Log** — persists deterministic risk predictions for future outcome tracking.
- **Works without AI** — if watsonx credentials aren't configured, every deterministic feature above still works. Only the AI findings/checks section is skipped.

## Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

### AI Agent (optional)

The AI findings/checks feature needs IBM watsonx.ai credentials. Everything else in Guardian works without this step.

1. Copy the example env file:
   ```bash
   cp env.example .env
   ```
2. Open `.env` and fill in your real values:
   ```
   WATSONX_API_KEY=your-ibm-cloud-api-key
   WATSONX_URL=https://us-south.ml.cloud.ibm.com
   WATSONX_PROJECT_ID=your-watsonx-project-id
   ```
   Get these from [IBM watsonx.ai](https://dataplatform.cloud.ibm.com) — the API key from "Manage IBM Cloud API keys," and the Project ID from your project's "Developer access" panel.
3. `.env` is gitignored — never commit real credentials.

If `.env` is missing or incomplete, `guardian analyze` still runs normally; the "Important findings" and "Recommended checks" sections will just say the Agent is unavailable.

## Usage

All commands are run as a Python module from the repository root:

```bash
python3 -m src.cli <command> ...
```

**Scan a repository** (required once, before analyzing):

```bash
python3 -m src.cli scan .
```

**Analyze a Git diff:**

```bash
python3 -m src.cli analyze . --diff HEAD~1..HEAD
```

**Analyze specific files:**

```bash
python3 -m src.cli analyze . --files src/payment.py
```

**Quick mode — file names and risk scores only, no AI call:**

```bash
python3 -m src.cli analyze . --diff HEAD~1..HEAD --file-name
```

**Show more (or fewer) files in full detail** (default: top 3 riskiest):

```bash
python3 -m src.cli analyze . --diff HEAD~1..HEAD -n 10
```

**JSON output** (for scripts/tooling):

```bash
python3 -m src.cli analyze . --diff HEAD~1..HEAD --json
```

Full flag reference:

```bash
python3 -m src.cli --help
python3 -m src.cli scan --help
python3 -m src.cli analyze --help
```

The default scan database is:

```text
<repo>/.guardian/guardian.db
```

Use `--db` on either command to use a custom path.

## Architecture

```text
src/
├── adapters/       # Language-specific analysis
├── agent/          # Agent loop, tools, provider integration
├── cli/            # CLI parsing, commands, passports, rendering
├── evidence_store/ # SQLite persistence
├── git_history/    # Git history analysis
├── risk_scorer.py
└── scanner.py
```

Deterministic components calculate repository facts. The Agent consumes those facts through tools and does not calculate risk, blast radius, or Git-history evidence itself.

## Development

Run the full test suite:

```bash
pytest
```

Agent tests use mocks and do not require live watsonx credentials.

## Status

### Current MVP

- Repository scanning
- Dependency and blast-radius analysis
- Git-history analysis
- Deterministic risk scoring
- SQLite Evidence Store
- Agent tool-calling loop
- Agent → Change Passport integration
- CLI scan/analyze workflow with JSON, quick-list, and top-N detail modes
- Prediction Log persistence
- Full functionality without AI credentials configured

### Future

Additional language adapters, richer repository signals, prediction outcome tracking, a `--verbose` flag for full risk-factor detail, and an optional web frontend.

## License

See [LICENSE](LICENSE).
