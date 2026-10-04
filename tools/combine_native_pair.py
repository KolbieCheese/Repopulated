"""Create a labelled side-by-side preview of one owned, finished test pair."""
import argparse
import json
from pathlib import Path
import subprocess

from record_native_pair import ROOT,OWNER


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    args=parser.parse_args()
    folder=args.folder.resolve()
    recording_root=(ROOT/'.runtime'/'smoothness-recordings').resolve()
    if folder.parent!=recording_root or not folder.name.isdigit() or args.folder.is_symlink():
        parser.error('Expected a recorder-owned numeric folder in .runtime/smoothness-recordings')
    manifest=folder/'recording.json'
    metadata=json.loads(manifest.read_text(encoding='utf-8'))
    if metadata.get('createdBy')!=OWNER or not metadata.get('complete'):
        parser.error('The owned capture has not completed successfully')
    ffmpeg=ROOT/'.runtime'/'media-tools'/'imageio_ffmpeg'/'binaries'/'ffmpeg-win-x86_64-v7.1.exe'
    font='C\\:/Windows/Fonts/arial.ttf'
    graphs=[]
    for index,side in enumerate(('Host','Client')):
        graphs.append(f"[{index}:v]setpts=PTS-STARTPTS,drawtext=fontfile='{font}':text='{side}':"
            f"fontsize=22:fontcolor=white:box=1:boxcolor=black@0.8:boxborderw=6:x=12:y=12[{side}]")
    graphs.append('[Host][Client]hstack=inputs=2:shortest=1[out]')
    startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error',
        '-i',str(folder/'host.mp4'),'-i',str(folder/'client.mp4'),
        '-filter_complex',';'.join(graphs),'-map','[out]','-an','-fps_mode','vfr',
        '-c:v','libx264','-preset','fast','-crf','25','-threads','2','-pix_fmt','yuv420p',
        '-movflags','+faststart','-y',str(folder/'pair.mp4')],check=True,startupinfo=startup,timeout=120)
    metadata['pairedPreview']={'file':'pair.mp4','left':'host','right':'client',
        'timing':'Each capture starts at zero; capture initialization may differ. Native traces use a common clock.'}
    manifest.write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    print(folder/'pair.mp4')


if __name__=='__main__':main()
