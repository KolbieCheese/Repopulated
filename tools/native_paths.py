"""Separate packaged resources from writable multiplayer state."""
import os
from pathlib import Path
import sys

REPO_ROOT=Path(__file__).resolve().parents[1]
FROZEN=bool(getattr(sys,'frozen',False))
STATE_ROOT=(Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData'/'Local')))/'Repopulated') if FROZEN else REPO_ROOT/'.runtime'
NATIVE_DLL=(Path(sys._MEIPASS) if FROZEN else REPO_ROOT/'.runtime')/'RepopulatedDiagnostic.dll'
