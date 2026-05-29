# Project Instructions

This repository contains a Python console application. Use `uv` for environment management, dependency installation, and running commands.

## Workflow

- Use `uv sync` to create or update the local environment from the project lockfile.
- Use `uv run` for all Python commands, tests, and scripts.
- Prefer `uv add <package>` and `uv remove <package>` for dependency changes.
- Keep changes small and focused. Avoid unrelated refactors.

## Console App Rules

- Build the application as a CLI-style console program, not a GUI.
- Keep the entry point simple and readable.
- Put reusable logic in functions or modules instead of the main script.
- Handle invalid input gracefully and print clear error messages.

## Rich

- Use `rich` for terminal output, status messages, tables, panels, and error formatting.
- Keep output useful and compact. Do not overuse styling.
- Prefer `Console()` and `print()` from `rich` instead of raw `print()` when formatting matters.

## PyProfibus

- Use `pyprofibus` for PROFIBUS communication and device interactions.
- Keep protocol-specific details isolated from the CLI layer.
- Add explicit error handling around bus connection, device access, and timeouts.
- Document assumptions about bus address, adapters, and target hardware in code or README updates when needed.

## Code Style

- Write idiomatic Python with clear names and type hints where helpful.
- Prefer standard library modules first, then add dependencies only when they provide clear value.
- Avoid hidden side effects in imports.
- Keep functions short and testable.
- **Always use `from X import Y` form for every import — never bare `import X`.** This applies to the standard library (`from sys import exit`), third-party packages (`from click import group, option`), and submodules (`from serial.tools import list_ports`). No exceptions.
- Prefer use of pathlib.

## Validation

- Run the app with `uv run` after changes to confirm behavior.
- Add or update tests for behavior changes when practical.
- If hardware access is required and unavailable, validate with the closest deterministic checks and note the limitation.
