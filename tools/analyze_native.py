"""Build-specific export/disassembly evidence, without launching Reassembly."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.runtime' / 'python-tools'))
import capstone
import pefile

TARGETS = {
    'zone_update': '?Update@GameZone@@QEAAXXZ',
    'zone_clusters': '?getClusters@GameZone@@QEBAAEBV?$vector@PEAUBlockCluster@@V?$allocator@PEAUBlockCluster@@@std@@@std@@XZ',
    'ai_update': '?update@AI@@QEAAX_N@Z',
    'ai_faction': '?getFaction@AI@@QEBAHXZ',
    'cluster_faction': '?getFaction@BlockCluster@@QEBAHXZ',
    'cluster_body': '?getBody@BlockCluster@@QEAAPEAUcpBody@@XZ',
    'cluster_angle': '?setAngle@BlockCluster@@QEAAXM@Z',
    'cluster_position': '?setPos@BlockCluster@@QEAAXU?$tvec2@M$0A@@glm@@@Z',
    'body_position': '?getPos@Body@@QEBA?AU?$tvec2@M$0A@@glm@@XZ',
    'body_velocity': '?setVel@Body@@QEAAXU?$tvec2@M$0A@@glm@@@Z',
}
KNOWN_HASH = '8af8e056bd9c75916dd19b67b1b2a394454b22159a3eda86d1080766d0fcf45c'


def analyze(exe):
    binary = exe.read_bytes()
    image = pefile.PE(data=binary)
    exports = {s.name.decode(): s.address for s in image.DIRECTORY_ENTRY_EXPORT.symbols if s.name}
    decoder = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    result = {'schema': 1, 'sha256': hashlib.sha256(binary).hexdigest(),
              'known_build': hashlib.sha256(binary).hexdigest() == KNOWN_HASH,
              'machine': hex(image.FILE_HEADER.Machine), 'export_count': len(exports), 'functions': {}}
    for key, name in TARGETS.items():
        if name not in exports:
            result['functions'][key] = {'symbol': name, 'missing': True}
            continue
        rva = exports[name]
        instructions = []
        for ins in decoder.disasm(image.get_data(rva, 512), image.OPTIONAL_HEADER.ImageBase + rva):
            instructions.append({'rva': hex(ins.address - image.OPTIONAL_HEADER.ImageBase),
                                 'bytes': ins.bytes.hex(), 'mnemonic': ins.mnemonic, 'operands': ins.op_str})
            if ins.mnemonic == 'ret':
                break
        result['functions'][key] = {'symbol': name, 'rva': hex(rva), 'prefix': image.get_data(rva, 16).hex(),
                                    'instructions': instructions}
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe', type=Path, required=True)
    p.add_argument('--out', type=Path, default=Path('.runtime/native-analysis.json'))
    args = p.parse_args()
    result = analyze(args.exe)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'functions'}, indent=2))
    for name, fn in result['functions'].items():
        print(name, fn.get('rva', 'MISSING'))
