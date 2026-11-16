#!/usr/bin/env python3
"""Render an Agent Scope Diff comparison as private, self-contained HTML."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

STYLES = """
:root{color-scheme:dark;--bg:#10121e;--panel:#171b2b;--edge:#333952;--ink:#f0effb;--muted:#b9bfd7;--green:#9becbd;--pink:#f4a8d8;--yellow:#ffd695;--accent:#beaaff}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(ellipse at 15% 0,#29224a 0,transparent 52%),var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:1040px;margin:auto;padding:clamp(24px,6vw,72px) clamp(16px,4vw,42px)}
header{margin-bottom:26px}
.kicker,.tag,th,.stat span{font-size:.71rem;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
.kicker{color:var(--accent)}
h1{font-size:clamp(2.35rem,6vw,4.5rem);letter-spacing:-.07em;line-height:1.04;margin:12px 0}
p{margin:0;color:var(--muted)}
.summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:24px 0}
.stat,.board{border:1px solid var(--edge);border-radius:14px;background:var(--panel)}
.stat{padding:13px 16px}.stat strong{display:block;font-size:1.8rem;line-height:1.1;margin-bottom:4px}.stat span{color:var(--muted);letter-spacing:.06em}
.board{padding:clamp(12px,3vw,24px)}
table{border-collapse:collapse;width:100%;table-layout:fixed;text-align:left}
th,td{padding:12px 8px;vertical-align:top;overflow-wrap:anywhere}
th:first-child{width:43%}
th{color:var(--muted);border-bottom:1px solid var(--edge);font-size:.75rem}
td{border-bottom:1px solid var(--edge)}
tbody tr:last-child td{border-bottom:0}
code,.loc{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.loc{display:block;letter-spacing:0;text-transform:none;color:var(--ink);margin-top:4px;font-size:.92rem}
.tag{display:inline-block;color:var(--muted);letter-spacing:.05em}.good{color:var(--green)}.skip{color:var(--pink)}.warn{color:var(--yellow)}
.count,.foot{color:var(--muted);font-size:.86rem}
.count{display:block}.foot{margin-top:16px}
@media(max-width:540px){.summary{gap:6px}.stat{padding:9px}.stat span{font-size:.61rem}th,td{padding:8px 4px;font-size:.78rem}.count{font-size:.69rem}.tag{font-size:.62rem}}
@media print{:root{color-scheme:light;--ink:#151526;--muted:#363649;--panel:#fff;--edge:#bcc0c9;--accent:#452279;--green:#005432;--pink:#860340;--yellow:#825000}body{background:#fff}main{padding:0}}
"""
ACTIVE = {"picked", "selected", "included", "loaded", "truncated", "partial"}
SKIP = {"shadowed", "skipped", "budget-exhausted", "excluded", "missing", "not_found"}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def relative(raw: str, root: str, *, home: bool = False) -> str:
    """Render only project-relative paths (or a basename for outside paths)."""
    cls = PureWindowsPath if "\\" in raw or (len(raw) > 1 and raw[1] == ":") else PurePosixPath
    path = cls(raw)
    if home:
        return "$CODEX_HOME/" + path.name
    if path.is_absolute() or path.anchor:
        try:
            path = path.relative_to(cls(root))
        except ValueError:
            return "./…/" + path.name
    if ".." in path.parts:
        return "./…/" + path.name
    return "./" + path.as_posix() if path.parts else "."


def status(row: dict | None) -> str:
    if not row:
        return '<span class="tag">Not in scope</span>'
    state = str(row["status"])
    css = "warn" if state in {"truncated", "partial", "error", "unreadable"} else "good" if state in ACTIVE else "skip" if state in SKIP else ""
    used = row.get("included_bytes") or 0
    amount = f'<span class="count">{int(used):,} B included</span>' if used else ""
    return f'<span class="tag {css}">{esc(state.replace("_", " "))}</span>{amount}'


def paths(side: dict) -> dict:
    return {
        (str(item["scope"]), relative(str(item["path"]), str(side["root"]), home=item["scope"] == "global")): item
        for item in side["files"]
    }


def render(data: dict) -> str:
    if data.get("schema_version") != 1 or data.get("mode") != "compare":
        raise ValueError("unsupported comparison schema")
    left, right = data["left"], data["right"]
    a, b = paths(left), paths(right)
    keys = sorted(a.keys() | b.keys(), key=lambda k: (k[0] != "global", k[1]))
    diff = sum(
        tuple(a.get(k, {}).get(col) for col in ("status", "included_bytes", "digest"))
        != tuple(b.get(k, {}).get(col) for col in ("status", "included_bytes", "digest"))
        for k in keys
    )
    rows = "\n".join(
        f'<tr><td><code>{esc(k[1])}</code></td><td>{status(a.get(k))}</td><td>{status(b.get(k))}</td></tr>'
        for k in keys
    ) or '<tr><td colspan="3">No instruction files found in either scope.</td></tr>'
    labels = [esc(relative(str(s["cwd"]), str(s["root"]))) for s in (left, right)]
    budgets = [(int(s["used_bytes"]), int(s["budget"])) for s in (left, right)]
    notes = sum(len(side.get("warnings", [])) for side in (left, right))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Agent Scope Diff · Comparison</title><style>{STYLES}</style></head>
<body><main>
<header><div class="kicker">Agent Scope Diff / Codex CLI preview</div>
<h1>Same repo.<br>Different rules.</h1>
<p>See which instruction files each launch directory would load, skip, or truncate.</p></header>
<section class="summary" aria-label="Summary">
<div class="stat"><strong>{diff}</strong><span>Instruction differences</span></div>
<div class="stat"><strong>{budgets[0][0]:,} B</strong><span>Left project budget used</span></div>
<div class="stat"><strong>{budgets[1][0]:,} B</strong><span>Right project budget used</span></div>
</section>
<section class="board" aria-label="Instruction comparison">
<table><thead><tr><th scope="col">Instruction file</th>
<th scope="col">Left <span class="loc">{labels[0]}</span><span class="count">{budgets[0][1]:,} B limit</span></th>
<th scope="col">Right <span class="loc">{labels[1]}</span><span class="count">{budgets[1][1]:,} B limit</span></th>
</tr></thead><tbody>{rows}</tbody></table></section>
<p class="foot">Local simulation. File contents, full machine paths and warning details are omitted. Review relative filenames before sharing.{f" See CLI for {notes} note(s)." if notes else ""}</p>
</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="Comparison JSON file (default: stdin)")
    args = parser.parse_args()
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read())
        result = render(data)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(f"report.py: expected valid compare --json input ({type(error).__name__})", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
