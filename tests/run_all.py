"""Run every suite and summarise.

    python tests/run_all.py

test_real.py and test_hotkeys.py inject genuine input: a handful of clicks onto
a throwaway window they create themselves, and F13/F14 key presses. Nothing is
sent to any other application, but do not type while they run.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = [
    ("engine   (timing, limits, randomization)", "test_engine.py"),
    ("hotkeys  (real global hooks)", "test_hotkeys.py"),
    ("real     (a genuine Win32 target window)", "test_real.py"),
    ("gui      (widgets, profiles, dialogs)", "test_gui.py"),
]


def main() -> int:
    results = []
    for label, name in SUITES:
        print("\n" + "=" * 68)
        print("RUNNING  %s" % label)
        print("=" * 68)
        proc = subprocess.run([sys.executable, os.path.join(HERE, name)],
                              cwd=os.path.dirname(HERE))
        results.append((label, proc.returncode == 0))

    print("\n" + "=" * 68)
    for label, ok in results:
        print("  %s  %s" % ("PASS" if ok else "FAIL", label))
    print("=" * 68)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
