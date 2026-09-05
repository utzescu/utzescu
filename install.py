#!/usr/bin/env python3
"""Install the dashboard on this machine: build it, put a "Dashboard" shortcut on
the desktop, and refresh it every 6 hours.

    python3 install.py                 # install (build + shortcut + schedule)
    python3 install.py --no-schedule   # build + shortcut only
    python3 install.py --interval 12   # refresh every 12 hours instead
    python3 install.py --status        # what is installed right now
    python3 install.py --uninstall     # remove the shortcut and the schedule

Linux uses a ~/Desktop/.desktop link and crontab; macOS a .webloc link and a
launchd agent; Windows a .url shortcut and a Scheduled Task. The HTML file
itself is never touched by uninstall.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashkit import build as builder  # noqa: E402

SOURCE_ROOT = Path(__file__).resolve().parent
ROOT = SOURCE_ROOT           # where the installed copy lives; --install-to changes it
PYTHON = sys.executable or "python3"
MARKER = "# dashkit-dashboard"
PAYLOAD = ["dashboard.py", "install.py", "pricing.json", "README.md",
           "Install-Dashboard.cmd", "dashkit"]
LAUNCH_LABEL = "com.dashkit.dashboard"
TASK_NAME = "DashkitDashboard"


def run(args, **kwargs):
    """subprocess.run that returns None when the executable is not installed."""
    try:
        return subprocess.run(args, capture_output=True, text=True, **kwargs)
    except (FileNotFoundError, OSError):
        return None


# --------------------------------------------------------------------- paths

def builder_path():
    return ROOT / "dashboard.py"


def quiet_python():
    """pythonw runs the refresh without flashing a console window."""
    if sys.platform != "win32":
        return PYTHON
    candidate = Path(PYTHON).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else PYTHON


def copy_payload(target, warn=print):
    """Copy the toolkit into `target` (a synced folder, a USB stick, anywhere).

    dashboard.config.json is never overwritten - that file holds the user's
    choices and, in a shared folder, the other devices' settings."""
    import shutil
    target = Path(target).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    for name in PAYLOAD:
        src = SOURCE_ROOT / name
        if not src.exists():
            continue
        dst = target / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)
    config = target / "dashboard.config.json"
    if not config.exists():
        shutil.copy2(SOURCE_ROOT / "dashboard.config.json", config)
        # A copied config must not point back at the machine it came from.
        text = json.loads(config.read_text(encoding="utf-8"))
        text["output"] = "dashboard.html"
        text["data_dir"] = "data"
        config.write_text(json.dumps(text, indent=2) + "\n", encoding="utf-8")
    else:
        warn(f"kept existing {config}")
    return target


def desktop_dir():
    home = Path.home()
    if sys.platform == "win32":
        for var in ("USERPROFILE", "OneDrive"):
            base = os.environ.get(var)
            if base and (Path(base) / "Desktop").is_dir():
                return Path(base) / "Desktop"
    xdg = run(["xdg-user-dir", "DESKTOP"])
    if xdg and xdg.returncode == 0 and xdg.stdout.strip():
        candidate = Path(xdg.stdout.strip())
        if candidate.is_dir():
            return candidate
    for name in ("Desktop", "Schreibtisch", "Bureau", "Escritorio", "Área de Trabalho", "Birou"):
        if (home / name).is_dir():
            return home / name
    return home / "Desktop"


DESKTOP_OVERRIDE = None


def shortcut_path(name):
    ext = {"win32": ".url", "darwin": ".webloc"}.get(sys.platform, ".desktop")
    base = Path(DESKTOP_OVERRIDE).expanduser() if DESKTOP_OVERRIDE else desktop_dir()
    return base / (name + ext)


# ----------------------------------------------------------------- shortcut

def write_shortcut(target_html, name):
    path = shortcut_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = target_html.resolve().as_uri()

    if sys.platform == "win32":
        path.write_text(f"[InternetShortcut]\nURL={url}\nIconIndex=0\n", encoding="utf-8")
    elif sys.platform == "darwin":
        with path.open("wb") as fh:
            plistlib.dump({"URL": url}, fh)
    else:
        path.write_text(
            "[Desktop Entry]\nVersion=1.0\nType=Link\n"
            f"Name={name}\nComment=Locally built dashboard\nURL={url}\nIcon=text-html\n",
            encoding="utf-8")
        path.chmod(0o755)
        # GNOME will not launch a .desktop file it does not trust.
        run(["gio", "set", str(path), "metadata::trusted", "true"])
    return path


def remove_shortcut(name):
    removed = []
    base = Path(DESKTOP_OVERRIDE).expanduser() if DESKTOP_OVERRIDE else desktop_dir()
    for ext in (".url", ".webloc", ".desktop"):
        candidate = base / (name + ext)
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    return removed


# ----------------------------------------------------------------- schedule

def _command(config_path):
    return f'"{PYTHON}" "{builder_path()}" --config "{config_path}" --quiet'


def write_refresh_script(config_path):
    """A double-clickable refresh, and the thing the scheduler runs on Windows.

    Written against the script's own folder wherever possible, so a copy sitting
    in a synced folder keeps working on a device that mounts it on another drive
    letter or path."""
    config_path = Path(config_path)
    try:
        local_config = config_path.resolve().relative_to(Path(ROOT).resolve())
    except ValueError:
        local_config = None

    if sys.platform == "win32":
        path = Path(ROOT) / "refresh.cmd"
        config_arg = f'%~dp0{local_config}' if local_config else str(config_path)
        path.write_bytes((
            "@echo off\r\n"
            "rem Rebuilds the dashboard. Double-click any time; the scheduler runs it too.\r\n"
            'cd /d "%~dp0"\r\n'
            f'"{quiet_python()}" "%~dp0dashboard.py" --config "{config_arg}" --quiet\r\n'
        ).encode("utf-8"))
    else:
        path = Path(ROOT) / "refresh.sh"
        config_arg = f'"$(dirname "$0")/{local_config}"' if local_config else f'"{config_path}"'
        path.write_text(
            "#!/bin/sh\n# Rebuilds the dashboard. Run it any time; the scheduler runs it too.\n"
            'cd "$(dirname "$0")" || exit 1\n'
            f'"{PYTHON}" "$(dirname "$0")/dashboard.py" --config {config_arg} --quiet\n',
            encoding="utf-8")
        path.chmod(0o755)
    return path


def has_crontab():
    return run(["crontab", "-l"]) is not None


def _crontab_read():
    out = run(["crontab", "-l"])
    return out.stdout if out and out.returncode == 0 else ""


def _crontab_write(text):
    proc = run(["crontab", "-"], input=text)
    if proc is None:
        raise RuntimeError("crontab is not installed")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "crontab failed")


def schedule_cron(config_path, hours):
    write_refresh_script(config_path)
    lines = [ln for ln in _crontab_read().splitlines() if MARKER not in ln]
    lines.append(f"0 */{hours} * * * cd {ROOT} && {_command(config_path)} {MARKER}")
    _crontab_write("\n".join(lines).strip() + "\n")
    return f"crontab entry every {hours}h"


def unschedule_cron():
    current = _crontab_read()
    if MARKER not in current:
        return None
    _crontab_write("\n".join(ln for ln in current.splitlines() if MARKER not in ln).strip() + "\n")
    return "crontab entry"


SYSTEMD_UNIT = "dashkit-dashboard"


def systemd_dir():
    return Path.home() / ".config" / "systemd" / "user"


def has_systemd():
    out = run(["systemctl", "--user", "--version"])
    return out is not None and out.returncode == 0


def schedule_systemd(config_path, hours):
    unit_dir = systemd_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / f"{SYSTEMD_UNIT}.service").write_text(
        "[Unit]\nDescription=Rebuild the dashboard\n\n[Service]\nType=oneshot\n"
        f"WorkingDirectory={ROOT}\n"
        f'ExecStart={PYTHON} {BUILDER} --config "{config_path}" --quiet\n',
        encoding="utf-8")
    (unit_dir / f"{SYSTEMD_UNIT}.timer").write_text(
        "[Unit]\nDescription=Rebuild the dashboard every "
        f"{hours}h\n\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec={hours}h\n"
        "Persistent=true\n\n[Install]\nWantedBy=timers.target\n",
        encoding="utf-8")
    run(["systemctl", "--user", "daemon-reload"])
    enabled = run(["systemctl", "--user", "enable", f"{SYSTEMD_UNIT}.timer"])
    if enabled is None or enabled.returncode != 0:
        raise RuntimeError((enabled.stderr.strip() if enabled else "systemctl unavailable")
                           or "systemctl enable failed")
    started = run(["systemctl", "--user", "start", f"{SYSTEMD_UNIT}.timer"])
    if started is None or started.returncode != 0:
        # Enabled but not started - normal when no user session bus is running yet.
        return (f"systemd user timer {SYSTEMD_UNIT}.timer every {hours}h "
                "(enabled; starts at your next login)")
    return f"systemd user timer {SYSTEMD_UNIT}.timer every {hours}h"


def unschedule_systemd():
    timer = systemd_dir() / f"{SYSTEMD_UNIT}.timer"
    if not timer.exists():
        return None
    run(["systemctl", "--user", "disable", "--now", f"{SYSTEMD_UNIT}.timer"])
    timer.unlink()
    service = systemd_dir() / f"{SYSTEMD_UNIT}.service"
    if service.exists():
        service.unlink()
    run(["systemctl", "--user", "daemon-reload"])
    return f"systemd user timer {SYSTEMD_UNIT}.timer"


def launchd_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_LABEL}.plist"


def schedule_launchd(config_path, hours):
    path = launchd_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": LAUNCH_LABEL,
        "ProgramArguments": [PYTHON, str(BUILDER), "--config", str(config_path), "--quiet"],
        "WorkingDirectory": str(ROOT),
        "StartInterval": hours * 3600,
        "RunAtLoad": True,
        "StandardErrorPath": str(Path.home() / "Library" / "Logs" / "dashkit-dashboard.log"),
    }
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)
    run(["launchctl", "unload", str(path)])
    loaded = run(["launchctl", "load", str(path)])
    if loaded is None or loaded.returncode != 0:
        raise RuntimeError((loaded.stderr.strip() if loaded else "launchctl unavailable")
                           or "launchctl load failed")
    return f"launchd agent {LAUNCH_LABEL} every {hours}h"


def unschedule_launchd():
    path = launchd_path()
    if not path.exists():
        return None
    run(["launchctl", "unload", str(path)])
    path.unlink()
    return f"launchd agent {LAUNCH_LABEL}"


def schedule_task(config_path, hours):
    script = write_refresh_script(config_path)
    args = ["schtasks", "/create", "/f", "/tn", TASK_NAME, "/sc", "hourly",
            "/mo", str(hours), "/tr", f'"{script}"']
    out = run(args)
    if out is None:
        raise RuntimeError("schtasks is not available")
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip() or "schtasks failed")
    return f"scheduled task {TASK_NAME} every {hours}h"


def unschedule_task():
    out = run(["schtasks", "/delete", "/f", "/tn", TASK_NAME])
    return f"scheduled task {TASK_NAME}" if out and out.returncode == 0 else None


def schedule(config_path, hours):
    if sys.platform == "win32":
        return schedule_task(config_path, hours)
    if sys.platform == "darwin":
        return schedule_launchd(config_path, hours)
    if has_crontab():
        return schedule_cron(config_path, hours)
    if has_systemd():
        return schedule_systemd(config_path, hours)
    raise RuntimeError("neither cron nor systemd --user is available on this machine")


def unschedule():
    if sys.platform == "win32":
        return unschedule_task()
    if sys.platform == "darwin":
        return unschedule_launchd()
    return unschedule_cron() or unschedule_systemd()


def schedule_status():
    if sys.platform == "win32":
        out = run(["schtasks", "/query", "/tn", TASK_NAME])
        return f"scheduled task {TASK_NAME}" if out and out.returncode == 0 else None
    if sys.platform == "darwin":
        return f"launchd agent {LAUNCH_LABEL}" if launchd_path().exists() else None
    line = next((ln for ln in _crontab_read().splitlines() if MARKER in ln), None)
    if line:
        return f"crontab: {line.split(MARKER)[0].strip()}"
    if (systemd_dir() / f"{SYSTEMD_UNIT}.timer").exists():
        return f"systemd user timer {SYSTEMD_UNIT}.timer"
    return None


# ---------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", type=Path, default=builder.DEFAULT_CONFIG)
    ap.add_argument("--name", default="Dashboard", help="shortcut name (default: Dashboard)")
    ap.add_argument("--interval", type=int, default=6, metavar="HOURS",
                    help="refresh interval in hours (default: 6)")
    ap.add_argument("--no-schedule", action="store_true", help="skip the refresh schedule")
    ap.add_argument("--no-shortcut", action="store_true", help="skip the desktop shortcut")
    ap.add_argument("--install-to", type=Path, metavar="DIR",
                    help="copy the toolkit into DIR first and install from there "
                         "(e.g. a Google Drive folder shared between devices)")
    ap.add_argument("--desktop", type=Path, metavar="DIR",
                    help="where to put the shortcut (default: this account's desktop)")
    ap.add_argument("--status", action="store_true", help="report what is installed")
    ap.add_argument("--uninstall", action="store_true", help="remove shortcut and schedule")
    args = ap.parse_args(argv)

    global ROOT, DESKTOP_OVERRIDE
    DESKTOP_OVERRIDE = args.desktop
    config_path = Path(args.config).expanduser().resolve()

    if args.install_to:
        # --status / --uninstall only need to look at the folder, not re-copy it
        target = (Path(args.install_to).expanduser() if (args.status or args.uninstall)
                  else copy_payload(args.install_to))
        ROOT = target.resolve()
        # after a copy, the config that matters is the one in the target folder
        if args.config == builder.DEFAULT_CONFIG:
            config_path = (ROOT / "dashboard.config.json").resolve()
        print(f"files      {ROOT}")
    config = builder.load_config(config_path)
    out_path = Path(config["_output_path"])

    if args.status:
        print(f"files      {ROOT}")
        print(f"config     {config_path}")
        print(f"device     {builder.device_name(config)}")
        print(f"dashboard  {out_path}" + ("" if out_path.exists() else "  (not built yet)"))
        found = [p for p in (shortcut_path(args.name),) if p.exists()]
        print(f"shortcut   {found[0] if found else 'none on ' + str(desktop_dir())}")
        print(f"schedule   {schedule_status() or 'none'}")
        return 0

    if args.uninstall:
        for path in remove_shortcut(args.name):
            print(f"removed {path}")
        removed = unschedule()
        print(f"removed {removed}" if removed else "no schedule was installed")
        print(f"kept {out_path} — delete it yourself if you want it gone")
        return 0

    if not 1 <= args.interval <= 24:
        raise SystemExit("--interval must be between 1 and 24 hours")

    data = builder.build(config)
    if not data["reports"]:
        print("no data collected — nothing installed. Check the config:", config_path, file=sys.stderr)
        for problem in data["problems"]:
            print("  " + problem, file=sys.stderr)
        return 1
    written = builder.render(data, out_path)
    print(f"built      {written}  ({builder.summarize(data)})")
    print(f"device     {config['_device']}" +
          (f"  (merging {len(data['devices'])} devices)" if len(data.get("devices") or []) > 1 else ""))
    print(f"refresh    {write_refresh_script(config_path)}")

    if not args.no_shortcut:
        try:
            print(f"shortcut   {write_shortcut(written, args.name)}")
        except OSError as exc:
            print(f"shortcut   failed: {exc}", file=sys.stderr)

    if not args.no_schedule:
        try:
            print(f"schedule   {schedule(config_path, args.interval)}")
        except (RuntimeError, OSError, FileNotFoundError) as exc:
            print(f"schedule   failed: {exc}", file=sys.stderr)
            print("           run the build yourself with: "
                  f"{PYTHON} {BUILDER} --config {config_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
