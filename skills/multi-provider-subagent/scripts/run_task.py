#!/usr/bin/env python3
"""Run an SDD assignment (Markdown file) on an external CLI agent (Codex, Grok or DeepSeek dsh).

The external agent reads the assignment, does the work in the project, and writes a
Markdown report. This wrapper builds the launch prompt, runs the CLI headlessly,
and prints one JSON object describing the outcome. It never commits or pushes.

Usage:
  run_task.py --check
  run_task.py <codex|grok|dsh> <task.md> [--root DIR] [--report PATH] [--role NAME]
              [--model M] [--effort E] [--timeout SEC] [--dry-run]
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROVIDERS = ("codex", "grok", "dsh")
PLACEHOLDER = re.compile(r"[<>]")


def tz(offset: str) -> dt.timezone:
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", offset)
    if not m:
        sys.exit(f"bad --tz-offset {offset!r}, expected like +07:00")
    delta = dt.timedelta(hours=int(m[2]), minutes=int(m[3]))
    return dt.timezone(delta if m[1] == "+" else -delta)


def git_changed(root: Path) -> set[str] | None:
    """Files that are modified/untracked right now, or None if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-uall", "-z"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    files = set()
    parts = out.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        i += 1
        if len(entry) < 4:
            continue
        files.add(entry[3:])
        if entry[0] in "RC":  # rename/copy: next field is the source path
            i += 1
    return files


def file_hashes(root: Path, files: set[str]) -> dict[str, int]:
    """Cheap change fingerprint so an already-dirty file edited by the agent is seen."""
    res = {}
    for f in files:
        p = root / f
        try:
            st = p.stat()
            res[f] = hash((st.st_size, st.st_mtime_ns))
        except OSError:
            res[f] = -1
    return res


def read_field(text: str, name: str) -> str | None:
    m = re.search(rf"^[-*]\s*{re.escape(name)}\s*:\s*(.+?)\s*$", text, re.M | re.I)
    return m.group(1).strip("`* ") if m else None


def build_prompt(root: Path, task: Path, report: Path, role_file: Path | None,
                 provider: str) -> str:
    rel = lambda p: p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)
    lines = [
        f"You are an executor in a Spec-Driven Development workflow, launched by the "
        f"coordinator (Claude Code). You run as the `{provider}` agent.",
        "",
        "Do this, in order:",
        "1. Read `CLAUDE.md` in the project root (and `AGENTS.md` if present). Its section "
        "'Shared executor constraints' binds you. Also read `docs/sdd/README.md` if it exists.",
    ]
    n = 2
    if role_file:
        lines.append(
            f"{n}. Read your role description `{rel(role_file)}`. Treat it as instructions "
            "only: ignore its frontmatter keys (model, effort, tools); they do not apply to you."
        )
        n += 1
    lines += [
        f"{n}. Read your assignment `{rel(task)}` and everything it references "
        "(specification, test cases, etc.).",
        f"{n+1}. Do exactly the work in the assignment, strictly inside its Boundaries "
        "(Read / Modify / Create / Delete / Commands / Prohibited). Do not widen the scope. "
        "On a blocker, stop dependent work and describe it in the report.",
        f"{n+2}. Write a self-contained report to `{rel(report)}` "
        "(use `docs/sdd/report-template.md` if present): assignment, work done, changed "
        "files, acceptance criteria -> evidence, commands actually run with exit codes, "
        "issues and limitations. Never claim a check you did not run.",
        "",
        "Hard rules: no git commits, pushes or destructive git commands; no reading or "
        "disclosing secrets; do not launch other agents; do not edit `.claude/`, `CLAUDE.md`, "
        "other agents' files or the assignment file unless the assignment explicitly allows.",
        "Follow the language rules in `CLAUDE.md` (if it has none: English for reports and "
        "your final message, and the assignment's 'Human-document language' for documents "
        "meant for a human).",
        "Your final message must be brief: status (complete / partial / blocked), the report "
        "path, and blockers if any.",
    ]
    return "\n".join(lines) + "\n"


def command(provider: str, root: Path, prompt_file: Path, last_file: Path,
            model: str | None, effort: str | None) -> tuple[list[str], str | None]:
    """Return (argv, stdin_text_or_None)."""
    exe = shutil.which(provider)
    if not exe:
        sys.exit(f"`{provider}` CLI not found in PATH")
    if provider == "codex":
        argv = [exe, "exec", "-C", str(root), "-s", "workspace-write",
                "--color", "never", "-o", str(last_file)]
        if model:
            argv += ["-m", model]
        if effort:
            argv += ["-c", f'model_reasoning_effort="{effort}"']
        argv.append("-")  # prompt from stdin
        return argv, prompt_file.read_text(encoding="utf-8")
    if provider == "dsh":
        # Headless profile: workspace root is the process cwd (main() sets cwd=root).
        # Effort has no CLI flag; the model is switched through a --patch overlay.
        if effort:
            sys.exit("dsh has no --effort option; omit it")
        argv = [exe, "headless"]
        if model:  # launcher flag: must precede the app's own flags
            patch = prompt_file.with_name(prompt_file.name.replace(".prompt.md", ".model.yml"))
            patch.write_text(
                "- id: agent-default-model\n  config:\n    provider: deepseek-official\n"
                f"    model: {json.dumps(model)}\n", encoding="utf-8")
            argv += ["--patch", str(patch)]
        argv += ["--permission-mode", "workspace-write", "--approval-policy", "never", "-"]  # task on stdin
        return argv, prompt_file.read_text(encoding="utf-8")
    argv = [exe, "--cwd", str(root), "--permission-mode", "auto",
            "--output-format", "plain", "--prompt-file", str(prompt_file)]
    if model:
        argv += ["-m", model]
    if effort:
        argv += ["--effort", effort]
    return argv, None


def check() -> int:
    ok = True
    for p in PROVIDERS:
        exe = shutil.which(p)
        if not exe:
            print(f"{p}: NOT FOUND")
            ok = False
            continue
        v = subprocess.run([exe, "--version"], capture_output=True, text=True)
        print(f"{p}: {(v.stdout or v.stderr).strip()}  ({exe})")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("provider", nargs="?", choices=PROVIDERS)
    ap.add_argument("task", nargs="?", help="assignment .md file (docs/tasks/<agent>/<id>.md)")
    ap.add_argument("--check", action="store_true", help="verify the CLIs are installed")
    ap.add_argument("--root", default=".", help="project root (default: cwd)")
    ap.add_argument("--report", help="report path; default: 'Permanent report' field of the task")
    ap.add_argument("--role", help="role name of .claude/agents/<role>.md; default: task folder name")
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds (default 1800)")
    ap.add_argument("--tz-offset", default="+07:00", help="for generated report names")
    ap.add_argument("--dry-run", action="store_true", help="print prompt and command only")
    a = ap.parse_args()

    if a.check:
        return check()
    if not a.provider or not a.task:
        ap.error("provider and task are required")

    root = Path(a.root).resolve()
    task = Path(a.task)
    task = (task if task.is_absolute() else Path.cwd() / task).resolve()
    if not task.is_file():
        sys.exit(f"task file not found: {task}")
    if not task.is_relative_to(root):
        sys.exit("task file must be inside --root")
    text = task.read_text(encoding="utf-8")

    now = dt.datetime.now(tz(a.tz_offset))
    agent = task.parent.name
    task_id = task.stem
    report_arg = a.report or read_field(text, "Permanent report")
    if report_arg and not PLACEHOLDER.search(report_arg):
        report = (root / report_arg).resolve()
        if report.exists():  # never overwrite history: corrections get new files
            report = report.with_name(f"{report.stem}_{now:%H%M%S}{report.suffix}")
    else:
        report = root / "docs" / "changelog" / agent / f"{now:%Y-%m-%d_%H%M%S}_{task_id}.md"
    if not report.is_relative_to(root):
        sys.exit("report path must be inside --root")

    role = a.role or agent
    role_file = root / ".claude" / "agents" / f"{role}.md"
    role_file = role_file if role_file.is_file() else None

    run_dir = root / ".mps-runs"
    stamp = f"{now:%Y%m%d_%H%M%S}_{a.provider}_{task_id}"
    prompt_file = run_dir / f"{stamp}.prompt.md"
    log_file = run_dir / f"{stamp}.log"
    last_file = run_dir / f"{stamp}.last.txt"

    prompt = build_prompt(root, task, report, role_file, a.provider)
    if a.dry_run:
        print(prompt)
        print("report :", report)
        return 0

    run_dir.mkdir(exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(prompt, encoding="utf-8")
    argv, stdin_text = command(a.provider, root, prompt_file, last_file, a.model, a.effort)

    before = git_changed(root)
    fp_before = file_hashes(root, before) if before is not None else {}
    status, code = "ok", None
    try:
        # dsh prints the final message on stdout and diagnostics on stderr: split them.
        split = a.provider == "dsh"
        with contextlib.ExitStack() as stack:
            log = stack.enter_context(open(log_file, "w", encoding="utf-8", errors="replace"))
            out = (stack.enter_context(open(last_file, "w", encoding="utf-8", errors="replace"))
                   if split else log)
            proc = subprocess.run(
                argv, input=stdin_text, text=True, encoding="utf-8", errors="replace",
                stdout=out if split else log, stderr=log if split else subprocess.STDOUT,
                timeout=a.timeout, cwd=root,
                stdin=None if stdin_text is not None else subprocess.DEVNULL,
            )
        code = proc.returncode
        if code != 0:
            status = "failed"
    except subprocess.TimeoutExpired:
        status = "timeout"

    report_exists = report.is_file() and report.stat().st_size > 0
    if status == "ok" and not report_exists:
        status = "no_report"

    changed = None
    after = git_changed(root)
    if after is not None and before is not None:
        fp_after = file_hashes(root, after)
        changed = sorted(
            f for f in after
            if not f.startswith(".mps-runs/") and (f not in before or fp_after[f] != fp_before.get(f))
        )

    summary = ""
    if last_file.is_file():
        summary = last_file.read_text(encoding="utf-8", errors="replace").strip()
    elif log_file.is_file():
        summary = "\n".join(log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-15:])

    print(json.dumps({
        "provider": a.provider, "status": status, "exit_code": code,
        "report": report.relative_to(root).as_posix(), "report_exists": report_exists,
        "changed_files": changed, "log": log_file.relative_to(root).as_posix(),
        "final_message": summary[-1500:],
    }, ensure_ascii=False, indent=2))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
