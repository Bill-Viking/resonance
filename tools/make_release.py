#!/usr/bin/env python3
"""Export an audited public file set into a fresh repository. Never push it."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


ROOT_FILES = frozenset('''AGENTS.md ASSISTANT.md CHANGELOG.md INTEGRATIONS.md LICENSE
PRODUCT.md README.md SECURITY.md VERSION monitor.py policy.py release.json
requirements.txt resonance resonance.bat samples.py server.py workflow.py .gitignore'''.split())
PUBLIC_DIRECTORIES = ('static/', 'examples/', 'tests/', 'mac-launcher/')
PUBLIC_DOCS = frozenset(('docs/ACTIONS.md', 'docs/MONITORING.md'))
PUBLIC_TOOLS = frozenset(('tools/make_release.py', '.github/workflows/checks.yml',
                          '.claude/skills/resonance/SKILL.md'))


def public_path(name):
    path = Path(name)
    return (not path.is_absolute() and '..' not in path.parts and
            (name in ROOT_FILES or name in PUBLIC_DOCS or name in PUBLIC_TOOLS or
             name.startswith(PUBLIC_DIRECTORIES)))


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def export(root, output, version):
    root, output = Path(root).resolve(), Path(output).absolute()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-z0-9.-]+)?', version):
        raise ValueError('Use a release version such as 0.1.0-preview.1.')
    if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('Commit the reviewed tree before making a release.')
    if output.exists() or output.is_symlink():
        raise ValueError('The release output must be a new folder.')
    if output.resolve().is_relative_to(root):
        raise ValueError('Put the release outside the source repository.')
    entries = git(root, 'ls-tree', '-rz', '--full-tree', 'HEAD').split(b'\0')
    files = {}
    for entry in filter(None, entries):
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, _ = metadata.split()
        name = raw_name.decode('utf-8')
        if not public_path(name):
            continue
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('Release files must be ordinary files: ' + name)
        files[name] = (git(root, 'show', 'HEAD:' + name), mode == b'100755')
    missing = ROOT_FILES - files.keys()
    if missing:
        raise ValueError('Missing public files: ' + ', '.join(sorted(missing)))
    if files['VERSION'][0].decode().strip() != version:
        raise ValueError('The requested version must match the committed VERSION file.')
    # Scan the chosen blobs before creating an output. No private pattern file or
    # user data is read. A hit needs human inspection, never automatic rewriting.
    private_markers = re.compile(rb'/Users/(?!example(?:/|\b))[^\s/"\'<>]+|'
                                 rb'C:\\{1,2}Users\\{1,2}(?!example(?:\\|\b))[^\s\\"\'<>]+')
    for name, (content, _) in files.items():
        if name == 'tools/make_release.py':
            continue  # This scanner necessarily contains its own forbidden words.
        if private_markers.search(content):
            raise ValueError('Inspect a private-system marker before release: ' + name)
    output.mkdir(parents=True)
    manifest = {'version': version, 'files': {}}
    for name, (content, executable) in sorted(files.items()):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(0o755 if executable else 0o644)
        manifest['files'][name] = {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    (output / 'RELEASE-MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return output, len(files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        output, count = export(root, args.out, args.version)
        # Run only the exported code and its synthetic temporary fixtures.
        for command in ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'],
                        ['node', 'tests/test_ui_actions.cjs'],
                        ['node', 'tests/test_ui_communication.cjs'],
                        ['node', 'tests/test_ui_today.cjs']):
            subprocess.run(command, cwd=output, check=True)
        git(output, 'init', '-b', 'main')
        git(output, 'config', 'user.name', 'Resonance contributors')
        git(output, 'config', 'user.email', 'contributors@example.invalid')
        git(output, 'config', 'commit.gpgsign', 'false')
        git(output, 'config', 'tag.gpgsign', 'false')
        git(output, 'add', '.')
        git(output, 'commit', '-m', 'Resonance ' + args.version + ' source preview')
        git(output, 'tag', 'v' + args.version)
        print(f'Release prepared: {output} ({count} public files; no remote or push).')
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, str(error) + '\nRelease was not published. Preserve any output for inspection.\n')


if __name__ == '__main__':
    main()
