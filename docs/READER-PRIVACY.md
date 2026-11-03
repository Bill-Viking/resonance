# Resonance Reader privacy

Resonance Reader is the optional browser companion to the local Resonance app.
It reads a limited amount of your own writing after you click. It does not run
scheduled checks, publish, edit, delete, send messages or switch accounts.

## What it handles

To find a supported account, it reads the active X handle or YouTube handle and
channel identifier. You decide whether to save that account in Resonance.
To run a check, it reads at most three recent own X posts or YouTube comments,
their source links, available dates and authorship evidence. It reports what it
could confirm and what it could not read. It does not read private messages,
passwords, authentication cookies, browsing history, images or video content.

## Where that information goes

The extension passes observations to the Resonance page at
`http://127.0.0.1:4317/` on this computer. The local app validates and saves the
completed check, including text with no rule matches, in its local database.
The extension keeps its current work in memory and does not persist post text.
There is no developer collection endpoint, analytics service, advertising use,
sale of data or model service in either the extension or local application.

Opening provider pages uses your browser's existing session and sends the normal
page requests to X, YouTube or Google. Those services have their own policies.
The extension does not send your writing to an additional external service.
An external assistant you choose to use is separate and uses its own permissions
and data practices; Resonance does not automatically send it your records.

## Permissions and control

The browser's site permissions cover all pages on X, YouTube and Google activity
when you allow them. The reader's actual operations are narrower: it opens its
own supported account pages for a deliberate discovery or check. You can remove
each provider's permission in Reader settings or uninstall the extension.
Removing the extension does not delete data already saved in the local app.

Saved text, check receipts and revision history remain until you remove the
local workspace. **Settings → Your data** identifies its folder. Quit Resonance
before removing that folder; also remove exported backups if you want those
copies gone. Keep any backup you still need. The current edition has no
per-source erasure control for the immutable reader history. Portable backups
are readable JSON, not encrypted archives. No automatic remote backup is made.

This policy describes reader version 0.1.0 and app candidate 0.1.0-preview.7.
Reader-store publication is still pending.
