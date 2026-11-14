# precompact-protector

Precompact-protector saves the last turns of a Claude Code session before compaction and prints a short working-memory card when the session starts again. Compaction summarizes the conversation and drops the rest, so decisions from the last hour can go with it.

## Install

```bash
git clone https://github.com/LucasCooperBey/precompact-protector.git
```

Open `settings-snippet.json`, replace its placeholder path with the real absolute path to this directory, and merge its hook entries into `~/.claude/settings.json`. Append the entries to existing hook arrays rather than replacing them. There is nothing to install with pip.

## How it works

The PreCompact hook runs for automatic and manual compaction. It reads the transcript tail and writes a checkpoint. The SessionStart hook runs only for the `compact` matcher and prints a card. The card can include an optional `REHYDRATE.md` written by your own summarizer.

When that hook is given the transcript, the floor opens with `- Compaction N of this transcript, this one included`. N counts `compact_boundary` rows already in the transcript, sidechain rows excluded, plus one, because the row for this compaction is written after the hook runs. The floor then quotes the last words you typed. Tool results, skill bodies, compact summaries, and injected notices are skipped. A prompt you queued while the agent was busy counts. A slash command is shown as its name and arguments. When the transcript contains none of your words, the line says none was found. Files in play and the checkpointed-turn count still come from the newest checkpoint. Without a transcript, the card quotes the last user turn stored in that checkpoint.

A helper agent gets a one-line note instead of the card when the hook names a model the main chat's records do not show and a subagent file written in the last 15 minutes holds a reply on that model. Every uncertain case prints the card, including a helper on the main chat's own model. That card says a helper on the same model also receives it.

The card is orientation for the new context. Check specifics against the conversation and files before relying on them.

## Example

After a session where you asked for a rename, got it, and then asked for a `--dry-run` flag, Claude Code compacts. The PreCompact hook saves a checkpoint under `~/.claude/precompact/sessions/<session>/`, and when the session resumes the SessionStart hook prints this card into the new context:

```
Context protector: working memory from before this compaction. Orientation only; check specifics before relying on them.

## Before compaction (raw floor)
- Compaction 1 of this transcript, this one included
- A helper agent on the main chat's model also gets this card: if you are one, keep to your own task
- Last words you typed (2026-11-12T21:01:00Z): Now add a --dry-run flag to the cli.
- 3 turns checkpointed at 20261113T050816Z-968360
```

The card quotes the last thing you typed, so the model picks up the open ask (the flag) and does not stop at the finished one (the rename).

## Data and privacy

Data lives under `~/.claude/precompact` by default. Set `PRECOMPACT_DIR` to use another directory. To delete the data, run `rm -rf ~/.claude/precompact`.

Everything stays on your machine. Checkpoint and log files are created with mode 0600, directories under the data root with mode 0700, and nothing is sent anywhere.

## Limits

The capture hook reads at most the last 600 KB of the transcript and keeps up to 40 user and assistant turns. The card searches for your words, and for the main chat's model, in steps: the last 600 KB, then the last 4 MB, then the last 16 MB. When the file is larger than that and those tails hold no typed words, the word search reads the whole file one line at a time. Rows are split on newline bytes only, so a line separator pasted inside a JSON string stays inside its row. The compaction count reads the whole file. The card is a deterministic floor, not a summary, and it stays within 2600 characters.

## Tests

Run the tests with `python3 -m unittest discover -s tests`.

Door 20 of 100 Doors to Recursion: https://github.com/LucasCooperBey/100-doors
