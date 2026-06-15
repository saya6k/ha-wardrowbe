---
name: conventional-commit
description: Write and validate Conventional Commit messages for this HACS integration so release-please computes the next semantic version and CHANGELOG. Use whenever committing — a non-conforming message produces no release.
---

# Conventional commit (release-please-aware)

release-please parses every commit on `main` to compute the next **semantic
version** (bumped in `custom_components/<domain>/manifest.json`) and the
CHANGELOG. A message that does not parse is ignored — no version, no CHANGELOG.

## Format

```
<type>(<scope>): <subject>
```

- **scope is optional.** This is a single integration, so scope does not route
  anything — use it only to name the area touched: `config-flow`, `sensor`,
  `coordinator`, `api`, `docs`, `ci`, `deps`.

## Types → release effect

| Type | Effect |
|---|---|
| `feat` | minor bump · "Features" |
| `fix` / `perf` / `revert` | patch bump |
| `docs` / `refactor` / `build` / `ci` | patch bump · in CHANGELOG |
| `chore` / `test` / `style` | no release (hidden) |
| `<type>!` or `BREAKING CHANGE:` footer | major bump |

## Rules

- Imperative subject, ≤ ~72 chars, no trailing period.
- **Never** `--no-verify` / `--no-gpg-sign`; if a hook fails, fix the cause.
- **Don't hand-edit** `manifest.json` `version` in a feature commit —
  release-please owns it.

## Output

- The proposed commit message in a code block.
