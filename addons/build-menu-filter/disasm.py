"""Dump known x64 function boundaries for review; no game execution."""
import bisect
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parents[1]/'.runtime/python-tools'))
import pefile,capstone
p=pefile.PE('D:/SteamLibrary/steamapps/common/Reassembly/win64/ReassemblyRelease.exe')
c=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64)
entries=p.DIRECTORY_ENTRY_EXCEPTION;starts=[e.struct.BeginAddress for e in entries]
for arg in sys.argv[1:]:
    addr=int(arg,16);e=entries[bisect.bisect_right(starts,addr)-1].struct
    text='\n'.join(f'{i.address:x}: {i.mnemonic} {i.op_str}' for i in c.disasm(p.get_data(e.BeginAddress,e.EndAddress-e.BeginAddress),e.BeginAddress))
    (ROOT/'research'/f'{e.BeginAddress:#x}.asm').write_text(text)
    print(f'{e.BeginAddress:#x} - {e.EndAddress:#x}')
