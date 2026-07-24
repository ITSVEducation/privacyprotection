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

## Critical environment gotcha: spacy/ginza version pin

`pyproject.toml` pins `spacy>=3.4.4,<3.8.0`. **Do not remove this pin or let it float to spacy>=3.8** —
that version breaks `ja_ginza`'s `compound_splitter` pipeline factory (confection's stricter config
validation rejects the factory's own `default_config={"split_mode": None}` against its `str` type
hint), and `ja_ginza` has not published a fix. This was diagnosed empirically, not guessed. If you
ever need to touch NLP dependencies, re-verify `spacy.load("ja_ginza")` actually produces entities
(`doc.ents`) before assuming it works — a config error at load time is easy to mistake for "not
installed." Also note: `spacy` imports `click` directly at runtime without declaring it as a
dependency in some resolutions — it's pinned explicitly in `pyproject.toml` for that reason.

## Architecture

Three layers, strictly separated by dependency direction — GUI depends on services, services depend
on core+handlers, core and handlers never import from each other's siblings except through the
composition points below. This isolation is deliberate (see `docs/02-architecture.md`): the core
layer is GUI-free and fully unit-testable; the GUI is a thin wrapper that can't be unit tested the
same way. `docs/02-architecture.md` is the authoritative, detailed description — this section is the
index plus the handful of privacy-critical invariants that must never regress.

**`core/`** — detection, masking, and restoration, all pure functions/classes over plain strings.
No file I/O, no GUI, no network. Three independent detectors (`PatternDetector` regex,
`DictionaryDetector` term list, `NerDetector` lazy-loaded GiNZA NER) each return
`list[Detection]` and are combined by `Detector`. Invariants that must hold:
- **Overlap = trim, never discard** (`core/spans.py`'s `remaining_spans()`, used by both
  `patterns.py` and `detector.py`): when a lower-priority detection partially overlaps a
  higher-priority one without full containment, it is **trimmed to its non-overlapping remainder,
  not dropped**. A regression to discard-on-overlap silently drops real PII (e.g. address abutting a
  phone number) — a reviewer treats it as a privacy bug, not a style nit.
- **One `Masker` per batch**: reuse a single `Masker` instance across a whole folder so the same
  real-world value always maps to the same token (`【人名_1】` etc.); see
  `Masker.scan_existing_tokens()` for collision avoidance with pre-existing token-shaped text.
- **Mapping CSV is deliberately plain text** (`mapping_io.py`, `.pmap.csv`, UTF-8 BOM) — threat
  model is "don't let PII reach a cloud AI," not at-rest protection. It rejects duplicate tokens on
  load; don't relax that check.
- **`Restorer` uses exact string match only** — never fuzzy-match or auto-correct a garbled token;
  an altered token is left in place and reported as unknown, not guessed at.

**`handlers/`** — one class per file format implementing the `FileHandler` ABC (`read_fragments`,
`write_fragments`). A `Fragment` is a location-tagged piece of text; handlers extract only the
fragments worth scanning and write back *only* those, leaving formatting/formulas/unrelated cells
untouched. `TextHandler` preserves exact byte-level encoding and line endings — a masked plain-text
file is byte-identical to the original except for the substituted spans.

**`services/`** — orchestrates handlers + core into whole-file and whole-folder mask/restore
operations, plus the post-processing report shown to the user. The report **never includes an actual
detected PII value**, only category names and counts — a hard constraint from the design doc.

**`gui/`** — PySide6. Talks only to `services/`, never imports from `core/` or `handlers/` directly.

## Working conventions established during implementation

- Token category labels are a **fixed, closed vocabulary** defined once in `core/models.py`
  (`CATEGORY_LABELS`) — several distinct detection categories intentionally share one label (e.g.
  `POSTAL`, `MYNUMBER`, `CREDITCARD` all render as `番号`). Don't add a new label without checking
  whether an existing one already covers the concept.
- Every `Detection.text` must equal `original_text[detection.start:detection.end]` exactly — this
  invariant has caused real bugs (regex `.group()` vs. recomputed slices diverging after a span was
  trimmed) and is worth an explicit test assertion whenever you touch span arithmetic.
- Error messages and the eventual user-facing report must never contain an actual detected value —
  only counts, categories, and file/token names. This has been treated as a hard constraint in every
  task so far, not something to weigh against convenience.
- The plan's own reference code for a task is a *starting point*, not gospel — several tasks so far
  have found and fixed genuine bugs in it (reversed token numbering, an unreachable "undecodable
  bytes" test case, discard-on-overlap instead of trim). Run the given tests against reference code
  before trusting it; treat test failures as real, not as something to work around by weakening the
  test.
