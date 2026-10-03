"""Keep Windows from throttling compute processes to efficiency cores.

Windows 11 applies power throttling ("EcoQoS") to processes it judges to be
background work -- started from a hidden window, a scheduled task, a service.
On hybrid CPUs such as the 13th-gen Core i5-HX this machine runs, that means
the efficiency cores at reduced frequency. Measured on the full-corpus batch
run: twelve workers held to 22% of a core each, the run crawling with the
CPU 70% idle; after opting out, 95% each and the run 4.3x faster.

The opt-out is the documented per-process switch,
`SetProcessInformation(ProcessPowerThrottling)` with execution-speed
throttling explicitly disabled. It changes nothing system-wide and nothing on
any other platform.

Reference: https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-setprocessinformation
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_done = False


def opt_out_of_power_throttling() -> bool:
    """Ask Windows to run this process at full speed. Idempotent; returns
    whether the request was accepted (always False off Windows)."""
    global _done
    if _done or sys.platform != "win32":
        return _done
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _State(ctypes.Structure):
            _fields_ = [("Version", wt.ULONG), ("ControlMask", wt.ULONG), ("StateMask", wt.ULONG)]

        process_power_throttling = 4
        execution_speed = 0x1
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wt.HANDLE
        # Declared, not inferred: the current-process pseudo-handle is -1 as
        # an unsigned 64-bit value, which ctypes cannot pass as the default
        # C int -- the untyped call raises OverflowError before reaching Windows.
        k32.SetProcessInformation.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        k32.SetProcessInformation.restype = wt.BOOL
        state = _State(1, execution_speed, 0)
        ok = bool(k32.SetProcessInformation(k32.GetCurrentProcess(), process_power_throttling,
                                            ctypes.byref(state), ctypes.sizeof(state)))
        _done = ok
        if not ok:
            logger.debug("SetProcessInformation refused: %s", ctypes.get_last_error())
        return ok
    except Exception as exc:                        # noqa: BLE001
        logger.debug("power throttling opt-out unavailable: %s", exc)
        return False
