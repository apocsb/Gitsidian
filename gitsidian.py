#!/usr/bin/env python3
"""Gitsidian: Export git commit history to Obsidian-friendly Markdown notes.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import textwrap
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Iterable, Tuple
import subprocess

APP_NAME = "gitsidian"
CONFIG_VERSION = 1

# ---------------------------------------------------------------------------
# Platform-specific config dir
# ---------------------------------------------------------------------------

def user_config_dir() -> Path:
    if sys.platform.startswith("linux") or sys.platform.startswith("freebsd"):
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        return Path(base) / APP_NAME
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    else:  # Windows or others
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(base) / APP_NAME

CONFIG_PATH = user_config_dir() / "config.json"

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class RepoOptions:
    includeMerges: bool = False
    includeDiff: bool = False
    includeDiffStat: bool = True
    fileNameStyle: str = "sha"  # sha | date-sha | short-sha
    maxInitialCommitsPerBranch: Optional[int] = None
    skipBinaryDiff: bool = True

@dataclass
class RepoConfig:
    id: str
    name: str
    repoPath: str
    vaultPath: str
    branches: List[str]
    options: RepoOptions
    lastSync: Dict[str, Optional[str]]
    createdAt: str
    updatedAt: str

@dataclass
class AppConfig:
    version: int
    repos: List[RepoConfig]

# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    pass

def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig(version=CONFIG_VERSION, repos=[])
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in config: {e}")
    if raw.get("version") != CONFIG_VERSION:
        # For now just warn
        print(f"[warn] Config version mismatch (found {raw.get('version')}, expected {CONFIG_VERSION})", file=sys.stderr)
    repos: List[RepoConfig] = []
    for r in raw.get("repos", []):
        options_dict = r.get("options", {})
        repo_cfg = RepoConfig(
            id=r["id"],
            name=r.get("name", r["id"]),
            repoPath=r["repoPath"],
            vaultPath=r["vaultPath"],
            branches=r.get("branches", []),
            options=RepoOptions(**options_dict),
            lastSync=r.get("lastSync", {}),
            createdAt=r.get("createdAt", datetime.now(timezone.utc).isoformat()),
            updatedAt=r.get("updatedAt", datetime.now(timezone.utc).isoformat()),
        )
        repos.append(repo_cfg)
    return AppConfig(version=raw.get("version", CONFIG_VERSION), repos=repos)

def save_config(cfg: AppConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    serializable = {
        "version": cfg.version,
        "repos": [
            {
                "id": r.id,
                "name": r.name,
                "repoPath": r.repoPath,
                "vaultPath": r.vaultPath,
                "branches": r.branches,
                "options": asdict(r.options),
                "lastSync": r.lastSync,
                "createdAt": r.createdAt,
                "updatedAt": r.updatedAt,
            }
            for r in cfg.repos
        ],
    }
    CONFIG_PATH.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def eprint(*a, **k):
    print(*a, **k, file=sys.stderr)

def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def run_git(repo_path: Path, args: List[str], check: bool = True) -> str:
    """Run a git command in repo_path and return stdout text."""
    proc = subprocess.run(["git", "-C", str(repo_path)] + args, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.stdout.decode("utf-8", errors="replace")

def ensure_git_repo(path: Path) -> bool:
    try:
        out = run_git(path, ["rev-parse", "--is-inside-work-tree"]).strip()
        return out == "true"
    except subprocess.CalledProcessError:
        return False

def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def sanitize_filename(name: str) -> str:
    """Return a filesystem- and Obsidian-safe filename by removing control chars
    and collapsing whitespace. Keeps the extension if present.
    """
    import re
    if not name:
        return name
    # preserve extension
    ext = ''
    if '.' in name:
        parts = name.rsplit('.', 1)
        base, ext = parts[0], '.' + parts[1]
    else:
        base = name
    # remove bidi/zero-width and control characters
    base = re.sub(r'[\u200B-\u200D\uFEFF]', '', base)
    base = re.sub(r'[\x00-\x1F\x7F]', '', base)
    # replace any remaining whitespace with a single dash
    base = re.sub(r'\s+', '-', base)
    # remove any path separators or pipes
    base = base.replace('/', '-').replace('\\', '-').replace('|', '-')
    # collapse multiple dashes
    base = re.sub(r'-{2,}', '-', base)
    base = base.strip('-')
    return base + ext

# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def run_add_wizard(cfg: AppConfig, args: argparse.Namespace) -> int:
    print("Add new repository configuration")
    repo_path = input("Repository path (existing git repo): ").strip()
    if not repo_path:
        eprint("Repository path required")
        return 1
    p = Path(repo_path).expanduser().resolve()
    if not p.exists():
        eprint("Path does not exist")
        return 1
    # Validate git repo
    if not ensure_git_repo(p):
        eprint("Not a git repository")
        return 1
    name = input("Display name (blank => folder name): ").strip() or p.name
    vault_path = input("Vault (output) path: ").strip()
    if not vault_path:
        eprint("Vault path required")
        return 1
    vp = Path(vault_path).expanduser().resolve()
    vp.mkdir(parents=True, exist_ok=True)

    # Branch strategy placeholder: empty => all branches
    branches_raw = input("Branches (comma separated, blank => all local branches): ").strip()
    branches = [b.strip() for b in branches_raw.split(',') if b.strip()] if branches_raw else []

    include_diffstat = yes_no("Include diffstat? [Y/n] ", default=True)
    include_diff = yes_no("Include full diff? (larger notes) [y/N] ", default=False)
    include_merges = yes_no("Include merge commits? [y/N] ", default=False)

    file_name_style = choose_option(
        "Filename style",
        ["sha", "date-sha", "short-sha"],
        default="sha",
    )

    max_initial = input("Limit initial commits per branch (blank => no limit): ").strip()
    max_initial_int = int(max_initial) if max_initial.isdigit() else None

    repo_id = slugify(name)
    if any(r.id == repo_id for r in cfg.repos):
        suffix = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        repo_id = f"{repo_id}-{suffix}"

    now = datetime.now(timezone.utc).isoformat()
    new_repo = RepoConfig(
        id=repo_id,
        name=name,
        repoPath=str(p),
        vaultPath=str(vp),
        branches=branches,
        options=RepoOptions(
            includeMerges=include_merges,
            includeDiff=include_diff,
            includeDiffStat=include_diffstat,
            fileNameStyle=file_name_style,
            maxInitialCommitsPerBranch=max_initial_int,
        ),
        lastSync={},
        createdAt=now,
        updatedAt=now,
    )
    cfg.repos.append(new_repo)
    save_config(cfg)
    print(f"Added repo '{name}' (id={repo_id}).")
    return 0

# ---------------------------------------------------------------------------
# Helper prompt utilities
# ---------------------------------------------------------------------------

def yes_no(prompt: str, default: bool) -> bool:
    resp = input(prompt).strip().lower()
    if not resp:
        return default
    return resp in ("y", "yes", "true", "1")

def choose_option(title: str, options: List[str], default: str) -> str:
    print(f"{title}:")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}{' (default)' if opt == default else ''}")
    resp = input("Choose number (blank => default): ").strip()
    if resp.isdigit():
        idx = int(resp) - 1
        if 0 <= idx < len(options):
            return options[idx]
    return default

def slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (' ', '_', '-', '.'):  # unify to '-'
            out.append('-')
    slug = ''.join(out)
    while '--' in slug:
        slug = slug.replace('--', '-')
    return slug.strip('-') or 'repo'

# ---------------------------------------------------------------------------
# Git operations and templating
# ---------------------------------------------------------------------------

FIELD_SEP = "\x1f"  # unit separator between fields
REC_SEP = "\x1e"    # record separator between commits

def list_local_branches(repo_path: Path) -> List[str]:
    out = run_git(repo_path, [
        "for-each-ref", "--format=%(refname:short)", "refs/heads"
    ])
    branches = [line.strip() for line in out.splitlines() if line.strip()]
    return branches

def iter_commits(repo_path: Path, branch: str, since_sha: Optional[str], include_merges: bool,
                 limit: Optional[int]) -> Iterable[Dict[str, Any]]:
    """Yield commits after since_sha (exclusive) on branch in chronological order."""
    # Compose pretty format with record separators
    fmt = "%H%x1f%h%x1f%an%x1f%ae%x1f%ai%x1f%s%x1f%B%x1f%P%x1e"
    base_args = ["log", branch, f"--pretty=format:{fmt}", "--reverse"]
    if not include_merges:
        base_args.append("--no-merges")
    if since_sha:
        # sanitize any stray whitespace/newlines from stored SHAs
        since_sha = since_sha.strip()
        if not since_sha:
            since_sha = None

    if since_sha:
        # ancestry-path keeps only commits reachable from branch through ancestry
        # Note: if since_sha isn't an ancestor (rebased), we may get zero results.
        base_args.insert(1, f"{since_sha}..{branch}")  # results after since_sha
        # Remove duplicate branch name (now appears twice)
        base_args.pop(2)
    if limit:
        base_args.extend(["-n", str(limit)])

    try:
        out = run_git(repo_path, base_args)
        records = [r for r in out.split(REC_SEP) if r.strip()]
        if not records and since_sha:
            # Fallback: full history if since_sha not in ancestry
            out = run_git(repo_path, ["log", branch, f"--pretty=format:{fmt}", "--reverse"] + (["--no-merges"] if not include_merges else []))
            records = [r for r in out.split(REC_SEP) if r.strip()]
        for rec in records:
            fields = rec.split(FIELD_SEP)
            if len(fields) < 8:
                continue
            full, short, an, ae, ai, subject, body, parents = fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6], fields[7]
            parents_list = [p for p in parents.strip().split() if p]
            yield {
                "sha": full,
                "short": short,
                "author": an,
                "email": ae,
                "date": ai,
                "subject": subject.strip(),
                "body": body.rstrip(),
                "parents": parents_list,
            }
    except subprocess.CalledProcessError as e:
        eprint(f"git log failed on branch {branch}: {e}")

def get_diffstat(repo_path: Path, sha: str) -> str:
    """Return a diffstat for a single commit.

    Primary method uses `git show --stat` for the commit. If that produces no
    output (edge cases, certain configs), fall back to `git diff --stat sha^!`.
    """
    try:
        out = run_git(repo_path, ["show", "--no-color", "--stat", "--format=", sha])
        out = out.strip()
        if out:
            return out
    except subprocess.CalledProcessError:
        pass
    # Fallback: explicit single-commit diff using sha^!
    try:
        out2 = run_git(repo_path, ["diff", "--no-color", "--stat", "--no-ext-diff", f"{sha}^!"])
        return out2.strip()
    except subprocess.CalledProcessError:
        return ""

def get_diff(repo_path: Path, sha: str, skip_binary: bool = True) -> str:
    try:
        args = ["show", "--no-color", "--format=", sha]
        if skip_binary:
            args.insert(1, "--no-textconv")
        out = run_git(repo_path, args)
        return out.strip()
    except subprocess.CalledProcessError:
        return ""


def update_note_diff_sections(note_path: Path, diffstat: str, diff: str, include_diffstat: bool, include_diff: bool) -> None:
    """Update (or insert) the Diff stats and Diff sections in a commit note.

    - If include_diffstat is False, remove the Diff stats section if present.
    - If include_diff is False, remove the Diff section if present.
    - Otherwise insert or replace the fenced code blocks with the provided content.
    """
    try:
        txt = note_path.read_text(encoding='utf-8')
    except Exception:
        return

    import re

    def replace_section(text: str, heading: str, content: str, keep: bool) -> str:
        # Look for a heading like '## Diff stats' or '## Diff'
        pattern = rf"(^##\s*{re.escape(heading)}\s*\n)(```[\s\S]*?```)(\n|$)"
        m = re.search(pattern, text, flags=re.MULTILINE)
        if m:
            if not keep:
                # remove entire matched block
                return text[:m.start()] + text[m.end():]
            # replace fenced block with new content
            new_block = f"## {heading}\n```\n{content}\n```\n"
            return text[:m.start()] + new_block + text[m.end():]
        else:
            if not keep:
                return text
            # append at end
            if not text.endswith('\n'):
                text += '\n'
            text += f"\n## {heading}\n```\n{content}\n```\n"
            return text

    new_txt = txt
    new_txt = replace_section(new_txt, "Diff stats", diffstat, include_diffstat)
    new_txt = replace_section(new_txt, "Diff", diff, include_diff)

    if new_txt != txt:
        try:
            atomic_write(note_path, new_txt)
        except Exception:
            pass


def normalize_diff_sections(text: str, diffstat: str, diff: str, include_diffstat: bool, include_diff: bool) -> str:
    """Apply the same transformation logic used by update_note_diff_sections to an
    in‑memory string so that freshly rendered templates match the stable format
    we later enforce when backfilling diff/diffstat sections.

    Without this, the initial template (which may contain an extra blank line
    around the fenced code blocks) differs from the normalized format, causing
    every sync to see a content difference and rewrite notes unnecessarily.
    """
    import re

    def repl(existing: str, heading: str, content: str, keep: bool) -> str:
        pattern = rf"(^##\s*{re.escape(heading)}\s*\n)(```[\s\S]*?```)(\n|$)"
        m = re.search(pattern, existing, flags=re.MULTILINE)
        if m:
            if not keep:
                return existing[:m.start()] + existing[m.end():]
            new_block = f"## {heading}\n```\n{content}\n```\n"
            return existing[:m.start()] + new_block + existing[m.end():]
        else:
            if not keep:
                return existing
            if not existing.endswith('\n'):
                existing += '\n'
            existing += f"\n## {heading}\n```\n{content}\n```\n"
            return existing

    out = text
    # If sections are enabled but content is empty, we still include the headers
    # and show a small placeholder so users see the structure on first import.
    stats_content = (diffstat or "").strip() or "(none)"
    diff_content = (diff or "").strip() or "(none)"
    keep_stats = bool(include_diffstat)
    keep_diff = bool(include_diff)
    out = repl(out, "Diff stats", stats_content, keep_stats)
    out = repl(out, "Diff", diff_content, keep_diff)
    return out


def ensure_diff_sections(note_path: Path, repo_path: Path, sha: str, include_diffstat: bool, include_diff: bool, skip_binary: bool) -> None:
    """Ensure diff/diffstat sections have real content in an existing note if requested.

    Replaces "(none)" placeholders with actual data; appends missing sections.
    Never removes sections or overwrites real user content.
    """
    try:
        txt = note_path.read_text(encoding="utf-8")
    except Exception:
        return
    
    import re
    changed = False
    
    # Handle Diff stats section
    if include_diffstat:
        ds = get_diffstat(repo_path, sha)
        if ds.strip():
            # Check if section exists with (none) placeholder
            pattern = r"(## Diff stats\s*\n```\s*\n)\(none\)(\s*\n```)"
            if re.search(pattern, txt):
                txt = re.sub(pattern, rf"\1{ds}\2", txt)
                changed = True
            # If section doesn't exist at all, append it
            elif "## Diff stats" not in txt:
                if not txt.endswith('\n'):
                    txt += '\n'
                txt += f"\n## Diff stats\n```\n{ds}\n```\n"
                changed = True
    
    # Handle Diff section
    if include_diff:
        df = get_diff(repo_path, sha, skip_binary=skip_binary)
        if df.strip():
            # Check if section exists with (none) placeholder
            pattern = r"(## Diff\s*\n```\s*\n)\(none\)(\s*\n```)"
            if re.search(pattern, txt):
                txt = re.sub(pattern, rf"\1{df}\2", txt)
                changed = True
            # If section doesn't exist at all, append it
            elif "## Diff" not in txt:
                if not txt.endswith('\n'):
                    txt += '\n'
                txt += f"\n## Diff\n```\n{df}\n```\n"
                changed = True
    
    if changed:
        atomic_write(note_path, txt)

DEFAULT_COMMIT_TEMPLATE = """---\ntitle: \"{{title}}\"\nsha: \"{{sha}}\"\nshort: \"{{short}}\"\nauthor: \"{{author}}\"\nemail: \"{{email}}\"\ndate: \"{{date}}\"\nbranch: \"{{branch}}\"\nparents: {{parents_json}}\ntags: [\"git\",\"commit\", \"{{repo}}\", \"{{branch}}\"]\n---\n# {{title}}\n\nSHA: `{{sha}}`  \nAuthor: {{author}} <{{email}}>  \nDate: {{date}}\n\n## Parents\n{{parents_list}}\n\n## Message\n{{body}}\n\n## Diff stats\n```\n{{diffstat}}\n```\n\n{{#if diff}}\n## Diff\n```\n{{diff}}\n```\n{{/if}}\n"""

DEFAULT_BRANCH_TEMPLATE = """---\ntitle: \"Branch Index: {{branch}}\"\nbranch: \"{{branch}}\"\nupdated: \"{{updated}}\"\ntags: [\"git\",\"branch\",\"index\"]\n---\n# Branch: {{branch}}\n\nHead: [[{{head_note}}]]\n\n## Commits (latest first)\n{{commit_links}}\n"""

def load_template_from_vault(vault_path: Path, name: str) -> Optional[str]:
    override = vault_path / ".gitsidian" / "templates" / f"{name}.md"
    if override.exists():
        try:
            return override.read_text(encoding="utf-8")
        except Exception:
            return None
    return None

def render_template(tmpl: str, ctx: Dict[str, str]) -> str:
    # Support two conditional syntaxes: {{#diff}}...{{/diff}} and {{#if diff}}...{{/if}}
    def remove_or_keep_section(text: str, name: str) -> str:
        # Support multiple opening forms and multiple closing forms.
        open_patterns = [f"{{{{#{name}}}}}", f"{{{{#if {name}}}}}"]
        close_patterns = [f"{{{{/{name}}}}}", "{{/if}}"]
        out = text
        for open_pat in open_patterns:
            while True:
                si = out.find(open_pat)
                if si == -1:
                    break
                # find the earliest closing token after si
                ei = -1
                chosen_close = None
                for cp in close_patterns:
                    pos = out.find(cp, si)
                    if pos != -1 and (ei == -1 or pos < ei):
                        ei = pos
                        chosen_close = cp
                if ei == -1:
                    # malformed: remove only the opening tag to avoid infinite loop
                    out = out[:si] + out[si+len(open_pat):]
                    continue
                inner = out[si+len(open_pat):ei]
                if ctx.get(name):
                    out = out[:si] + inner + out[ei+len(chosen_close):]
                else:
                    out = out[:si] + out[ei+len(chosen_close):]
        return out

    processed = remove_or_keep_section(tmpl, "diff")

    # Support both raw and YAML-escaped placeholders. ctx may contain raw strings and
    # we expect corresponding YAML-escaped variants with suffix '_yaml'.
    for k, v in ctx.items():
        # raw replacement
        processed = processed.replace(f"{{{{{k}}}}}", v)
        # YAML-escaped replacement: key_yaml
        yaml_key = f"{k}_yaml"
        if yaml_key in ctx:
            processed = processed.replace(f"{{{{{yaml_key}}}}}", ctx[yaml_key])
    return processed

def parents_links(vault: Path, branch: str, parents: List[str], style: str) -> Tuple[str, str]:
    """Return a markdown bullet list of parent links using local note names when possible.

    Searches the vault branch folder for files containing the parent SHA (short or full).
    If a matching note is found, link to its stem (no .md). Otherwise fall back to short SHA.
    """
    links = []
    branch_dir = vault / "branches" / branch
    for p in parents:
        link_target = None
        # try exact/full sha in filenames, then short sha
        try_seeds = [p, p[:7]] if p else []
        if branch_dir.exists():
            for seed in try_seeds:
                for cand in branch_dir.glob(f"*{seed}*.md"):
                    # pick first candidate
                    link_target = cand.stem
                    break
                if link_target:
                    break
        if not link_target:
            # fallback to short sha
            link_target = p[:7] if p else '(unknown)'
        links.append(f"- [[{link_target}]]")
    json_val = json.dumps(parents)
    return ("\n".join(links) if links else "(none)"), json_val

def compute_filename(style: str, sha: str, date_iso: str, title: str) -> str:
    if style == "sha":
        return f"{sha}.md"
    if style == "short-sha":
        return f"{sha[:7]}.md"
    if style == "date-sha":
        d = date_iso.split(" ")[0]
        return f"{d}-{sha[:12]}.md"
    # fallback
    return f"{sha}.md"

def write_commit_note(vault: Path, branch: str, commit: Dict[str, Any], opts: RepoOptions, repo_id: Optional[str] = None) -> Path:
    # Prepare context
    parents_markdown, parents_json = parents_links(vault, branch, commit.get("parents", []), opts.fileNameStyle)
    # Prepare both raw and YAML-escaped context values. YAML-escaped versions
    # are JSON-encoded strings so they are safe when inserted into YAML frontmatter.
    # Normalize and strip key context values to avoid embedded newlines/control chars
    raw_title = (commit.get("subject", "Untitled") or "Untitled").strip()
    sha_val = (commit.get("sha", "") or "").strip()
    short_val = (commit.get("short", sha_val[:7]) or sha_val[:7]).strip()
    author_val = (commit.get("author", "") or "").strip()
    email_val = (commit.get("email", "") or "").strip()
    date_val = (commit.get("date", "") or "").strip()
    body_val = (commit.get("body", "") or "").rstrip()

    # build repo:branch tag if repo_id provided
    repo_branch_tag = f"{repo_id}:{branch}" if repo_id else branch
    ctx = {
        "title": raw_title,
        "title_yaml": json.dumps(raw_title),
        "sha": sha_val,
        "sha_yaml": json.dumps(sha_val),
        "short": short_val,
        "short_yaml": json.dumps(short_val),
        "author": author_val,
        "author_yaml": json.dumps(author_val),
        "email": email_val,
        "email_yaml": json.dumps(email_val),
        "date": date_val,
        "date_yaml": json.dumps(date_val),
        "branch": branch,
        "branch_yaml": json.dumps(branch),
        "repo": repo_id or "",
        "repo_yaml": json.dumps(repo_id or ""),
        "repo_branch_tag": repo_branch_tag,
        "repo_branch_tag_yaml": json.dumps(repo_branch_tag),
        "parents_list": parents_markdown,
        "parents_json": parents_json,
        "body": body_val or "(no message)",
        "diffstat": commit.get("diffstat", ""),
        "diff": commit.get("diff", ""),
    }

    tmpl = load_template_from_vault(vault, "commit") or DEFAULT_COMMIT_TEMPLATE
    content = render_template(tmpl, ctx)

    # Normalize diff sections so comparison with existing note is stable and
    # matches the format produced by update_note_diff_sections (avoids always
    # rewriting files due to trivial whitespace differences).
    content = normalize_diff_sections(
        content,
        ctx.get("diffstat", ""),
        ctx.get("diff", ""),
        opts.includeDiffStat,
        opts.includeDiff,
    )

    fname = compute_filename(opts.fileNameStyle, commit["sha"], commit.get("date", ""), ctx["title"])
    # sanitize generated filename to avoid embedded newlines or control chars
    fname = sanitize_filename(fname)
    # ensure extension present
    if not fname.lower().endswith('.md'):
        fname = fname + '.md'
    note_path = vault / "branches" / branch / fname

    # Never overwrite an existing commit note: preserve any user edits.
    # If the file exists, skip writing entirely.
    if note_path.exists():
        return note_path

    atomic_write(note_path, content)
    return note_path

def write_branch_index(vault: Path, repo_path: Path, branch: str) -> None:
    # Build index by scanning existing notes in the vault branch folder.
    branch_dir = vault / "branches" / branch
    links: List[str] = []
    if not branch_dir.exists():
        branch_dir.mkdir(parents=True, exist_ok=True)

    # helper to extract sha, date, title and author from a note file
    def extract_meta(note_path: Path) -> Optional[Tuple[datetime, str, str, str, str]]:
        try:
            txt = note_path.read_text(encoding="utf-8")
        except Exception:
            return None
        # extract frontmatter block if present
        sha_val = None
        date_val = None
        title_val = None
        author_val = None
        if txt.startswith("---"):
            try:
                end = txt.find('\n---', 3)
                if end != -1:
                    fm = txt[3:end]
                else:
                    fm = txt
            except Exception:
                fm = txt
            # find lines like: sha: "..." or sha: ...
            for line in fm.splitlines():
                if line.lstrip().lower().startswith('sha:'):
                    _, v = line.split(':', 1)
                    sha_val = v.strip().strip(' "\'')
                if line.lstrip().lower().startswith('date:'):
                    _, v = line.split(':', 1)
                    date_val = v.strip().strip(' "\'')
                if line.lstrip().lower().startswith('title:'):
                    _, v = line.split(':', 1)
                    title_val = v.strip().strip(' "\'')
                if line.lstrip().lower().startswith('author:'):
                    _, v = line.split(':', 1)
                    author_val = v.strip().strip(' "\'')
        # fallback: search body for SHA: `...` and Date: ...
        if not sha_val:
            import re
            m = re.search(r"SHA:\s*`([0-9a-fA-F]{7,40})`", txt)
            if m:
                sha_val = m.group(1)
        if not date_val:
            import re
            m = re.search(r"Date:\s*(.+)$", txt, re.MULTILINE)
            if m:
                date_val = m.group(1).strip()

        # fallback: find first H1/H2 in body as title
        if not title_val:
            import re
            m = re.search(r"^#\s+(.+)$", txt, re.MULTILINE)
            if m:
                title_val = m.group(1).strip()
        if not author_val:
            import re
            m = re.search(r"Author:\s*(.+)$", txt, re.MULTILINE)
            if m:
                author_val = m.group(1).strip()

        # parse date_val to datetime if possible
        dt = None
        if date_val:
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(date_val)
            except Exception:
                try:
                    dt = datetime.fromisoformat(date_val)
                except Exception:
                    dt = None
        if not dt:
            # fallback to file mtime
            try:
                dt = datetime.fromtimestamp(note_path.stat().st_mtime, timezone.utc)
            except Exception:
                return None

        if not sha_val:
            # if no sha, use filename without extension as candidate
            sha_val = note_path.stem
        if not title_val:
            # fallback to filename as title
            title_val = note_path.stem
        if not author_val:
            author_val = ""
        # sanitize returned filename
        safe_name = sanitize_filename(note_path.name.strip())
        return (dt, safe_name, title_val, author_val, sha_val)

    metas: List[Tuple[datetime, str, str, str, str]] = []
    for p in branch_dir.glob('*.md'):
        if p.name == 'index.md':
            continue
        m = extract_meta(p)
        if m:
            metas.append(m)

    # sort newest first
    metas.sort(key=lambda t: t[0], reverse=True)
    for dt, name, title, author, sha in metas:
        # sanitize filename to avoid embedded newlines or whitespace
        safe_name = name.replace('\n', '').replace('\r', '').strip()
        # sanitize title for wiki-alias (remove pipeline and closing brackets)
        safe_title = title.replace(']]', '').replace('|', '¦')
        # collapse whitespace and remove newlines
        import re as _re
        safe_title = _re.sub(r"\s+", " ", safe_title).strip()
        # human-friendly date
        try:
            date_str = dt.strftime('%Y-%m-%d %H:%M %z')
        except Exception:
            date_str = ''
        # ensure no stray newlines
        date_str = date_str.replace('\n', ' ').replace('\r', ' ')
        # Build a compact bullet entry with alias, date, author and short sha
        short_sha = (sha[:7] if sha else safe_name[:7])
        safe_author = (author or '').replace('\n', ' ').replace('\r', '').strip()
        # Use wiki-link without extension and without './' so Obsidian resolves locally
        if safe_name.lower().endswith('.md'):
            link_target = safe_name[:-3]
        else:
            link_target = safe_name
        entry = f"- [[{link_target}|{safe_title}]] — {date_str}"
        if safe_author:
            entry += f" — {safe_author}"
        entry += f" — {short_sha}"
        links.append(entry)

    head_note = ''
    if metas:
        # Head note should be the first note name without extension (wiki-link target)
        first_name = metas[0][1].replace('\n', '').replace('\r', '').strip()
        head_note = first_name[:-3] if first_name.lower().endswith('.md') else first_name

    tmpl = load_template_from_vault(vault, "branch-index") or DEFAULT_BRANCH_TEMPLATE
    content = render_template(tmpl, {
        "branch": branch,
        "branch_yaml": json.dumps(branch),
        "updated": datetime.now(timezone.utc).isoformat(),
        "updated_yaml": json.dumps(datetime.now(timezone.utc).isoformat()),
        "commit_links": "\n".join(links) if links else "(no commits)",
        "head_note": head_note,
    })
    idx_path = branch_dir / "index.md"
    atomic_write(idx_path, content)

# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def cmd_list(cfg: AppConfig, args: argparse.Namespace) -> int:
    if not cfg.repos:
        print("No repositories configured. Use 'add' to register one.")
        return 0
    for r in cfg.repos:
        print(f"- {r.id}: {r.name}\n  repo: {r.repoPath}\n  vault: {r.vaultPath}\n  branches: {'all' if not r.branches else ', '.join(r.branches)}")
    return 0

def cmd_remove(cfg: AppConfig, args: argparse.Namespace) -> int:
    rid = args.id
    before = len(cfg.repos)
    cfg.repos = [r for r in cfg.repos if r.id != rid]
    if len(cfg.repos) == before:
        eprint(f"No repo with id '{rid}' found")
        return 1
    save_config(cfg)
    print(f"Removed repo '{rid}'. (No files deleted)")
    return 0

def cmd_doctor(cfg: AppConfig, args: argparse.Namespace) -> int:
    ok = True
    print("Environment check:")
    if git_available():
        print("  ✔ git available")
    else:
        print("  ✖ git NOT available in PATH")
        ok = False
    print(f"Config path: {CONFIG_PATH}")
    print(f"Repositories configured: {len(cfg.repos)}")
    for r in cfg.repos:
        exists = Path(r.repoPath).exists()
        print(f"  - {r.id}: repo {'ok' if exists else 'MISSING'}; vault {'ok' if Path(r.vaultPath).exists() else 'MISSING'}")
    return 0 if ok else 1

def cmd_sync(cfg: AppConfig, args: argparse.Namespace) -> int:
    # Accept repo id via positional `repo` or `--id`. If none provided and only one repo
    # is configured, use that as a convenience so users can run `gitsidian sync`.
    rid = args.repo or args.id
    if not rid:
        if len(cfg.repos) == 1:
            rid = cfg.repos[0].id
        else:
            eprint("Repository id required (pass as positional or --id). Use 'gitsidian list' to see configured repos.")
            return 1
    repo = next((r for r in cfg.repos if r.id == rid), None)
    if not repo:
        eprint(f"Repo id '{rid}' not found")
        return 1
    return perform_sync(repo, cfg)

def cmd_sync_all(cfg: AppConfig, args: argparse.Namespace) -> int:
    if not cfg.repos:
        print("No repositories configured.")
        return 0
    any_fail = False
    for r in cfg.repos:
        rc = perform_sync(r, cfg)
        if rc != 0:
            any_fail = True
    return 1 if any_fail else 0

def perform_sync(repo: RepoConfig, cfg: AppConfig) -> int:
    repo_path = Path(repo.repoPath)
    vault = Path(repo.vaultPath)
    if not ensure_git_repo(repo_path):
        eprint(f"Not a git repo: {repo_path}")
        return 1
    vault.mkdir(parents=True, exist_ok=True)

    # Determine branches
    if repo.branches:
        branches = repo.branches
    else:
        branches = list_local_branches(repo_path)
    if not branches:
        print(f"[sync] No branches found for {repo.name}")
        return 0

    processed_total = 0
    for br in branches:
        print(f"[sync] Branch {br}")
        last = repo.lastSync.get(br)
        if isinstance(last, str):  # sanitize stored sha
            last = last.strip() or None
        limit = repo.options.maxInitialCommitsPerBranch if not last else None
        commits = list(iter_commits(repo_path, br, last, repo.options.includeMerges, limit))
        if not commits:
            print("  up to date")
            write_branch_index(vault, repo_path, br)
            continue
        for c in commits:
            # Determine target note path early
            fname = compute_filename(repo.options.fileNameStyle, c["sha"], c.get("date", ""), c.get("subject", ""))
            fname = sanitize_filename(fname)
            if not fname.lower().endswith('.md'):
                fname = fname + '.md'
            note_path = vault / "branches" / br / fname
            if note_path.exists():
                # Existing note: ensure diff sections present if requested
                ensure_diff_sections(note_path, repo_path, c["sha"], repo.options.includeDiffStat, repo.options.includeDiff, repo.options.skipBinaryDiff)
                continue
            # New note: fetch diffs if requested
            c["diffstat"] = get_diffstat(repo_path, c["sha"]) if repo.options.includeDiffStat else ""
            c["diff"] = get_diff(repo_path, c["sha"], skip_binary=repo.options.skipBinaryDiff) if repo.options.includeDiff else ""
            write_commit_note(vault, br, c, repo.options, repo.id)
            processed_total += 1
        # update lastSync for this branch to newest commit processed
        repo.lastSync[br] = commits[-1]["sha"]
        write_branch_index(vault, repo_path, br)
        
        # After processing new commits, ensure all existing notes have diff sections if requested
        if repo.options.includeDiffStat or repo.options.includeDiff:
            branch_dir = vault / "branches" / br
            if branch_dir.exists():
                for note_file in branch_dir.glob("*.md"):
                    if note_file.name == "index.md":
                        continue
                    # Extract SHA from frontmatter or filename
                    try:
                        txt = note_file.read_text(encoding="utf-8")
                        sha = None
                        if txt.startswith("---"):
                            end = txt.find('\n---', 3)
                            fm = txt[3:end] if end != -1 else txt
                            for line in fm.splitlines():
                                if line.lstrip().lower().startswith('sha:'):
                                    _, v = line.split(':', 1)
                                    sha = v.strip().strip(' "\'')
                                    break
                        if not sha:
                            sha = note_file.stem
                        if sha:
                            ensure_diff_sections(note_file, repo_path, sha, repo.options.includeDiffStat, repo.options.includeDiff, repo.options.skipBinaryDiff)
                    except Exception:
                        continue

    repo.updatedAt = datetime.now(timezone.utc).isoformat()
    save_config(cfg)
    print(f"[sync] Done: {processed_total} new commits written for '{repo.name}'.")
    return 0

# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitsidian",
        description="Export git commit history to Obsidian notes (one commit per Markdown file).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """Examples:\n  gitsidian add\n  gitsidian list\n  gitsidian sync --id my-repo\n  gitsidian doctor\n"""
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add", help="Add a repository via wizard")
    p_add.set_defaults(func=run_add_wizard)

    p_list = sub.add_parser("list", help="List configured repositories")
    p_list.set_defaults(func=cmd_list)

    p_remove = sub.add_parser("remove", help="Remove a configured repository")
    p_remove.add_argument("--id", required=True, help="Repository id to remove")
    p_remove.set_defaults(func=cmd_remove)

    p_sync = sub.add_parser("sync", help="Sync a single repository")
    # allow repository id as either --id or positional (nargs='?') so users can run:
    #   gitsidian sync formo
    # or
    #   gitsidian sync --id formo
    p_sync.add_argument("--id", required=False, help="Repository id to sync")
    p_sync.add_argument("repo", nargs="?", help="Repository id (positional, optional)")
    p_sync.set_defaults(func=cmd_sync)

    p_sync_all = sub.add_parser("sync-all", help="Sync all repositories")
    p_sync_all.set_defaults(func=cmd_sync_all)

    p_doctor = sub.add_parser("doctor", help="Run environment checks")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser

# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        cfg = load_config()
    except ConfigError as e:
        eprint(f"Config error: {e}")
        return 1
    return args.func(cfg, args)  # type: ignore[arg-type]

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
