#!/usr/bin/env python3
"""Agent Scope Diff: offline, read-only instruction discovery simulation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

VERSION = "0.1.0"
DEFAULT_BUDGET = 32768
MAX_GLOBAL_BYTES = 8 * 1024 * 1024


def directory(value: str) -> Path:
    result = Path(value).expanduser().resolve()
    if not result.is_dir():
        raise ValueError(f"Not a directory: {value}")
    return result


def filename(value: str) -> str:
    if not value or value in (".", "..") or any(c in value for c in ('/', '\\', ':', '\0')):
        raise ValueError(f"Fallback must be a plain filename: {value!r}")
    return value


def root_for(cwd: Path, explicit: str | None) -> tuple[Path, str]:
    if explicit:
        root = directory(explicit)
        if not cwd.is_relative_to(root):
            raise ValueError(f"Working directory {cwd} is outside root {root}")
        return root, "explicit root"
    for candidate in (cwd, *cwd.parents):
        if (candidate / '.git').exists():
            return candidate, "nearest .git marker"
    return cwd, "no .git marker; current directory only"


def explain(cwd_value: str, *, root: str | None = None, codex_home: str | None = None,
            budget: int = DEFAULT_BUDGET, fallbacks: list[str] | None = None) -> dict:
    if budget < 0:
        raise ValueError("Budget must be non-negative")
    cwd = directory(cwd_value)
    project_root, root_reason = root_for(cwd, root)
    names = list(dict.fromkeys(['AGENTS.override.md', 'AGENTS.md', *[filename(x) for x in (fallbacks or [])]]))
    dirs = [cwd]
    while dirs[-1] != project_root:
        dirs.append(dirs[-1].parent)
    dirs.reverse()
    files = []
    warnings = []
    remaining = budget

    def scan(folder: Path, scope: str, candidates: list[str]) -> None:
        nonlocal remaining
        selected = None
        for name in candidates:
            path = folder / name
            try:
                if not path.is_file():
                    continue
                size = path.stat().st_size
                item = dict(path=str(path), scope=scope, status='shadowed', size_bytes=size,
                            included_bytes=0, reason='', digest=None)
                if selected is not None:
                    item['reason'] = f"Higher-priority file selected: {selected.name}"
                    files.append(item)
                    continue
                selected = path
                if scope == 'project' and remaining == 0:
                    item.update(status='budget-exhausted', reason='Project byte budget exhausted; contents not read')
                    files.append(item)
                    continue
                limit = remaining if scope == 'project' else MAX_GLOBAL_BYTES
                if scope == 'global' and size > limit:
                    item.update(status='error', reason='Global file exceeds 8 MiB safety limit; contents not read')
                    warnings.append(f"Cannot simulate oversized global instructions: {path}")
                    files.append(item)
                    continue
                with path.open('rb') as handle:
                    data = handle.read(limit)
                text = data.decode('utf-8', errors='replace')
                if not text.strip():
                    item.update(status='empty', reason=('Empty global file; trying the next global candidate' if scope == 'global' else 'Selected file is empty or whitespace-only; lower-priority files remain shadowed'))
                    if scope == 'global':
                        selected = None
                else:
                    included = len(data)
                    item.update(status='truncated' if size > included else 'picked', included_bytes=included,
                                digest=hashlib.sha256(data).hexdigest(),
                                reason='Only the remaining project byte budget is included' if size > included else 'First available file at this directory')
                    if scope == 'project':
                        remaining -= included
                    if '\ufffd' in text:
                        warnings.append(f"UTF-8 replacement needed (invalid bytes or split character): {path}")
                files.append(item)
            except OSError as exc:
                files.append(dict(path=str(path), scope=scope, status='error', size_bytes=None,
                                  included_bytes=0, reason=f"{type(exc).__name__}: {exc.strerror}", digest=None))
                warnings.append(f"Unreadable candidate: {path}; result is incomplete")
                # Do not simulate a fallback past an unreadable higher-priority candidate.
                break

    if codex_home:
        scan(directory(codex_home), 'global', names[:2])
    for folder in dirs:
        scan(folder, 'project', names)
    return dict(schema_version=1, mode='explain', cwd=str(cwd), root=str(project_root),
                root_reason=root_reason, budget=budget, used_bytes=budget - remaining,
                codex_home=str(directory(codex_home)) if codex_home else None,
                files=files, warnings=warnings,
                limitations=[
                    'Simulation of local Codex CLI file discovery, not evidence of a live session or model compliance.',
                    'No config files, session transcripts, imports, trust settings, remote environments, or host instructions are inspected.',
                    'Only the selected cwd ancestor chain is modeled; instructions for later-opened nested files are not predicted.',
                    'Global instructions are inspected only with --codex-home; fallback names and root must be supplied explicitly when configured.',
                    'Paths and SHA-256 fingerprints may be private. Review before sharing. Instruction text is never included.'
                ])


def compare(left: dict, right: dict) -> dict:
    def entries(report):
        return {(x['scope'], x['path']): x for x in report['files']}
    a, b = entries(left), entries(right)
    def ref(key):
        return dict(scope=key[0], path=key[1])
    added = [ref(k) for k in sorted(b.keys() - a.keys())]
    removed = [ref(k) for k in sorted(a.keys() - b.keys())]
    changed = [dict(**ref(k), before=a[k], after=b[k]) for k in sorted(a.keys() & b.keys()) if a[k] != b[k]]
    return dict(schema_version=1, mode='compare', left=left, right=right,
                added=added, removed=removed, changed=changed)


def human(report: dict) -> str:
    if report['mode'] == 'compare':
        out = ['Agent Scope Diff | compare', '', 'LEFT', human(report['left']), '', 'RIGHT', human(report['right']), '', 'DIFFERENCES']
        for label in ('added', 'removed', 'changed'):
            for item in report[label]:
                out.append(f"  {label.upper():7} [{item['scope']}] {item['path']}")
        if not any(report[x] for x in ('added', 'removed', 'changed')):
            out.append('  No discovered instruction differences.')
        return '\n'.join(out)
    out = [f"Agent Scope Diff | {report['cwd']}",
           f"Root: {report['root']} ({report['root_reason']})",
           f"Project budget: {report['used_bytes']:,} / {report['budget']:,} bytes",
           'Global: ' + (report['codex_home'] or 'not inspected; opt in with --codex-home'), '']
    for item in report['files']:
        out.append(f"  {item['status'].upper():16} [{item['scope']}] {item['path']}")
        out.append(f"    {item['included_bytes']} / {item['size_bytes']} bytes. {item['reason']}.")
    if not report['files']:
        out.append('  No instruction candidates found on this ancestor chain.')
    if report['warnings']:
        out += ['', *[f"WARNING: {w}" for w in report['warnings']]]
    out += ['', 'Simulation only. No configs or live sessions read. Text omitted; paths and hashes may be private.']
    return '\n'.join(out)


def safe_terminal(value: str) -> str:
    # A hostile filename must not inject ANSI escapes into the terminal.
    return ''.join(c if (c == '\n' or c.isprintable()) else f'\\u{ord(c):04x}' for c in value)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, epilog='Examples: agent_scope_diff.py apps/web --json | agent_scope_diff.py compare . apps/web')
    p.add_argument('paths', nargs='*', help='[cwd] or compare LEFT RIGHT')
    p.add_argument('--root', help='Explicit project root (must contain each cwd)')
    p.add_argument('--codex-home', help='Opt in to inspecting AGENTS files in this global home directory')
    p.add_argument('--budget', type=int, default=DEFAULT_BUDGET, help='Project byte budget (default: 32768)')
    p.add_argument('--fallback', action='append', default=[], metavar='NAME', help='Additional filename, repeatable, in priority order')
    p.add_argument('--json', action='store_true', help='Deterministic structured output; no instruction text')
    p.add_argument('--version', action='version', version=f'Agent Scope Diff {VERSION}')
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    try:
        kwargs = dict(root=args.root, codex_home=args.codex_home, budget=args.budget, fallbacks=args.fallback)
        if args.paths and args.paths[0] == 'compare':
            if len(args.paths) != 3:
                raise ValueError('compare requires LEFT and RIGHT directories')
            report = compare(explain(args.paths[1], **kwargs), explain(args.paths[2], **kwargs))
        else:
            if len(args.paths) > 1:
                raise ValueError('Expected one cwd, or compare LEFT RIGHT')
            report = explain(args.paths[0] if args.paths else '.', **kwargs)
        print(json.dumps(report, indent=2, ensure_ascii=True, sort_keys=True) if args.json else safe_terminal(human(report)))
        reports = [report['left'], report['right']] if report['mode'] == 'compare' else [report]
        return 2 if any(x['status'] == 'error' for r in reports for x in r['files']) else 0
    except (ValueError, OSError) as exc:
        if args.json:
            print(json.dumps(dict(schema_version=1, mode='error', error=str(exc)), sort_keys=True))
        else:
            print(f'agent-scope-diff: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
