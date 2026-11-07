"""Core analysis logic for py-lean."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import tomlkit

from py_lean_package.models import (
    AnalysisResult,
    Issue,
    IssueType,
    LeanConfig,
    _is_readme_pattern,
)


def read_gitignore(project_root: Path) -> list[str]:
    """Parse a local `.gitignore` file and return normalized ignore patterns.

    Returns an empty list if the file does not exist or cannot be read.
    Skips blank lines, comments (lines starting with `#`), and negation patterns (starting with `!`).
    Normalizes patterns by stripping leading `/` (which anchors to the repo root) and trailing `/`
    (which indicates a directory-only match).
    """
    gitignore_path = project_root / ".gitignore"
    try:
        content = gitignore_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []

    patterns: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        # Skip empty lines, comments, and negation patterns
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Normalize: strip leading `/` (root anchor) and trailing `/` (directory indicator)
        line = line.removeprefix("/").removesuffix("/")
        if line:
            patterns.append(line)
    return patterns


def read_global_gitignore() -> list[str]:
    """Read the global gitignore file configured via `core.excludesFile`.

    Returns an empty list if the config is not set or the file cannot be read.
    Uses the same normalization as `read_gitignore`.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--get", "core.excludesFile"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        excludes_file = result.stdout.strip()
        if not excludes_file:
            return []

        content = Path(excludes_file).expanduser().read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return []

    patterns: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        line = line.removeprefix("/").removesuffix("/")
        if line:
            patterns.append(line)
    return patterns


def is_gitignored(pattern: str, gitignore_entries: list[str]) -> bool:
    """Check if a pattern is already covered by a .gitignore entry.

    Uses simple string matching and basic glob matching.
    """
    pattern = pattern.rstrip("/")
    for entry in gitignore_entries:
        # Direct match
        if entry == pattern:
            return True
        # Glob-like match
        if fnmatch.fnmatch(pattern, entry):
            return True
    return False


def load_manifest(manifest_path: Path) -> tomlkit.TOMLDocument:
    """Load and parse a pyproject.toml file."""
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")
    content = manifest_path.read_text(encoding="utf-8")
    return tomlkit.parse(content)


def check_build_system(manifest: tomlkit.TOMLDocument) -> bool:
    """Verify the project uses uv_build as its build backend.

    Checks for [build-system] with `requires` containing `uv_build` and
    `build-backend` set to `uv_build`.

    Returns:
        True if the build system is correctly configured for uv_build.
    """
    build_system = manifest.get("build-system", {}) or {}
    requires = build_system.get("requires", []) or []
    backend = build_system.get("build-backend", "")

    has_uv_build_requires = any("uv_build" in str(r) for r in requires)
    has_uv_build_backend = str(backend) == "uv_build"

    return has_uv_build_requires and has_uv_build_backend


def _normalize_pattern(pattern: str) -> str:
    """Normalize a pattern by stripping trailing slashes.

    Ensures that `tests/` and `tests` are treated identically throughout the codebase.
    """
    return pattern.rstrip("/")


def load_config(
    manifest: tomlkit.TOMLDocument | None = None,
    patterns: list[str] | None = None,
) -> LeanConfig:
    """Load py-lean configuration with the following precedence:

    1. CLI --patterns (highest priority — completely replaces other sources)
    2. tool.uv.build-backend.source-exclude from pyproject.toml
    3. DEFAULT_SOURCE_EXCLUDE_PATTERNS (lowest priority — used when nothing is configured)

    Args:
        manifest: Parsed pyproject.toml document to read source-exclude from.
        patterns: CLI-provided patterns that override all other sources.

    Returns:
        LeanConfig with patterns set according to the precedence rules above.
    """
    config = LeanConfig()

    # If CLI patterns are provided, they take full precedence
    if patterns is not None:
        config.patterns = [_normalize_pattern(p) for p in patterns]
        return config

    # Otherwise, try to load from tool.uv.build-backend.source-exclude
    if manifest is not None:
        source_exclude = _get_source_exclude_patterns(manifest)
        if source_exclude:
            config.patterns = [_normalize_pattern(p) for p in source_exclude]

    # If neither CLI nor source-exclude provides patterns, effective_patterns()
    # will fall back to DEFAULT_SOURCE_EXCLUDE_PATTERNS
    return config


def _get_source_exclude_patterns(manifest: tomlkit.TOMLDocument) -> list[str]:
    """Extract source-exclude patterns from tool.uv.build-backend.source-exclude."""
    patterns: list[str] = []
    tool = manifest.get("tool", {})
    if tool is None:
        return patterns
    uv = tool.get("uv", {})
    if uv is None:
        return patterns
    build_backend = uv.get("build-backend", {})
    if build_backend is None:
        return patterns
    source_exclude = build_backend.get("source-exclude", [])
    if source_exclude is not None:
        patterns = [str(p) for p in source_exclude]
    return patterns


def _pattern_covers(pattern: str, exclude_pattern: str) -> bool:
    """Check if a source-exclude pattern covers a given artifact pattern.

    Both patterns are normalized (trailing slashes stripped) before comparison.
    Patterns are treated as glob patterns. The exclude_pattern must match
    the artifact pattern or be a parent directory of it.
    """
    pattern = _normalize_pattern(pattern)
    exclude_pattern = _normalize_pattern(exclude_pattern)

    # Direct match
    if fnmatch.fnmatch(pattern, exclude_pattern):
        return True

    # Check if the exclude pattern is a parent directory
    # e.g., exclude "tests" covers "tests/**"
    if pattern.startswith(exclude_pattern + "/"):
        return True

    # Check if the pattern's top-level component matches
    pattern_top = pattern.split("/")[0]
    exclude_top = exclude_pattern.split("/")[0]
    return bool(fnmatch.fnmatch(pattern_top, exclude_top))


def detect_missing_source_exclude(
    manifest: tomlkit.TOMLDocument,
    project_root: Path,
    config: LeanConfig,
    gitignore_entries: list[str] | None = None,
) -> list[Issue]:
    """Detect common repository artefacts that exist but are not in source-exclude.

    Checks if artefacts listed in the config's patterns (or defaults) exist in the
    project root but are not covered by tool.uv.build-backend.source-exclude.
    Artefacts already covered by .gitignore entries are skipped.
    """
    if gitignore_entries is None:
        gitignore_entries = []

    issues: list[Issue] = []
    exclude_patterns = _get_source_exclude_patterns(manifest)
    patterns_to_check = config.effective_patterns()

    for pattern in patterns_to_check:
        # Check if this pattern exists in the project root
        if not _artifact_exists(project_root, pattern):
            continue

        # Skip if already covered by .gitignore
        if is_gitignored(pattern, gitignore_entries):
            continue

        # Check if it's covered by any source-exclude pattern
        covered = any(_pattern_covers(pattern, ep) for ep in exclude_patterns)
        if not covered:
            issues.append(
                Issue(
                    issue_type=IssueType.MISSING_SOURCE_EXCLUDE,
                    subject=pattern,
                    description=(
                        f"Artefact '{pattern}' exists in the project root but is not listed in "
                        f"tool.uv.build-backend.source-exclude."
                    ),
                    location="pyproject.toml",
                    suggestion=f"Add '{pattern}' to tool.uv.build-backend.source-exclude.",
                )
            )

    return issues


def _artifact_exists(project_root: Path, pattern: str) -> bool:
    """Check if an artifact matching the pattern exists in the project root.

    Trailing slashes are stripped before checking, so `tests/` and `tests` are equivalent.

    On case-insensitive filesystems (e.g., macOS default), `Path.exists()` returns
    `True` for both `CHANGELOG.md` and `changelog.md` when only one exists.
    This function reads the actual directory entries to verify the filename matches
    the pattern exactly, preventing duplicate entries like both `CHANGELOG.md` and
    `changelog.md` from being added to the exclude list.
    """
    pattern = _normalize_pattern(pattern)

    # Handle glob patterns like *.egg-info, *.pyc, *.pyo
    if "*" in pattern:
        matches = list(project_root.glob(pattern))
        return len(matches) > 0

    # Handle directory/file patterns — use case-sensitive check
    return _path_exists_exact(project_root, pattern)


def _path_exists_exact(base: Path, pattern: str) -> bool:
    """Check if a path exists with an exact (case-sensitive) name match.

    On case-insensitive filesystems, `Path.exists()` will match case variants.
    This function reads directory entries to verify the exact filename.
    """
    # Quick check: if the path doesn't exist at all (even case-insensitively), skip.
    path = base / pattern
    if not path.exists():
        return False

    # Split the pattern into parent directory and name
    if "/" in pattern:
        parent_rel, name = pattern.rsplit("/", 1)
        search_dir = base / parent_rel
    else:
        search_dir = base
        name = pattern

    # Read directory entries and check for exact name match
    try:
        for entry in search_dir.iterdir():
            if entry.name == name:
                return True
    except OSError:
        pass

    return False


def detect_toml_configs(
    manifest: tomlkit.TOMLDocument,
    project_root: Path,
    gitignore_entries: list[str] | None = None,
) -> list[Issue]:
    """Detect TOML configuration files at the project root that are not in source-exclude.

    Scans for `*.toml` files in the project root, excludes `pyproject.toml`,
    and checks if each found file is covered by source-exclude patterns.
    Files already covered by .gitignore entries are skipped.
    """
    if gitignore_entries is None:
        gitignore_entries = []

    issues: list[Issue] = []
    exclude_patterns = _get_source_exclude_patterns(manifest)

    # Find all .toml files at the project root (not in subdirectories)
    toml_files = sorted(project_root.glob("*.toml"))

    for toml_file in toml_files:
        # Skip pyproject.toml — it's the main project file
        if toml_file.name == "pyproject.toml":
            continue

        # Skip if not a file (e.g., directory)
        if not toml_file.is_file():
            continue

        filename = toml_file.name

        # Skip if already covered by .gitignore
        if is_gitignored(filename, gitignore_entries):
            continue

        # Check if it's covered by any source-exclude pattern
        covered = any(_pattern_covers(filename, ep) for ep in exclude_patterns)
        if not covered:
            issues.append(
                Issue(
                    issue_type=IssueType.MISSING_SOURCE_EXCLUDE,
                    subject=filename,
                    description=(
                        f"TOML config file '{filename}' exists in the project root but is not "
                        f"listed in tool.uv.build-backend.source-exclude."
                    ),
                    location="pyproject.toml",
                    suggestion=f"Add '{filename}' to tool.uv.build-backend.source-exclude.",
                )
            )

    return issues


def detect_readme_in_source_exclude(
    manifest: tomlkit.TOMLDocument,
) -> list[Issue]:
    """Detect if README.md is listed in source-exclude (should use project.readme instead).

    README.md belongs in project.readme, not in source-exclude.
    """
    issues: list[Issue] = []
    exclude_patterns = _get_source_exclude_patterns(manifest)

    for pattern in exclude_patterns:
        if _is_readme_pattern(pattern):
            issues.append(
                Issue(
                    issue_type=IssueType.README_IN_SOURCE_EXCLUDE,
                    subject=pattern,
                    description=(
                        f"README file '{pattern}' is listed in "
                        f"tool.uv.build-backend.source-exclude, but should be specified via "
                        f"project.readme instead."
                    ),
                    location="pyproject.toml",
                    suggestion="Remove README.md from source-exclude and set project.readme = \"README.md\".",
                )
            )

    return issues


def _get_project_readme(manifest: tomlkit.TOMLDocument) -> str | None:
    """Extract the project.readme value from the manifest.

    project.readme can be:
    - A string: readme = "README.md"
    - A table: readme = {file = "README.md"}
    - A list of tables (for multiple locales)

    Returns the readme filename if set, None otherwise.
    """
    project = manifest.get("project", {})
    if project is None:
        return None
    readme = project.get("readme")
    if readme is None:
        return None

    # String form: readme = "README.md"
    if isinstance(readme, str):
        return readme

    # Table form: readme = {file = "README.md"}
    if hasattr(readme, "get"):
        return readme.get("file")

    # List form (multiple locales): readme = [{file = "README.md"}, ...]
    if isinstance(readme, list) and len(readme) > 0:
        first = readme[0]
        if hasattr(first, "get"):
            return first.get("file")
        if isinstance(first, str):
            return first

    return None


def detect_missing_project_readme(
    manifest: tomlkit.TOMLDocument,
    project_root: Path,
) -> list[Issue]:
    """Detect if README.md exists but project.readme is not set.

    When README.md is present in the repository, project.readme should be set
    so that packaging tools include it properly in the distribution.
    """
    issues: list[Issue] = []

    # Check if README.md exists (case-insensitive check using directory listing)
    readme_exists = False
    readme_filename = None
    try:
        for entry in project_root.iterdir():
            if entry.name.lower() == "readme.md" and entry.is_file():
                readme_exists = True
                readme_filename = entry.name
                break
    except OSError:
        pass

    if not readme_exists:
        return issues

    # Check if project.readme is set
    project_readme = _get_project_readme(manifest)
    if project_readme is None:
        issues.append(
            Issue(
                issue_type=IssueType.MISSING_PROJECT_README,
                subject=readme_filename or "README.md",
                description=(
                    f"README file '{readme_filename}' exists in the repository, but "
                    f"project.readme is not set in pyproject.toml."
                ),
                location="pyproject.toml",
                suggestion=f'Set project.readme = "{readme_filename}" in pyproject.toml.',
            )
        )

    return issues


def analyze(
    manifest_path: Path,
    patterns: list[str] | None = None,
) -> AnalysisResult:
    """Run full analysis on a project.

    Checks for missing source-exclude patterns for common repository artefacts,
    TOML configuration files, and README.md handling.
    Artefacts already covered by .gitignore (local or global) are skipped.

    Args:
        manifest_path: Path to the pyproject.toml file.
        patterns: Optional list of artefact patterns to check for source-exclude.

    Returns:
        AnalysisResult containing all detected issues.
    """
    project_root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    config = load_config(manifest=manifest, patterns=patterns)

    # Collect gitignore entries from local .gitignore and global excludesFile
    gitignore_entries: list[str] = []
    gitignore_entries.extend(read_gitignore(project_root))
    gitignore_entries.extend(read_global_gitignore())

    issues: list[Issue] = []
    issues.extend(detect_missing_source_exclude(manifest, project_root, config, gitignore_entries))
    issues.extend(detect_toml_configs(manifest, project_root, gitignore_entries))
    issues.extend(detect_readme_in_source_exclude(manifest))
    issues.extend(detect_missing_project_readme(manifest, project_root))

    return AnalysisResult(manifest_path=manifest_path, issues=issues)
