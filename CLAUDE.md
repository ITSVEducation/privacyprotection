# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

Single checkout at `C:\Users\119003\git\privacyprotection`, branch `master` — the original
`pii-masking-app` feature branch has been merged (`git log` shows the merge commit) and the
temporary worktree that once held the implementation is gone. Source lives under a `src/` layout:
the `privacyprotection` package is at `src/privacyprotection/` and is imported via the editable
install (`pip install -e`), so `python -m privacyprotection.gui.app` works from the repo root.

## What this project is

A fully-local Windows desktop app that masks personal/confidential information in text before it's
pasted into an AI service, and can reverse the masking afterwards. The core mask/restore pipeline,
file handlers, services, and PySide6 GUI are all implemented. Full requirements are in:
- `docs/` — the curated, as-built specification set (start at `docs/README.md`). This is the
  primary, up-to-date spec, split into 01-overview / 02-architecture / 03-masking-spec /
  04-ui-and-operations / 05-known-issues-and-roadmap. It replaces the original single-file design
  spec. Section numbers referenced elsewhere in this file (e.g. "design doc §4") now live in these
  documents (§1–§2 in 01, §3–§5 in 02, §4.2–§4.8 in 03, §6–§9 in 04).
- `docs/superpowers/plans/2026-07-23-pii-masking-app.md` — the 20-task TDD implementation plan
  (historical record of how it was built)

## Commands

```bash
# Environment: Python 3.11 required (a later Python may be the system default — check with
# `python --version`; this project was set up using a pyenv-style installed 3.11.12, not the
# system Python). Venv already exists at .venv/ in the repo root.
.venv/Scripts/pip install -e ".[dev]"        # install/update deps (editable + dev extras)

.venv/Scripts/python -m pytest tests/ -v     # run the full suite
.venv/Scripts/python -m pytest tests/core/test_masker.py -v            # one file
.venv/Scripts/python -m pytest tests/core/test_masker.py::test_name -v # one test

# GUI entry point:
.venv/Scripts/python -m privacyprotection.gui.app

# PyInstaller build (frozen .exe):
powershell -ExecutionPolicy Bypass -File scripts/build.ps1
```

There is no lint/format tooling configured (no ruff/black/flake8 config exists) — don't invent one
unprompted.

## Critical environment gotcha: dependency pins that must not float

`pyproject.toml` pins `spacy>=3.4.4,<3.8.0` and `numpy<2`. **Both upper bounds are load-bearing** —
dropping either kills GiNZA NER, i.e. masking stops detecting anything, and both failure modes read
as "the model isn't installed" rather than "the pin was wrong." Per-pin rationale is in the
`pyproject.toml` comments; both were diagnosed empirically, not guessed. A PreToolUse hook
(`scripts/hooks/guard-dependency-pins.ps1`) blocks edits that remove them. If you do raise one
deliberately, re-verify that `spacy.load("ja_ginza")` actually produces entities (`doc.ents`) before
assuming it works. Also note: `spacy` imports `click` at runtime without declaring it as a
dependency in some resolutions — it's pinned explicitly for that reason.

## Architecture

Three layers, one-directional dependencies: GUI → services → core + handlers. Core and handlers
never import each other. `docs/02-architecture.md` is the authoritative description of interfaces,
data flow, output atomicity, and folder-walk boundary conditions — read it there rather than
re-deriving from source. This section is the index plus the invariants.

| Layer | Role |
|---|---|
| `gui/` | PySide6. Calls `services/` only — never imports `core/` or `handlers/` |
| `services/` | Binds handlers + core into whole-file and whole-folder mask/restore; builds the post-run report |
| `core/` | Detection (`PatternDetector` / `DictionaryDetector` / `NerDetector`, combined by `Detector`), `Masker`, `Restorer`. Pure functions over strings — no file I/O, no GUI, no network |
| `handlers/` | One `FileHandler` per format. Extracts and writes back only the text fragments worth scanning, leaving formatting/formulas/unrelated cells untouched. `TextHandler` output is byte-identical to the original except for the substituted spans |

### Privacy invariants — never regress these

Rationale and verification steps live in `.claude/rules/core-invariants.md`, which loads when you
open `core/` or `services/`. A PostToolUse hook runs the corresponding tests on every edit to those
directories (`.claude/settings.json`).

- Overlapping detections are **trimmed to their non-overlapping remainder, never discarded**.
- `Detection.text` always equals `original_text[detection.start:detection.end]`.
- **One `Masker` per batch**, so the same real value maps to the same token across a whole folder.
- `Restorer` matches tokens **exactly** — never fuzzy-match or auto-correct a garbled token.
- The mapping CSV (`.pmap.csv`) is **deliberately plain text** and rejects duplicate tokens on load.
- Reports, logs, and error messages carry **categories and counts only, never a detected value**.

## Working conventions

- The implementation plan's own reference code is a *starting point*, not gospel — several tasks
  found genuine bugs in it (reversed token numbering, an unreachable "undecodable bytes" test case,
  discard-on-overlap instead of trim). Run the given tests against reference code before trusting
  it; treat failures as real, not as something to work around by weakening the test.
