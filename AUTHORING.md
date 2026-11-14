# Public writing and artwork with Resonance

Use this workflow when the user asks an external assistant to prepare a new
public post, reply or artwork through Resonance. It governs preparation and
optional attribution. It does not publish anything, grant account access or
authorize changes to existing posts. Public-post checks, edits and deletions
still follow [ASSISTANT.md](ASSISTANT.md).

The exact two-line withdrawal text in the [Work Safe procedure](docs/SAFE-WORK.md)
is a narrow, user-authorized edit fallback, not a RES signature or general
attribution default.

## Branding is the user's choice

**New users start with no RES signature, repository link or graphic banner.**
Using Resonance does not require advertising it. Do not add an AI credit line as
a substitute for an unwanted signature. Do not falsely claim human authorship
or remove other people's existing credits. Answer authorship questions honestly.
A destination’s required AI-content label is separate from RES branding: Off does
not disable it; raise it with the user when applicable.

Users can open **Settings & data → Preferences: personal brand and RES credit**
and save their choice under **RES credit on public drafts**:

| Setting | What it asks the assistant to include |
| --- | --- |
| **Text signature: Off** (default) | No added RES text signature or project link. |
| **Text signature: Short — RES** | A compact `— RES` signature. |
| **Text signature: Full — RES and project link** | The two-line signature below. |
| **Include my approved RES banner** (off by default) | The user's separately supplied, approved image, when available. This choice is independent of the text signature. |

These are instructions for an external assistant. The local app has no model,
does not generate or attach a banner, and never adds branding to saved text or
publishes it automatically. The choice applies to new public drafts, not mail,
imported posts, report excerpts or the exact replacement text of an approved edit.

## Read the choice before drafting

Run the read-only profile command from the Resonance folder (Windows: `py -3`
or `python` in place of `python3`):

```text
python3 monitor.py --profile
```

The `brand` object contains `public_signature` (`off`, `compact` or `full`) and
`public_banner` (`true` or `false`). Missing fields in an older profile mean
`off` and `false`. The profile command works without a running app. It creates
no files and runs no database upgrade. If it refuses, explain the limit; do not
change the user's data to read a preference. A new draft does not require a
saved account. If no saved preference can be read and the user has given none,
prepare the draft without branding.

The user's explicit instruction for this item wins. “No signature,” “no branding,”
or “use this exactly” means no added signature, banner, credit or repository
link. A requested custom signature replaces the default; never append RES twice.
A separately stated standing preference also applies until the user changes it;
do not apply one person's advertising choice to anyone else. If a standing
preference conflicts with a saved choice and it is unclear which is newer,
ask one short question instead of guessing. A one-item override does not change
the saved choice or grant permission to rewrite earlier work.

When the user chooses full text, use:

```text
RES — Your Personal Brand Assistant
https://github.com/Bill-Viking/resonance
```

Keep the actual URL without tracking parameters. Do not claim that a platform
makes it clickable unless it does. If the chosen treatment does not fit a length
limit, show a shorter alternative for the user to review. Do not silently change
their saved preference.

If the user requests creation credit, describe the contribution truthfully, for
example “Prepared with RES · AI-assisted” or “Created by RES · AI-generated.”
RES identifies only the work the external assistant contributed. Never replace
it with an internal session's persona, model, vendor, machine or operational
banner. Preserve the user's and other authors' existing credits.

## Optional graphic banner

Use a graphic only when the user opts in through the saved setting or explicitly
asks for one. Use the exact approved asset supplied separately by that user.
Preserve its wording, layout and proportions. Do not regenerate, redesign, crop
or overwrite it. If it is unavailable, prepare the text and say the image is
unavailable; do not invent a replacement or claim it was attached. Private
artwork and approval records stay outside source, fixtures and release exports.

## Prepare and check the final item

1. Identify the destination, whether this is a new item or a reply, and any exact
   wording or attribution instruction. Keep the user's voice and authorship.
2. Read the saved choice and apply any direct user instruction. Show the finished
   text and any requested media together. Keep preference and implementation
   explanations outside the public copy.
3. Check that off means no added branding; opt-in choices match the preview;
   custom wording appears only once; no internal session plate entered a public
   field; and any approved graphic remains unchanged.
4. Publication is separate work. This edition has no guarded publish/reply
   action. A draft or saved branding choice grants no permission to post,
   attach media, edit or delete. Follow the separately authorized publishing
   workflow with its exact destination, final preview, required confirmation,
   attempt record and verified receipt. Never retrofit a published item to
   apply a preference, and never add branding after approval.

Drafting alone needs no sign-in. If the user separately authorizes browser-based
publication, first inspect the browser inventory your tools expose and name the
browser you can actually use. A login in the user's other browser is not a login
in the assistant's built-in browser. If sign-in is needed, open the supported
browser's sign-in page and give one concrete step at a time, naming that window
and page. The user enters credentials; never collect or move them. Afterward,
verify the live account and carry forward the unchanged approved item without
inventing an extra discretionary approval loop. Required tool confirmations and
the separately authorized publishing workflow still apply.
