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
| Agent runtime | Hand-written tool-calling loop — no framework. AI provider: IBM watsonx.ai, via its chat API's native tool calling (confirmed models include `ibm/granite-3-8b-instruct`) |

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

**Dependency Analyzer:** determine what actually depends on a file — never let an LLM guess this. Build **one** language adapter for the MVP (Python via `ast`). Shared edge format regardless of language: `(source_file, target_file, relationship_type, confidence)` — the rest of Guardian never needs to know how an adapter works internally, so adding a language later is a contained change. **Blast radius:** never store the precomputed transitive closure — load direct `edges` into NetworkX at query time and call `descendants()` then; the passport only ever shows a summary (direct vs. indirect counts — see Section 6).

**Risk Scorer:** must be fully deterministic — never ask an AI to assign a score. Signals: fan-in, bug-fix commit frequency (keyword heuristic: fix/bug/hotfix/revert/issue refs), ownership concentration, staleness (excluded from Phase 1 by default).
```
risk_score = 10 × [0.40×percentile_rank(bug_fix_count) + 0.30×percentile_rank(fan_in)
                    + 0.20×ownership_concentration + 0.10×percentile_rank(days_since_last_touch)]
```
**Percentile rank definition (exact, don't reimplement differently elsewhere):** `percentile_rank(x) = count(values < x) / (n - 1)`, for a batch of size `n > 1`. This is endpoint-preserving — the lowest value in the batch gets exactly `0.0`, the highest gets exactly `1.0`, everything else falls proportionally in between. For `n = 1` (a single file, no distribution to compare against), define it as `0.0` — this is a required special case, since `n - 1 = 0` would otherwise divide by zero. This is deliberately different from the standard exclusive percentile rank (`count(values < x) / n`, which never reaches 1.0) — the `0=lowest, 1=highest` framing is far more explainable in the passport (e.g. "highest fan-in of all analyzed files, 100th percentile") than a value like `66.7%` for what is actually the maximum in the batch.

Percentile rank, not min-max — min-max lets one outlier "god file" crush every other score toward zero, and rank-based scoring matches the score being explicitly relative, not absolute. **The score is a relative risk indicator, not a probability of failure** — never describe `8.7/10` as "87% chance of failure." **Weight renormalization when staleness is excluded:** compute the active weights at runtime from the base weights (0.40/0.30/0.20/0.10), dividing each by the sum of only the active weights — do not hardcode pre-rounded literals (e.g. 0.444/0.333/0.222), since rounding each independently causes the weights to sum to 0.999 instead of 1.0, silently capping the true achievable max at 9.99 instead of 10. Output must include contributing factors (`fan_in`, `bug_fix_count`, `top_author_pct`, `days_since_last_touch`) so it's explainable, not a black box.

**Seeding & incremental updates (`guardian scan [path]`, path optional, defaults to cwd):** the expensive work (AST parsing, `git log` calls) must only ever touch files that actually changed since the last scan — never re-parse or re-`git log` a file that didn't change. The cheap work (recomputing percentile ranks, since it's pure math over numbers already cached, no disk/git I/O) always runs across every file, every scan, because percentile rank is relative — one file's raw numbers changing can shift another untouched file's rank.

Cache schema:
- `files(path, last_touch_commit, last_touch_date, fan_in_count, bug_fix_count, top_author_pct, risk_score)` — store `last_touch_date`, never a precomputed "days since" count, since that silently goes stale by the mere passage of time even when nothing changed.
- `edges(source_file, target_file, relationship_type, confidence)` — direct edges only, no precomputed closures.
- `scan_meta(last_scan_commit_hash, branch)` — one row, tracks what the cache was last built from and which branch it reflects.

**Detecting what changed:** use `git diff --name-status -M <scan_meta.last_scan_commit_hash> HEAD` (not `--name-only`) — this reports each file's change type: `A` (added), `M` (modified), `D` (deleted), or `R<pct>` (renamed, with a similarity percentage). This distinction matters, because each type needs different handling (below). Separately, `git status`/uncommitted changes are never relevant here — the checkpoint comparison is always between two committed states.

**A file's own history** (bug-fix count, ownership %) is built with `git log --follow -- <path>`, never plain `git log` — `--follow` keeps tracking a file's history across a rename, so a renamed file doesn't wrongly look like it has a fresh, clean history when it's actually the same code with the same track record.

Scan algorithm, per changed file per its status:
- **Modified (`M`):** re-parse its AST for the new import set, re-run `git log --follow` to refresh its own row in `files`. Diff old vs. new imports to update `edges` (add rows for new imports, remove for dropped ones). For every file that gained or lost an incoming edge because of this (the changed file's import **targets**, not its dependents), bump `fan_in_count` by ±1.
- **Added (`A`):** parse it fresh, add a new row to `files` and new `edges` for its imports; bump `fan_in_count` on its targets.
- **Deleted (`D`):** remove its row from `files`. Remove `edges` where it was the `source_file`. For `edges` where it was the `target_file` (other files still importing something that no longer exists), don't just silently drop these — record them as a **broken import** fact, since that's a real risk signal, not noise. Decrement `fan_in_count` for whatever it used to import.
- **Renamed (`R<pct>`), high confidence (e.g. ≥90%):** carry the `files` row forward under the new path, keeping its historical signals intact (the file's history didn't reset). Update any `edges` referencing the old path to the new one.
- **Renamed, low confidence:** treat as delete-old + add-new rather than guessing — consistent with the "never guess, mark lower confidence or skip" rule for the analyzer itself.

After all changed files are processed: recompute percentile ranks and `risk_score` across every row in `files` — cheap, in-memory, no I/O — then update `scan_meta.last_scan_commit_hash` to the new HEAD.

**Branch changes:** before doing an incremental diff, compare the currently checked-out branch to `scan_meta.branch`. If it differs, don't attempt an incremental update at all — do a full rescan instead. Diffing file lists across two unrelated branch histories isn't a meaningful incremental update, and trying anyway risks silently mixing evidence from two different codebase states. Update `scan_meta.branch` after the rescan.

On the very first run (no `scan_meta` row yet), do a full pass over every file instead of a diff.

## 5. Evidence Store, Change Passport, Prediction Log & Feedback Loop

- **Evidence Store:** SQLite. Core tables (`files`, `edges`, `scan_meta`) are defined in Section 4. Add a `predictions` table for the Prediction Log below, and style fingerprints/decisions in Phase 2. Keep the schema simple until the MVP proves what's actually needed.
- **Change Passport:** answers "what should I know before merging this?" — files changed, blast radius, risk flags, relevant history, plus style/decision conflicts and recommended checks once implemented. Every claim needs a traceable source (commit hash, file path, PR number). Never show an unsupported claim.
- **Prediction Log (part of Phase 1, not optional):** every passport generated gets permanently recorded — never discarded. This is the foundation for the Feedback Loop below.
- **Feedback Loop (future-facing):** long-term, compare each prediction to what actually happened after merge (correct / false positive / false negative). **For the hackathon: do not claim Guardian automatically learns or adjusts its own weights unless actually implemented** — it may only measure and surface prediction accuracy. Simulated demo outcomes must be clearly labeled as simulated, never presented as real.

## 6. Input & CLI Output (confirmed)

**Input (reconciled — one consistent form):** `guardian analyze [path] --diff <ref1>..<ref2>` or `guardian analyze [path] --files <file1> [file2 ...]`. `path` is optional and defaults to `.` (current directory), matching `guardian scan`'s convention — the two commands must behave the same way here. `--diff` uses git's own `A..B` range syntax so it's immediately familiar. `--diff` is the primary mode (runs `git diff --name-status -M <ref1> <ref2>` internally to get the changed-file list, everything downstream operates on that); `--files` is the secondary/direct mode, bypassing the diff — useful for testing a single file's passport without a real diff existing.

**Pre-flight checks, in this order, before anything else runs:** (1) does `path` exist on disk — if not, `Error: '<path>' does not exist.`; for `--files`, check each file individually the same way. (2) is `path` inside a git repository (`git rev-parse --is-inside-work-tree`) — if not, `Error: '<path>' is not a git repository. Guardian needs git history to compute risk scores.` (3) does the repo have any commits yet — if not, `No commit history found — risk scores require at least one commit.` Never let a raw git error or a Python traceback reach the user for any of these; catch and explain.

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

**Error handling:** never silently swallow failures — return a useful error, never replace missing evidence with a guess. This includes the pre-flight checks in Section 6 (missing path/file, not a git repo, empty repo) and any other unexpected failure (Git command failure, missing record, AI API failure). E.g., *"Historical decision data unavailable — cannot determine if this conflicts with a previous decision"* beats inventing an answer.

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
