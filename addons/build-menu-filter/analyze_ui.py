"""Locate UI-related string references and containing x64 functions, read-only."""
import bisect
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'.runtime/python-tools'))
import capstone
import pefile

exe=Path('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe')
pe=pefile.PE(str(exe))
data=exe.read_bytes()
strings={}
for m in re.finditer(rb'[ -~]{6,}',data):
    if any(x in m[0].lower() for x in (b'palette',b'constructor',b'cycle block',b'block type')):
        try:strings[pe.get_rva_from_offset(m.start())]=m[0].decode()
        except pefile.PEFormatError:pass
entries=pe.DIRECTORY_ENTRY_EXCEPTION
starts=[e.struct.BeginAddress for e in entries]
section=next(s for s in pe.sections if s.Name.startswith(b'.text'))
cs=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);cs.skipdata=True
result=[]
for ins in cs.disasm(section.get_data(),section.VirtualAddress):
    if 'rip' not in ins.op_str:continue
    m=re.search(r'rip ([+-]) (0x[0-9a-f]+)',ins.op_str)
    if not m:continue
    target=ins.address+ins.size+int(m[2],16)*(1 if m[1]=='+' else -1)
    if target not in strings:continue
    entry=entries[bisect.bisect_right(starts,ins.address)-1].struct
    if entry.BeginAddress<=ins.address<entry.EndAddress:
        row={'string':strings[target],'reference':hex(ins.address),'start':hex(entry.BeginAddress),'end':hex(entry.EndAddress)}
        result.append(row);print(json.dumps(row),flush=True)
out=ROOT/'research';out.mkdir(exist_ok=True)
(out/'ui-xrefs.json').write_text(json.dumps(result,indent=2))
for row in result:
    start,end=int(row['start'],16),int(row['end'],16)
    path=out/(row['start']+'.asm')
    if not path.exists():
        path.write_text('\n'.join(f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in cs.disasm(pe.get_data(start,end-start),start)))
