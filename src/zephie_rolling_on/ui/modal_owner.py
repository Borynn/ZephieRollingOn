"""Keep modal dialogs above their owner without global topmost windows."""
from __future__ import annotations

import sys
import tkinter as tk


def attach_modal_owner(dialog: tk.Toplevel, master: tk.Misc) -> None:
    """Bind native ownership, activation recovery, and automatic cleanup once."""
    if not hasattr(dialog, '_modal_owner_guard'):
        dialog._modal_owner_guard = _ModalOwner(dialog, master.winfo_toplevel())


class _ModalOwner:
    def __init__(self, dialog: tk.Toplevel, owner: tk.Misc) -> None:
        self.dialog = dialog
        self.owner = owner
        self._raise_job = None
        self._focus_binding = owner.bind('<FocusIn>', self._owner_focused, add='+')
        self._owner_map_binding = owner.bind('<Map>', self._mapped, add='+')
        dialog.bind('<Map>', self._mapped, add='+')
        dialog.bind('<Destroy>', self._destroyed, add='+')
        if dialog.winfo_viewable():
            self._set_native_owner()

    def _mapped(self, event) -> None:
        if event.widget is self.dialog or event.widget is self.owner:
            if self.dialog.winfo_viewable():
                self._set_native_owner()

    def _set_native_owner(self) -> None:
        # Startup notices can have an intentionally hidden root. Attaching to
        # that root would risk hiding the only visible notice along with it.
        if sys.platform != 'win32' or not self.owner.winfo_viewable():
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL('user32', use_last_error=True)
        get_root = user32.GetAncestor
        get_root.argtypes = (wintypes.HWND, wintypes.UINT)
        get_root.restype = wintypes.HWND
        set_owner = getattr(user32, 'SetWindowLongPtrW', None)
        if set_owner is None:
            set_owner = user32.SetWindowLongW
        set_owner.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_owner.restype = ctypes.c_ssize_t
        hwnd = get_root(self.dialog.winfo_id(), 2)
        owner_hwnd = get_root(self.owner.winfo_id(), 2)
        if hwnd and owner_hwnd and hwnd != owner_hwnd:
            ctypes.set_last_error(0)
            previous = set_owner(hwnd, -8, owner_hwnd)
            error = ctypes.get_last_error()
            if not previous and error:
                raise ctypes.WinError(error)

    def _owner_focused(self, event) -> None:
        if event.widget.winfo_toplevel() is self.owner and self._raise_job is None:
            self._raise_job = self.dialog.after_idle(self._raise_modal)

    def _raise_modal(self) -> None:
        self._raise_job = None
        if self.dialog.winfo_viewable() and self.dialog.grab_current() is self.dialog:
            self.dialog.lift(self.owner)
            self.dialog.focus_set()

    def _destroyed(self, event) -> None:
        if event.widget is not self.dialog:
            return
        if self._raise_job is not None:
            self.dialog.after_cancel(self._raise_job)
            self._raise_job = None
        if self.owner.winfo_exists():
            self.owner.unbind('<FocusIn>', self._focus_binding)
            self.owner.unbind('<Map>', self._owner_map_binding)
            self.owner.after_idle(self._restore_owner_focus)

    def _restore_owner_focus(self) -> None:
        try:
            # A failure callback may already have reopened another modal dialog.
            if (
                self.owner.winfo_exists()
                and self.owner.state() == 'normal'
                and self.owner.grab_current() is None
            ):
                self.owner.focus_force()
        except tk.TclError:
            pass
