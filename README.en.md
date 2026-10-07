<picture>
  <source media="(prefers-color-scheme: dark)" srcset="readme-assets/header-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="readme-assets/header-light.svg">
  <img alt="Context Logger · Preserve Development Context · ✦ EricMingle69" src="readme-assets/header-light.svg" width="100%">
</picture>

<p align="center">
  <a href="README.md">简体中文</a> · <a href="README.en.md">English</a> · <a href="PERSONAL-NOTICE.md">✦ EricMingle69</a>
</p>

# Context Logger · Preserve Development Context

Save one identified Codex or Claude Code session as verifiable local context.
No summarization model or vector database is required. Raw is authoritative; Markdown and SQLite indexes are rebuildable.

## Resolve before saving

Use the entry from the repository root, replacing these placeholders:

```sh
python3 scripts/transcript_manager.py resolve \
  --source codex \
  --session-id <current-thread-id> \
  --project-root /absolute/path/to/workspace \
  --module-id <registered-module-id>
```

For Claude Code, use `--source claude-code` and an explicit session ID, or a SessionStart anchor described in [SKILL.md](SKILL.md).
Check the source, session and target, then run `save` with the same resolution parameters.
Run `verify --target-dir /absolute/path/to/archive --session-id <session-id>` using the resolved archive directory; replace the path and ID placeholders.
Only `verified=true` from `verify` establishes data-layer consistency.

## Four data layers

| Layer | Purpose |
| --- | --- |
| Raw JSONL | Original bytes and rebuilding source |
| Normalized events | Unified cross-host events |
| Markdown chunks / indexes | Reading and navigation |
| SQLite FTS5 | Disposable, rebuildable full-text index |

User and AI bodies remain complete across Markdown chunks.
Markdown displays tool inputs/results up to 2,000 characters, and result indexing up to 8,000; full contents remain referenced in Raw.

## Install the Claude Hook

```sh
bash install.sh
```

This registers SessionStart while retaining existing configuration, writing the real `~/.claude/settings.json` and Hook file.
`install.sh` does not forward arguments. Use [the Python installer](scripts/install_claude_hook.py) directly for temporary targets or restoration.
If existing `hooks` is absent or `null`, it stops and requires a trusted baseline rather than treating configuration as empty.

## Retrieve and rebuild

Read the module `INDEX.md`, locate candidates with `search`, then read relevant chunks with `show`; avoid loading whole transcript directories.
`rebuild-index` derives data from Raw and Manifest. If derivation fails, Raw remains and state becomes `needs_rebuild`.
Managed workspace policies require a registered module target; transcripts are not automatically created at the root.

## Sources and license

See [source boundaries in SKILL.md](SKILL.md#来源边界); ordinary ChatGPT chats are outside the current scope.
Session bodies, Raw and credentials stay within local authorization; use redacted feedback examples.
[MIT License](LICENSE): Copyright (c) 2026 Eric Mingle (Ming-Sir-69).

---

Documentation maintained by **✦ EricMingle69** · [Ming-Sir-69](https://github.com/Ming-Sir-69)  
[Personal identity, licensing and permissions](PERSONAL-NOTICE.md) · The header follows your GitHub theme.
