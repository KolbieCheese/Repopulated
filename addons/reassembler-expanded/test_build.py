import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import build


class DataTests(unittest.TestCase):
    def test_comments_translation_and_optional_commas(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'blocks.lua'
            path.write_text('{ -- comment\n {17000, name=_("a # tag")_("b") features=PALETTE|GENERATOR, powerCapacity=30}, }')
            parsed = build.records(path)[0]
            self.assertEqual(build.get(parsed, 'name'), '_("a # tag")_("b")')
            self.assertEqual(build.get(parsed, 'features'), 'PALETTE|GENERATOR')
            self.assertEqual(build.parse(build.emit(parsed)), parsed)

    def test_bad_dimensions_and_fractional_points_rejected(self):
        config = json.loads((build.ROOT / 'config.json').read_text())
        config['reactor']['width'] = 81
        with self.assertRaises(ValueError):
            build.validate(config)
        config['reactor']['width'] = 80
        config['reactor']['points'] = 3000.5
        with self.assertRaises(ValueError):
            build.validate(config)


class InstalledSourceTests(unittest.TestCase):
    def test_inheritance_shapes_hidden_parts_and_stable_ids(self):
        workshop = Path('D:/SteamLibrary/steamapps/workshop/content/329130')
        log = Path.home() / 'Saved Games/Reassembly/data/log_latest.txt'
        if not workshop.exists() or not log.exists():
            self.skipTest('Installed sources unavailable')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'generated'
            config_path = Path(folder) / 'config.json'
            config = json.loads((build.ROOT / 'config.json').read_text())
            config_path.write_text(json.dumps(config))
            with contextlib.redirect_stdout(io.StringIO()):
                build.build(config_path, workshop, log, output)
            registry = json.loads((output / 'registry.json').read_text())
            blocks = {build.number(b[0][1]): b for b in build.records(output / 'blocks.lua')}
            self.assertEqual(len(blocks), 1551)
            inherited = blocks[registry['blocks']['818042635:2000203']]
            self.assertEqual(build.get(inherited, 'shape'), 'SQUARE')
            self.assertEqual(build.get(inherited, 'scale'), '2')
            self.assertIsNone(build.get(inherited, 'extends'))
            core = blocks[registry['blocks']['818042635:2000201']]
            self.assertIn('NOPALETTE', build.get(core, 'features'))
            launcher = blocks[registry['blocks']['3240818467:153']]
            replica = build.get(launcher, 'replicateBlock')
            self.assertIsInstance(replica, list)
            shapes = {build.number(s[0][1]): s for s in build.records(output / 'shapes.lua')}
            mirror = shapes[registry['shapes']['818042635:100706']]
            self.assertEqual(build.number(build.get(mirror, 'mirror_of')), registry['shapes']['818042635:100705'])
            config['sourceWorkshopIds'].reverse()
            config['reactor']['generation'] = 12000
            config_path.write_text(json.dumps(config))
            with contextlib.redirect_stdout(io.StringIO()):
                build.build(config_path, workshop, log, output)
            self.assertEqual(registry, json.loads((output / 'registry.json').read_text()))
            reactor = build.records(output / 'blocks.lua')[0]
            self.assertEqual(build.get(reactor, 'generatorCapacityPerSec'), '12000')


if __name__ == '__main__':
    unittest.main()
