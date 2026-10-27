# precompact-protector

Precompact-protector saves the last turns of a Claude Code session before compaction and prints a short working-memory card when the session starts again. Compaction summarizes the conversation and drops the rest, so decisions from the last hour can go with it.

## Install

Clone this repository. Open `settings-snippet.json`, replace its placeholder path with the real absolute path to this directory, and merge its hook entries into `~/.claude/settings.json`. Append the entries to existing hook arrays rather than replacing them. There is nothing to install with pip.

## How it works

The PreCompact hook runs for automatic and manual compaction. It reads the transcript tail and writes a checkpoint. The SessionStart hook runs only for the `compact` matcher and prints a card with an optional `REHYDRATE.md` written by your own summarizer, the last user intent, files mentioned in recent turns, and the checkpoint turn count and timestamp.

The card is orientation for the new context. Check specifics against the conversation and files before relying on them.

## Data and privacy

Data lives under `~/.claude/precompact` by default. Set `PRECOMPACT_DIR` to use another directory. To delete the data, run `rm -rf ~/.claude/precompact`.

Everything stays on your machine. Checkpoint and log files are created with mode 0600, directories under the data root with mode 0700, and nothing is sent anywhere.

## Limits

The hook reads at most the last 600 KB of the transcript and keeps up to 40 user and assistant turns. The card is a deterministic floor, not a summary.

## Tests

Run the tests with `python3 -m unittest discover -s tests`.

Door 20 of 100 Doors to Recursion: https://github.com/LucasCooperBey/100-doors
