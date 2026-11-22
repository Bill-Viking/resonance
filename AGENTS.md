# Resonance — for AI agents

Start with the contract for the work you were asked to do.

## You are preparing public writing or artwork

For a new post, reply, standalone creation or requested attribution, read
[AUTHORING.md](AUTHORING.md) in full. Public RES credit is optional and off by default. Read the saved
choice with `python3 monitor.py --profile`; the user’s instruction for this item
wins. Use a banner only on saved opt-in or explicit request, with the user’s
approved asset.
Internal session plates are not public signatures.
Preparing a draft does not authorize publication or retroactive changes.

## You are helping someone use Resonance

For a one-time social notification review or help drafting a reply to a
selected conversation, follow **Social inbox assistance** in
[ASSISTANT.md](ASSISTANT.md#social-inbox-assistance).

You were asked to run their check, bring in their posts, or carry out an edit or deletion they asked for.

Read [ASSISTANT.md](ASSISTANT.md) in full, then follow it. It is the whole contract: the steps, every command, the report shape and the safety rules. Nothing in this file adds to it or overrides it.

## You are changing the source

Read [README.md](README.md) and [PRODUCT.md](PRODUCT.md) first. Then hold these lines:

- **Run every suite before and after a change.** `python3 -m unittest discover -s tests`, then `node tests/test_ui_actions.cjs`, `node tests/test_ui_communication.cjs`, `node tests/test_ui_today.cjs`, `node tests/test_ui_reader.cjs`, `node tests/test_browser_reader.cjs`, `node tests/test_reader_discovery.cjs` and `node tests/test_reader_pipeline.cjs`.
- **Tests use temporary data folders only.** Never read, copy or point a test at anyone's real data folder.
- **Synthetic values only** in code, tests, examples and docs: no real handle, name, mailbox, path or saved text.
- **Three things are the product, not details.** The app makes no outgoing connections, holds no credentials, and has no model inside. A change that breaks one of them is a different product.
- **Keep the words true.** [SECURITY.md](SECURITY.md) describes the server's checks and [ASSISTANT.md](ASSISTANT.md) describes the commands. When you change either behavior, change its page in the same commit.

Maintainers keep working notes under `docs/internal/` in their own checkout. That folder is not part of a release; when it is present, read `docs/internal/AGENT-NOTES.md` before you start.
