"""AutoClicker entry point.

    python main.py                 launch the GUI
    python main.py --profile Fast  launch with a saved profile loaded
    python main.py --start         begin clicking immediately
    python main.py --minimized     start hidden in the tray
"""

from __future__ import annotations

import argparse
import sys
import traceback


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="autoclicker", description="Windows auto clicker")
    parser.add_argument("--profile", metavar="NAME", help="load a saved profile on startup")
    parser.add_argument("--settings", metavar="PATH", help="load settings from a JSON file")
    parser.add_argument("--start", action="store_true", help="start clicking right away")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    if sys.platform != "win32":
        print("AutoClicker only runs on Windows.", file=sys.stderr)
        return 2

    args = parse_args(argv)

    from autoclicker import __version__
    if args.version:
        print("AutoClicker %s" % __version__)
        return 0

    from autoclicker import config as cfg
    from autoclicker import winapi

    # Must happen before Tk creates any window, or coordinates get scaled.
    winapi.enable_dpi_awareness()

    if args.settings:
        from pathlib import Path

        settings = cfg.load_settings(Path(args.settings))
    elif args.profile:
        settings = cfg.load_profile(args.profile)
        settings.last_profile = args.profile
    else:
        settings = cfg.load_settings()

    if args.minimized:
        settings.start_minimized = True

    from autoclicker.gui import AutoClickerApp

    app = AutoClickerApp(settings)
    if args.profile:
        app.log("Loaded profile '%s' from the command line." % args.profile)
    if args.start:
        app.root.after(400, app.start)
    app.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        try:
            import tkinter.messagebox as mb

            mb.showerror("AutoClicker", "Startup failed:\n\n%s" % traceback.format_exc())
        except Exception:
            pass
        sys.exit(1)
