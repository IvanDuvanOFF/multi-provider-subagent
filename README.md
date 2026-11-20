# multi-provider-subagent

A Claude Code skill that lets the coordinator hand an SDD assignment to an
external CLI agent — **OpenAI Codex**, **xAI Grok** or **DeepSeek (`dsh`)** — and get the result back as
a Markdown report. It is a companion to
[sdd-scaffold](https://github.com/IvanDuvanOFF/sdd-scaffold).

```text
docs/tasks/<agent>/<id>.md ──run_task.py──▶ codex | grok | dsh (headless) ──▶ docs/changelog/<agent>/<date>_<id>.md
                                   └─▶ JSON: status, report path, changed files, log
```

## Requirements

- Python 3, git, and the `codex`, `grok` and `dsh` CLIs in `PATH`, already logged in.
- A project with the sdd-scaffold structure.

Check: `python skills/multi-provider-subagent/scripts/run_task.py --check`

## Install

Plugin (once published): `/plugin marketplace add IvanDuvanOFF/multi-provider-subagent`,
then `/plugin install multi-provider-subagent@multi-provider-subagent`.
Manually: copy `skills/multi-provider-subagent` to `~/.claude/skills/`.

## Use

Ask Claude Code, e.g. "run docs/tasks/reviewer/R-01.md on Codex", or call directly:

```text
python <skill-dir>/scripts/run_task.py codex docs/tasks/reviewer/R-01.md
python <skill-dir>/scripts/run_task.py grok  docs/tasks/test-writer/T-02.md --effort high --dry-run
python <skill-dir>/scripts/run_task.py dsh   docs/tasks/test-writer/T-03.md --model deepseek-v4-pro
```

Options: `--root`, `--report`, `--role`, `--model`, `--effort`, `--timeout`,
`--tz-offset`, `--dry-run`. Run artifacts go to `.mps-runs/` (gitignore it).

## How it runs each provider

| | Codex | Grok | dsh |
| --- | --- | --- | --- |
| Invocation | `codex exec -s workspace-write -C <root> -` | `grok --permission-mode auto --prompt-file …` | `dsh headless --permission-mode workspace-write --approval-policy never -` |
| Write limit | OS sandbox, project root | none — verify `changed_files` | `workspace-write` at cwd (enforcement untested) — verify `changed_files` |
| Project rules | `AGENTS.md`; the prompt adds `CLAUDE.md` | `CLAUDE.md` natively | the prompt adds `CLAUDE.md` |

Findings from testing (Codex 0.166, Grok 1.0.89, dsh 0.2.2-rc.5, Windows): Grok headless needs
`--permission-mode auto` (`acceptEdits` and `dontAsk` refuse writes); Codex stops
if the assignment says `Commands: none` because it reads files via the shell.
dsh has no effort flag; `--model` is applied through a generated `--patch` overlay (valid names: `deepseek-flash`, `deepseek-v4-pro`).

## Limits

Path boundaries in assignments are instructions. Code is sent to the providers.
Nothing here commits, pushes, or edits CLI configs.