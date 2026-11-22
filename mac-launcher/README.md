# Resonance Source Launcher for Mac

This optional convenience launcher starts an audited source release with the Mac's `/usr/bin/python3` (3.9 or newer). It is distinct from the self-contained desktop app: its name is **Resonance Source Launcher**, and its bundle identifier ends in `.resonance.launcher`. The source release must stay in its selected folder. `./resonance` remains available for a normal Terminal launch.

## Build

First make a clean audited export with `tools/make_release.py`. The launcher builder verifies that export's manifest and file hashes; it refuses the working tree, missing files and changed bytes.

```
python3 mac-launcher/build.py --runtime /path/to/audited/runtime
```

The default output is `~/Applications/Resonance Source Launcher.app`. The builder refuses to replace an existing app. It needs the Mac developer tools and creates an ad-hoc signature, not a Developer ID signature. It derives the version from `VERSION`, requires a valid owner in `release.json`, and sets the compiler and bundle minimum to macOS 13. Native minimum-OS acceptance remains separate.

The config records the verified runtime location and release fingerprints. Normally the server resolves the saved-data folder when it starts, including its existing legacy-folder rules. An explicit `--data-dir` pins a chosen folder. Neither rebuilding nor stopping deletes saved data. Logs and ownership files live separately in `~/Library/Logs/Resonance Source Launcher`; the current and rotated log are each bounded to 256 KiB.

## Start and stop

Opening the launcher starts an owned supervisor and server, waits for the matching page, then opens the browser. Repeated clicks reuse only that launcher's owned server. A different or unknown listener is left alone. Closing the browser leaves the server running.

Stop the server through the helper inside the same app:

```
/usr/bin/python3 "$HOME/Applications/Resonance Source Launcher.app/Contents/Resources/launch.py" --stop
```

The supervisor checks the exact direct child, command, birth identity and ownership record before terminating it. The command never signals a process merely because it uses port 4317 or has a saved PID. A missing, stale or unknown owner produces a refusal. It cannot adopt or stop a background process left by an older preview. Quit that earlier copy through its original Terminal or process controls after identifying it.

Stop the old owned release before rebuilding or switching runtimes. The helper checks the stored manifest, server and page fingerprints on launch, but a matching page alone is not release authentication. This source convenience launcher does not replace signed consumer-install testing.

## Checks

```
python3 -m unittest discover -s tests -p test_mac_launcher.py -v
python3 -m unittest discover -s tests -p test_legacy_build.py -v
```

Tests use temporary synthetic servers, files and ownership records. They cover repeated starts, conflicting listeners, safe stopping, changed releases, bounded logs and builder validation. Native wrapper execution and an upgrade from an installed older preview remain separate acceptance checks.
