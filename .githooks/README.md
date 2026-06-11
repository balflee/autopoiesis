# Git hooks — secret/PII guard

A `pre-commit` hook runs [gitleaks](https://github.com/gitleaks/gitleaks) over
your **staged** changes and **refuses the commit** if it finds a secret or a
personal email. This is the active line of defence; `.gitignore` is the passive
one (it only stops accidental `git add` of ignored paths — the hook also catches
a secret pasted into an otherwise-tracked source file, and a forced `git add -f`).

## Enable it (one-time, per clone)

Git hooks are **not** transmitted by `git clone`, so each working copy enables them once:

```bash
# 1) install gitleaks  (any one of these)
winget install gitleaks
#   or: scoop install gitleaks
#   or download a release binary: https://github.com/gitleaks/gitleaks/releases

# 2) point git at this hooks directory
git config core.hooksPath .githooks
```

That's it. From then on every `git commit` is scanned. Config lives in
`.gitleaks.toml` (repo root).

Prefer the [pre-commit framework](https://pre-commit.com)? `pip install pre-commit
&& pre-commit install` instead — it reads `.pre-commit-config.yaml`, which calls
your locally-installed gitleaks (no Go toolchain needed).

## When it blocks you

- **Real secret/PII** → remove it from the staged files. If it was already
  committed, rotate the credential — rewriting history alone does not un-leak it.
- **False positive** → add the path or value to the `[allowlist]` in
  `.gitleaks.toml`.
- **Genuinely need to bypass once** (discouraged) → `git commit --no-verify`.
