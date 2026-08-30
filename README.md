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

## Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Scan a repository:

```bash
guardian scan ./my-repo
```

Analyze a Git change:

```bash
guardian analyze ./my-repo --diff HEAD~1..HEAD
```

Analyze specific files:

```bash
guardian analyze ./my-repo --files src/payment.py
```

Get JSON output:

```bash
guardian analyze ./my-repo --diff HEAD~1..HEAD --json
```

The default Evidence Store is:

```text
<repo>/.guardian/guardian.db
```

Use `--db` to provide a custom database path.

## Architecture

```text
src/
├── adapters/       # Language-specific analysis
├── agent/          # Agent loop, tools, provider integration
├── cli/            # CLI parsing, commands, passports, rendering
├── evidence_store/ # SQLite persistence
├── git_history/    # Git history analysis
├── analyzer.py
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
- CLI scan/analyze workflow
- JSON output
- Prediction Log persistence

### Future

Additional language adapters, richer repository signals, prediction outcome tracking, and an optional web frontend.

## License

See [LICENSE](LICENSE).
