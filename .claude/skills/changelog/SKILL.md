---
name: changelog
description: Generate or update CHANGELOG.md based on git history and semantic versioning
disable-model-invocation: true
---

# Changelog Skill

Update the project's `CHANGELOG.md` following these rules.

## Process

1. Read the current `CHANGELOG.md` (create it if it doesn't exist).
2. Read `pyproject.toml` to get the current version.
3. Run `git log --oneline` from the last tagged release (or all history if no tags) to understand what changed.
4. Read the actual diffs with `git diff <last-tag>..HEAD` to understand the substance of changes.
5. Update the `[Unreleased]` section, or move `[Unreleased]` entries into a new versioned section if a version bump has occurred.

## Writing Rules

- **Write for humans.** This is NOT a dump of git commits. Describe *why* things changed and what impact it has.
- **Omit noise.** Skip trivial changes like typo fixes, formatting, internal refactors, or CI tweaks unless they significantly affect users.
- **Use imperative mood.** Start entries with action verbs: "Add", "Remove", "Fix", "Change".
- **Mark breaking changes.** Prefix with `**Breaking:**` and place at the top of the category.
- **Link to context.** Include PR or issue numbers in parentheses at the end of entries where available.
- **Keep it skimmable.** One line per change. No paragraphs.

## Format

Use [Keep a Changelog](https://keepachangelog.com/) format with reverse chronological order:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- New feature description

### Changed
- Change to existing functionality

### Deprecated
- Feature that will be removed

### Removed
- Feature that was taken out

### Fixed
- Bug fix description

### Security
- Vulnerability fix description

## [0.1.0] - 2025-01-15

### Added
- Initial release features
```

## Categories (use only those that apply)

- **Added** — new features
- **Changed** — changes to existing functionality
- **Deprecated** — soon-to-be-removed features
- **Removed** — features that have been taken out
- **Fixed** — bug fixes
- **Security** — vulnerability fixes

## Versioning

- Follow SemVer: `Major.Minor.Patch`
- Date every release using ISO 8601: `YYYY-MM-DD`
- The `[Unreleased]` section always sits at the top as a staging area
