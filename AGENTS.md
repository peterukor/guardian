# AGENTS.md — Guardian

If something here conflicts with an instruction given in chat, prefer what's in chat for that session — but flag the conflict, don't silently override this file.

Guardian is an evidence-based pre-merge intelligence system. It investigates code changes using the real structure and history of a codebase, then uses an AI Agent to turn that evidence into a prioritized **Change Passport**. Guardian must stay a substantial, working system with the AI removed — the AI reasons over evidence, it never invents it.

## 1. Design philosophy: core test & division of labor

> If the AI model were removed, would there still be a substantial, working piece of software?

If no, the feature is wrong for this project. Back every feature with real engineering (parsers, graphs, deterministic calculations, Git history, databases) — never replace these with a prompt. **AI may:** reason over evidence, judge relevance, request more evidence, summarize, generate the passport. **AI must never:** invent facts, dependencies, or decisions, or calculate metrics Guardian can calculate itself.

- **Deterministic engines (no LLM, ever):** dependency graph, risk score, style fingerprinting, fact storage — must work correctly with the AI turned off.
- **The Agent:** reads evidence already computed, decides signal vs. noise, fetches more if incomplete, synthesizes the passport via a real multi-step tool-calling loop — never one giant prompt with everything dumped in.
- **One exception:** the Git/PR History Miner (Phase 2) may use an LLM to classify free-text history into structured decisions (regex can't do this) — acceptable only because the output is persisted, structured data, not an ephemeral answer.

Every fact shown to a user must trace back to the Evidence Store or another named source.

## 2. Tech stack & architecture

| Area | Choice |
|---|---|
| Backend | Python |
| Code parsing | `ast` (built-in) |
| Dependency graph | NetworkX (`descendants()`/`ancestors()` for blast radius) |
| Git history | `subprocess` calling Git CLI directly |
| Database | SQLite |
| Initial interface | CLI |
| Frontend (later) | React + TypeScript allowed once the CLI and Agent loop fully work — not before. Calls a small API over the Evidence Store; never duplicates engine logic |
| Agent runtime | Hand-written tool-calling loop — no framework. AI provider still being finalized — see Section 3 |

Start simple; don't add infrastructure for its own sake (Postgres, Docker, etc. can come later if justified). **Fixed regardless:** the Dependency Analyzer, Risk Scorer, Evidence Store, and Agent must never be split into separately-networked services — a frontend calling one local API over the Evidence Store is fine, turning the engines into microservices is not.

```text
Git/PR History Miner ───────┐
Static Dependency Analyzer ─┤
Risk Scorer ─────────────────┼──> Evidence Store ──> AI Agent ──> Change Passport
Style Fingerprinter ────────┘
```
Engines communicate only through the shared Evidence Store, never each other's internals — this keeps each independently testable. The Agent is a consumer/reasoner over evidence, never the source of truth.

## 3. Agent runtime & behavior

Before writing much Agent-specific code, verify (with a small proof of concept, don't assume) that the chosen runtime supports: (1) programmatic/non-interactive calls from Python, (2) custom tools/functions the Agent can call, (3) multi-step tool calling — call a tool, see the result, decide whether to call another, (4) a final synthesized response after that loop.

Example tools: `get_risk_score(file)`, `get_blast_radius(file)`, `get_style_conflicts(diff)`, `get_related_decisions(files)`. Don't dump the whole Evidence Store into one prompt when tool-based retrieval is practical. Guardian's core functionality must not depend on any one specific AI provider.

**Loop shape:** get risk info → inspect blast radius → identify important signals → request more evidence if needed → evaluate → rank findings → generate passport. The Agent must clearly separate facts from its own reasoning, and say so explicitly when evidence is missing rather than guessing.

## 4. Deterministic engines: Dependency Analyzer & Risk Scorer

**Dependency Analyzer:** determine what actually depends on a file — never let an LLM guess this. Build **one** language adapter for the MVP (Python via `ast`). Shared edge format regardless of language: `(source_file, target_file, relationship_type, confidence)` — the rest of Guardian never needs to know how an adapter works internally, so adding a language later is a contained change. **Blast radius** = full transitive closure (`descendants()`), grouped by depth internally, but the passport only ever shows a summary (direct vs. indirect counts — see Section 6). **Seeding:** a separate `guardian scan` populates the Evidence Store from a full-repo pass, once; `guardian analyze` always reads from that cache and never re-parses per run.

**Risk Scorer:** must be fully deterministic — never ask an AI to assign a score. Signals: fan-in, bug-fix commit frequency (keyword heuristic: fix/bug/hotfix/revert/issue refs), ownership concentration, staleness (excluded from Phase 1 by default).
```
risk_score = 10 × [0.40×percentile_rank(bug_fix_count) + 0.30×percentile_rank(fan_in)
                    + 0.20×ownership_concentration + 0.10×percentile_rank(days_since_last_touch)]
```
Percentile rank, not min-max — min-max lets one outlier "god file" crush every other score toward zero, and rank-based scoring matches the score being explicitly relative, not absolute. If staleness is excluded, renormalize remaining weights to sum to 1.0 (0.40→0.444, 0.30→0.333, 0.20→0.222) so the max stays a true 10, not 9. **The score is a relative risk indicator, not a probability of failure** — never describe `8.7/10` as "87% chance of failure." Output must include contributing factors (`fan_in`, `bug_fix_count`, `top_author_pct`, `days_since_last_touch`) so it's explainable, not a black box.

## 5. Evidence Store, Change Passport, Prediction Log & Feedback Loop

- **Evidence Store:** SQLite. Minimum: files, dependencies, risk info, changes, predictions. Add style fingerprints/decisions in Phase 2. Keep the schema simple until the MVP proves what's actually needed.
- **Change Passport:** answers "what should I know before merging this?" — files changed, blast radius, risk flags, relevant history, plus style/decision conflicts and recommended checks once implemented. Every claim needs a traceable source (commit hash, file path, PR number). Never show an unsupported claim.
- **Prediction Log (part of Phase 1, not optional):** every passport generated gets permanently recorded — never discarded. This is the foundation for the Feedback Loop below.
- **Feedback Loop (future-facing):** long-term, compare each prediction to what actually happened after merge (correct / false positive / false negative). **For the hackathon: do not claim Guardian automatically learns or adjusts its own weights unless actually implemented** — it may only measure and surface prediction accuracy. Simulated demo outcomes must be clearly labeled as simulated, never presented as real.

## 6. Input & CLI Output (confirmed)

**Input:** two modes. Primary: `guardian analyze <ref1> <ref2>` (two git refs/commit hashes) — runs `git diff --name-only` internally to get the changed file list, everything downstream operates on that. Secondary: `guardian analyze --files <path>` to pass specific files directly, bypassing the diff — useful for testing a single file's passport without a real diff existing.

**Output format (confirmed):**
```
$ guardian analyze . --diff HEAD~1..HEAD

GUARDIAN CHANGE PASSPORT
────────────────────────
Risk: HIGH — 8.7/10

Changed files: src/payment.py
Blast radius: 17 dependent files (x direct, n indirect)

Risk factors:
  Fan-in: 17
  Bug-fix commits: 6
  Top author concentration: 73%
  Days since last touch: 41

Important findings:
  ...

Recommended checks:
  ...
```
Build this as a structured object first, then render it to text — this lets a future `--json` flag or web UI reuse the exact same data without re-deriving it.

## 7. Build order, testing & error handling

**Build order (do not skip ahead) — Phase 1 (MVP, stop here if time is short):** Dependency Analyzer (one language) → Risk Scorer → SQLite Evidence Store → Agent integration → CLI Change Passport → Prediction Log. **Phase 2 (only after Phase 1 fully works):** second language adapter (if useful) → Style Fingerprinter → Git/PR History Miner + Decision Nodes → fuller multi-step Agent orchestration. **Phase 3 (roadmap only, don't attempt unless asked):** runtime instrumentation, production incident integration, full Flight Recorder, chaos/failure injection, automatic incident linking, automatic risk-weight tuning.

**Testing:** keep scope narrow, not exhaustive. Prioritize logic that's easy to get subtly wrong and expensive to discover wrong later: import/dependency resolution edge cases, the risk formula's math, blast-radius graph direction, Agent tool correctness. Skip simple, obviously-correct glue code (arg parsing, getters, formatting). Gut check: could this be silently wrong in a way that wouldn't show up until the demo? If yes, test it; if no, skip it. **Failure mode to treat as a bug:** the Agent stating any number, relationship, or fact that wasn't actually retrieved from evidence.

**Error handling:** never silently swallow failures (invalid file, Git command failure, missing record, AI API failure) — return a useful error, never replace missing evidence with a guess. E.g., *"Historical decision data unavailable — cannot determine if this conflicts with a previous decision"* beats inventing an answer.

## 8. Code quality, git discipline & security

Simple, readable Python; clear names; no clever one-liners at the cost of clarity. Separate concerns into files (parsing, graph, git analysis, risk calc, DB access, Agent logic, CLI). Don't build abstractions before they solve a real problem.

**Comments:** every function/class gets a short docstring stating what it does and *why* — not a restatement of its name. 1–3 sentences: purpose first, then any non-obvious behavior (edge cases handled, what a field like `confidence` means here, why a choice was made a certain way). Add inline comments only where intent isn't obvious from the code itself — never comment something the code already says clearly (e.g., don't write `# increment counter` above `count += 1`). Needing several inline comments to explain one function is usually a sign to split it into smaller, named functions instead.

**Branching & commit workflow (follow this exact sequence per feature/component):**
1. Create a new branch for the feature/component (see naming convention below) and **push it upstream immediately**, before writing code — this creates the remote branch and a visible starting point.
2. Write code. Once a meaningful chunk exists across the new file(s) for that branch (e.g., the adapter's core logic is in place and runs, even before it's fully polished), **commit**. Run tests and check the diff first.
3. When the branch has done its job (the component works and its tests pass), **merge it and commit** — don't leave finished work sitting unmerged on a branch.

Message format: `<type>: <short description>`, imperative mood, lowercase type prefix. Types: `feat` (new capability), `fix` (bug fix), `test` (adding/updating tests), `refactor` (no behavior change), `docs` (comments/README/AGENTS.md), `chore` (setup, dependencies, config). Examples:
```
feat: add Python AST import extraction and graph construction
fix: resolve relative imports one level up correctly
test: add coverage for unresolved external imports
docs: document risk formula weights in AGENTS.md
chore: add networkx to requirements.txt
```
Don't mix unrelated changes into one commit — if the message needs "and" to describe two different things, it's probably two commits.

**Branch naming:** `<type>/<short-description>`, matching the commit-type prefixes above — e.g. `feat/dependency-analyzer`, `feat/risk-scorer`, `feat/evidence-store`, `feat/agent-tools`, `fix/blast-radius-depth`, `test/risk-formula`. Lowercase, hyphen-separated, no spaces or ticket numbers needed for a hackathon.

Never commit API keys, tokens, passwords, or `.env` files with real secrets — use environment variables, keep credentials out of the repo, don't log them.

## 9. Demo philosophy & anti-patterns

The demo's credibility rests on one thing: show the raw engine output first (e.g., `Fan-in: 17, Bug-fix commits: 6, Top author: 73%, Risk: 8.7/10`), unstyled, to prove it's computed — then show what the Agent did with it. That gap is the product.

Glance here mid-session if unsure:
- ❌ Agent computing a risk score itself instead of calling the documented formula.
- ❌ Agent inventing or estimating a fact (bug count, authorship %, past decision) instead of retrieving it.
- ❌ One mega-prompt with everything dumped in, no intermediate tool calls.
- ❌ Skipping the raw-evidence view in the demo before showing the passport.
- ❌ Building a second language adapter or a second style-pattern category before the first is solid.
- ❌ Implying Guardian self-tunes or "learns" automatically — it doesn't yet, it only measures and surfaces accuracy.
- ❌ Splitting the core engines into separately-networked services (Section 2).

## 10. Scope, ambiguity & communication style

One feature that works reliably beats five that barely work. Before building anything new, ask: does it solve a real problem, does it need real engineering, would Guardian still have value without the AI, can it be built reliably in the time left, does it strengthen the core idea? If not, skip it. Tech choices can evolve when a concrete need justifies it.

When something is genuinely ambiguous: identify it, state the assumption explicitly, pick the smallest reasonable implementation, and continue — don't silently expand scope or change a major architectural decision without flagging it. Goal: a small, real, technically defensible system that demonstrates Guardian's core idea well — not the biggest possible system.

**When explaining things to the project owner:** simple English, explain unfamiliar concepts from the ground up (don't assume distributed-systems/compiler/AI-agent background), explain *why* not just *what to type*, note tradeoffs briefly, and recommend one approach rather than listing every option.
