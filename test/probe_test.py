import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('inspect_install', Path(__file__).resolve().parents[1] / 'tools' / 'inspect_install.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_named_exports_use_name_table_not_function_table(self):
        # Independent tiny PE64 image: functions and names intentionally differ.
        data = bytearray(0x1000)
        data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 0x80)
        data[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<HH', data, 0x84, 0x8664, 1)
        struct.pack_into('<H', data, 0x94, 240)
        optional = 0x98
        struct.pack_into('<H', data, optional, 0x20b)
        struct.pack_into('<II', data, optional + 112, 0x1000, 100)
        section = optional + 240
        struct.pack_into('<IIII', data, section + 8, 0x800, 0x1000, 0x800, 0x400)
        struct.pack_into('<III', data, 0x400 + 24, 1, 0x1100, 0x1120)
        struct.pack_into('<I', data, 0x500, 0x1300)  # function RVA, not an export name
        struct.pack_into('<I', data, 0x520, 0x1140)
        data[0x540:0x547] = b'TickFn\0'
        data[0x700:0x707] = b'wrong!\0'
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / 'fixture.exe'
            exe.write_bytes(data)
            result = probe.pe_info(exe)
        self.assertEqual(result['exports'], ['TickFn'])
        self.assertEqual(result['machine'], '0x8664')
        self.assertEqual(result['import_libraries'], [])

    def test_non_pe_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / 'bad.exe'
            exe.write_bytes(b'not an executable')
            with self.assertRaises(ValueError):
                probe.pe_info(exe)


if __name__ == '__main__':
    unittest.main()
