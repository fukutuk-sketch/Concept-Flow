# ConceptFlow for Blender

Blender for thinking about pictures, not just making 3D. / 3Dを作るためではなく、絵を考えるためのBlender。

A Blender add-on for concept artists: build a graybox space, try lighting moods in one click, swap in final assets, and keep every idea in a branching history.

> **Status: in development (v0.2.0).** Not released. For the full project background, rules, architecture, what is done and what is next, read [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) first.

## Features (v0.2)

- **Graybox** — walls, pillars, stairs, floors, doors, windows and blocks; one-click room/building generator with automatic openings.
- **Branch Tree** — save the scene state (camera, lights, world, visibility, color management, transforms) and branch from anywhere.
- **Look Presets** — HORROR / NOIR / MOONLIGHT / GOLDEN HOUR / NEON, non-destructive; reset brings everything back.
- **Asset Swap** — register assets per part type and swap the whole graybox for them in one click; switch Graybox / Final any time.
- English UI with a Japanese translation.

Planned for v0.3: Auto Variations, Compare & Apply, Read Views.

## Install (development)

1. Copy the `conceptflow/` folder into Blender's add-ons folder, for example on Windows:
   `%APPDATA%\Blender Foundation\Blender\4.5\scripts\addons\conceptflow\`
2. Enable **ConceptFlow** in Preferences > Add-ons.
3. In the 3D Viewport press `N` and open the **ConceptFlow** tab.

Developed and tested on Blender 4.5.4 LTS (EEVEE Next).

## Repository layout

```
conceptflow/   the add-on package
tools/         development helpers (not part of the add-on)
PROJECT_CONTEXT.md   project background, rules, status, roadmap
```
