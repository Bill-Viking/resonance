#!/usr/bin/env python3
"""Export an audited public file set into a fresh repository. Never push it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT_FILES = frozenset('''AGENTS.md ASSISTANT.md AUTHORING.md CHANGELOG.md INTEGRATIONS.md LICENSE
PRODUCT.md README.md SECURITY.md THIRD-PARTY-NOTICES.txt VERSION assistant_kit.py monitor.py policy.py reader_checks.py release.json
requirements.txt resonance resonance.bat samples.py server.py workflow.py .gitattributes .gitignore'''.split())
PUBLIC_DIRECTORIES = ('static/', 'examples/', 'tests/', 'mac-launcher/')
PUBLIC_DOCS = frozenset(('docs/ACTIONS.md', 'docs/DESKTOP.md', 'docs/MONITORING.md', 'docs/READER-PRIVACY.md',
                         'docs/SIGNING.md'))
REQUIRED_REVIEW_DOCS = frozenset(('docs/SAFE-WORK.md', 'docs/FACEBOOK-DISCOVERY.md'))
PUBLIC_DOCS |= REQUIRED_REVIEW_DOCS
# Reusable skills are public one file at a time; never expose a whole skill tree.
PUBLIC_SKILLS = frozenset(('skills/res-work-safe/SKILL.md', 'skills/res-work-safe/agents/openai.yaml',
                           'skills/res-work-safe/scripts/check_coverage.py',
                           'skills/res-work-safe/scripts/discover_facebook.py',
                           'skills/res-work-safe/scripts/capture_facebook_activity.js'))
# Listed one by one: a new tool or workflow is private until someone adds it here.
PUBLIC_DESKTOP = frozenset(('desktop/launch.py', 'desktop/requirements-build.txt', 'desktop/hooks/hook-workflow.py',
                            'desktop/build-targets.json', 'desktop/third-party-sources.json', 'desktop/locks/bootstrap.txt',
                            'desktop/locks/macos-universal2.txt', 'desktop/locks/windows-x86_64.txt'))
PUBLIC_TOOLS = frozenset(('tools/make_release.py', 'tools/build_desktop.py', 'tools/build_reader.py',
                          'tools/prepare_build.py', 'tools/sign_release.py',
                          '.github/workflows/checks.yml', '.github/workflows/desktop-candidate.yml',
                          '.github/workflows/desktop-sign.yml',
                          '.claude/skills/resonance/SKILL.md'))
REQUIRED_PUBLIC = ROOT_FILES | REQUIRED_REVIEW_DOCS | PUBLIC_SKILLS | frozenset((
    'tools/prepare_build.py', 'desktop/build-targets.json', 'desktop/locks/bootstrap.txt',
    'desktop/locks/macos-universal2.txt', 'desktop/locks/windows-x86_64.txt', 'desktop/third-party-sources.json',
    'tools/sign_release.py', 'docs/SIGNING.md', '.github/workflows/desktop-sign.yml',
))
# The production Resonance Reader package, one file at a time. A new
# file in browser-reader/ stays private until someone adds it here.
PUBLIC_READER = frozenset('browser-reader/' + name for name in '''README.md bridge.js manifest.json options.html
options.js popup.html popup.js reader.js shared.js worker.js icon-16.png icon-32.png icon-128.png'''.split())
# Every Node suite that must pass inside the exported tree. The reader suites run from synthetic
# fixtures that ship under tests/, never from experiments/.
NODE_SUITES = ('tests/test_ui_actions.cjs', 'tests/test_ui_communication.cjs', 'tests/test_ui_today.cjs',
               'tests/test_ui_reader.cjs', 'tests/test_browser_reader.cjs', 'tests/test_reader_discovery.cjs',
               'tests/test_reader_pipeline.cjs')


def public_path(name):
    path = Path(name)
    return (not path.is_absolute() and '..' not in path.parts and
            (name in ROOT_FILES or name in PUBLIC_DOCS or name in PUBLIC_SKILLS or name in PUBLIC_TOOLS or name in PUBLIC_DESKTOP or
             name in PUBLIC_READER or name.startswith(PUBLIC_DIRECTORIES)))


def validation_commands():
    return ([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], *(['node', suite] for suite in NODE_SUITES))


RELEASE_NAME = 'Resonance contributors'
RELEASE_EMAIL = 'contributors@example.invalid'
MANIFEST_NAME = 'RELEASE-MANIFEST.json'


def git(root, *args, input=None):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM='1')
    return subprocess.check_output(
        ['git', '-C', str(root), '-c', 'core.fsmonitor=false', *args],
        env=env, input=input, timeout=30,
    )


def release_git(root, *args, input=None):
    return git(root, '-c', f'core.hooksPath={os.devnull}',
               '-c', f'core.excludesFile={os.devnull}', '-c', f'core.attributesFile={os.devnull}',
               '-c', f'user.name={RELEASE_NAME}', '-c', f'user.email={RELEASE_EMAIL}',
               '-c', 'commit.gpgsign=false', '-c', 'tag.gpgsign=false',
               '--literal-pathspecs', *args, input=input)


def tree_entries(root, revision='HEAD'):
    for entry in filter(None, git(root, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0')):
        metadata, raw_name = entry.split(b'\t', 1)
        mode, kind, _ = metadata.split()
        yield raw_name.decode('utf-8'), mode, kind


def export(root, output, version):
    output, files = _export(root, output, version)
    return output, len(files) - 1


def _export(root, output, version):
    root, output = Path(root).resolve(), Path(output).absolute()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?', version):
        raise ValueError('Use a release version such as 0.1.0-preview.1.')
    if git(root, 'status', '--porcelain', '--untracked-files=all').strip():
        raise ValueError('Commit the reviewed tree before making a release.')
    if output.exists() or output.is_symlink():
        raise ValueError('The release output must be a new folder.')
    if output.resolve().is_relative_to(root):
        raise ValueError('Put the release outside the source repository.')
    revision = git(root, 'rev-parse', '--verify', 'HEAD').decode('ascii').strip()
    files = {}
    for name, mode, kind in tree_entries(root, revision):
        if not public_path(name):
            continue
        if kind != b'blob' or mode not in (b'100644', b'100755'):
            raise ValueError('Release files must be ordinary files: ' + name)
        files[name] = (git(root, 'show', revision + ':' + name), mode == b'100755')
    missing = REQUIRED_PUBLIC - files.keys()
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
    manifest_bytes = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    manifest_path = output / MANIFEST_NAME
    manifest_path.write_bytes(manifest_bytes)
    manifest_path.chmod(0o644)
    files[MANIFEST_NAME] = (manifest_bytes, False)
    return output, files


def verify_commit(output, expected):
    revision = git(output, 'rev-parse', '--verify', 'HEAD').decode('ascii').strip()
    if git(output, 'show', '-s', '--format=%P', revision).strip():
        raise ValueError('The export commit must not have a parent.')
    actual = {name: (mode, kind) for name, mode, kind in tree_entries(output, revision)}
    if actual.keys() != expected.keys():
        extra = ', '.join(sorted(actual.keys() - expected.keys())) or 'none'
        missing = ', '.join(sorted(expected.keys() - actual.keys())) or 'none'
        raise ValueError(f'The committed export does not match the manifest (extra: {extra}; missing: {missing}).')
    for name, (content, executable) in expected.items():
        mode = b'100755' if executable else b'100644'
        if actual[name] != (mode, b'blob') or git(output, 'show', revision + ':' + name) != content:
            raise ValueError('The committed export bytes or mode differ: ' + name)
    identity = git(output, 'show', '-s', '--format=%an%x00%ae%x00%cn%x00%ce', revision).rstrip(b'\n').split(b'\0')
    if identity != [RELEASE_NAME.encode(), RELEASE_EMAIL.encode()] * 2:
        raise ValueError('The export commit does not have the synthetic author and committer.')
    return revision


def nul_paths(names):
    return b''.join(name.encode('utf-8') + b'\0' for name in names)


def commit_export(output, version, expected):
    if (output / '.git').exists() or (output / '.git').is_symlink():
        raise ValueError('The export repository must be freshly initialized.')
    release_git(output, 'init', '-b', 'main', '--template=')
    release_git(output, 'add', '--force', '--pathspec-from-file=-', '--pathspec-file-nul',
                input=nul_paths(sorted(expected)))
    # Preserve source Git modes even on hosts with no executable mode on disk.
    for executable in (False, True):
        names = [name for name, (_, flag) in expected.items() if flag == executable]
        if names:
            release_git(output, 'update-index', '--chmod=' + ('+x' if executable else '-x'),
                        '-z', '--stdin', input=nul_paths(names))
    release_git(output, 'commit', '-m', 'Resonance ' + version + ' source preview')
    verified = verify_commit(output, expected)
    release_git(output, 'tag', 'v' + version, verified)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        output, expected = _export(root, args.out, args.version)
        # Run only the exported code and its synthetic temporary fixtures.
        for command in validation_commands():
            # The full Python suite takes about seven minutes on native Windows; every other command keeps five.
            limit = 900 if sys.platform == 'win32' and command[0] == sys.executable else 300
            subprocess.run(command, cwd=output, stdin=subprocess.DEVNULL, check=True, timeout=limit)
        commit_export(output, args.version, expected)
        print(f'Release prepared: {output} ({len(expected) - 1} public files; no remote or push).')
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\nRelease was not published. Preserve any output for inspection.\n')


if __name__ == '__main__':
    main()
