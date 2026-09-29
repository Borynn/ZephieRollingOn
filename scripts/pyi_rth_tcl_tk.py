# PyInstaller runtime hook: pin bundled Tcl/Tk so conda base Tcl 8.6.15
# cannot override the app's 8.6.13 data (exact version required by init.tcl).
from __future__ import annotations

import os
import sys


def _pin() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return
    tcl = os.path.join(base, "_tcl_data")
    tk = os.path.join(base, "_tk_data")
    if os.path.isdir(tcl):
        os.environ["TCL_LIBRARY"] = tcl
    if os.path.isdir(tk):
        os.environ["TK_LIBRARY"] = tk


_pin()
