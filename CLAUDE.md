# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This directory is a **git worktree**, not the main checkout. The main repo lives at
`C:\Users\119003\git\privacyprotection` (branch `master`, spec/plan docs only, no source code).
This worktree is checked out on branch `pii-masking-app` and contains the actual implementation.
`git worktree list` from either location shows both. Do not delete this worktree without checking
for uncommitted work — everything under `.superpowers/sdd/` (see below) is git-ignored scratch state
that only exists here.

## What this project is

A fully-local Windows desktop app that masks personal/confidential information in text before it's
pasted into an AI service, and can reverse the masking afterwards. Full requirements are in:
- `docs/` — the curated, as-built specification set (start at `docs/README.md`). This is the
  primary, up-to-date spec, split into 01-overview / 02-architecture / 03-masking-spec /
  04-ui-and-operations / 05-known-issues-and-roadmap. It replaces the original single-file design
  spec. Section numbers referenced elsewhere in this file (e.g. "design doc §4") now live in these
  documents (§1–§2 in 01, §3–§5 in 02, §4.2–§4.8 in 03, §6–§9 in 04).
- `docs/superpowers/plans/2026-07-23-pii-masking-app.md` — the 20-task TDD implementation plan
  (historical record of how it was built)

**Resuming implementation work:** this project is being built task-by-task via the
`superpowers:subagent-driven-development` skill. Progress is tracked in
`.superpowers/sdd/progress.md` (git-ignored — read it directly, don't assume it's committed) and in
git history (`git log --oneline`). Check that ledger before re-dispatching any task — completed tasks
should not be redone. Each task's implementer/reviewer dispatch briefs were written to
`.superpowers/sdd/task-N-brief.md` / `task-N-report.md` (also git-ignored) and are safe to regenerate
via that skill's `scripts/task-brief` if missing.

## Commands

```bash
# Environment: Python 3.11 required (a later Python may be the system default — check with
# `python --version`; this project was set up using a pyenv-style installed 3.11.12, not the
# system Python). Venv already exists at .venv/ in this worktree.
.venv/Scripts/pip install -e ".[dev]"        # install/update deps (editable + dev extras)

.venv/Scripts/python -m pytest tests/ -v     # run the full suite
.venv/Scripts/python -m pytest tests/core/test_masker.py -v            # one file
.venv/Scripts/python -m pytest tests/core/test_masker.py::test_name -v # one test

# GUI entry point (once Task 17+ lands):
.venv/Scripts/python -m privacyprotection.gui.app

# PyInstaller build (Task 20):
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
composition points below. This isolation is deliberate (see design doc §4): the core layer is
GUI-free and fully unit-testable; the GUI is a thin wrapper that can't be unit tested the same way.

**`core/`** — detection, masking, and restoration, all pure functions/classes over plain strings.
No file I/O, no GUI, no network.
- Three independent detectors — `PatternDetector` (regex), `DictionaryDetector` (custom term list),
  `NerDetector` (GiNZA named-entity recognition, lazy-loaded) — each implement `.detect(text) ->
  list[Detection]` and are combined by `Detector`, which also resolves overlaps between them.
- **Overlap resolution policy** (`core/spans.py`'s `remaining_spans()`, shared by both `patterns.py`
  (resolving its own 6 regex categories against each other) and `detector.py` (resolving across the
  3 detector sources)): when a lower-priority detection partially overlaps a higher-priority one
  *without one fully containing the other*, the lower-priority one is **trimmed to its non-overlapping
  remainder, never discarded outright**. This was a deliberate fix during implementation — the
  original plan's reference code discarded on any overlap, which silently dropped real PII (e.g. an
  address immediately followed by a phone number with no separating space). If you touch overlap
  logic anywhere in this codebase, preserve trim-not-discard semantics; a reviewer will flag a
  regression to discard-on-overlap as a reintroduced privacy bug, not a style nit.
- `Masker` turns a resolved detection list into masked text plus a `MappingTable`, in one of two
  modes: reversible token substitution (`【<category label>_<n>】`, e.g. `【人名_1】`) or irreversible
  redaction (`●●●●`). One `Masker` instance must be reused across an entire batch (e.g. a whole
  folder) for the "same real-world value always gets the same token" guarantee to hold — see
  `Masker.scan_existing_tokens()` for how it avoids colliding with pre-existing token-shaped text in
  the input.
- `mapping_io.py` persists a `MappingTable` to/from a human-readable CSV (`.pmap.csv`, UTF-8 BOM,
  Japanese headers) — deliberately plain text, not encrypted (see design doc §1.1: the threat model
  is "don't let PII reach a cloud AI," not "protect data at rest on the user's own machine"). It
  rejects duplicate tokens on load (a real risk if a user manually merges two independently-generated
  per-file mapping CSVs) — don't relax that check.
- `Restorer` reverses tokens back to original text using **exact string match only**. It must never
  fuzzy-match or auto-correct a token an AI has garbled — an altered token is left in place and
  reported as unknown, not guessed at.

**`handlers/`** — one class per file format, all implementing the `FileHandler` ABC
(`read_fragments(path) -> list[Fragment]`, `write_fragments(src, dst, masked_fragments)`). A
`Fragment` is a location-tagged piece of text (a cell, a paragraph, a whole file, etc.) — handlers
are responsible for extracting exactly the fragments worth scanning for PII and writing back *only*
those fragments' text, leaving everything else (formatting, formulas, unrelated cells) untouched.
`TextHandler` preserves the source file's exact byte-level encoding and line endings on write — a
masked plain-text file must be byte-identical to the original except for the substituted spans.

**`services/`** *(not yet built — Tasks 14-15)* — will orchestrate handlers + core into whole-file
and whole-folder mask/restore operations, plus the post-processing report shown to the user (the
report never includes an actual detected PII value, only category names and counts — this is a hard
constraint from the design doc, not a preference).

**`gui/`** *(not yet built — Tasks 17-19)* — PySide6. Talks only to `services/`, never imports from
`core/` or `handlers/` directly.

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
