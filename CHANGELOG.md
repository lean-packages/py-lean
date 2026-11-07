# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `README.md` added to default exclude patterns: The lint command now checks that `README.md`
  is not listed in `source-exclude` (it should be specified via `project.readme` instead)
  and reports an error if it is. Additionally, the lint command verifies that `project.readme`
  is set when `README.md` exists in the repository.

### Fixed

- `create` and `update` commands never add `README.md` to `source-exclude`: README files belong
  in `project.readme`, not `source-exclude`. The `update` command will also remove any existing
  `README.md` entry from `source-exclude`.

## [0.2.4] - 2026-11-06

### Fixed

- On case-insensitive filesystems (e.g., macOS default), artifact existence checks now verify exact
  filename matches, preventing duplicate entries like both `CHANGELOG.md` and `changelog.md` from being
  added to the source-exclude list.

## [0.2.3] - 2026-10-31

### Fixed

- The success message for `py-lean lint` is now aligned with `cargo-lean`'s output.

## [0.2.2] - 2026-10-31

### Fixed

- TOML configuration files at the project root (e.g. `.ruff.toml` or `mypy.toml`) are now detected as source-exclude
candidates. `pyproject.toml` is always excluded from this check.

## [0.2.1] - 2026-10-30

### Fixed

- `check_build_system` now only accepts `uv_build` as a valid build backend — the invalid `uv_build.build` variant has
been removed, as it is not a valid uv convention.

## [0.2.0] - 2026-10-30

### Changed

- `--patterns` option now accepts a single space-separated string (e.g., `-p "tests/ benchmarks/ docs/"`) instead of
requiring repeated flags.

### Fixed

- Patterns are now normalized by stripping trailing slashes, so `tests/` and `tests` are treated identically
across all commands and configuration sources.

## [0.1.1] - 2026-10-29

### Changed

- Rename package `py-lean` to `py-lean-package`.

## [0.1.0] - 2026-10-29

### Added

- Initial release of `py-lean-package`.
- `lint` command: analyze projects for missing `source-exclude` patterns.
- `create` command: add missing `source-exclude` patterns to `pyproject.toml`.
- `update` command: automatically add missing `source-exclude` patterns to `pyproject.toml`.
- Support for `--format json` output.
- Support for `--dry-run` and `--diff` flags.
- Customizable artefact patterns via `--patterns` CLI option.
- Default patterns for common directories (`tests/`, `.venv/`, `__pycache__/`, etc.).
- Gitignore awareness: artefacts covered by `.gitignore` are skipped.
