#!/usr/bin/env python3
"""Publish only public browser assets, then verify their delivery before switching Next."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit, quote
from urllib.request import Request, urlopen


def release_settings(slot):
    config = json.loads((slot / '.next/required-server-files.json').read_text())['config']
    prefix = config.get('assetPrefix', '')
    if not prefix:
        return None
    url = urlsplit(prefix)
    release = (slot / '.cdn-release').read_text().strip()
    if (not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}', release)
            or url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.port or url.query or url.fragment or url.path != '/releases/' + release):
        raise ValueError('invalid built CDN prefix or release identifier')
    return prefix, release


def inventory(directory):
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError('missing or symlinked asset directory')
    files = []
    for parent, dirs, names in os.walk(directory, followlinks=False):
        for name in dirs + names:
            if (Path(parent) / name).is_symlink():
                raise ValueError('asset symlinks are not allowed')
        dirs[:] = [name for name in dirs if not name.startswith('.')]
        for name in sorted(names):
            # Source maps may include source code. Hidden files are never public.
            if name.startswith('.') or name.endswith('.map'):
                continue
            path = Path(parent) / name
            if not path.is_file():
                raise ValueError('asset must be a regular file')
            files.append(path.relative_to(directory))
    return sorted(files)


def publish(slot, root):
    settings = release_settings(slot)
    if settings is None:
        return None
    prefix, release = settings
    sources = [(slot / '.next/static', Path('_next/static')), (slot / 'public', Path('public'))]
    entries = [(source, dest, inventory(source)) for source, dest in sources]
    if not entries[0][2] or not entries[1][2]:
        raise ValueError('empty browser asset set')
    releases = root / 'releases'
    if root.is_symlink() or releases.is_symlink():
        raise ValueError('asset root must not be a symlink')
    releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    target = releases / release
    if target.exists() or target.is_symlink():
        raise ValueError('release already exists; immutable assets cannot be overwritten')
    stage = Path(tempfile.mkdtemp(prefix='.pending-', dir=releases))
    try:
        for source, destination, files in entries:
            for relative in files:
                output = stage / destination / relative
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                shutil.copyfile(source / relative, output)
                output.chmod(0o644)
        stage.chmod(0o755)
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    # Prefer small real JS and images; verify both exported directories.
    samples = []
    for source, destination, files in entries:
        preferred = [f for f in files if f.suffix in ('.js', '.webp', '.png', '.svg')]
        relative = min(preferred or files, key=lambda f: (source / f).stat().st_size)
        samples.append(destination / relative)
    return prefix, target, samples


def verify(prefix, target, samples):
    for relative in samples:
        expected = (target / relative).read_bytes()
        request = Request(prefix + '/' + quote(relative.as_posix()), headers={'Accept-Encoding': 'identity'})
        with urlopen(request, timeout=30) as response:
            # Bound responses even when a misconfigured origin returns unexpected content.
            actual = response.read(len(expected) + 1)
            if response.status != 200 or hashlib.sha256(actual).digest() != hashlib.sha256(expected).digest():
                raise ValueError('CDN returned incorrect browser asset bytes')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slot', required=True, type=Path)
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    result = publish(args.slot, args.root)
    if result is None:
        print('CDN disabled in built Next config; no assets published')
        return
    verify(*result)
    print('CDN assets published and verified: ' + result[0])


if __name__ == '__main__':
    main()
