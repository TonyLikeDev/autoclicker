"""Build AutoClicker.exe with PyInstaller.

    python build_exe.py              one-file exe  -> dist/AutoClicker.exe
    python build_exe.py --onedir     folder build  -> dist/AutoClicker/
    python build_exe.py --console    keep a console window (for debugging)

The build runs inside build/venv so nothing is installed system-wide. That venv
is created on first run from a CPython interpreter; MSYS2/MinGW Python is
skipped because PyInstaller does not support it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / "build" / "venv"
VENV_PY = VENV / "Scripts" / "python.exe"
ICON = ROOT / "assets" / "autoclicker.ico"
NAME = "AutoClicker"


def find_cpython() -> str:
    """Locate an interpreter PyInstaller can actually use."""
    if sys.platform != "win32":
        raise SystemExit("AutoClicker.exe can only be built on Windows.")

    candidates = []
    # The running interpreter, unless it is an MSYS2/MinGW build.
    if "GCC" not in sys.version and "msys" not in sys.executable.lower():
        candidates.append(sys.executable)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(str(Path(local) / "Microsoft" / "WindowsApps" / "python.exe"))
        candidates += [str(p / "python.exe") for p in
                       sorted((Path(local) / "Programs" / "Python").glob("Python3*"))]
    candidates += [str(p) for p in sorted(Path("C:/").glob("Python3*/python.exe"))]
    candidates += [str(p) for p in
                   sorted(Path("C:/Program Files").glob("Python3*/python.exe"))]

    for exe in candidates:
        if not exe or not Path(exe).exists():
            continue
        try:
            out = subprocess.run(
                [exe, "-c", "import sys,tkinter;"
                            "print('GCC' in sys.version, sys.version.split()[0])"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            continue
        if out.returncode == 0 and out.stdout.startswith("False"):
            print("Using interpreter: %s  (Python %s)"
                  % (exe, out.stdout.split()[1]))
            return exe
    raise SystemExit(
        "No suitable CPython with tkinter was found.\n"
        "PyInstaller cannot build from MSYS2/MinGW Python. Install CPython from\n"
        "python.org (tick 'tcl/tk and IDLE') and run this script again.")


def ensure_venv() -> Path:
    if not VENV_PY.exists():
        base = find_cpython()
        print("Creating build venv in %s ..." % VENV)
        subprocess.run([base, "-m", "venv", str(VENV)], check=True)
    try:
        subprocess.run([str(VENV_PY), "-c", "import PyInstaller"],
                       check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("Installing PyInstaller ...")
        subprocess.run([str(VENV_PY), "-m", "pip", "install", "--upgrade",
                        "--disable-pip-version-check", "pyinstaller"], check=True)
    return VENV_PY


def main() -> int:
    parser = argparse.ArgumentParser(description="Build AutoClicker.exe")
    parser.add_argument("--onedir", action="store_true",
                        help="folder build; starts faster than one-file")
    parser.add_argument("--console", action="store_true",
                        help="keep a console window for debugging")
    parser.add_argument("--clean", action="store_true",
                        help="discard cached build state first")
    args = parser.parse_args()

    if not ICON.exists():
        print("Icon missing, generating it ...")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")],
                       check=True)

    python = ensure_venv()

    work = ROOT / "build" / "pyinstaller"
    cmd = [
        str(python), "-m", "PyInstaller",
        "--noconfirm",
        "--name", NAME,
        "--icon", str(ICON),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(work),
        "--specpath", str(ROOT / "build"),
        # Ship the icons so the window and tray use the real logo.
        "--add-data", "%s;assets" % (ROOT / "assets"),
        # Nothing here needs these, and they add tens of megabytes.
        "--exclude-module", "numpy",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    cmd.append("--onedir" if args.onedir else "--onefile")
    cmd.append("--console" if args.console else "--windowed")
    if args.clean:
        cmd.append("--clean")
    cmd.append(str(ROOT / "main.py"))

    print("\n" + " ".join(cmd) + "\n")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        return result.returncode

    target = (ROOT / "dist" / NAME / (NAME + ".exe")) if args.onedir \
        else (ROOT / "dist" / (NAME + ".exe"))
    if not target.exists():
        print("Build reported success but %s is missing." % target)
        return 1

    size = target.stat().st_size
    print("\n" + "=" * 60)
    print("Built %s" % target)
    print("      %.1f MB" % (size / 1024 / 1024))
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
