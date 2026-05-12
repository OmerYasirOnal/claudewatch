# Conversation log format

ClaudeWatch parses the JSONL files Claude Code writes for each session. This
doc captures the parts we depend on so future schema drift can be diagnosed
quickly.

## Where the logs live

In probe order:

1. `~/.claude/projects/<cwd-as-dashes>/<session-uuid>.jsonl`  ← current
2. `~/.config/claude/projects/<cwd-as-dashes>/<session-uuid>.jsonl`
3. `~/Library/Application Support/Claude/projects/<cwd-as-dashes>/<session-uuid>.jsonl`

**Folder name:** the cwd path with `/` replaced by `-`.

Example: cwd `/Users/me/Projects/site` → folder `-Users-me-Projects-site`.

## Linkage to a running process

Two strategies, tried in order:

1. If the `claude` cmdline contains `--resume <uuid>` or `--session-id <uuid>`,
   pick the file `<uuid>.jsonl` in the cwd-matched folder.
2. Otherwise, pick the freshest `*.jsonl` in that folder by mtime.

The first path is the canonical one; the second is a fallback for sessions
that started without resume.

## Entry shape

Every entry is a single-line JSON object. Top-level keys observed:

```
type           one of: user, assistant, system, last-prompt, permission-mode,
               attachment, file-history-snapshot, ai-title
sessionId      same UUID for every entry in the file
cwd            absolute path
gitBranch      string or "HEAD"
version        Claude CLI version string (e.g. "2.1.132")
timestamp      ISO-8601 with trailing Z
uuid           per-entry UUID
parentUuid     UUID of the entry this one replies to
```

### `assistant` entries

```
type: "assistant"
message:
  model: "claude-opus-4-7"
  id: "msg_..."
  content: [ { type: "thinking"|"text"|"tool_use", ...} ]
  stop_reason
  usage:
    input_tokens
    output_tokens
    cache_creation_input_tokens
    cache_read_input_tokens
    server_tool_use, service_tier, iterations[]
```

The parser sums `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
and `cache_read_input_tokens` across every `assistant` entry.

For each `content` block:
- `type: "thinking"` → set `thinking_enabled = true`
- `type: "tool_use"` → increment `tool_calls.total`, `tool_calls.breakdown[name]`, record as `last_used`

### `permission-mode` entries

```
type: "permission-mode"
permissionMode: "auto" | "default" | "plan" | "bypassPermissions" | ...
sessionId: ...
```

The latest `permission-mode` entry wins; falls back to cmdline flags if absent.

## What we tolerate

- Unknown `type` values are ignored, not errors
- Missing fields default to 0/None
- Malformed JSON lines are skipped (logged at WARNING)
- Empty content arrays, missing usage, missing message — all handled

## What we don't (yet)

- We don't parse `attachment` content
- We don't render tool input/output content (privacy default)
- We don't deduplicate entries across resumed sessions — counts include all
  assistant turns in the file

## Updating the parser

When Claude Code introduces a new entry type or renames a usage field, the
parser will silently keep working with the fields it still understands. To
re-validate:

```bash
pytest tests/test_conversation_log.py -v
```

Update `tests/fixtures/sample_log.jsonl` if you want to lock in expectations
against a newer log shape.
