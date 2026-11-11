# Desktop candidate: build and use

A **candidate** is a self-contained Mac app or Windows folder that a person can open without installing Python, Node, a Terminal or Xcode. It is built by PyInstaller 6.22.3 in one-folder, windowed mode with no UPX compression, from an audited source export. Nothing here publishes, signs or notarizes anything.

The packaged app is the same Resonance: it listens only on `127.0.0.1:4317`, makes no outgoing connections, holds no credentials and has no model inside. A small window says **Resonance running** and offers **Open Resonance** and **Quit**. Closing the window stops the local server and keeps everything you saved. If the address is already used by another program, Resonance shows a plain message and the log location; it never stops or reuses the other program.

## What a candidate is, and is not

| Stage | What it means | Status |
| --- | --- | --- |
| Source preview | `./resonance` or `resonance.bat` with Python installed | Exists today |
| **Unsigned candidate** | The archive these steps produce. A Mac may ad-hoc sign the pieces on Apple silicon; that is not an identity | What this page builds |
| Signed release | Mac: Developer ID signature and Apple notarization. Windows: Authenticode signature | Not built; needs the owner's credentials |
| Physical first-user proof | A person on a real Mac and a real Windows computer unzips it, opens it, uses it, quits it, and their data is intact | Separate; a build or CI run is not this |

An unsigned candidate shows operating-system warnings (Gatekeeper on Mac, SmartScreen on Windows) and may need the person to confirm they trust it. Do not describe a candidate as "signed", "notarized" or "verified on Windows" until each is separately true.

## Build it

PyInstaller does not cross-compile. Build on the system you are building for: a Mac makes the Mac app and a Windows computer makes the Windows folder. A Mac cannot build `Resonance.exe`.

1. Install Python 3.11 that includes its own Tcl/Tk 8.6 or newer (the python.org installer does; some package-manager builds do not). Do not use Apple's system Python: it is linked to Apple's deprecated system Tcl/Tk 8.5, whose window can come up with blank labels, and the build tool refuses it. Check with `python3 -c "import tkinter; print(tkinter.TclVersion, tkinter.TkVersion)"`, which prints the versions without opening a window.
2. Install the build tools into that Python. This is the only step that uses a package index, and it is for the builder, never the end user:

   ```
   python3 -m pip install -r desktop/requirements-build.txt      # Windows: py -3 -m pip install -r desktop\requirements-build.txt
   ```

3. Make an audited export outside the working checkout. This commits nothing to your repository and pushes nothing:

   ```
   python3 tools/make_release.py --version "$(cat VERSION)" --out /path/to/runtime
   ```

4. Look at what will happen. `--plan` verifies the export and prints the native command without writing anything:

   ```
   python3 tools/build_desktop.py --runtime /path/to/runtime --out /path/to/candidate --plan
   ```

5. Build. `--out` must not exist yet:

   ```
   python3 tools/build_desktop.py --runtime /path/to/runtime --out /path/to/candidate
   ```

The tool refuses to run, and writes nothing, when the runtime is the source working checkout, has no valid `RELEASE-MANIFEST.json`, lists or contains a private or internal file, an unlisted file, a symbolic link or an unsafe path, or has any file whose size or SHA-256 differs from the manifest; and when `--out` already exists or is inside the runtime or the source checkout. It also stops if PyInstaller or tzdata is not the pinned version, Tk is missing, or the Tcl or the Tk this Python is linked to is older than 8.6.

Only verified files are copied into a temporary staging folder: the modules, `static/`, the practice seed in `examples/`, `VERSION`, `LICENSE`, `README.md`, `ASSISTANT.md` and the other public documents. The Python modules are frozen into the app, including the ones loaded by name at run time (`server`, which `samples.Workspace` imports for the store). The same source files also ship as data beside them, only because the assistant commands (`python3 monitor.py …`) run from the app's data root. Tests, tools, workflows and the staging folder are not shipped, and no private value is frozen into configuration. On a Mac the existing `static/icon.svg` is rendered to an icon with the repository's own `mac-launcher/render-icon.swift` (`--no-mac-icon` skips it). On Windows pass an existing `.ico` with `--icon`; there is no converter, so without one the default icon is used.

The result in `--out`:

- `dist/Resonance.app` (Mac) or `dist/Resonance/Resonance.exe` with its `_internal` folder (Windows)
- `Resonance-<version>-<mac|windows>-<architecture>-candidate.zip`, with paths preserved (made with `ditto` on Mac)
- `BUILD-MANIFEST.json`: source version, OS and architecture, Python, PyInstaller, tzdata, Tcl and Tk versions (so the toolkit a candidate was built with can be audited), the SHA-256 and size of every file in `dist/`, and the archive's SHA-256

Before it writes the archive, the tool runs the built program once with no window, against a throwaway synthetic data folder: `Resonance --self-test --data-dir <temp> --port 0` starts the server, requests the page from this computer, loads a time zone and checks the practice seed. The same command can be used by hand. It never opens a window or a browser.

## Use it

Unzip, then open `Resonance.app` (Mac) or `Resonance.exe` (Windows). The browser opens at `http://127.0.0.1:4317`. **Open Resonance** opens it again; **Quit** or closing the window stops it. Your data stays in the folder named in the README (`~/Library/Application Support/Resonance` on a Mac, `%LOCALAPPDATA%\Resonance` on Windows), outside the app, so replacing the app never touches it.

The launcher's own log is `~/Library/Logs/Resonance/launcher.log` on a Mac and `%LOCALAPPDATA%\Resonance Launcher\launcher.log` on Windows. It records short events (started, stopped, the kind of error) and never post text, account names, request bodies or credentials.

The assistant's `python3 monitor.py …` commands run with the assistant's own Python, from the app's internal data root, whose path is named in the prompt Resonance offers for your assistant. The packaged app itself needs no Python.

## CI

`.github/workflows/desktop-candidate.yml` is started by hand (`workflow_dispatch`) and nothing else. On `macos-latest` and `windows-latest` with Python 3.11 it installs the build tools, runs the Python suite and all seven Node suites, makes the allowlisted export (which runs the export's own suites), checks the plan, builds natively and uploads the archive and `BUILD-MANIFEST.json` as workflow artifacts for 14 days. It creates no release, uses no secrets or signing identity and publishes nothing. A green run is build evidence only, not physical Windows or Mac proof.
