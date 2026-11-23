# Desktop candidate: build and use

Download the [signed Windows x64 ZIP](https://github.com/Bill-Viking/resonance/releases/download/v0.1.0-rc.1-windows.2/Resonance-0.1.0-rc.1-windows.2-x86_64-signed.zip), extract the entire folder, then open `Resonance.exe` inside it. Python is bundled. The Apple Silicon app is awaiting signing and notarization. On macOS, open the [source ZIP](https://github.com/Bill-Viking/resonance/releases/download/v0.1.0-rc.1/Resonance-0.1.0-rc.1-source.zip) as a local project in Codex or Claude Code. Running the dashboard from source requires Python 3.9 or later.

The app listens only on `127.0.0.1:4317`, makes no outgoing connections, holds no credentials and has no model inside. Its small window offers **Open Resonance** and **Quit**. Closing that window stops the local server and preserves saved data. Closing only the browser does not quit the app.

## Build an unsigned candidate

Build natively on each target. The intended targets are macOS 13 or newer, separately for Apple silicon and Intel, and Windows 10 or newer on x86-64. These are build targets; minimum-OS compatibility still requires physical tests. PyInstaller does not cross-compile.

Use ordinary, GIL-enabled CPython **3.14.10**, with Tcl/Tk 8.6 or newer. The
builder records the Tcl patch level and Tk major and minor version. It records
an exact Tk patch level when the Tk scripts declare it or the builder supplies
an independently verified value with `--tk-patch-level`. Apple's system Python
is unsuitable. A Python built for a newer macOS cannot prove compatibility
with an older one merely by changing an environment variable.

Create a new isolated build environment outside the checkout:

```
python3 tools/prepare_build.py --python /path/to/python3.14 --target macos-arm64 --out /path/to/build-venv
```

Use `macos-x86_64` for Intel or `windows-x86_64` for Windows. On Windows, the selected interpreter and venv executable have Windows paths; the latter is `build-venv\Scripts\python.exe`. On Mac it is `build-venv/bin/python`. `--plan` inspects the target and locks without creating the environment.

The preparation step is a builder-only network operation. It installs exact, hash-verified wheels, including all build dependencies, from platform locks in `desktop/locks/`. It disables user configuration and inherited Python/pip injection settings, refuses an existing output folder and records both pip installation reports. Do not install build packages into the system Python or bypass the lock with `requirements-build.txt`.

From a clean, committed source checkout, make an audited export outside the checkout. Replace the example version with the exact value in `VERSION`:

```
/path/to/build-venv/bin/python -I -B tools/make_release.py --version 0.1.0-rc.1 --out /path/to/runtime
```

Developer note: `.gitattributes` pins text to LF on checkout (`* text=auto eol=lf`) so native and export builds see the same bytes whatever a developer's `core.autocrlf` is, and the exporter's isolated status check stays clean on Windows. Git's binary detection and `THIRD-PARTY-NOTICES.txt` (`-text`) remain byte-exact. Global Git configuration is not changed.

The exporter runs the public copy's Python and seven Node suites. It constructs fresh synthetic Git history with isolated Git configuration and checks the committed tree against the manifest. Review the exported file-list difference before distribution; broad public source directories still need human review.

Inspect and then build the native candidate using that same venv:

```
/path/to/build-venv/bin/python -I -B tools/build_desktop.py --runtime /path/to/runtime --out /path/to/candidate --plan
/path/to/build-venv/bin/python -I -B tools/build_desktop.py --runtime /path/to/runtime --out /path/to/candidate
```

The output must be new and outside both the checkout and runtime. The tool verifies every release-manifest entry and refuses internal files, links, unlisted files, changed bytes and missing required files. It stages only verified bytes. It checks the venv's exact package set, wheel-install evidence and lock hashes, then runs PyInstaller with `-E -s` from an empty working directory and a sanitized environment.

The output contains the native app under `dist/`, a candidate ZIP and `BUILD-MANIFEST.json`. The manifest records the source manifest digest, full Python build, native architecture, intended minimum OS, Tcl/Tk version evidence, complete pip freeze, wheel-install evidence, self-test result and file/archive hashes. An unsigned manifest is evidence for that unsigned archive only. Signing changes bytes and requires a new final manifest.

The build requires a regular `THIRD-PARTY-NOTICES.txt` at
`Resonance.app/Contents/Resources/THIRD-PARTY-NOTICES.txt` on Mac or
`Resonance/_internal/THIRD-PARTY-NOTICES.txt` on Windows. Its bytes must match the
verified export. The manifest's `third_party_notices` entry records the path,
SHA-256 and byte count from that check. The source catalog at
`desktop/third-party-sources.json` records the length and hash of each upstream
notice. Review each native build's dependency inventory against that catalog
before distribution. Mac bundle versions and minimum OS are
set before the ad-hoc signature is renewed and verified. Windows receives a version
resource and a default icon made from the existing verified Reader icon;
`--icon` can supply an override. Physical Windows metadata and icon display still
need native inspection.

The built executable performs a headless smoke check against temporary synthetic
data, with its logs and JSON report also in temporary folders. It imports
`tkinter`, starts a Tcl interpreter with `tkinter.Tcl()` and records `tcl: true`
and the exact `tcl_patch`. It also serves the page, checks the assistant prompt
and checks time-zone support before stopping. The file report works even when
a Windows windowed executable has no stdout. Tk widgets, browser behavior,
VoiceOver and NVDA require separate native checks.

The Mac build checks ZIP metadata before writing the candidate manifest. It
accepts ditto's provenance-only AppleDouble sidecars. Quarantine and other
unsupported metadata fail the build. The ZIP and partial build output stay
available for diagnosis.

## Open and quit

Choose **Using RES in Codex or Claude Code** at the bottom of any workspace page for the illustrated
user manual. It opens in a separate tab so you can keep your place in RES.

On Mac, unzip the candidate, move **Resonance.app** to Applications, then open it there. The launcher refuses a translocated Mac app before binding or touching saved data and explains the move. On Windows, extract the entire folder before opening **Resonance.exe**; keep its accompanying files together.

An unsigned candidate has not passed Developer ID/notarization or Authenticode checks. Candidate testing does not establish signed-download behavior or first-user readiness. Do not remove quarantine or weaken operating-system protections as a release procedure.

If port 4317 is occupied, the app leaves the other listener alone and shows a message. An earlier source launcher may have left a background copy running without a window. The new source launcher has an ownership-checked stop command; see [its instructions](../mac-launcher/README.md). It cannot adopt or stop an unknown older process. An operating-system denial or reserved port has a different message. Port 4317 is retained deliberately because the Reader's local host permission uses it; changing it requires a coordinated app and Reader update.

Your saved data stays in the location described in the [README](../README.md), outside the app. The launcher's bounded event log is `~/Library/Logs/Resonance/launcher.log` on Mac or `%LOCALAPPDATA%\Resonance Launcher\launcher.log` on Windows. It records event words and error types, never saved text, account names, request bodies or credentials.

## Assistant files

The packaged app verifies its public assistant files against an embedded manifest and copies them to a stable per-user folder: `~/Library/Application Support/Resonance Assistant` on Mac or `%LOCALAPPDATA%\Resonance Assistant` on Windows. Each version has a directory named with the full manifest digest. The prompt gives that exact path. The kit contains public code and documentation only; no saved workspace is copied.

Existing kits are verified before reuse and never overwritten if changed. Repositories, recognized cloud locations, data folders and symlink destinations are refused. Verification failures produce a repair message. `monitor.py` disables bytecode writes before importing local modules. The external assistant still needs its own Python; the packaged app does not.

Signed-resource integrity after an assistant command and behavior on a quarantined download remain native acceptance checks.

## CI and release evidence

The manual `desktop-candidate.yml` workflow uses pinned action commits, CPython 3.14.10 and Node 24. It builds on explicit Apple silicon, Intel and Windows x86-64 runners, uses the locked venv, runs all suites, tests the audited export and uploads unsigned candidate evidence. It uses no signing credentials and publishes no release. A green build is not physical platform, screen-reader or independent-user proof. Developers: `tools/make_release.py` bounds each exported test command to five minutes, except the native Windows Python suite, which has fifteen; a failed or timed-out check prevents the release commit and tag.

A signed release must verify the exact unsigned input, sign and verify it, remove signing-key access before running its self-test, then on Mac notarize and staple it. Only afterward may it create the distribution archive and final hashes. All signing, native-install, Reader-store, accessibility, independent-user and combined-source review gates remain separate.

The staged commands and their checkpoint requirements are in [Signing](SIGNING.md).
