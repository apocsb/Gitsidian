# Gitsidian

Export a Git repository's commit history into Obsidian-friendly Markdown notes.

Gitsidian produces one Markdown file per commit, organized into per-branch folders inside your Obsidian vault. Each commit note includes YAML frontmatter (sha, author, date, tags), the commit message, parent links and optional diffstat/full diff. A branch `index.md` is generated to browse commits easily.

Why use Gitsidian?
- Keep a readable, linkable history of commits in Obsidian.
- Works incrementally: only new commits are written on subsequent syncs.
- Templates are configurable per-vault so you can match your Obsidian workflow.
- If it's not in Obsidian, has it even happened?

How it works
- The CLI calls `git` to list commits for the configured branches.
- For each commit it renders a Markdown note from a template and writes it to `<vault>/branches/<branch>/<note>.md`.
- A branch `index.md` is built by scanning the branch folder and listing commits as wiki-links.
- The tool records last-synced SHAs to do incremental updates.

Features
- Cross-platform Python CLI (Linux/macOS/Windows)
- One note per commit with YAML frontmatter
- Obsidian wiki-links between commits and branch index
- Optional diffstat and full diff inclusion
- Template overrides per vault (`.gitsidian/templates/`)
- Safe atomic writes and idempotent behavior

## Install

- Requires: Python 3.8+ and Git in PATH.
- Clone or download this folder; no extra dependencies required.

Recommended (safe) options

1) pipx (recommended for CLI tools)

```bash
# Install pipx via your distro (example for Arch-based systems):
sudo pacman -S python-pipx
python3 -m pipx ensurepath
# then restart your shell or source your profile

# Install the local project into a pipx-managed venv (editable):
pipx install --editable /home/user/Projects/Gitsidian
```

2) Development virtualenv (for development/contributing)

```bash
cd /home/user/Projects/Gitsidian
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# run the CLI while the venv is active:
gitsidian --help
```

User-local install (acceptable if you prefer not to use pipx/venv)

```bash
# installs into your user site-packages and places the script in ~/.local/bin
python3 -m pip install --user -e /home/user/Projects/Gitsidian
# make sure ~/.local/bin is on your PATH
```

Risks when modifying system Python

On some Linux distributions (for example Arch derivatives) the system Python is "externally managed" and the OS will refuse to let pip modify it unless you pass
`--break-system-packages`. That flag will allow uninstall/install but can break OS package tooling. Prefer pipx or a venv instead of using `--break-system-packages`.

If you choose to override the protection (risky):

```bash
python3 -m pip uninstall gitsidian --break-system-packages
python3 -m pip install -e /home/user/Projects/Gitsidian --break-system-packages
```

Notes about the `gitsidian` command

If the package is installed into a location on your PATH (system/site, pipx shims, or `~/.local/bin`) then the `gitsidian` command will be available in your shell. If you use a venv, activate it first or call the tool via the venv's `bin/gitsidian`.

## Usage

Run help:

If installed via pip/pipx/user-install:

```bash
gitsidian --help
```

If running from the source tree (no install):

```bash
python3 gitsidian.py --help
```

Core commands:
- `add` (wizard) — register a repo and vault target
- `list` — show saved repos
-- `sync --id <repoId>` or `sync <repoId>` — export notes incrementally
- `sync-all` — export for all saved repos
- `remove --id <repoId>` — remove from config (does not touch files)
- `doctor` — environment and config sanity check

Convenience: if you have a single configured repo, `gitsidian sync` with no arguments will sync that repo.

## Output structure

Vault folder:
```
<your-vault>/
  .gitsidian/
    cache.json
    templates/ (optional overrides)
  branches/
    <branch-name>/
      <sha>.md
      index.md
```

## Templates

Built-in templates are embedded; you can override by placing files in:
```
<your-vault>/.gitsidian/templates/
  commit.md
  branch-index.md
```

Placeholders available in templates:
- `{{title}}`, `{{sha}}`, `{{short}}`, `{{author}}`, `{{email}}`, `{{date}}`, `{{repo}}`, `{{branch}}`
- `{{parents_list}}` (markdown list of wiki-links)
- `{{body}}` (full commit message)
- `{{diffstat}}` (git show --stat)
- `{{diff}}` (full diff when enabled)

Tags: by default templates include separate `repo` and `branch` tags (for example `repo:formo` and `branch:main`) to avoid characters that are invalid in Obsidian tags.

## Config location

- Linux: `~/.config/gitsidian/config.json`
- macOS: `~/Library/Application Support/gitsidian/config.json`
- Windows: `%APPDATA%/gitsidian/config.json`

## Notes

- Idempotency: the tool is incremental and will skip writing notes that appear unchanged. However, notes will be updated when their rendered content changes (for example, when you change templates, enable diff capture, or backfill diffstat). The tool attempts safe atomic writes and will only rewrite files when needed.
- If a branch is rebased and your last synced commit disappears, the tool will fall back to a full scan for that branch.
- For large repos, you can set a maximum initial export limit during the wizard.

## License

MIT
