---
name: ship-pr
description: Drive this HACS integration's commit → push → PR → merge flow so release-please versions every change. Use when shipping a change as a pull request — it enforces branch-first, Conventional-Commit titles, and the right merge method.
---

# Ship a change (commit → push → PR → merge)

release-please parses commits on `main` to cut semantic-version releases. A
malformed commit/PR title means the change never reaches a release. Defer to
[[conventional-commit]] for wording and [[hacs-preflight]] for sanity checks.

## 1. Branch

- **Never commit on `main`.** `git switch -c <type>/<short-slug>`
  (e.g. `feat/add-rain-sensor`, `fix/auth-retry`).

## 2. Pre-PR checks

- Run [[hacs-preflight]] (Python compile, JSON, manifest keys, i18n parity,
  brand asset). CI repeats HACS + hassfest but does not replace local checks.

## 3. Commit

- Use [[conventional-commit]]. **Never** `--no-verify` / `--no-gpg-sign`.
- Don't hand-bump `manifest.json` `version` — release-please owns it.

## 4. Push & open PR

- `git push -u origin <branch>`.
- `gh pr create` — the **PR title MUST be a valid Conventional Commit**. On a
  squash merge the title becomes the commit on `main` that release-please parses.

## 5. Merge

- This is a **single package**, so a **squash merge is fine** — the PR title is
  the one release-relevant commit. (No multi-scope rebase concern as in
  `ha-apps`.)
- After merge, release-please opens (or updates) a `chore: release <ver>` PR
  whose diff is the auto-bumped `manifest.json` + `CHANGELOG.md` — review, don't
  edit.

## 6. After merge

- `git switch main && git pull --ff-only origin main`.
- **Merging the release PR** tags `v<ver>`, writes the CHANGELOG, and bumps
  `manifest.json` — then `git pull --ff-only` again to sync. HACS surfaces the
  new GitHub release to users.

## IMPORTANT

- The diff of a `chore: release <ver>` PR is auto-generated — review, don't edit.
- Set the repo's **Pages source to "GitHub Actions"** once, so the Docs
  workflow can publish the Zensical site.
