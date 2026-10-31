"""Temporarily prevent Windows idle sleep on the calling thread."""

import ctypes
import sys
from contextlib import contextmanager

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


@contextmanager
def keep_awake(enabled: bool):
    """Hold a sleep request until all work and shutdown cleanup finish."""
    if not enabled:
        yield
        return

    if sys.platform != "win32":
        print("[!] --keep-awake is only supported on Windows; continuing without sleep prevention.")
        yield
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_execution_state = kernel32.SetThreadExecutionState
    # EXECUTION_STATE is an unsigned DWORD, including the high ES_CONTINUOUS bit.
    set_execution_state.argtypes = [ctypes.c_uint32]
    set_execution_state.restype = ctypes.c_uint32

    previous_state = set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    if not previous_state:
        raise RuntimeError("Windows rejected --keep-awake: unable to prevent automatic sleep.")

    try:
        print("[*] --keep-awake enabled: preventing Windows idle sleep until this command exits.")
        yield
    finally:
        # Restore on the same thread, including after Ctrl+C or SystemExit.
        if not set_execution_state(ES_CONTINUOUS | previous_state):
            print("[!] Unable to restore Windows sleep state; the request will clear when this thread exits.")
