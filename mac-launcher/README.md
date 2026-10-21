# Resonance Mac app (optional)

`mac-launcher/` builds a small click-to-open app for macOS. It uses the Resonance
speech-bubble icon, opens your default browser and needs no Terminal window. It is
a convenience for people comfortable with a build step; `./resonance` remains the
supported way to start Resonance.

The native wrapper runs a bundled Python helper. The helper checks the local
index page, reuses a matching listener, or starts the selected copy in the
background. A file lock prevents repeated clicks through this launcher from
starting several servers. A startup failure appears in a dialog; a listener
with a different index page is left alone. Closing the browser leaves the local server
running; after a restart, clicking the icon starts it again. It is not a login
item, a scheduler, a separate browser window or a connection to any platform.

## Build and update

Build against a finished copy of a release in a folder of its own, never an
unfinished working tree:

```sh
python3 mac-launcher/build.py --runtime "/path/to/a/finished/copy/of/resonance"
```

The output defaults to `~/Applications/Resonance.app`. The builder refuses to
overwrite an existing app; set the old one aside first, then rebuild and verify
before replacing it. It uses the Mac's developer tools, AppKit's SVG renderer,
iconutil and a local ad-hoc code signature. The helper uses `/usr/bin/python3`
(3.9 or newer). The bundle identifier is `io.github.<owner>.resonance`, with the
owner read from `release.json`. The build records the runtime folder, and the
data folder Resonance resolved at build time, in the app's bundled config; the
source checkout is not needed to open the app.

Rebuild when you select another release. The helper compares the served index
page with the selected copy. It refuses a listener with a different page, but it
cannot tell apart two releases whose index page is identical. Stop the previous
Resonance server before you switch releases; do not rely on the launcher to
identify the whole release. Your data stays in
your Resonance data folder. The launcher's own log and lock file are kept in
`~/Library/Application Support/Resonance`.

## Checks

```sh
python3 -m unittest discover -s tests -p test_mac_launcher.py -v
```

Six process checks: a cold start and reuse, four clicks at once, a conflicting
release left alone, a missing runtime, a changed runtime, and a failed start
with a local log. They use temporary synthetic servers and data. Nothing real
is touched.
