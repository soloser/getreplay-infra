"""Real filesystem publication and HTTP probes, without production services."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from io import BytesIO

spec = importlib.util.spec_from_file_location('publish_assets', Path(__file__).resolve().parents[1] / 'publish-assets.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class PublishTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.slot = self.root / 'slot'
        self.assets = self.root / 'assets'
        for directory in ['.next/static/chunks', '.next/server', 'public/maps']:
            (self.slot / directory).mkdir(parents=True)
        self.config('https://static.getreplay.gg/releases/release1')
        (self.slot / '.cdn-release').write_text('release1')
        for name, content in {
            '.next/static/chunks/app.js': 'console.log(1)',
            '.next/static/chunks/app.js.map': 'PRIVATE SOURCE',
            '.next/server/private.js': 'SERVER ONLY',
            '.env.production': 'SECRET',
            'public/maps/map.png': 'IMAGE BYTES',
            'public/.env': 'HIDDEN',
        }.items():
            (self.slot / name).write_text(content)

    def config(self, prefix):
        (self.slot / '.next/required-server-files.json').write_text(json.dumps({'config': {'assetPrefix': prefix}}))

    def test_disabled_is_noop(self):
        self.config('')
        self.assertIsNone(publisher.publish(self.slot, self.assets))
        self.assertFalse(self.assets.exists())

    def test_publishes_only_browser_files_and_keeps_previous_release(self):
        previous = self.assets / 'releases/old/public'
        previous.mkdir(parents=True)
        (previous / 'file.png').write_text('old')
        prefix, target, samples = publisher.publish(self.slot, self.assets)
        self.assertEqual('https://static.getreplay.gg/releases/release1', prefix)
        self.assertEqual({'_next/static/chunks/app.js', 'public/maps/map.png'}, {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()})
        self.assertEqual('old', (previous / 'file.png').read_text())
        self.assertEqual(2, len(samples))
        self.assertEqual(0o755, target.stat().st_mode & 0o777)

    def test_immutable_release_cannot_be_replaced(self):
        publisher.publish(self.slot, self.assets)
        (self.slot / '.next/static/chunks/app.js').write_text('CHANGED')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            publisher.publish(self.slot, self.assets)
        self.assertEqual('console.log(1)', (self.assets / 'releases/release1/_next/static/chunks/app.js').read_text())

    def test_symlinks_and_bad_release_fail_before_publication(self):
        for name in ['public/link', '.next/static/link']:
            link = self.slot / name
            link.symlink_to(self.slot / '.env.production')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                publisher.publish(self.slot, self.assets)
            link.unlink()
        (self.slot / '.cdn-release').write_text('../other')
        with self.assertRaises(ValueError):
            publisher.publish(self.slot, self.assets)
        self.assertFalse(self.assets.exists())

    def test_partial_copy_never_exposes_a_release(self):
        with patch.object(publisher.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                publisher.publish(self.slot, self.assets)
        self.assertEqual([], list((self.assets / 'releases').iterdir()))

    def test_probe_requires_correct_bytes_from_both_paths(self):
        prefix, target, samples = publisher.publish(self.slot, self.assets)
        def response(request, timeout):
            self.assertEqual(30, timeout)
            relative = request.full_url.removeprefix(prefix + '/')
            stream = BytesIO((target / relative).read_bytes())
            stream.status = 200
            return stream
        with patch.object(publisher, 'urlopen', side_effect=response) as request:
            publisher.verify(prefix, target, samples)
            self.assertEqual(2, request.call_count)
        wrong = BytesIO(b'HTML fallback'); wrong.status = 200
        with patch.object(publisher, 'urlopen', return_value=wrong):
            with self.assertRaisesRegex(ValueError, 'incorrect'):
                publisher.verify(prefix, target, samples)


if __name__ == '__main__':
    unittest.main()
