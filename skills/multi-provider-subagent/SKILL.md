---
name: multi-provider-subagent
description: "Delegate an SDD assignment (a Markdown file in docs/tasks/) to an external CLI agent — OpenAI Codex or xAI Grok — and get its report back as a Markdown file in docs/changelog/. Use when the user asks to run a task, review, test writing or second opinion on Codex or Grok, to use another vendor's model as a subagent, or to compare providers. Works with the sdd-scaffold structure; not for ordinary Claude subagents."
---

# Multi-provider subagents (Codex, Grok)

Claude Code is the coordinator. External agents are headless CLI processes
launched by `scripts/run_task.py`. The contract is the same as for native SDD
subagents: **assignment .md in → report .md out**. Only a short JSON status comes
back to the session; the substance is in the report file.

## Prerequisites

- The project follows sdd-scaffold (`CLAUDE.md`, `docs/tasks/`, `docs/changelog/`,
  `docs/sdd/`). If not, offer the `sdd-scaffold` skill first. Do not create
  structure here.
- `python <skill-dir>/scripts/run_task.py --check` shows both CLIs. Both must be
  logged in already (`codex login`, `grok login`); never handle credentials.
- The project is a git repository (the script reports changed files via git).

## Workflow

1. **Assignment.** Write `docs/tasks/<agent>/<task-id>.md` from
   `docs/sdd/task-template.md`. Everything the executor needs must be in it or
   referenced from it: specification and version, AC, exact Read / Modify /
   Create / Delete paths, commands and their side effects, completion criteria.
   The external agent has **no memory of this conversation**. Implementation
   needs an approved specification, as in any SDD flow.
   - `<agent>` is the role (`test-writer`, `reviewer`, …). If
     `.claude/agents/<agent>.md` exists, its body is given to the executor as the
     role description (frontmatter `model`/`effort`/`tools` is ignored).
   - Never write `Commands: none`. Codex reads files through shell commands and
     will stop as "blocked" under such a rule (seen in testing). Say instead
     "read-only viewing commands only (Get-Content, rg); nothing that writes".
   - Set the `Permanent report:` line to a concrete path. If left as a
     placeholder, the script generates
     `docs/changelog/<agent>/<date>_<time>_<task-id>.md`.
2. **Choose the provider** (see below), preview with `--dry-run` if unsure:

   ```text
   python <skill-dir>/scripts/run_task.py codex docs/tasks/reviewer/R-01.md
   python <skill-dir>/scripts/run_task.py grok  docs/tasks/test-writer/T-02.md --effort high
   ```

   Run from the project root (or pass `--root`). Runs take minutes: launch with
   `run_in_background` and continue other work; you are notified on exit. Default
   timeout 1800 s (`--timeout`). Independent assignments for different providers
   may run in parallel **only if their Modify/Create paths do not overlap**
   (otherwise sequentially, or use separate git worktrees).
3. **Verify, don't trust.** Read the JSON, then the report. Check
   `changed_files` against the assignment's Modify/Create list: write boundaries
   are instructions, not a sandbox, especially for Grok. Re-run the key checks
   yourself or via a Claude reviewer when the result matters. Report status is
   not human acceptance.
4. **Failures.** `no_report` / `failed` / `timeout`: read the log named in the
   JSON (`.mps-runs/*.log`), fix the assignment or environment, and rerun. A
   rerun never overwrites an existing report (a time suffix is added). Record
   unavailable models or auth problems as a flag in `docs/changelog/flags.md`
   instead of silently switching providers.

## Choosing a provider

No fixed ranking; ask the user when it matters. Useful defaults:

- **Independent review / second opinion** → a provider different from the one
  that wrote the code.
- **Implementation and test writing** → Codex (`workspace-write` sandbox enforces
  the project root).
- **Quick, cheap analysis or large parallel fan-out** → Grok.
- Use `--model` / `--effort` only when the user asked; otherwise CLI defaults
  from the user's own config apply (do not edit their configs).

## Provider specifics

| | Codex | Grok |
| --- | --- | --- |
| Command | `codex exec -s workspace-write -C <root> -` (prompt on stdin) | `grok --permission-mode auto --prompt-file …` |
| Write limit | Sandbox: project root only | Not path-restricted: verify `changed_files` |
| Project rules | Reads `AGENTS.md`; the prompt makes it read `CLAUDE.md` | Reads `CLAUDE.md` natively |
| Effort | `-c model_reasoning_effort=…` via `--effort` | `--effort` |
| Network | Off in sandbox | On |

## Rules

- No commits, pushes or settings changes by external agents (the prompt forbids
  them; confirm in `changed_files` / `git status`).
- Do not put secrets in assignments; external providers receive the project's
  files and the prompt. Tell the user once per session that code is sent to
  OpenAI / xAI, and do not run on repositories the user marked private without
  their go-ahead.
- Add `.mps-runs/` to the project's `.gitignore` (prompt, log and last-message
  files of each run).
- Nested delegation is not allowed: the prompt tells executors not to launch agents.
