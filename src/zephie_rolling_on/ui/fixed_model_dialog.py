"""Model dialogs stay centered on their owner and have no draggable title bar."""
from __future__ import annotations

import tkinter as tk

from zephie_rolling_on.ui.ui_geometry import ensure_master_visible, should_set_transient


class FixedModelDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.withdraw()
        self._owner = master.winfo_toplevel()
        self._center_job = None
        self._fixed_size = None
        restored = ensure_master_visible(self._owner)
        if should_set_transient(self._owner, restored=restored):
            self.transient(self._owner)
        self.overrideredirect(True)
        self.resizable(False, False)
        self._owner_binding = self._owner.bind('<Configure>', self._owner_changed, add='+')
        self.bind('<Configure>', self._dialog_changed, add='+')
        self.bind('<Destroy>', self._cleanup_centering, add='+')

    def _owner_changed(self, event):
        if event.widget is self._owner:
            self._schedule_center()

    def _dialog_changed(self, event):
        if event.widget is self and self._fixed_size is not None:
            self._schedule_center()

    def _schedule_center(self):
        if self._center_job is None and self.winfo_exists():
            self._center_job = self.after_idle(self._place_centered)

    def _place_centered(self):
        self._center_job = None
        if self._fixed_size is None or not self.winfo_exists():
            return
        w, h = self._fixed_size
        owner = self._owner
        x = owner.winfo_rootx() + (owner.winfo_width() - w) // 2
        y = owner.winfo_rooty() + (owner.winfo_height() - h) // 2
        if owner.state() in ('iconic', 'withdrawn') or x < -10000 or y < -10000:
            x = (self.winfo_screenwidth() - w) // 2
            y = (self.winfo_screenheight() - h) // 2
        # Always anchor to the owner, including after Windows places a newly mapped window.
        if (self.winfo_width(), self.winfo_height(), self.winfo_rootx(), self.winfo_rooty()) != (w,h,x,y):
            # A leading '+' keeps negative virtual-desktop coordinates absolute;
            # '-50' alone means 50 pixels from the screen's right edge in Tk.
            self.geometry(f'{w}x{h}+{x}+{y}')

    def show_centered(self, width: int, height: int):
        self._fixed_size = (int(width), int(height))
        self._place_centered()
        self.deiconify()
        self.lift()
        self.grab_set()
        self.focus_set()
        self._schedule_center()

    def _cleanup_centering(self, event):
        if event.widget is not self:
            return
        if self._center_job is not None:
            self.after_cancel(self._center_job)
            self._center_job = None
        if self._owner_binding is not None and self._owner.winfo_exists():
            self._owner.unbind('<Configure>', self._owner_binding)
            self._owner_binding = None
