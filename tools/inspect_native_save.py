"""Read-only exact-build save-path references; never accesses player saves."""
import argparse
import bisect
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.runtime'/'python-tools'))
import capstone
import pefile
from coop_wire import GAME_HASH


def inspect(exe):
    data=exe.read_bytes()
    if hashlib.sha256(data).hexdigest()!=GAME_HASH:raise ValueError('Unsupported executable build')
    pe=pefile.PE(data=data)
    labels={}
    for label in ('Saving Game','SaveGame::getPath','SaveGame::createInitial','blueprints.lua','save.lua'):
        needle=label.encode()+b'\0';offset=0
        while (offset:=data.find(needle,offset))>=0:
            labels[pe.get_rva_from_offset(offset)]=label;offset+=len(needle)
    entries=pe.DIRECTORY_ENTRY_EXCEPTION;starts=[e.struct.BeginAddress for e in entries]
    disassembler=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_64);disassembler.skipdata=True
    text=next(s for s in pe.sections if s.Name.startswith(b'.text'));references=[]
    for address,size,mnemonic,operands in disassembler.disasm_lite(text.get_data(),text.VirtualAddress):
        match=re.search(r'rip ([+-]) (0x[0-9a-f]+)',operands)
        if not match:continue
        target=address+size+int(match[2],16)*(1 if match[1]=='+' else -1)
        if target not in labels:continue
        index=bisect.bisect_right(starts,address)-1
        if index<0:continue
        entry=entries[index].struct
        if address>=entry.EndAddress:continue
        references.append({'label':labels[target],'referenceRva':hex(address),
                           'functionRva':hex(entry.BeginAddress),'functionEndRva':hex(entry.EndAddress)})
    return {'gameHash':GAME_HASH,'references':references,'verifiedSaveCall':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--exe',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(inspect(args.exe),indent=2))


if __name__=='__main__':main()
