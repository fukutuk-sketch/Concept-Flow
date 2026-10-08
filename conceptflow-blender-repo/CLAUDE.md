# CLAUDE.md

This repository is **ConceptFlow for Blender**, a Blender add-on that is planned for sale.

**Before doing anything, read `PROJECT_CONTEXT.md` completely.** It holds the project background, the rules that must always be followed, the architecture, what is implemented, what is not, and the plan for v0.3.

Short reminders (details are in `PROJECT_CONTEXT.md`):

- The owner is a concept artist, not a programmer. Talk to him in Japanese, keep reports short.
- The add-on must stand alone: never import the owner's personal "Concept series" add-ons, and use the `CF_` prefix for all new data.
- UI is English with a Japanese translation in `conceptflow/translations.py`.
- Before committing: `python3 -m py_compile conceptflow/*.py` and `python3 tools/i18n_check.py conceptflow`.
- Cloud sessions cannot run Blender. Say clearly which changes still need a check in real Blender.
- After finishing work, update `PROJECT_CONTEXT.md`.
