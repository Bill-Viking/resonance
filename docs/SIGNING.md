# Desktop signing preparation

Use this only on an already built and reviewed desktop candidate. The tool does
not build, install dependencies, obtain credentials or publish anything.
The commands below describe the staged path; native signing, notarization and
fresh-user acceptance have yet to be proven. Source and synthetic test results
do not authorize a consumer release.

Use a dedicated signing account or disposable native runner, a clean Python
3.9+ installation, and a private local parent directory for the new output.
Use a reviewed copy of this tool outside the artifact. Keep the candidate,
checkout and staging folder under the release operator's exclusive control.
Digest checks protect the handoff between phases; they are not a sandbox for
an untrusted executable or protection from a hostile local account. Run
Python with `-I -B` from a clean environment. The tool passes only a small
allowlist of environment variables to native commands and the candidate.

## 1. Verify the reviewed input

Set `OUT` to a **new** folder under an existing private parent, `ARCHIVE` to
the native candidate ZIP, and `BUILD_MANIFEST` to its `BUILD-MANIFEST.json`.
Set `ARCHIVE_SHA` and `BUILD_MANIFEST_SHA` to the independently reviewed,
lowercase SHA-256 digests. Do not use hashes supplied by the artifact itself
as your expected values. On macOS use the build-origin ZIP; the tool refuses
quarantined input and leaves quarantine intact.

```sh
python3 -I -B tools/sign_release.py verify --out "$OUT" \
  --archive "$ARCHIVE" --manifest "$BUILD_MANIFEST" \
  --archive-sha256 "$ARCHIVE_SHA" --manifest-sha256 "$BUILD_MANIFEST_SHA"
```

Each successful state-changing command prints one SHA-256 checkpoint. Keep it separately
from the staging directory and pass it as `--state-sha256` to the next phase
(`CHECKPOINT` in the examples). Advance only after a zero exit status.
Never recompute an expected checkpoint from a changed state file. A failed
native phase may have changed the staged tree; use a fresh output and the
original reviewed candidate if that happens. Check an existing Apple
submission before submitting again after a notary timeout.

Verification checks archive metadata, the launcher architecture and the
complete declared file and link inventories before creating the output.
The build manifest must record the canonical regular `THIRD-PARTY-NOTICES.txt`
file, its byte count and SHA-256. Its bytes must agree with the inventory and
the actual tree at each phase. An alias or symlink alone does not count.
The ZIP limit is 256 MiB compressed, 768 MiB expanded and 20,000 entries.
A Mac ZIP may contain conventional `ditto` AppleDouble records for the host
provenance attribute. The same policy applies to both internal workflow
carriers and the ZIPs created for notarization and final delivery. The tool
rejects unsupported metadata before native extraction or Apple submission,
including quarantine, unknown attributes and resource forks. It leaves
quarantine intact. Safe relative app links and executable bits are preserved.
If a real staple needs metadata outside this policy, finalization stops for
native review. Windows links are rejected.

On macOS, bundle directories use mode `0755` and regular files use `0644` or
`0755` according to their executable classification. Signing and stapling
apply these permissions before recording their checkpoints. The checkpoints
bind the modes, and finalization verifies them again in the unpacked ZIP.
The hosted restores validate bundle modes before extraction. Staging outside
the bundle and temporary signing secrets retain their private permissions.

## 2. Sign and verify without running the app

On native macOS, use an already provisioned, disposable signing keychain
with a Developer ID Application identity. Set `SIGNING_KEYCHAIN` to its
absolute, resolved `.keychain-db` path outside the stage, and `IDENTITY` to the
public 40-hex certificate thumbprint. No password, private key file or PFX
is a CLI argument.

```sh
python3 -I -B tools/sign_release.py sign --out "$OUT" --state-sha256 "$CHECKPOINT" \
  --identity "$IDENTITY" --keychain "$SIGNING_KEYCHAIN"
```

Save the SHA-256 printed by `sign` as `SIGN_CHECKPOINT` separately from the
stage. Keep it through notarization, even after self-test prints a new checkpoint.

The tool signs nested Mach-O code and containers from the inside out, then
the app, with hardened runtime and a secure timestamp. It verifies Developer
ID, the exact certificate leaf thumbprint, one Apple Team ID across all signing
targets, timestamp and hardened runtime. It also verifies the app with
`codesign --verify --deep --strict`. It adds no entitlements. A framework
must have one conventional version, a real versioned binary and the usual
`Current` and root binary links. Any other layout stops before signing.
Native acceptance of the actual frozen Python and Tcl/Tk layout is still
required.

On native Windows x64, use a certificate already installed in the
isolated account's Personal store and the installed Windows SDK x64
`signtool.exe`. In PowerShell, set `$out`, `$archive`, `$buildManifest`,
`$archiveSha`, `$buildManifestSha` and `$checkpoint` to the corresponding
values above. Set `$identity` to the certificate thumbprint, `$signtool` to
the absolute SDK path and `$timestampUrl` to a public HTTPS RFC 3161
timestamp URL. The tool checks SignTool against the registered SDK
installation and never searches the artifact or downloads an SDK.

```powershell
py -3 -I -B tools/sign_release.py verify --out $out --archive $archive --manifest $buildManifest --archive-sha256 $archiveSha --manifest-sha256 $buildManifestSha
# Set $checkpoint to the SHA-256 printed by verify before running sign.
py -3 -I -B tools/sign_release.py sign --out $out --state-sha256 $checkpoint --identity $identity --signtool $signtool --timestamp-url $timestampUrl
```

SignTool signs the PE files with SHA-256 and requests an RFC 3161 SHA-256
timestamp. Verification uses the Authenticode policy, checks all signatures
and rejects timestamp warnings. No PFX import is performed by this tool.

### Azure Artifact Signing preflight (Windows, qualification only)

`azure-preflight` is the first step toward a Windows Azure signing route. It
is **not** a signing route yet: `sign` still uses only the installed
certificate above, and nothing here signs, calls Azure or the network, reads a
credential, or runs a candidate. It checks, on native x64 Windows, the exact
policy and installed toolchain that a later live dispatch would use, and
exits non-zero on the first failure.

The private policy and the toolchain lock stay outside the candidate and its
`--out` stage. The reviewer computes the policy's SHA-256 independently and
passes it in; a digest printed by the artifact or the same job is not
independent. The policy carries the lock's digest, so one independent value
authenticates both files and every pinned tool file.

```json
{"schema": 1, "endpoint": "https://eus.codesigning.azure.net",
 "account": "example-account", "profile": "example-profile",
 "expected_profile_eku": "1.3.6.1.4.1.311.97.1.2.3",
 "timestamp_url": "https://tsa.example.test/rfc3161",
 "toolchain_sha256": "<SHA-256 of the lock file>"}
```

```json
{"schema": 1, "signtool": {"version": "10.0.12345.0", "sha256": "<SignTool SHA-256>"},
 "dlib": {"files": {"Azure.CodeSigning.Dlib.dll": "<SHA-256>", "sub/Dependency.dll": "<SHA-256>"}}}
```

All values above are synthetic. The policy must have exactly these keys. The
endpoint is an `https://<region>.codesigning.azure.net` origin with no port,
path or credentials. The EKU is the profile-specific OID under
`1.3.6.1.4.1.311.97`. Use the value authenticated for this particular Public
Trust profile. The common Public Trust marker and the Private Trust identity
and CI policy branches cannot identify that profile. The timestamp URL must
use HTTPS. Microsoft's documented timestamp URL uses HTTP, so it
is refused until an approved HTTPS RFC 3161 endpoint exists. The lock lists
every file of the Dlib folder; an extra, missing or changed file fails.

```powershell
# Set $policyDigest to the reviewer's separately recorded lowercase SHA-256.
py -3 -I -B tools/sign_release.py azure-preflight --out $out `
  --azure-policy $policy --azure-policy-sha256 $policyDigest --toolchain-lock $lock `
  --signtool $signtool --dlib-dir $dlibDir
```

SignTool must pass
the same installed-SDK check as `sign` (registered Windows Kits root, `x64`)
and match the lock's version and SHA-256. It and the Dlib DLL must be x64 PE
files. The Dlib folder must be an absolute resolved path with no links.

On success it prints what was verified (digests, SignTool version and the
Dlib file set) and what was not. It never prints the endpoint, account,
profile, EKU or timestamp URL. It does **not** verify that the account or
profile exists, that the EKU is the profile's real EKU, workload identity
federation, timestamp service behavior, the Dlib credential chain, or any
signature. The result is a point-in-time check; a later sign phase must
repeat it. The lock's real package pins are still to be established and
reviewed. Without them, no live signing may be dispatched.

## 3. Remove signing access and run the signed self-test

Remove the temporary Mac signing keychain and its identity, or remove the
Windows certificate and access to its private key. Remove any PFX or other
secret material from the test account and clear secret-bearing environment
variables. Keep notary credentials out of the account until this phase has
finished. The flag attests to those conditions; the tool also checks the
Mac keychain path and visible identities, or the Windows user and machine
Personal stores. Those checks do not prove the absence of unrelated
credentials or hardware/provider access.

On Mac, before executing the candidate, use the checkpoint printed by
`sign` to read the public anchor after cleanup:

```sh
python3 -I -B tools/sign_release.py signed-anchor --out "$OUT" --state-sha256 "$SIGN_CHECKPOINT"
```

Keep the JSON outside the stage, under the trusted signing operator's control.
Its `tree_sha256`, `identity` and `team_id` are the signed tree hash, the
40-hex certificate leaf thumbprint and the 10-character Apple Team ID.
Retain those values alongside `SIGN_CHECKPOINT`. Do not derive any of them
from the self-tested stage.

```sh
python3 -I -B tools/sign_release.py self-test --out "$OUT" --state-sha256 "$SIGN_CHECKPOINT" \
  --credentials-removed
```

```powershell
py -3 -I -B tools/sign_release.py self-test --out $out --state-sha256 $checkpoint --credentials-removed
```

Only this command executes the candidate. It first verifies the signatures
again, then runs `--self-test --data-dir TEMP --log-dir TEMP --self-test-report TEMP --port 0`
with separate disposable paths, no window, and a two-minute limit. It requires
the expected version, the successful frozen-app, seed and assistant-kit
results, and a headless Tcl result (`tcl: true` and a numeric `N.N.N`
`tcl_patch`). The Tcl interpreter probe does not open Tk or exercise a screen
reader. A change to the signed tree stops the process.

## 4. Notarize on macOS, then finalize

Set `SIGNED_TREE_SHA`, `SIGNING_IDENTITY` and `SIGNING_TEAM_ID` from the
independent anchor you saved after signing, and `CHECKPOINT` from self-test.
Keep `SIGN_CHECKPOINT` at the original value printed by `sign`. The original
checkpoint binds the version, source digests and all immutable signing evidence
across the job that runs the app. The original signed and self-tested tree hashes
and the current tree must match the SIGN tree hash. The original leaf and Team ID
must also match; signature checks enforce both.

Run this read-only check before provisioning notary credentials:

```sh
python3 -I -B tools/sign_release.py check-notarize --out "$OUT" --state-sha256 "$CHECKPOINT" \
  --expected-sign-state-sha256 "$SIGN_CHECKPOINT" \
  --expected-signed-tree-sha256 "$SIGNED_TREE_SHA" --expected-identity "$SIGNING_IDENTITY" \
  --expected-team-id "$SIGNING_TEAM_ID"
```

After the check succeeds, make a separately provisioned `notarytool`
keychain profile available in the dedicated account. Set `NOTARY_PROFILE`
to its public profile name. To use a profile stored in a disposable keychain,
also pass `--notary-keychain "$NOTARY_KEYCHAIN"` with its absolute, resolved
`.keychain-db` path outside the stage. This selects the keychain for submission
without changing the default. The tool submits a ZIP of the signed app, waits
at most 12 minutes at Apple, requires an `Accepted` response and submission
ID, then staples and validates the ticket and rechecks the signatures.
It parses notary JSON from stdout alone and does not print native command
output or retrieve a notary log.

The `notarize` command repeats the checkpoint and anchor checks before any
native command.

```sh
python3 -I -B tools/sign_release.py notarize --out "$OUT" --state-sha256 "$CHECKPOINT" \
  --expected-sign-state-sha256 "$SIGN_CHECKPOINT" \
  --expected-signed-tree-sha256 "$SIGNED_TREE_SHA" --expected-identity "$SIGNING_IDENTITY" \
  --expected-team-id "$SIGNING_TEAM_ID" \
  --keychain-profile "$NOTARY_PROFILE"
# Set CHECKPOINT to the SHA-256 printed by notarize before finalizing.
python3 -I -B tools/sign_release.py finalize --out "$OUT" --state-sha256 "$CHECKPOINT"
```

On Windows, set `$checkpoint` to the self-test checkpoint and finalize
directly:

```powershell
py -3 -I -B tools/sign_release.py finalize --out $out --state-sha256 $checkpoint
```

Use only the ZIP and `RELEASE-BUILD-MANIFEST.json` under `final/` for the
next review. The tool recreates the archive, verifies its contents, and on
Mac reopens it with `ditto` and rechecks the extracted signatures and
stapled ticket. The final manifest records final file/link and ZIP hashes,
the signed self-test report, original candidate ZIP and build-manifest
digests, the source runtime manifest digest, the notices evidence, and
evidence for each stage. On Mac, finalization rechecks the original SIGN
checkpoint using the pre-staple signed tree hash. The notarized and final evidence
retain the checkpoint and anchor separately from the stapled tree hash.
It does not reuse unsigned self-test results or hash the unsigned tree as
the final tree.

## Automation and release gates

`desktop-sign.yml` prepares a **manual Mac signing workflow** for a private
repository. Restrict repository and artifact access to the release operators.
Before dispatch, review the exact workflow source and candidate. Protect the
default branch, require reviewers for both environments below, and restrict
both environments to that branch. Store each credential in its own environment.
This preparation did not configure any environment or credentials.

1. **`resonance-signing`.** The job checks the protected default branch and
   exact workflow SHA, a successful manual candidate run in the same repository
   at that SHA, the explicit artifact name, and both independently reviewed
   digests. The signing step uses environment secrets
   `MAC_CERTIFICATE_P12_BASE64` and `MAC_CERTIFICATE_PASSWORD`, and the public
   variable `MAC_SIGNING_IDENTITY` (40-hex Developer ID Application thumbprint).
   It decodes the P12 in a private directory and passes the password to OpenSSL
   by environment. The key and certificate go into a disposable blank-password
   keychain. After cleanup, the job exports the public SIGN anchor from the
   validated signing checkpoint and uploads the internal stage ZIP.
2. **Signed self-test.** A fresh native runner with no protected environment
   or signing or notary secrets verifies the ZIP digest from the signing job
   before extraction. It uses the signing checkpoint and confirms signing
   access is gone before running the candidate. It returns a new stage ZIP and
   checkpoint. This is the only job that executes the candidate.
3. **`resonance-notarization`.** After self-test succeeds, a separate native
   runner checks the new stage ZIP before extracting it. It compares the
   restored state and tree with the original checkpoint and anchor from the SIGN
   job before provisioning any notary credentials, and repeats the check in the
   notarize phase.
   The notarization step uses only the
   environment secret `MAC_NOTARY_KEY_P8_BASE64` and public variables
   `MAC_NOTARY_KEY_ID` and `MAC_NOTARY_ISSUER` for the owner's App Store
   Connect team API key. It validates and stores a profile in an explicit
   temporary keychain. After notarization and cleanup, it finalizes and
   uploads only the final ZIP and `RELEASE-BUILD-MANIFEST.json`. If a
   submission fails or times out, inspect the original Apple submission
   before attempting another one.

All three jobs use the same native architecture and the signing tool from
the exact workflow commit without persisting Git credentials. The workflow
runs no repository tests, dependency installs, or app build. Both credential
jobs restore the original user keychain search list immediately after
creating their disposable keychain, leave the default keychain alone, and
clean up the temporary keychain and files before upload. Internal stage
artifacts expire after one day; final workflow evidence expires after three
days. There is no release publication.

Local synthetic trials proved P12 extraction with an environment-only
password, disposable keychain import, local storage of a synthetic API key
profile without validation, and keychain removal and search list restoration.
They did not prove Developer ID signing, hardened runtime, Apple API
authentication or submission, or behavior on GitHub-hosted runners. The
workflow has not run on GitHub. Validate the native and hosted path, including
failure and cancellation cleanup, before relying on it. Review the candidate
before signing; the checkpoint chain is not a sandbox for hostile code.

Windows remains a local installed-certificate and Windows SDK SignTool
route, pending native proof. `azure-preflight` qualifies Azure inputs only.

Native proof still includes actual Developer ID and Authenticode signing,
successful macOS notarization and stapling, a quarantined first-user
launch with Gatekeeper intact, Windows SmartScreen and Defender, both
supported Mac architectures, and the browser and physical first-user checks.

References: [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution),
[Apple distribution signing](https://developer.apple.com/documentation/xcode/creating-distribution-signed-code-for-the-mac),
and [PyInstaller feature notes](https://pyinstaller.org/en/stable/feature-notes.html).
