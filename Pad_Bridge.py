"""
Pad Bridge  -  make ANY generic controller work like an Xbox 360 pad, with vibration.

What it does
  * finds every connected controller (both ports of a PS2 adapter, USB pads, ...),
  * gives each one its own virtual Xbox 360 pad (uses the free ViGEmBus driver),
  * forwards game vibration to the real controller (if it does not know how to make a pad
    vibrate, it looks the pad up on the internet and remembers the answer),
  * hides the real controllers from games with HidHide, and un-hides them when you quit.

Usage
  Double-click start_pad_bridge.bat        (or:  python pad_bridge.py)
  --setup        redo the button setup (press each button once) for the connected controllers
  --info         only list what was found (and the versions of everything), then exit
  --learn        test the standard vibration commands on a pad and remember the one that works
  --test         shake each pad and check that the RIGHT controller shakes; remembers the answer
  --tune         find the weakest vibration your pad can feel and set a strength multiplier
                 (saved per pad model)
  --doctor       check ViGEmBus, HidHide, admin rights, packages and internet; says how to fix
                 each problem
  --only 1,2     bridge only these joystick numbers from the list
  --swap         swap which adapter port gets which vibration (this run only; --test remembers it)
  --no-hide      do not touch HidHide
  --offline      never look anything up on the internet
  --restore      undo anything this program left in HidHide, then exit
  --show         print what is pressed (for checking a layout)
  --update-db    download the community controller list now (otherwise: once a week)
  --recheck      ask the internet again about pads that were "not listed" (otherwise: once a week)
  --dpad-stick   for pads stuck in digital mode: the D-pad also moves the left stick
  --guide        hold Start+Back for half a second to press the Xbox Guide button
  --tray         run from an icon in the system tray (status, pause, quit) and hide this window
  --autostart on|off|status   start with Windows (it always starts in the tray)
  --no-boost     do not ask Windows for 1 ms timer accuracy and a slightly higher priority
  --ignore LIST  never bridge these controllers (VID:PID such as 046d:c24f, or part of the name),
                 for example a racing wheel or a flight stick:  --ignore 046d:c24f,hotas
  --include LIST bridge these even though they look like a wheel / flight stick / not a gamepad
  --init-profile write an editable profile (dead zones, remapping, rapid fire ...) for each
                 connected pad into pad_bridge_config.json
  --version      print the version number and exit

Start it BEFORE you start the game. Press Ctrl+C or close the window to stop.
Only one copy can run at a time (a second one says so and closes).

Controller layouts: the community list of thousands of pads (SDL_GameControllerDB) is downloaded
next to this script and refreshed once a week, so many unknown pads work without any setup.
Your own lines can go in gamecontrollerdb.txt (same folder); they win over the community list.
Your own --setup always wins over both.

Everything it prints is also written to pad_bridge.log (next to this script; the previous log is
kept as pad_bridge.log.1). Your settings are in pad_bridge_config.json; if that file is ever
damaged it is renamed to pad_bridge_config.json.bad-<date> instead of being overwritten.
Optional extras you can add to pad_bridge_config.json by hand:
  "options": {"dpad_as_stick": true, "guide": true}     same as --dpad-stick / --guide, always on
  "options": {"ignore": ["046d:c24f"], "include": []}    same as --ignore / --include, always on
  "tuning": {"0810:0001": {"gain": 1.5, "min": 40}}      what --tune saves (per VID:PID)
  "swap_by_model": {"0810:0001": true}                   what --test saves (per VID:PID)

Profiles (a small version of what remapping tools such as reWASD do). Run  --init-profile  to get a
starting point, then edit the numbers. A profile can be given for "*" (every pad), for a model
("0810:0001") or for one exact pad (its GUID, see --info); the more specific one wins. The file is
checked once a second while Pad Bridge runs, so you can change a value and feel it at once.
  "profiles": {"0810:0001": {
      "left_stick":  {"deadzone": 0.10, "anti_deadzone": 0.15, "curve": 1.4, "sensitivity": 1.0,
                      "outer": 1.0, "invert_x": false, "invert_y": false, "radial": true},
      "right_stick": {...same keys...},
      "triggers":    {"deadzone": 0.05, "max": 0.9, "curve": 1.0, "digital": false, "threshold": 0.5},
      "swap_sticks": false,
      "remap":  {"A": "B", "B": "A", "LT": "LB", "X": "none", "B12": "Y", "P1": "A"},
      "turbo":  {"X": 10},                 rapid fire: Hz (1-30) while the button is held
      "toggle": ["L3"],                    press once = held, press again = released
      "shift":  {"hold": "BACK", "remap": {"A": "X", "B": "Y"}},   a second layer while BACK is held
      "dpad_as_stick": true, "guide": false}}      (these two override --dpad-stick / --guide)
  deadzone      the stick must move this far (0-0.9) before anything happens; the rest is smoothly
                stretched to full range (a round dead zone, not a square one)
  anti_deadzone the first bit of movement already gives this much output (for games with a big dead zone)
  curve         above 1 = finer control near the middle, below 1 = quicker response
  sensitivity   multiplies the stick output (0.5 = half speed, 1.5 = reaches full early)
  outer         how far the stick must be pushed to count as 'full' (for worn / short-travel sticks)
  remap         input -> output. Outputs: A B X Y LB RB LT RT BACK START L3 R3 UP DOWN LEFT RIGHT GUIDE,
                or none. Inputs are those too, plus B<n> = the raw button number n of the pad
                (shown by --show with the setup layout), and P1-P4 / MISC / TOUCHPAD where the pad
                and your pygame provide them.
"""
import argparse
import copy
import difflib
import json
import math
import os
import platform
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")  # keep reading while a game has focus
# PlayStation pads: SDL only switches them to the report mode that allows vibration over Bluetooth
# when asked to. (Harmless for every other pad; a hint SDL does not know is ignored.)
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS4_RUMBLE", "1")
os.environ.setdefault("SDL_JOYSTICK_HIDAPI_PS5_RUMBLE", "1")

VERSION = "4.0"
FROZEN = bool(getattr(sys, "frozen", False))   # True when this is the PyInstaller-built .exe
if FROZEN:
    HERE = os.path.dirname(os.path.abspath(sys.executable))   # settings and log live next to the .exe
    SCRIPT = os.path.basename(sys.executable)
    RUN_CMD = SCRIPT
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    SCRIPT = os.path.basename(__file__)
    RUN_CMD = "python " + SCRIPT
ARGS = sys.argv[1:]

if __name__ == "__main__" and ("-h" in ARGS or "--help" in ARGS):
    print(__doc__.replace("python pad_bridge.py", RUN_CMD))
    sys.exit(0)

CACHE_FILE = os.path.join(HERE, "pad_bridge_cache.json")  # things that can be re-downloaded; safe to delete
DB_USER = os.path.join(HERE, "gamecontrollerdb.txt")                  # optional: your own controller lines
DB_COMMUNITY = os.path.join(HERE, "gamecontrollerdb_community.txt")   # downloaded copy of the community list
DB_ACTIVE = os.path.join(HERE, "gamecontrollerdb_active.txt")         # the two joined; this is what SDL reads
DB_URLS = [
    "https://raw.githubusercontent.com/mdqinc/SDL_GameControllerDB/master/gamecontrollerdb.txt",
    "https://cdn.jsdelivr.net/gh/mdqinc/SDL_GameControllerDB@master/gamecontrollerdb.txt",
]
DB_MAX_AGE = 7 * 24 * 3600   # refresh the community list once a week
LOOKUP_TTL = 7 * 24 * 3600   # how long a "this pad is not listed" answer is trusted
RETRY_AFTER = 10 * 60        # after a failed download, wait this long before trying again


LOG_FILE = os.path.join(HERE, "pad_bridge.log")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class _Log:
    """Copies everything the program prints into pad_bridge.log. It never raises: if the file
    cannot be written the program simply carries on without a log."""
    MAX_BYTES = 512 * 1024

    def __init__(self):
        self.f = None
        self.lock = threading.Lock()
        self.partial = ""      # text printed with say_part() that is waiting for its line to end

    def open(self, path):
        with self.lock:
            if self.f is not None:
                return
            try:
                try:
                    if os.path.getsize(path) > self.MAX_BYTES:
                        os.replace(path, path + ".1")   # keep one older log
                except OSError:
                    pass
                self.f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
            except OSError:
                self.f = None
                return
        self.line("---- Pad Bridge %s | Python %s | %s ----"
                  % (VERSION, platform.python_version(), time.strftime("%Y-%m-%d %H:%M:%S")))

    def _write(self, text):    # the caller holds the lock
        try:
            self.f.write(text)
        except Exception:
            self.f = None

    def part(self, text):
        with self.lock:
            if self.f is not None:
                self.partial += str(text)

    def line(self, text=""):
        with self.lock:
            if self.f is None:
                self.partial = ""
                return
            text = self.partial + str(text)
            self.partial = ""
            text = text.strip("\n")
            if text:
                self._write("%s %s\n" % (time.strftime("%H:%M:%S"), text.replace("\n", "\n         ")))

    def raw(self, text):
        """Only for the log file (such as a traceback), never on the screen."""
        with self.lock:
            if self.f is not None:
                self._write("%s %s\n" % (time.strftime("%H:%M:%S"), str(text).rstrip()))


LOG = _Log()


def _out(text, end="\n"):
    try:
        print(text, end=end, flush=True)
    except UnicodeEncodeError:   # a pad name with a letter this console cannot show
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        try:
            print(str(text).encode(enc, "replace").decode(enc), end=end, flush=True)
        except Exception:
            pass
    except Exception:
        pass   # no console (pythonw, closed window): the log file still gets everything


def say(msg=""):
    _out(msg)
    LOG.line(msg)


def say_part(text):
    """Print text WITHOUT ending the line; the next say() finishes it."""
    _out(text, end="")
    LOG.part(text)


def step(label):
    """Start a status line such as  '  Getting the ID...'  - the result is added after it with say()."""
    say_part("  %s..." % label)


_LOGGED = set()


def log_exception(context):
    """Put the current exception's full traceback in the log (not on screen), once per place."""
    if context in _LOGGED or len(_LOGGED) >= 50:
        return
    _LOGGED.add(context)
    LOG.raw("%s\n%s" % (context, traceback.format_exc()))


def install_excepthooks():
    """Anything that would crash the program (or one of its threads) is also written to the log."""
    old = sys.excepthook

    def hook(kind, value, tb):
        try:
            LOG.raw("UNCAUGHT ERROR\n" + "".join(traceback.format_exception(kind, value, tb)))
        except Exception:
            pass
        old(kind, value, tb)
    sys.excepthook = hook
    if hasattr(threading, "excepthook"):
        old_thread = threading.excepthook

        def thread_hook(args):
            try:
                LOG.raw("ERROR IN THREAD %s\n%s" % (getattr(args.thread, "name", "?"), "".join(
                    traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))
            except Exception:
                pass
            old_thread(args)
        threading.excepthook = thread_hook


def load_json(path, default, keep_bad_copy=False):
    """The JSON in a file, or `default` if there is none. With keep_bad_copy, a file that exists but
    cannot be read is set aside (renamed), so nothing in it is ever silently overwritten."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as err:
        if keep_bad_copy and isinstance(err, ValueError):   # damaged JSON / not UTF-8
            set_aside_damaged(path)
        return default


def set_aside_damaged(path, hint=None):
    if hint is None:
        hint = ("open the HidHide Configuration Client if your controllers are still hidden"
                if os.path.basename(path) == "pad_bridge_hidhide_state.json" else "a fresh one is started")
    try:
        if os.path.getsize(path) == 0:
            return
        bad = "%s.bad-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        os.replace(path, bad)
        say("(%s was damaged and could not be read. It was kept as %s; %s.)"
            % (os.path.basename(path), os.path.basename(bad), hint))
    except OSError:
        pass


def write_text_atomic(path, text):
    """Write a file so that a crash half-way never leaves half a file behind."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def save_json(path, data):
    try:
        write_text_atomic(path, json.dumps(data, indent=2))
    except Exception as err:
        say("(could not save %s: %s)" % (os.path.basename(path), err))


def load_cache():
    cache = load_json(CACHE_FILE, {})
    return cache if isinstance(cache, dict) else {}


def days_ago(seconds):
    days = int(seconds // 86400)
    return "today" if days < 1 else ("1 day ago" if days == 1 else "%d days ago" % days)


def _why(err):
    """The real reason a download failed, in a few words (plus the system's own text)."""
    if isinstance(err, urllib.error.HTTPError):
        return "HTTP %d %s" % (err.code, err.reason)
    raw = str(getattr(err, "reason", err)).strip().replace("\n", " ")[:110]
    low = raw.lower()
    if "certificate" in low or "ssl" in low:
        kind = "SSL certificate check failed"
    elif "getaddrinfo" in low or "name or service" in low or "name resolution" in low:
        kind = "DNS lookup failed (no internet?)"
    elif "timed out" in low:
        kind = "timed out"
    elif "refused" in low or "forcibly" in low or "reset" in low or "unreachable" in low:
        kind = "connection blocked or refused (firewall / proxy?)"
    else:
        kind = "network error"
    return "%s [%s]" % (kind, raw)


def _get(url, timeout=10):
    """(text, None) or (None, reason)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "pad_bridge"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace"), None
    except Exception as err:
        return None, _why(err)


# --------------------------------------------------------------------------
# Community controller list (SDL_GameControllerDB): the button layout of thousands of pads.
# SDL reads it when it starts, so it has to be in place BEFORE pygame is used.
# --------------------------------------------------------------------------
def count_db_lines(text):
    return sum(1 for line in text.splitlines() if line and not line.startswith("#"))


def build_active_db():
    """Join the community list and your own gamecontrollerdb.txt (yours last, so yours wins)."""
    parts = []
    for path in (DB_COMMUNITY, DB_USER):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                parts.append(f.read().rstrip("\n") + "\n")
        except OSError:
            continue
    if not parts:
        return None
    merged = "".join(parts)
    try:
        with open(DB_ACTIVE, "r", encoding="utf-8", errors="replace") as f:
            if f.read() == merged:
                return DB_ACTIVE
    except OSError:
        pass
    try:
        write_text_atomic(DB_ACTIVE, merged)
    except OSError as err:
        say("(could not write %s: %s)" % (os.path.basename(DB_ACTIVE), err))
        return DB_USER if os.path.exists(DB_USER) else None
    return DB_ACTIVE


def user_db_is_big():
    try:
        with open(DB_USER, "r", encoding="utf-8", errors="replace") as f:
            return count_db_lines(f.read()) > 300
    except OSError:
        return False


def prepare_controller_db(offline, force):
    """Download the community controller list if it is missing or a week old.
    Returns the file SDL should read, or None."""
    cache = load_cache()
    info = cache.get("db")
    if not isinstance(info, dict):
        info = cache["db"] = {}
    now = time.time()
    have = os.path.exists(DB_COMMUNITY)
    age = now - os.path.getmtime(DB_COMMUNITY) if have else None
    due = force or not have or age > DB_MAX_AGE
    note = ""
    if due and offline:
        note = "--offline is on, so it was not refreshed"
    elif due and not force and now - info.get("failed_at", 0) < RETRY_AFTER:
        note = "the last download failed a few minutes ago, so it is not tried again yet"
    elif due:
        step("Updating the community controller list")
        text, problems = None, []
        for url in DB_URLS:
            body, why = _get(url, timeout=6 if have else 15)
            if body is not None and body.count("\n") > 300 and "platform:Windows" in body:
                text = body
                break
            problems.append("%s: %s" % (url.split("/")[2], why or "that is not the controller list"))
        if text is not None:
            try:
                write_text_atomic(DB_COMMUNITY, text)
                info.pop("failed_at", None)
                say(" done (%d controllers)" % count_db_lines(text))
                if user_db_is_big():
                    say("    (your own gamecontrollerdb.txt is big too. If it is only an old copy of this list,")
                    say("     delete it: lines in it win over the download.)")
            except OSError as err:
                say(" downloaded, but it could not be saved (%s)" % err)
        else:
            info["failed_at"] = now
            say(" failed")
            for line in problems:
                say("    " + line)
            say("    (%s)" % ("using the older copy" if have else "continuing without it; pads still work"))
        save_json(CACHE_FILE, cache)
        return build_active_db()
    if have:
        say("Controller list: community copy from %s%s" % (days_ago(age), " (%s)" % note if note else ""))
    else:
        say("Controller list: not downloaded%s" % (" (%s)" % note if note else ""))
    return build_active_db()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
MODES = ("version", "doctor", "info", "autostart", "learn", "test", "tune", "init_profile", "restore")  # if several are given, the first one wins
GUARDED_MODES = ("run", "restore", "learn", "test", "tune", "init_profile")   # these must never run twice at the same time
DB_MODES = ("run", "info", "learn", "test", "tune")           # these read the community controller list


def parse_only(text):
    try:
        numbers = set(int(x) for x in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("use it like:  --only 1,2")
    if not numbers or min(numbers) < 1:
        raise argparse.ArgumentTypeError("use it like:  --only 1,2")
    return numbers


def parse_models(text):
    """'046d:c24f,hotas' -> {'046d:c24f', 'hotas'}: VID:PID pairs or pieces of a controller's name."""
    items = set(x.strip().lower() for x in text.split(",") if x.strip())
    if not items:
        raise argparse.ArgumentTypeError("use it like:  --ignore 046d:c24f,hotas")
    return items


def model_set(*sources):
    """Join the lists from the command line and from the settings file into one set of lower-case texts."""
    items = set()
    for source in sources:
        if isinstance(source, str):
            source = [source]
        for item in source or ():
            if isinstance(item, str) and item.strip():
                items.add(item.strip().lower())
    return items


def model_matches(entries, vid, pid, name):
    """Does a pad match one of the entries (an ID like '046d:c24f', or a piece of its name)?"""
    if not entries:
        return False
    key = "%04x:%04x" % (vid, pid) if vid and pid else None
    low = (name or "").lower()
    return any((key is not None and e == key) or (":" not in e and e in low) for e in entries)


class _Parser(argparse.ArgumentParser):
    """argparse, but friendly: a typo such as --setpu is refused (with a suggestion) instead of ignored."""

    def error(self, message):
        say("%s: %s" % (self.prog, message))
        say("Run  %s --help  to see all options." % RUN_CMD)
        sys.exit(2)

    def reject(self, extra):
        known = sorted(o for a in self._actions for o in a.option_strings if o.startswith("--"))
        for arg in extra:
            name = arg.split("=", 1)[0]
            if name.startswith("-"):
                near = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
                say("Unknown option %s%s" % (arg, ("  -  did you mean %s ?" % near[0]) if near else ""))
            else:
                say("Unexpected '%s'  -  options start with --" % arg)
        say("Run  %s --help  to see all options." % RUN_CMD)
        sys.exit(2)


def build_parser():
    p = _Parser(prog=SCRIPT, add_help=False, allow_abbrev=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--version", action="store_true")
    p.add_argument("--setup", action="store_true")
    p.add_argument("--info", action="store_true")
    p.add_argument("--learn", action="store_true")
    p.add_argument("--test", action="store_true")
    p.add_argument("--tune", action="store_true")
    p.add_argument("--doctor", action="store_true")
    p.add_argument("--only", type=parse_only, default=None, metavar="1,2")
    p.add_argument("--swap", action="store_true")
    p.add_argument("--no-hide", action="store_true")
    p.add_argument("--offline", action="store_true")
    p.add_argument("--restore", action="store_true")
    p.add_argument("--show", action="store_true")
    p.add_argument("--update-db", action="store_true")
    p.add_argument("--recheck", action="store_true")
    p.add_argument("--dpad-stick", action="store_true")
    p.add_argument("--guide", action="store_true")
    p.add_argument("--tray", action="store_true")
    p.add_argument("--autostart", choices=("on", "off", "status"), default=None)
    p.add_argument("--no-boost", action="store_true")
    p.add_argument("--ignore", type=parse_models, default=None, metavar="LIST")
    p.add_argument("--include", type=parse_models, default=None, metavar="LIST")
    p.add_argument("--init-profile", action="store_true")
    return p


def parse_args(argv):
    """Options as an object (opts.setup, opts.no_hide, ...). Exits with a hint on a mistake."""
    parser = build_parser()
    opts, extra = parser.parse_known_args(list(argv))
    if extra:
        parser.reject(extra)
    opts.mode = next((m for m in MODES if getattr(opts, m)), "run")
    return opts


def persistent_args(opts):
    """The options worth keeping when Pad Bridge is started with Windows (not the one-time ones)."""
    args = []
    if opts.only:
        args += ["--only", ",".join(str(n) for n in sorted(opts.only))]
    for name in ("swap", "no_hide", "offline", "dpad_stick", "guide", "no_boost"):
        if getattr(opts, name, False):
            args.append("--" + name.replace("_", "-"))
    for name in ("ignore", "include"):
        if getattr(opts, name, None):
            args += ["--" + name, ",".join(sorted(getattr(opts, name)))]
    return args


# --------------------------------------------------------------------------
# Windows helpers (every one of them is safe to call on a machine where it does not apply)
# --------------------------------------------------------------------------
def run_quiet(cmd, timeout=20):
    """Run a program without a window. Returns (exit code, its output as text); never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=NO_WINDOW)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as err:
        return 1, str(err)


def is_admin():
    try:
        import ctypes
        return bool(ctypes.WinDLL("shell32").IsUserAnAdmin())
    except Exception:
        return False


def own_console():
    """True if this program's console window exists only for it (it was double-clicked), so the
    window vanishes the moment the program ends."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        k32.GetConsoleProcessList.restype = wintypes.DWORD
        pids = (wintypes.DWORD * 4)()
        return k32.GetConsoleProcessList(pids, 4) == 1
    except Exception:
        return False


def pause_before_exit():
    """Let the person read a message before a window that is only ours closes."""
    if sys.stdin and own_console():
        try:
            input("\nPress Enter to close this window... ")
        except Exception:
            pass


MUTEX_NAME = "Global\\PadBridge_SingleInstance"
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
_MUTEX = None   # keeps the handle alive for as long as the program runs


def acquire_single_instance(name=MUTEX_NAME):
    """True if no other copy is running (and we now own the right to be the one). If the check itself
    cannot be made, the answer is True: a start is never blocked on a guess."""
    global _MUTEX
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.CreateMutexW(None, False, name)
        err = ctypes.get_last_error()
    except Exception:
        return True
    if not handle:
        if err != ERROR_ACCESS_DENIED:
            return True
        # Denied: either a copy that runs as administrator owns the mutex (so one is running), or this
        # account may not create it at all. Tell the two apart by looking for it.
        try:
            k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            k32.OpenMutexW.restype = wintypes.HANDLE
            opened = k32.OpenMutexW(0x00100000, False, name)      # SYNCHRONIZE
            if opened:
                k32.CloseHandle(opened)
                return False
            return ctypes.get_last_error() == ERROR_FILE_NOT_FOUND   # it does not exist: never block on a guess
        except Exception:
            return True
    if err == ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        return False
    _MUTEX = handle
    return True


def boost_timing():
    """Python before 3.11 sleeps in steps of about 15 ms on Windows, which makes a 4 ms input loop
    laggy. Ask Windows for 1 ms timer accuracy there, and for a slightly higher process priority.
    Returns a function that undoes it (or None)."""
    if sys.platform != "win32":
        return None
    undo, done = [], []
    try:
        import ctypes
        from ctypes import wintypes
        if sys.version_info < (3, 11):
            winmm = ctypes.WinDLL("winmm")
            winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
            winmm.timeBeginPeriod.restype = wintypes.UINT
            winmm.timeEndPeriod.argtypes = [wintypes.UINT]
            winmm.timeEndPeriod.restype = wintypes.UINT
            if winmm.timeBeginPeriod(1) == 0:
                undo.append(lambda: winmm.timeEndPeriod(1))
                done.append("1 ms timer")
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.SetPriorityClass.restype = wintypes.BOOL
        if k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00008000):   # ABOVE_NORMAL_PRIORITY_CLASS
            done.append("priority above normal")
    except Exception:
        pass
    LOG.line("timing boost: %s" % (", ".join(done) or "nothing could be changed"))

    def undo_all():
        for fn in undo:
            try:
                fn()
            except Exception:
                pass
    return undo_all


OPTS = parse_args(ARGS) if __name__ == "__main__" else parse_args([])

if __name__ == "__main__":
    if OPTS.version:
        print("Pad Bridge %s" % VERSION)
        sys.exit(0)
    LOG.open(LOG_FILE)
    install_excepthooks()
    if OPTS.mode in GUARDED_MODES and not acquire_single_instance():
        say("Pad Bridge is already running (look for its icon in the system tray, or for another window).")
        say("Close that one first. A second copy would create every virtual pad twice and fight over HidHide.")
        pause_before_exit()
        sys.exit(1)
    if OPTS.mode in DB_MODES:
        _db = prepare_controller_db(OPTS.offline, OPTS.update_db)
        if _db:
            os.environ.setdefault("SDL_GAMECONTROLLERCONFIG_FILE", _db)

PYGAME_ERROR = VGAMEPAD_ERROR = None
try:
    import pygame
except Exception as err:
    pygame, PYGAME_ERROR = None, err
try:
    import vgamepad as vg
except Exception as err:
    vg, VGAMEPAD_ERROR = None, err

try:
    from pywinusb import hid  # only needed for direct vibration on Twin/GreenAsia adapters
except Exception:
    hid = None

try:
    from pygame._sdl2 import controller as sdl_controller  # SDL's built-in controller layouts
except Exception:
    sdl_controller = None

try:
    import msvcrt  # Windows only; lets you press Enter to skip a setup step
except ImportError:
    msvcrt = None

CONFIG_FILE = os.path.join(HERE, "pad_bridge_config.json")
STATE_FILE = os.path.join(HERE, "pad_bridge_hidhide_state.json")
OLD_TWIN_FILE = os.path.join(HERE, "twin_bridge_mapping.json")  # saved by the first version

MAXVAL = 0x7F        # motor strength range of the Twin/GreenAsia adapters (0..0x7F)
DEADZONE = 0.06      # ignore tiny stick drift
LOOP_DELAY = 0.004   # about 250 updates per second
MAX_PADS = 4         # an Xbox 360 pad count games can see
XINPUT_SIG = "78696e707574"  # "xinput" at the start of an SDL GUID
GUIDE_HOLD = 0.5            # seconds Start+Back must be held to press the Guide button
RUMBLE_GAP = 1.0 / 60       # never write vibration to a pad more than about 60 times a second

# Pads whose vibration we can drive directly. The commands come from the Linux kernel's HID
# force-feedback drivers (hid-pl, hid-gaff, hid-dr, hid-sjoy, hid-zpff). Only IDs that those
# drivers list are here; 0810:0001 was also confirmed on a real adapter.
BUILTIN_RUMBLE = {
    (0x0810, 0x0001): "pl",   # Twin USB Joystick (PS2 adapter)      - confirmed on hardware
    (0x0E8F, 0x0003): "pl",   # GreenAsia USB Joystick / PS2 adapters
    (0x0E8F, 0x0012): "ga",   # GreenAsia 0x12 pads
    (0x0079, 0x0006): "dr",   # DragonRise generic USB joystick
    (0x0925, 0x0005): "sj",   # SmartJoy PLUS PS2 adapter
}

# "Pull from the internet": the Linux kernel's force-feedback drivers list the USB IDs they drive.
# For a pad that is not known above, those lists are downloaded (GitHub first, kernel.org as a
# backup) to find its method.
KERNEL_SOURCES = [
    "https://raw.githubusercontent.com/torvalds/linux/master/drivers/hid/",
    "https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/plain/drivers/hid/",
]
KERNEL_DRIVERS = {"hid-pl.c": "pl", "hid-gaff.c": "ga", "hid-dr.c": "dr", "hid-sjoy.c": "sj", "hid-zpff.c": "zp"}


def normalize_config(cfg):
    """Make sure every section of the settings exists and has the right kind of content."""
    if not isinstance(cfg, dict):
        cfg = {}
    for key in ("mappings", "rumble", "swap_by_model", "tuning", "options", "profiles"):
        if not isinstance(cfg.get(key), dict):
            cfg[key] = {}
    cfg.setdefault("swap_ports", False)
    return cfg


def load_config():
    cfg = load_json(CONFIG_FILE, {}, keep_bad_copy=True)
    if not isinstance(cfg, dict):
        set_aside_damaged(CONFIG_FILE)
        cfg = {}
    return normalize_config(cfg)


# --------------------------------------------------------------------------
# Button layouts
# --------------------------------------------------------------------------
def _button_flags():
    if vg is None:
        return {}
    X = vg.XUSB_BUTTON
    flags = {
        "A": X.XUSB_GAMEPAD_A, "B": X.XUSB_GAMEPAD_B, "X": X.XUSB_GAMEPAD_X, "Y": X.XUSB_GAMEPAD_Y,
        "LB": X.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": X.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "BACK": X.XUSB_GAMEPAD_BACK, "START": X.XUSB_GAMEPAD_START,
        "L3": X.XUSB_GAMEPAD_LEFT_THUMB, "R3": X.XUSB_GAMEPAD_RIGHT_THUMB,
        "UP": X.XUSB_GAMEPAD_DPAD_UP, "DOWN": X.XUSB_GAMEPAD_DPAD_DOWN,
        "LEFT": X.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": X.XUSB_GAMEPAD_DPAD_RIGHT,
    }
    guide = getattr(X, "XUSB_GAMEPAD_GUIDE", None)   # only pressed with --guide
    if guide is not None:
        flags["GUIDE"] = guide
    return flags


BUTTON_FLAGS = _button_flags()  # LT and RT are triggers


def _btn(i):
    return {"t": "button", "i": i}


def _hat(x, y):
    return {"t": "hat", "i": 0, "x": x, "y": y}


# Verified on a real "Twin USB Joystick" PS2 adapter (VID 0810 / PID 0001).
TWIN_LAYOUT = {
    "buttons": {
        "A": _btn(2), "B": _btn(1), "X": _btn(3), "Y": _btn(0),
        "LB": _btn(6), "RB": _btn(7), "LT": _btn(4), "RT": _btn(5),
        "BACK": _btn(8), "START": _btn(9), "L3": _btn(10), "R3": _btn(11),
        "UP": _hat(0, 1), "DOWN": _hat(0, -1), "LEFT": _hat(-1, 0), "RIGHT": _hat(1, 0),
    },
    "sticks": {
        "LX": {"axis": 0, "factor": 1}, "LY": {"axis": 1, "factor": -1},
        "RX": {"axis": 3, "factor": 1}, "RY": {"axis": 2, "factor": -1},
    },
}
KNOWN_LAYOUTS = {(0x0810, 0x0001): TWIN_LAYOUT}

# Models that are probably wired like the Twin adapter but have NOT been checked on real hardware.
# They are used (and labelled "untested") only when the community list does not know the pad, and
# they are never saved as if they were confirmed. Check one with --show, or run --setup.
UNTESTED_LAYOUTS = {(0x0810, 0x0002): TWIN_LAYOUT}
UNTESTED_NOTE = "Twin layout - UNTESTED on this model, check with --show"
GUESS_NOTE = "basic guess - not saved, run --setup to fix"

# A plain guess for controllers nobody has described yet. It is only a stop-gap: it is never saved,
# so the next start asks again. Run --setup to make a real layout.
GUESS_LAYOUT = {
    "buttons": {
        "A": _btn(0), "B": _btn(1), "X": _btn(2), "Y": _btn(3),
        "LB": _btn(4), "RB": _btn(5), "BACK": _btn(6), "START": _btn(7),
        "L3": _btn(8), "R3": _btn(9),
        "UP": _hat(0, 1), "DOWN": _hat(0, -1), "LEFT": _hat(-1, 0), "RIGHT": _hat(1, 0),
    },
    "sticks": {
        "LX": {"axis": 0, "factor": 1}, "LY": {"axis": 1, "factor": -1},
        "RX": {"axis": 2, "factor": 1}, "RY": {"axis": 3, "factor": -1},
    },
}

SETUP_BUTTONS = [
    ("A", "A / Cross (bottom button)"), ("B", "B / Circle (right button)"),
    ("X", "X / Square (left button)"), ("Y", "Y / Triangle (top button)"),
    ("LB", "left bumper (L1)"), ("RB", "right bumper (R1)"),
    ("LT", "left trigger (L2)"), ("RT", "right trigger (R2)"),
    ("BACK", "Back / Select"), ("START", "Start"),
    ("L3", "LEFT stick click (push it in)"), ("R3", "RIGHT stick click (push it in)"),
    ("UP", "D-pad UP"), ("DOWN", "D-pad DOWN"), ("LEFT", "D-pad LEFT"), ("RIGHT", "D-pad RIGHT"),
]
SETUP_STICKS = [
    ("LX", "Push the LEFT stick fully RIGHT and hold"),
    ("LY", "Push the LEFT stick fully UP and hold"),
    ("RX", "Push the RIGHT stick fully RIGHT and hold"),
    ("RY", "Push the RIGHT stick fully UP and hold"),
]


# --------------------------------------------------------------------------
# Reading controllers
# --------------------------------------------------------------------------
PENDING = []  # hot-plug events collected while we were busy


def pump_events():
    try:
        events = pygame.event.get()
    except pygame.error:
        return
    for ev in events:
        if ev.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            PENDING.append(ev)


def snapshot(js):
    pump_events()
    return (
        [js.get_axis(i) for i in range(js.get_numaxes())],
        [js.get_button(i) for i in range(js.get_numbuttons())],
        [tuple(js.get_hat(i)) for i in range(js.get_numhats())],
    )


def is_pressed(ctrl, snap):
    axes, buttons, hats = snap
    kind = ctrl.get("t")
    if kind == "button":
        return ctrl["i"] < len(buttons) and bool(buttons[ctrl["i"]])
    if kind == "axis":
        return ctrl["i"] < len(axes) and axes[ctrl["i"]] * ctrl["s"] > 0.5
    if kind == "hat":
        if ctrl["i"] >= len(hats):
            return False
        hx, hy = hats[ctrl["i"]]
        return (ctrl["x"] == 0 or hx == ctrl["x"]) and (ctrl["y"] == 0 or hy == ctrl["y"])
    return False


def trigger_value(ctrl, snap):
    """0..255 for LT / RT. A button gives 0 or 255. A trigger that is an analog axis (and was set up
    with this version, so its resting position is known) gives its real travel."""
    if not ctrl:
        return 0
    if ctrl.get("t") == "axis" and "rest" in ctrl and ctrl["i"] < len(snap[0]):
        sign, rest = ctrl["s"], ctrl["rest"]
        span = abs(sign - rest)
        if span >= 0.5:
            frac = max(0.0, min(1.0, (snap[0][ctrl["i"]] - rest) * sign / span))
            return 0 if frac < 0.05 else int(round(frac * 255))
    return 255 if is_pressed(ctrl, snap) else 0


def clean(v, dz=None):
    if abs(v) < (DEADZONE if dz is None else dz):
        return 0.0
    return max(-1.0, min(1.0, v))


def stick_value(cfg, axes):
    """One stick direction, -1..1, from the raw axis readings. A setup made with this version also
    knows the stick's resting position and how far it really travels (cheap pads rest off-centre and
    often never reach +-1); an older saved setup has neither, and then this is exactly as before."""
    center = cfg.get("center", 0.0)
    d = axes[cfg["axis"]] - center
    if "center" in cfg or "lo" in cfg or "hi" in cfg:
        span = (cfg.get("hi", 1.0) - center) if d >= 0 else (center - cfg.get("lo", -1.0))
        d /= max(span, 0.25)
    return max(-1.0, min(1.0, d)) * cfg["factor"]


def dpad_to_stick(state):
    """For pads stuck in digital mode (the sticks do nothing): while the real left stick is untouched,
    the D-pad also steers the left stick. The D-pad buttons themselves are still sent."""
    pressed, lt, rt, lx, ly, rx, ry = state
    if lx or ly:
        return state     # the real stick is being used: it wins
    dx = (1 if "RIGHT" in pressed else 0) - (1 if "LEFT" in pressed else 0)
    dy = (1 if "UP" in pressed else 0) - (1 if "DOWN" in pressed else 0)
    if not dx and not dy:
        return state
    if dx and dy:
        return (pressed, lt, rt, dx * 0.7071, dy * 0.7071, rx, ry)   # a diagonal is as long as a straight push
    return (pressed, lt, rt, float(dx), float(dy), rx, ry)


class GuideCombo:
    """Hold Start+Back for half a second and the virtual pad presses its Guide button. While the combo is
    held, Start and Back are not passed on to the game (and not afterwards either, until both are let go)."""

    def __init__(self, names=("START", "BACK"), hold=GUIDE_HOLD):
        self.names = frozenset(names)
        self.hold = hold
        self.since = None      # when the whole combo went down
        self.active = False

    def apply(self, state, now):
        pressed = state[0]
        held = self.names <= pressed
        if self.active:
            if not (self.names & pressed):          # everything let go: back to normal
                self.active, self.since = False, None
                return state
            pressed = pressed - self.names          # still holding part of it: keep it away from the game
            if held:
                pressed = pressed | frozenset(["GUIDE"])
            return (pressed,) + state[1:]
        if held:
            if self.since is None:
                self.since = now
            if now - self.since >= self.hold:
                self.active = True
                return ((pressed - self.names) | frozenset(["GUIDE"]),) + state[1:]
        else:
            self.since = None
        return state


# --------------------------------------------------------------------------
# Profiles: per-pad tweaks, like the remapping tools offer. Dead zones and response curves, button
# remapping, rapid fire, toggle, a shift layer. All of it is plain arithmetic on the pad's state, so
# it works the same for every kind of controller.
# --------------------------------------------------------------------------
OUTPUT_NAMES = ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "BACK", "START", "L3", "R3",
                "UP", "DOWN", "LEFT", "RIGHT", "GUIDE")
TRIGGERS = ("LT", "RT")
TRIGGER_PRESS = 128        # a trigger counts as "pressed" (when it is remapped to a button) from here
TURBO_MIN, TURBO_MAX = 1.0, 30.0
# Extra buttons some pads have (paddles, share button, touchpad click). SDL knows them, but only some
# builds of pygame expose them; what is missing here simply never fires.
EXTRA_NAMES = ("P1", "P2", "P3", "P4", "MISC", "TOUCHPAD")
_EXTRA_CONSTS = {"P1": "CONTROLLER_BUTTON_PADDLE1", "P2": "CONTROLLER_BUTTON_PADDLE2",
                 "P3": "CONTROLLER_BUTTON_PADDLE3", "P4": "CONTROLLER_BUTTON_PADDLE4",
                 "MISC": "CONTROLLER_BUTTON_MISC1", "TOUCHPAD": "CONTROLLER_BUTTON_TOUCHPAD"}
EXTRA_BUTTONS = dict((n, getattr(pygame, c)) for n, c in _EXTRA_CONSTS.items()
                     if getattr(pygame, c, None) is not None)
RAW_BUTTON_RE = re.compile(r"B\d{1,3}\Z")


def input_name(text):
    """'a' -> 'A', 'b12' -> 'B12' (raw button 12 of the pad). None if it is not an input we know."""
    name = str(text).strip().upper()
    if name in OUTPUT_NAMES or name in EXTRA_NAMES or RAW_BUTTON_RE.match(name):
        return name
    return None


def output_name(text):
    """(ok, name). 'none' / '' / null mean 'press nothing' (name None). ok is False for an unknown name."""
    if text is None:
        return True, None
    name = str(text).strip().upper()
    if name in ("", "NONE", "OFF", "-"):
        return True, None
    return (name in OUTPUT_NAMES), (name if name in OUTPUT_NAMES else None)


def _num(value, default, lo, hi):
    """A number from the settings, forced into lo..hi; `default` if it is not a number at all."""
    if isinstance(value, bool):
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:      # NaN
        return default
    return max(lo, min(hi, v))


def _flag(value, default=False):
    return value if isinstance(value, bool) else default


class StickShape:
    """Dead zone, response curve and sensitivity of one stick. The default dead zone is round (it looks
    at how far the stick is pushed, not at each direction alone) and the rest of the travel is stretched
    smoothly to full range, so there is no jump when the stick leaves the dead zone."""

    DEFAULTS = {"deadzone": DEADZONE, "anti_deadzone": 0.0, "curve": 1.0, "sensitivity": 1.0, "outer": 1.0,
                "invert_x": False, "invert_y": False, "radial": True}

    def __init__(self, deadzone=DEADZONE, anti_deadzone=0.0, curve=1.0, sensitivity=1.0, outer=1.0,
                 invert_x=False, invert_y=False, radial=True):
        self.deadzone = max(0.0, min(0.9, float(deadzone)))
        self.anti = max(0.0, min(0.9, float(anti_deadzone)))
        self.curve = max(0.2, min(5.0, float(curve)))
        self.sensitivity = max(0.1, min(3.0, float(sensitivity)))
        self.outer = min(1.0, max(float(outer), self.deadzone + 0.05))
        self.invert_x, self.invert_y, self.radial = bool(invert_x), bool(invert_y), bool(radial)

    @classmethod
    def from_config(cls, entry):
        entry = entry if isinstance(entry, dict) else {}
        d = cls.DEFAULTS
        return cls(_num(entry.get("deadzone"), d["deadzone"], 0.0, 0.9),
                   _num(entry.get("anti_deadzone"), d["anti_deadzone"], 0.0, 0.9),
                   _num(entry.get("curve"), d["curve"], 0.2, 5.0),
                   _num(entry.get("sensitivity"), d["sensitivity"], 0.1, 3.0),
                   _num(entry.get("outer"), d["outer"], 0.2, 1.0),
                   _flag(entry.get("invert_x")), _flag(entry.get("invert_y")),
                   _flag(entry.get("radial"), True))

    def _one(self, v):
        """The same shaping for one direction on its own (used when radial is off)."""
        a = abs(v)
        if a <= self.deadzone:
            return 0.0
        m = min(1.0, (a - self.deadzone) / (self.outer - self.deadzone))
        return math.copysign(self._curve(m), v)

    def _curve(self, m):
        if self.curve != 1.0:
            m = m ** self.curve
        if self.anti > 0.0:
            m = self.anti + (1.0 - self.anti) * m
        return min(1.0, m * self.sensitivity)

    def apply(self, x, y):
        if self.invert_x:
            x = -x
        if self.invert_y:
            y = -y
        if not self.radial:
            return self._one(x), self._one(y)
        r = math.hypot(x, y)
        if r <= self.deadzone or r <= 0.0:
            return 0.0, 0.0
        m = self._curve(min(1.0, (r - self.deadzone) / (self.outer - self.deadzone)))
        k = m / r
        return x * k, y * k

    def changes(self):
        """Short texts for everything that is not at its default (for --info and the start-up line)."""
        out = []
        for label, mine, key in (("dead zone", self.deadzone, "deadzone"), ("anti-dead zone", self.anti, "anti_deadzone"),
                                 ("curve", self.curve, "curve"), ("sensitivity", self.sensitivity, "sensitivity"),
                                 ("outer", self.outer, "outer")):
            if abs(mine - self.DEFAULTS[key]) > 1e-9:
                out.append("%s %.2f" % (label, mine))
        if self.invert_x:
            out.append("X inverted")
        if self.invert_y:
            out.append("Y inverted")
        if not self.radial:
            out.append("square dead zone")
        return out


class TriggerShape:
    """Dead zone, full-travel point and curve of one analog trigger (0..255), or turn it into a button."""

    DEFAULTS = {"deadzone": 0.0, "max": 1.0, "curve": 1.0, "digital": False, "threshold": 0.5}

    def __init__(self, deadzone=0.0, maximum=1.0, curve=1.0, digital=False, threshold=0.5):
        self.deadzone = max(0.0, min(0.9, float(deadzone)))
        self.maximum = min(1.0, max(float(maximum), self.deadzone + 0.05))
        self.curve = max(0.2, min(5.0, float(curve)))
        self.digital = bool(digital)
        self.threshold = max(0.05, min(0.95, float(threshold)))

    @classmethod
    def from_config(cls, *entries):
        """Later entries win: ({'deadzone': .1}, {'curve': 2}) = both keys."""
        merged = {}
        for entry in entries:
            if isinstance(entry, dict):
                merged.update(entry)
        d = cls.DEFAULTS
        return cls(_num(merged.get("deadzone"), d["deadzone"], 0.0, 0.9),
                   _num(merged.get("max"), d["max"], 0.2, 1.0),
                   _num(merged.get("curve"), d["curve"], 0.2, 5.0),
                   _flag(merged.get("digital")),
                   _num(merged.get("threshold"), d["threshold"], 0.05, 0.95))

    def apply(self, value):
        f = max(0.0, min(1.0, value / 255.0))
        if self.digital:
            return 255 if f >= self.threshold else 0
        if f <= self.deadzone:
            return 0
        f = min(1.0, (f - self.deadzone) / (self.maximum - self.deadzone))
        if self.curve != 1.0:
            f = f ** self.curve
        return int(round(f * 255))

    def changes(self):
        out = []
        if self.digital:
            out.append("acts as a button (from %.2f)" % self.threshold)
            return out
        for label, mine, key in (("dead zone", self.deadzone, "deadzone"), ("full at", self.maximum, "max"),
                                 ("curve", self.curve, "curve")):
            if abs(mine - self.DEFAULTS[key]) > 1e-9:
                out.append("%s %.2f" % (label, mine))
        return out


# What --init-profile writes. Every key is optional in a real profile; the values here do nothing special.
DEFAULT_PROFILE = {
    "left_stick": dict(StickShape.DEFAULTS),
    "right_stick": dict(StickShape.DEFAULTS),
    "triggers": dict(TriggerShape.DEFAULTS),
    "swap_sticks": False,
    "remap": {},
    "turbo": {},
    "toggle": [],
    "shift": {"hold": "", "remap": {}},
    "dpad_as_stick": None,
    "guide": None,
}
PROFILE_KEYS = tuple(DEFAULT_PROFILE) + ("left_trigger", "right_trigger")


def merge_profile(layers):
    """Join several profile dictionaries into one. The later one wins; the sections that are
    dictionaries (the sticks, remap, turbo ...) are joined key by key instead of replaced."""
    merged = {}
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                inner = dict(merged[key])
                inner.update(value)
                merged[key] = inner
            else:
                merged[key] = copy.deepcopy(value)
    return merged


def _suggest(word, allowed):
    near = difflib.get_close_matches(str(word), list(allowed), n=1, cutoff=0.6)
    return ("  (did you mean '%s'?)" % near[0]) if near else ""


class Profile:
    """The per-pad tweaks, built from a profile dictionary. Mistakes in it never stop anything: a wrong
    entry is skipped and listed in `problems`. It remembers things between calls (rapid fire timing,
    toggled buttons), so there is one Profile per pad."""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.problems = []
        for key in data:
            if key not in PROFILE_KEYS:
                self.problems.append("unknown setting '%s'%s" % (key, _suggest(key, PROFILE_KEYS)))
        for section in ("left_stick", "right_stick"):
            inner = data.get(section)
            if isinstance(inner, dict):
                for key in inner:
                    if key not in StickShape.DEFAULTS:
                        self.problems.append("unknown %s setting '%s'%s" % (section, key, _suggest(key, StickShape.DEFAULTS)))
        for section in ("triggers", "left_trigger", "right_trigger"):
            inner = data.get(section)
            if isinstance(inner, dict):
                for key in inner:
                    if key not in TriggerShape.DEFAULTS:
                        self.problems.append("unknown %s setting '%s'%s" % (section, key, _suggest(key, TriggerShape.DEFAULTS)))
        self.left = StickShape.from_config(data.get("left_stick"))
        self.right = StickShape.from_config(data.get("right_stick"))
        both = data.get("triggers")
        self.ltrig = TriggerShape.from_config(both, data.get("left_trigger"))
        self.rtrig = TriggerShape.from_config(both, data.get("right_trigger"))
        self.swap_sticks = _flag(data.get("swap_sticks"))
        self.dpad_as_stick = data.get("dpad_as_stick") if isinstance(data.get("dpad_as_stick"), bool) else None
        self.guide = data.get("guide") if isinstance(data.get("guide"), bool) else None
        self.remap = self._read_remap(data.get("remap"), "remap")
        shift = data.get("shift") if isinstance(data.get("shift"), dict) else {}
        hold = shift.get("hold")
        self.shift_hold = ""
        if hold not in (None, ""):
            name = input_name(hold)
            if name is None:
                self.problems.append("shift: '%s' is not a button name" % hold)
            else:
                self.shift_hold = name
        # while the shift button is held, the shift layer's remap is added on top of the normal one
        self.shift_map = dict(self.remap)
        self.shift_map.update(self._read_remap(shift.get("remap"), "shift remap"))
        self.turbo = {}
        turbo = data.get("turbo")
        for key, hz in (turbo.items() if isinstance(turbo, dict) else ()):
            name = str(key).strip().upper()
            if name not in OUTPUT_NAMES or name in TRIGGERS:
                self.problems.append("turbo: '%s' is not a button name%s" % (key, _suggest(name, OUTPUT_NAMES)))
                continue
            self.turbo[name] = _num(hz, 10.0, TURBO_MIN, TURBO_MAX)
        self.toggle = []
        toggle = data.get("toggle")
        for key in (toggle if isinstance(toggle, list) else ()):
            name = str(key).strip().upper()
            if name not in OUTPUT_NAMES or name in TRIGGERS:
                self.problems.append("toggle: '%s' is not a button name%s" % (key, _suggest(name, OUTPUT_NAMES)))
            elif name not in self.toggle:
                self.toggle.append(name)
        for name in sorted(self.extra_inputs()):
            if name in EXTRA_NAMES and name not in EXTRA_BUTTONS:
                self.problems.append("'%s' is not provided by this pygame, so it never fires" % name)
        self._latch, self._was_down, self._since = {}, {}, {}

    def _read_remap(self, table, label):
        out = {}
        for key, value in (table.items() if isinstance(table, dict) else ()):
            src = input_name(key)
            ok, dest = output_name(value)
            if src is None:
                self.problems.append("%s: '%s' is not a button name%s" % (label, key, _suggest(str(key).upper(), OUTPUT_NAMES)))
            elif not ok:
                self.problems.append("%s: '%s' is not a button name%s" % (label, value, _suggest(str(value).upper(), OUTPUT_NAMES)))
            else:
                out[src] = dest
        return out

    def extra_inputs(self):
        """Inputs that are not part of the pad's normal layout (raw button numbers, paddles ...)."""
        names = set(self.remap) | set(self.shift_map)
        if self.shift_hold:
            names.add(self.shift_hold)
        return set(n for n in names if n not in OUTPUT_NAMES)

    def apply(self, state, now):
        pressed, lt, rt, lx, ly, rx, ry = state
        lt, rt = self.ltrig.apply(lt), self.rtrig.apply(rt)
        lx, ly = self.left.apply(lx, ly)
        rx, ry = self.right.apply(rx, ry)
        if self.swap_sticks:
            lx, ly, rx, ry = rx, ry, lx, ly
        remap, consumed = self.remap, ()
        hold = self.shift_hold
        if hold and (hold in pressed or (hold == "LT" and lt >= TRIGGER_PRESS)
                     or (hold == "RT" and rt >= TRIGGER_PRESS)):
            remap, consumed = self.shift_map, (hold,)      # the shift button itself is not passed on
        analog = {"LT": lt, "RT": rt}
        trig, out = {"LT": 0, "RT": 0}, set()
        for t in TRIGGERS:
            if t in consumed:
                continue
            dest = remap[t] if t in remap else t
            if dest in TRIGGERS:
                trig[dest] = max(trig[dest], analog[t])        # a trigger onto a trigger keeps its travel
            elif dest and analog[t] >= TRIGGER_PRESS:
                out.add(dest)                                  # a trigger onto a button
        for n in pressed:
            if n in consumed or n in TRIGGERS:
                continue
            dest = remap[n] if n in remap else n
            if dest in TRIGGERS:
                trig[dest] = 255                               # a button onto a trigger
            elif dest:
                out.add(dest)
        for name in self.toggle:                               # press = on, press again = off
            down = name in out
            if down and not self._was_down.get(name, False):
                self._latch[name] = not self._latch.get(name, False)
            self._was_down[name] = down
            if self._latch.get(name):
                out.add(name)
            else:
                out.discard(name)
        for name, hz in self.turbo.items():                    # rapid fire: on first, then off, on ...
            if name in out:
                start = self._since.setdefault(name, now)
                if int((now - start) * hz * 2.0) % 2:
                    out.discard(name)
            else:
                self._since.pop(name, None)
        return (frozenset(out), trig["LT"], trig["RT"], lx, ly, rx, ry)

    def describe(self):
        bits = []
        for label, shape in (("left stick", self.left), ("right stick", self.right)):
            if shape.changes():
                bits.append("%s: %s" % (label, ", ".join(shape.changes())))
        for label, shape in (("left trigger", self.ltrig), ("right trigger", self.rtrig)):
            if shape.changes():
                bits.append("%s: %s" % (label, ", ".join(shape.changes())))
        if self.swap_sticks:
            bits.append("sticks swapped")
        if self.remap:
            bits.append("%d remapped" % len(self.remap))
        if self.shift_hold:
            bits.append("shift layer on %s" % self.shift_hold)
        if self.turbo:
            bits.append("rapid fire: " + ", ".join("%s %gHz" % kv for kv in sorted(self.turbo.items())))
        if self.toggle:
            bits.append("toggle: " + ", ".join(self.toggle))
        return "; ".join(bits) or "profile with default values"


def read_extras(js, controller, names):
    """Which of the extra inputs (raw buttons 'B12', paddles ...) are pressed right now."""
    out = set()
    for name in names:
        if RAW_BUTTON_RE.match(name):
            i = int(name[1:])
            if i < js.get_numbuttons() and js.get_button(i):
                out.add(name)
        elif controller is not None and name in EXTRA_BUTTONS and controller.get_button(EXTRA_BUTTONS[name]):
            out.add(name)
    return out


def joystick_state(js, mapping, dz=None):
    """State = (set of pressed button names, left trigger, right trigger, lx, ly, rx, ry).
    `dz` is the dead zone (None = the standard one); a profile does its own and asks for 0 here."""
    snap = snapshot(js)
    axes = snap[0]
    pressed = set(n for n, c in mapping["buttons"].items()
                  if n not in ("LT", "RT") and is_pressed(c, snap))
    sticks = {}
    for name in ("LX", "LY", "RX", "RY"):
        cfg = mapping["sticks"].get(name)
        v = 0.0
        if cfg and cfg["axis"] < len(axes):
            v = stick_value(cfg, axes)
        sticks[name] = clean(v, dz)
    return (frozenset(pressed),
            trigger_value(mapping["buttons"].get("LT"), snap), trigger_value(mapping["buttons"].get("RT"), snap),
            sticks["LX"], sticks["LY"], sticks["RX"], sticks["RY"])


def _controller_constants():
    names = {"A": "BUTTON_A", "B": "BUTTON_B", "X": "BUTTON_X", "Y": "BUTTON_Y",
             "BACK": "BUTTON_BACK", "START": "BUTTON_START", "L3": "BUTTON_LEFTSTICK",
             "R3": "BUTTON_RIGHTSTICK", "LB": "BUTTON_LEFTSHOULDER", "RB": "BUTTON_RIGHTSHOULDER",
             "UP": "BUTTON_DPAD_UP", "DOWN": "BUTTON_DPAD_DOWN",
             "LEFT": "BUTTON_DPAD_LEFT", "RIGHT": "BUTTON_DPAD_RIGHT"}
    axes = {"LX": "AXIS_LEFTX", "LY": "AXIS_LEFTY", "RX": "AXIS_RIGHTX", "RY": "AXIS_RIGHTY",
            "LT": "AXIS_TRIGGERLEFT", "RT": "AXIS_TRIGGERRIGHT"}
    try:
        return ({k: getattr(pygame, "CONTROLLER_" + v) for k, v in names.items()},
                {k: getattr(pygame, "CONTROLLER_" + v) for k, v in axes.items()})
    except AttributeError:
        return None


CTRL_CONSTS = _controller_constants()
CTRL_GUIDE = getattr(pygame, "CONTROLLER_BUTTON_GUIDE", None)   # only used with --guide


def _axis(v):
    """SDL gives whole numbers -32768..32767; some wrappers give floats -1..1. Return -1..1.
    (A whole number is ALWAYS the raw SDL kind: a stick that jitters between 0 and 1 out of 32768 must
    not be read as 'pushed fully'.)"""
    if isinstance(v, float) and abs(v) <= 1.0:
        return v
    return max(-1.0, min(1.0, float(v) / 32768.0))


def _trigger(v):
    """SDL triggers give whole numbers 0..32767; some wrappers give floats 0..1. Return 0..255."""
    frac = v if isinstance(v, float) and abs(v) <= 1.0 else float(v) / 32767.0
    return int(round(max(0.0, min(1.0, frac)) * 255))


def controller_state(c, native_guide=False, dz=None):
    buttons, axes = CTRL_CONSTS
    if hasattr(c, "attached") and not c.attached():
        raise pygame.error("controller detached")
    pressed = frozenset(n for n, k in buttons.items() if c.get_button(k))
    if native_guide and CTRL_GUIDE is not None and c.get_button(CTRL_GUIDE):
        pressed = pressed | frozenset(["GUIDE"])     # the pad's own Guide / Home button
    lt = _trigger(c.get_axis(axes["LT"]))
    rt = _trigger(c.get_axis(axes["RT"]))
    lx = clean(_axis(c.get_axis(axes["LX"])), dz)
    ly = clean(-_axis(c.get_axis(axes["LY"])), dz)   # SDL: up is negative; Xbox: up is positive
    rx = clean(_axis(c.get_axis(axes["RX"])), dz)
    ry = clean(-_axis(c.get_axis(axes["RY"])), dz)
    return (pressed, lt, rt, lx, ly, rx, ry)


def apply_state(pad, st):
    buttons, lt, rt, lx, ly, rx, ry = st
    for name, flag in BUTTON_FLAGS.items():
        if name in buttons:
            pad.press_button(button=flag)
        else:
            pad.release_button(button=flag)
    pad.left_trigger(value=lt)
    pad.right_trigger(value=rt)
    pad.left_joystick_float(x_value_float=lx, y_value_float=ly)
    pad.right_joystick_float(x_value_float=rx, y_value_float=ry)
    pad.update()


# --------------------------------------------------------------------------
# One-time setup by pressing each button
# --------------------------------------------------------------------------
def skip_requested():
    if msvcrt is None:
        return False
    while msvcrt.kbhit():
        if msvcrt.getwch() in ("\r", "\n"):
            return True
    return False


def settle(js, prev_base=None):
    """Wait until everything is released, then return a fresh 'resting' snapshot."""
    end = time.time() + 5
    while time.time() < end:
        axes, buttons, hats = snapshot(js)
        calm = not any(buttons) and all(h == (0, 0) for h in hats)
        if calm and prev_base is not None:
            calm = all(abs(v - b) < 0.35 for v, b in zip(axes, prev_base[0]))
        if calm:
            break
        time.sleep(0.02)
    time.sleep(0.25)
    return snapshot(js)


def detect_control(js, base):
    while True:
        if skip_requested():
            return None
        axes, buttons, hats = snapshot(js)
        for i, v in enumerate(buttons):
            if v:
                return {"t": "button", "i": i}
        for i, h in enumerate(hats):
            if h != (0, 0):
                return {"t": "hat", "i": i, "x": h[0], "y": h[1]}
        for i, v in enumerate(axes):
            if i < len(base[0]) and abs(v - base[0][i]) > 0.6:
                return {"t": "axis", "i": i, "s": 1 if v - base[0][i] > 0 else -1,
                        "rest": round(base[0][i], 3)}
        time.sleep(0.01)


def ctrl_key(ctrl):
    """Which physical input a detected control is (used to refuse the same one twice)."""
    kind = ctrl.get("t")
    if kind == "button":
        return ("button", ctrl["i"])
    if kind == "hat":
        return ("hat", ctrl["i"], ctrl["x"], ctrl["y"])
    if kind == "axis":
        return ("axis", ctrl["i"], ctrl["s"])
    return None


def detect_axis(js, base):
    while True:
        if skip_requested():
            return None
        axes = snapshot(js)[0]
        best = None
        for i, v in enumerate(axes):
            if i >= len(base[0]):
                continue
            dev = v - base[0][i]
            if abs(dev) > 0.6 and (best is None or abs(dev) > abs(best[1])):
                best = (i, dev)
        if best:
            return {"axis": best[0], "factor": 1 if best[1] > 0 else -1,
                    "center": round(base[0][best[0]], 3)}
        time.sleep(0.01)


def calibrate_sticks(js, mapping, base):
    """Learn each stick's resting position and its full travel by letting the person roll both sticks
    around once. Cheap pads rest off-centre (a drifting stick) and often do not reach +-1."""
    used = sorted(set(cfg["axis"] for cfg in mapping["sticks"].values()))
    if not used:
        return base
    say("Last step: roll BOTH sticks slowly around their outer edge once or twice, then let go.")
    say("(Press Enter on the keyboard to skip this.)")
    base = settle(js, base)
    rest = base[0]
    used = [a for a in used if a < len(rest)]
    lo = dict((a, rest[a]) for a in used)
    hi = dict((a, rest[a]) for a in used)
    moved, quiet_since = False, None
    end = time.time() + 15
    while time.time() < end:
        if skip_requested():
            say("Skipped: the sticks are used as they are.")
            return base
        axes = snapshot(js)[0]
        away = False
        for a in used:
            if a >= len(axes):
                continue
            lo[a], hi[a] = min(lo[a], axes[a]), max(hi[a], axes[a])
            if abs(axes[a] - rest[a]) > 0.15:
                away = True
        if away:
            moved, quiet_since = True, None
        elif moved:
            quiet_since = quiet_since or time.time()
            if time.time() - quiet_since > 0.8:
                break
        time.sleep(0.01)
    if not moved:
        say("No stick movement seen: the sticks are used as they are.")
        return base
    base = settle(js, base)
    rest = base[0]
    for cfg in mapping["sticks"].values():
        a = cfg["axis"]
        if a not in lo or a >= len(rest):
            continue
        cfg["center"] = round(rest[a], 3)
        if hi[a] - cfg["center"] >= 0.6:       # only trust a travel that is clearly a real push
            cfg["hi"] = round(hi[a], 3)
        if cfg["center"] - lo[a] >= 0.6:
            cfg["lo"] = round(lo[a], 3)
    say("Sticks calibrated.")
    return base


def run_wizard(js, name):
    say("\n=== Button setup for: %s ===" % name)
    say("If the pad has an Analog / Mode button, press it so the sticks work.")
    say("Press each button when asked. Press Enter on the keyboard to skip one.")
    say("Don't touch the pad for a moment...")
    time.sleep(1.5)
    base = snapshot(js)
    mapping = {"buttons": {}, "sticks": {}}
    taken = {}        # physical input -> the Xbox button it already belongs to
    for key, label in SETUP_BUTTONS:
        while True:
            base = settle(js, base)
            say_part("Press %s ..." % label)
            ctrl = detect_control(js, base)
            if ctrl is None:
                say(" skipped")
                break
            owner = taken.get(ctrl_key(ctrl))
            if owner:
                say(" that one is already %s. Press a different one (Enter skips)." % owner)
                continue
            say(" ok")
            mapping["buttons"][key] = ctrl
            taken[ctrl_key(ctrl)] = key
            break
    used_axes = {}    # axis number -> what already uses it
    for key, ctrl in mapping["buttons"].items():
        if ctrl.get("t") == "axis":
            used_axes.setdefault(ctrl["i"], key)
    for key, label in SETUP_STICKS:
        while True:
            base = settle(js, base)
            say_part("%s ..." % label)
            cfg = detect_axis(js, base)
            if cfg is None:
                say(" skipped")
                break
            owner = used_axes.get(cfg["axis"])
            if owner:
                say(" that stick direction is already %s. Move the other one (Enter skips)." % owner)
                continue
            say(" ok")
            mapping["sticks"][key] = cfg
            used_axes[cfg["axis"]] = key
            break
    base = calibrate_sticks(js, mapping, base)
    settle(js, base)
    say("Setup finished and saved.\n")
    return mapping


# --------------------------------------------------------------------------
# Vibration
# --------------------------------------------------------------------------
def _pad(payload, n):
    return (list(payload) + [0] * n)[:n]


def _scale(v255, top):
    return max(0, min(top, int(round(v255 * top / 255.0))))


# Each function returns the list of HID output payloads (without the report id), already padded
# to the report size n. 'strong' = big/left motor, 'weak' = small/right motor, both 0..255.
def cmds_pl(n, strong, weak):      # Twin / GreenAsia 0x03 (Linux hid-pl)
    s, w = _scale(strong, 0x7F), _scale(weak, 0x7F)
    if n >= 8:                     # four 16-bit values: 0, 0, strong, weak  (confirmed on hardware)
        return [_pad([0, 0, 0, 0, s, 0, w, 0], n)]
    return [_pad([0, 0, s, w], n)]  # four 8-bit values


def cmds_zp(n, strong, weak):      # Zeroplus (Linux hid-zpff)
    return [_pad([0x00, 0x02, _scale(strong, 0x7F), _scale(weak, 0x7F)], n)]


def cmds_dr(n, strong, weak):      # DragonRise (Linux hid-dr)
    if strong or weak:
        w = 0x0B if weak == 0x0A else weak   # 0x0A makes the big motor run flat out
        return [_pad([0x51, 0x00, w, 0x00, strong, 0x00, 0x00], n), _pad([0xFA, 0xFE], n)]
    return [_pad([0xF3], n)]


def cmds_ga(n, strong, weak):      # GreenAsia 0x12 (Linux hid-gaff)
    s, w = _scale(strong, 0xFE), _scale(weak, 0xFE)
    return [_pad([0x51, 0x00, w, 0x00, s, 0x00], n), _pad([0xFA, 0xFE], n)]


def cmds_sj(n, strong, weak):      # SmartJoy PLUS (Linux hid-sjoy); small motor is on/off only
    return [_pad([0x01, 1 if weak else 0, strong], n)]


class Family:
    def __init__(self, name, title, min_len, exact, func):
        self.name, self.title, self.min_len, self.exact, self.commands = name, title, min_len, exact, func


# Order matters: it is the order --learn tries them in.
FAMILIES = [
    Family("dr", "DragonRise", 7, (7,), cmds_dr),
    Family("ga", "GreenAsia 0x12", 6, (6,), cmds_ga),
    Family("sj", "SmartJoy PLUS", 3, (3,), cmds_sj),
    Family("zp", "Zeroplus", 4, (4,), cmds_zp),
    Family("pl", "Twin / GreenAsia 0x03", 4, (4, 8), cmds_pl),
]
FAMILY_BY_NAME = {f.name: f for f in FAMILIES}


def learn_candidates(endpoints):
    """(family, endpoints) pairs to try: exact report-size matches first, then looser ones."""
    exact_first, loose = [], []
    for fam in FAMILIES:
        exact = [ep for ep in endpoints if (ep[1] - 1) in fam.exact]
        rest = [ep for ep in endpoints if (ep[1] - 1) >= fam.min_len and ep not in exact]
        if exact:
            exact_first.append((fam, exact))
        if rest:
            loose.append((fam, rest))
    return exact_first + loose


# --------------------------------------------------------------------------
# Asking the internet which vibration method a pad uses
# --------------------------------------------------------------------------
DEFINE_RE = re.compile(r"#define[ \t]+(\w+)[ \t]+\(?(\w+)")
DEVICE_RE = re.compile(r"HID_USB_DEVICE\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)")


def _fetch_kernel_file(name, expect, order):
    """(text, None) or (None, reasons). Tries each source; the one that worked moves to the front."""
    reasons = []
    for base in list(order):
        text, why = _get(base + name)
        if text is not None and expect not in text:
            text, why = None, "got a page that is not the kernel file"
        if text is not None:
            order.remove(base)
            order.insert(0, base)
            return text, None
        reasons.append("%s: %s" % (base.split("/")[2], why))
    return None, reasons


def _number(token, defines, depth=0):
    """'0x0810', or a name such as USB_VENDOR_ID_GAMERON (looked up in the #defines), -> number."""
    try:
        return int(token, 0)
    except ValueError:
        pass
    if depth < 5 and token in defines:
        return _number(defines[token], defines, depth + 1)
    return None


def fetch_kernel_table():
    """Download the Linux HID force-feedback drivers and collect EVERY USB ID they list.
    It runs in a background thread, so it only returns data (it never prints).
    Returns {"table": {"vvvv:pppp": method}, "complete": bool, "failed": bool, "lines": [text]}."""
    order = list(KERNEL_SOURCES)
    ids, why = _fetch_kernel_file("hid-ids.h", "#define USB_VENDOR_ID_", order)
    if ids is None:
        return {"table": {}, "complete": False, "failed": True,
                "lines": ["could not read: hid-ids.h: " + "; ".join(why)]}
    host = order[0].split("/")[2]
    defines = dict(DEFINE_RE.findall(ids))
    table, problems = {}, []
    for fname, method in KERNEL_DRIVERS.items():
        src, why = _fetch_kernel_file(fname, "hid_device_id", order)
        if src is None:
            problems.append("could not read: %s: %s" % (fname, "; ".join(why)))
            continue
        local = dict(defines)
        local.update(DEFINE_RE.findall(src))
        for v, p in DEVICE_RE.findall(src):
            vid, pid = _number(v, local), _number(p, local)
            if vid is not None and pid is not None:
                table.setdefault("%04x:%04x" % (vid, pid), method)
    lines = problems or ["read %d files from %s (%d pads listed)" % (len(KERNEL_DRIVERS) + 1, host, len(table))]
    return {"table": table, "complete": not problems, "failed": not table and bool(problems), "lines": lines}


class KernelLookup:
    """Which Linux force-feedback driver (if any) lists a USB ID?

    The whole list of IDs is downloaded ONCE, in a background thread (so a pad plugged in during a
    game never freezes the other pads), and kept in the cache file for a week. A pad that is "not
    listed" is therefore not asked about again until the list is a week old (or you use --recheck).
    Everything except the download itself runs on the main thread."""

    def __init__(self, cache, offline, recheck):
        known = cache.get("kernel")
        if not isinstance(known, dict) or not isinstance(known.get("table"), dict):
            cache.pop("kernel", None)   # damaged cache: start again
        self.cache = cache
        self.offline = offline
        self.recheck = recheck
        self.results = queue.Queue()
        self.running = False
        self.failed_at = 0.0
        self.failed_why = ""

    @staticmethod
    def key(vid, pid):
        return "%04x:%04x" % (vid, pid)

    def method_for(self, vid, pid):
        if not vid or not pid:
            return None
        return (self.cache.get("kernel") or {}).get("table", {}).get(self.key(vid, pid))

    def status(self, vid, pid):
        """("method", name) | ("none", age in seconds) | ("need", None) | ("blocked", reason)"""
        method = self.method_for(vid, pid)
        if method:
            return "method", method
        known = self.cache.get("kernel") or {}
        age = time.time() - known.get("time", 0)
        if known.get("complete") and age < LOOKUP_TTL and not self.recheck:
            return "none", age
        if self.offline:
            return "blocked", "--offline is on"
        if self.running:
            return "need", None
        if time.time() - self.failed_at < RETRY_AFTER:
            return "blocked", self.failed_why
        return "need", None

    def start(self):
        if self.running or self.offline:
            return
        self.running = True
        threading.Thread(target=self._work, name="kernel-lookup", daemon=True).start()

    def _work(self):
        try:
            result = fetch_kernel_table()
        except Exception as err:
            result = {"table": {}, "complete": False, "failed": True, "lines": ["unexpected error: %s" % err]}
        self.results.put(result)

    def poll(self):
        """Main thread. The finished download (a dict), or None if nothing has finished."""
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return None
        self.running = False
        self.recheck = False
        if not result["complete"]:
            self.failed_at = time.time()
            self.failed_why = "the last internet lookup did not finish, it is retried in a few minutes"
        if result["table"]:
            table = dict((self.cache.get("kernel") or {}).get("table", {}))
            table.update(result["table"])
            self.cache["kernel"] = {"time": time.time(), "complete": result["complete"], "table": table}
            save_json(CACHE_FILE, self.cache)
        return result


class Tuning:
    """Per-pad vibration tweak. Cheap motors often do not spin at low values, so light game vibration
    vanishes: `min_start` lifts any vibration that is on to at least that level (0..255), and `gain`
    multiplies the strength. Saved per VID:PID by --tune."""

    def __init__(self, gain=1.0, min_start=0):
        self.gain = float(gain)
        self.min_start = int(min_start)

    @property
    def is_default(self):
        return self.gain == 1.0 and self.min_start == 0

    def one(self, value):
        value = int(value)
        if value <= 0:
            return 0                      # off stays off
        value = int(round(value * self.gain))
        if value <= 0:
            return 0                      # a gain below 1 can switch a very light vibration off
        return max(0, min(255, max(value, self.min_start)))

    def apply(self, large, small):
        return self.one(large), self.one(small)

    @classmethod
    def from_config(cls, entry):
        if not isinstance(entry, dict):
            return cls()
        try:
            gain = float(entry.get("gain", 1.0))
        except (TypeError, ValueError):
            gain = 1.0
        try:
            start = int(entry.get("min", 0))
        except (TypeError, ValueError):
            start = 0
        return cls(max(0.1, min(4.0, gain)), max(0, min(255, start)))

    def to_config(self):
        return {"gain": round(self.gain, 2), "min": self.min_start}

    def describe(self):
        return "strength x%.1f, light vibration starts at %d" % (self.gain, self.min_start)


class HidOut:
    """Opens the vibration (HID output) report of every HID interface of a VID/PID."""

    def __init__(self):
        self.groups = {}    # (vid, pid) -> list of (report, length_with_id, report_id)
        self.devices = {}   # (vid, pid) -> the HID devices that were opened for it

    def endpoints(self, vid, pid):
        key = (vid, pid)
        if hid is None:
            return []
        if not self.groups.get(key):
            eps = []
            try:
                devs = hid.HidDeviceFilter(vendor_id=vid, product_id=pid).get_devices()
            except Exception as err:
                say("  (could not list HID interfaces: %s)" % err)
                devs = []
            for dev in devs:
                try:
                    dev.open(output_only=True)
                except Exception as err:
                    say("  (could not open a vibration interface: %s)" % err)
                    continue
                reports = dev.find_output_reports()
                if reports:  # like the Linux drivers, use the first output report
                    raw = list(reports[0].get_raw_data())
                    eps.append((reports[0], len(raw), raw[0]))
                    self.devices.setdefault(key, []).append(dev)
                else:
                    try:
                        dev.close()
                    except Exception:
                        pass
            self.groups[key] = eps
        return self.groups[key]

    def forget(self, vid, pid):
        """The pad went away: drop its (now dead) handles, so that plugging it in again opens fresh ones."""
        for dev in self.devices.pop((vid, pid), []):
            try:
                dev.close()
            except Exception:
                pass
        self.groups.pop((vid, pid), None)

    def close(self):
        for key in list(self.devices):
            for dev in self.devices.pop(key, []):
                try:
                    dev.close()
                except Exception:
                    pass
        self.groups = {}


_REPORTED = set()


def report_once(text):
    """Print a problem the first time it happens and stay quiet about repeats (a game can ask for
    vibration many times a second), and never print more than a handful of different ones."""
    if text in _REPORTED or len(_REPORTED) >= 12:
        return
    _REPORTED.add(text)
    say(text)


class HidSink:
    """Sends the game's vibration to the pad with a HID output report.

    The game's rumble callback only drops the newest value into this object. A small writer thread
    sends it - never more than about 60 times a second, newest value wins - so a game that ramps its
    vibration cannot flood the adapter, and a slow or stuck adapter can never block the game or the
    input loop. `sent` is only updated after Windows accepted the data, so a write that fails (even
    the one that switches the motors off) is retried until it works."""

    def __init__(self, endpoint, family, name="", tuning=None, on_fail=None):
        self.rep, self.length, self.rid = endpoint
        self.family = family
        self.name = name
        self.tuning = tuning or Tuning()
        self.on_fail = on_fail           # called from the writer thread after a run of failed writes
        self.label = "direct HID command [%s]" % family.name
        self.cond = threading.Condition()
        self.pending = (0, 0)            # what the game wants now
        self.sent = (0, 0)               # what the pad has really been given
        self.closing = False
        self.thread = None
        self.fails = 0
        self.t_sent = 0.0
        self.next_try = 0.0

    def _start_thread(self):             # the caller holds self.cond
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._writer, name="vibration-writer", daemon=True)
            self.thread.start()

    def set(self, large, small):
        values = self.tuning.apply(int(large), int(small))
        with self.cond:
            if self.closing:
                return
            self.pending = values
            if values == self.sent:
                self.cond.notify_all()
                return
            self._start_thread()
            self.cond.notify_all()

    def tick(self, now):
        pass    # the writer thread does the work (SdlSink needs this call, so both have it)

    def close(self, send_stop=True, timeout=1.0):
        """Switch the motors off and end the writer thread. Waits (briefly) until the stop was written."""
        with self.cond:
            self.closing = True
            self.pending = (0, 0) if send_stop else self.sent
            if self.pending != self.sent:
                self._start_thread()
            self.cond.notify_all()
            thread = self.thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout)

    def _write(self, values):
        large, small = values
        try:
            for payload in self.family.commands(self.length - 1, large, small):
                if self.rep.send([self.rid] + payload) is False:
                    raise OSError("Windows did not accept the data")
            return True
        except Exception as err:
            text = (str(err).strip() or err.__class__.__name__)[:120]
            report_once("(vibration command to %s failed: %s - shown once; the pad may be unplugged, "
                        "or try --swap, --no-hide or --learn)" % (self.name or "the pad", text))
            return False

    def _writer(self):
        stop_tries = 0
        while True:
            with self.cond:
                while self.pending == self.sent and not self.closing:
                    self.cond.wait()
                want, closing = self.pending, self.closing
            if want == self.sent:
                return                                   # closing, and the motors are already off
            now = time.monotonic()
            due = self.next_try if self.fails else self.t_sent + RUMBLE_GAP
            if closing:
                due = now if not self.fails else min(due, now + 0.1)   # the stop goes out at once
            if due > now:
                with self.cond:
                    self.cond.wait(min(due - now, 0.25))  # newer values may arrive meanwhile
                continue
            ok = self._write(want)
            self.t_sent = time.monotonic()
            if ok:
                self.sent, self.fails = want, 0
                continue
            self.fails += 1
            self.next_try = self.t_sent + min(2.0, 0.1 * (2 ** min(self.fails, 5)))
            if self.fails == 6 and self.on_fail is not None:
                try:
                    self.on_fail()
                except Exception:
                    pass
            if closing:
                stop_tries += 1
                if stop_tries >= 3:
                    return


class SdlSink:
    """Vibration through SDL (works for many Xbox/PlayStation/Switch-style pads)."""

    def __init__(self, obj, name, tuning=None):
        self.obj = obj
        self.name = name
        self.tuning = tuning or Tuning()
        self.pending = (0, 0)
        self.sent = (0, 0)
        self.t_sent = 0.0
        self.dead = False
        self.closed = False
        self.label = "SDL rumble"

    def set(self, large, small):
        if not self.closed:
            self.pending = self.tuning.apply(int(large), int(small))

    def close(self, send_stop=True, timeout=None):
        self.pending = (0, 0)
        self.tick(time.time())
        self.closed = True

    def tick(self, now):
        if self.dead:
            return
        want = self.pending
        if want != self.sent or (want != (0, 0) and now - self.t_sent > 4.0):
            if want != (0, 0) and want != self.sent and now - self.t_sent < RUMBLE_GAP:
                return    # newest value wins: it goes out a few milliseconds later (a stop never waits)
            why = ""
            try:
                if want == (0, 0):
                    self.obj.stop_rumble()
                    ok = True
                else:
                    ok = self.obj.rumble(want[0] / 255.0, want[1] / 255.0, 10000)
            except Exception as err:
                ok = False
                why = " [%s]" % ((str(err).strip() or err.__class__.__name__)[:100])
            self.sent = want
            self.t_sent = now
            if not ok:
                self.dead = True
                say("(vibration: SDL cannot make %s vibrate%s - run  %s --learn  to look for a method)"
                    % (self.name, why, RUN_CMD))


# --------------------------------------------------------------------------
# HidHide: hide the real controllers from games
# --------------------------------------------------------------------------
def process_image_paths():
    paths = []
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(k32.GetCurrentProcess(), 0, buf, ctypes.byref(size)):
            paths.append(buf.value)
    except Exception:
        pass
    exes = (sys.executable,) if FROZEN else (sys.executable, getattr(sys, "_base_executable", None))
    for p in exes:
        if p and p.lower().endswith(".exe") and p.lower() not in [x.lower() for x in paths]:
            paths.append(p)
    return paths


def _which_on_path(name):
    """Like shutil.which, but it never looks in the current folder (Windows searches it first)."""
    here = os.path.normcase(os.path.abspath(os.getcwd()))
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or os.path.normcase(os.path.abspath(folder)) == here:
            continue
        cand = os.path.join(folder, name)
        if os.path.isfile(cand):
            return cand
    return None


def find_hidhide_cli():
    """HidHideCLI.exe: HidHide's own install folder first (so a look-alike file in the current folder or
    on a PATH entry anyone can write to is never run with administrator rights), then the registry,
    and only then PATH."""
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432"), r"C:\Program Files"]
    for root in roots:
        if not root:
            continue
        base = os.path.join(root, "Nefarius Software Solutions", "HidHide")
        for sub in ("", "x64"):
            cand = os.path.join(base, sub, "HidHideCLI.exe")
            if os.path.exists(cand):
                return cand
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CLASSES_ROOT):
            try:
                key = winreg.OpenKey(hive, r"SOFTWARE\Nefarius Software Solutions e.U.\Nefarius Software Solutions e.U. HidHide")
                value, _ = winreg.QueryValueEx(key, "Path")
                for sub in ("", "x64"):
                    cand = os.path.join(os.path.dirname(value) if value.lower().endswith(".exe") else value,
                                        sub, "HidHideCLI.exe")
                    if os.path.exists(cand):
                        return cand
            except OSError:
                continue
    except Exception:
        pass
    return _which_on_path("HidHideCLI.exe")


def _q(text):
    return '"%s"' % text


# A USB pad's device path reads  HID\VID_054C&PID_09CC\...  but over Bluetooth it reads
# HID\{00001124-...}_VID&0002054C_PID&09CC\...  (or ..._DEV_VID&02045E_PID&0B13...  for Bluetooth LE):
# the first digits of that VID are the "source" of the ID, the last four are the vendor.
_USB_PATH_RE = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", re.I)
_BT_PATH_RE = re.compile(r"VID&[0-9A-F]*?([0-9A-F]{4})_PID&([0-9A-F]{4})", re.I)


def vidpid_from_path(path):
    """(vid, pid) from a Windows device path, USB or Bluetooth style; None if there is none in it."""
    m = _USB_PATH_RE.search(path) or _BT_PATH_RE.search(path)
    if not m:
        return None
    return int(m.group(1), 16), int(m.group(2), 16)


class HidHide:
    def __init__(self, enabled):
        self.enabled = enabled
        self.cli = find_hidhide_cli() if enabled else None
        self.read = False
        self.cloak_was_on = False
        self.inv_was_on = False
        self.hidden_before = set()
        self.apps_before = set()
        self.added_devices = []
        self.added_apps = []
        self.cloak_added = False
        self.inv_changed = False

    @property
    def ready(self):
        return bool(self.enabled and self.cli)

    def _run(self, parts):
        cmd = '"%s" %s' % (self.cli, " ".join(parts))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=20, creationflags=NO_WINDOW)
            return r.returncode, r.stdout or "", r.stderr or ""
        except Exception as err:
            return 1, "", str(err)

    def _read_state(self):
        rc, out, err = self._run(["--cloak-state", "--inv-state", "--dev-list", "--app-list"])
        if rc != 0:
            return False, (err or out).strip()
        self.cloak_was_on = "--cloak-on" in out
        self.inv_was_on = "--inv-on" in out
        self.hidden_before = set(m.lower() for m in re.findall(r'--dev-hide\s+"([^"]+)"', out))
        self.apps_before = set(m.lower() for m in re.findall(r'--app-reg\s+"([^"]+)"', out))
        self.read = True
        return True, ""

    def _inventory(self, flag):
        rc, out, err = self._run([flag])
        devices = []
        try:
            data = json.loads(out[out.index("["):])
        except Exception:
            return devices

        def walk(node):
            if isinstance(node, dict):
                if "deviceInstancePath" in node:
                    devices.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(data)
        return devices

    def _targets(self, vidpids):
        wanted = set(vidpids)
        found = []
        for dev in self._inventory("--dev-gaming"):  # gaming devices only, never keyboards/mice
            path = str(dev.get("deviceInstancePath", ""))
            ids = vidpid_from_path(path)
            if ids is None or not path.upper().startswith("HID\\") or dev.get("present", True) is False:
                continue
            if ids in wanted and path not in found:
                found.append(path)
        return found

    def apply(self, vidpids):
        """Hide the real controllers (by VID/PID) from every program except this one."""
        if not self.ready or not vidpids:
            return False
        if not self.read:
            ok, why = self._read_state()
            if not ok:
                say("HidHide: could not talk to it (%s)" % (why or "unknown error"))
                say("         Close the 'HidHide Configuration Client' window if it is open.")
                return False
        paths = self._targets(vidpids)
        if not paths:
            say("HidHide does not list this controller as a gaming device, so it was not hidden.")
            say("  (You can hide it by hand in the 'HidHide Configuration Client'.)")
            return False
        new_devs = [p for p in paths if p.lower() not in self.hidden_before and p not in self.added_devices]
        new_apps = [e for e in process_image_paths()
                    if e.lower() not in self.apps_before and e not in self.added_apps]
        parts = []
        if self.inv_was_on and not self.inv_changed:
            parts.append("--inv-off")
        for exe in new_apps:
            parts += ["--app-reg", _q(exe)]
        for path in new_devs:
            parts += ["--dev-hide", _q(path)]
        if not self.cloak_was_on and not self.cloak_added:
            parts.append("--cloak-on")
        if parts:
            rc, out, err = self._run(parts)
            if rc != 0:
                say("HidHide: it refused the change (%s)" % ((err or out).strip() or rc))
                return False
            if self.inv_was_on:
                self.inv_changed = True
            if not self.cloak_was_on:
                self.cloak_added = True
        self.added_devices += new_devs
        self.added_apps += new_apps
        save_json(STATE_FILE, {"devices": self.added_devices, "apps": self.added_apps,
                               "cloak_added": self.cloak_added, "inv_changed": self.inv_changed,
                               "cli": self.cli})
        return True

    def restore(self):
        """Undo only what this program added."""
        if not self.ready:
            return
        parts = []
        for path in self.added_devices:
            parts += ["--dev-unhide", _q(path)]
        for exe in self.added_apps:
            parts += ["--app-unreg", _q(exe)]
        if self.cloak_added:
            parts.append("--cloak-off")
        if self.inv_changed:
            parts.append("--inv-on")
        if parts:
            self._run(parts)
        self.added_devices, self.added_apps = [], []
        self.cloak_added = self.inv_changed = False
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass

    def restore_leftover(self):
        """Clean up after a previous run that did not exit cleanly."""
        state = load_json(STATE_FILE, None, keep_bad_copy=True)
        if not state or not self.ready:
            return False
        self.added_devices = state.get("devices", [])
        self.added_apps = state.get("apps", [])
        self.cloak_added = bool(state.get("cloak_added"))
        self.inv_changed = bool(state.get("inv_changed"))
        say("Cleaning up what a previous run left in HidHide...")
        self.restore()
        return True


# --------------------------------------------------------------------------
# Devices and slots
# --------------------------------------------------------------------------
def guid_info(js):
    try:
        guid = js.get_guid().lower()
    except Exception:
        guid = ""
    vid = pid = None
    # SDL's GUID holds the USB IDs only in this layout (bus, crc, VID, 0000, PID, 0000, ...). A pad that
    # has no IDs gets the first letters of its NAME there instead, which must never be read as numbers.
    if (len(guid) == 32 and not guid.startswith(XINPUT_SIG)
            and guid[12:16] == "0000" and guid[20:24] == "0000"):
        try:
            vid = int(guid[10:12] + guid[8:10], 16) or None
            pid = int(guid[18:20] + guid[16:18], 16) or None
        except ValueError:
            pass
    return guid or ("name:" + js.get_name()), vid, pid


def instance_id(js):
    try:
        return js.get_instance_id()
    except Exception:
        return id(js)


# Devices that report axes and buttons but are not gamepads. Bridging one would hide it from the games
# that really want it (a racing wheel, a flight stick), so they are left alone unless you say --include.
NON_PAD_WORDS = ("wheel", "throttle", "rudder", "pedal", "hotas", "flight", "yoke",
                 "keyboard", "mouse", "trackball", "spacenavigator")


def skip_reason(js, guid, vid, pid=None, ignore=(), include=()):
    name = js.get_name().lower()
    if guid.startswith(XINPUT_SIG) or vid == 0x045E or any(k in name for k in ("xinput", "xbox", "x360", "xusb")):
        return "already an Xbox pad, games support it natively"
    if any(k in name for k in ("vjoy", "vigem", "virtual")):
        return "a virtual device"
    if model_matches(ignore, vid, pid, name):
        return "on your ignore list"
    if model_matches(include, vid, pid, name):
        return None      # you asked for it: no guessing about what kind of device it is
    if js.get_numaxes() < 2 or (js.get_numbuttons() < 4 and js.get_numhats() < 1):
        return "does not look like a gamepad"
    word = next((w for w in NON_PAD_WORDS if w in name), None)
    if word:
        hint = "%04x:%04x" % (vid, pid) if vid and pid else word
        return "looks like a %s, not a gamepad (to bridge it anyway: --include %s)" % (word, hint)
    return None


def sdl_knows(index):
    if sdl_controller is None or CTRL_CONSTS is None:
        return False
    try:
        return bool(sdl_controller.is_controller(index))
    except Exception:
        return False


class Slot:
    def __init__(self, js, number, name, guid, vid, pid, mode, mapping, source, controller=None):
        self.js, self.number, self.name = js, number, name
        self.guid, self.vid, self.pid = guid, vid, pid
        self.mode, self.mapping, self.source, self.controller = mode, mapping, source, controller
        self.iid = instance_id(js)
        self.pad = None
        self.sink = None
        self.last = None
        self.lost = False
        self.vibration = None   # True / False once Bridge.check_vibration has looked
        self.waiting = False    # True while an internet lookup for this pad is still running
        self.muted = False      # True while paused: the game's vibration is not passed on
        self.dpad_stick = False     # --dpad-stick
        self.combo = None           # a GuideCombo with --guide
        self.native_guide = False   # with --guide, a pad's own Guide / Home button is passed on too
        self.profile = None         # a Profile (dead zones, remapping ...) or None = the plain behaviour
        self.extra = ()             # extra inputs ("B12", "P1" ...) the profile asked for

    def read(self, now=None):
        now = time.time() if now is None else now
        profile = self.profile
        dz = 0.0 if profile is not None else None     # a profile does its own dead zone
        if self.mode == "controller":
            state = controller_state(self.controller, self.native_guide, dz)
        else:
            state = joystick_state(self.js, self.mapping, dz)
        if profile is not None:
            if self.extra:
                more = read_extras(self.js, self.controller, self.extra)
                if more:
                    state = (state[0] | more,) + state[1:]
            state = profile.apply(state, now)
        if self.dpad_stick:
            state = dpad_to_stick(state)
        if self.combo is not None:
            state = self.combo.apply(state, now)
        return state

    def start(self, rumble_sink):
        self.pad = vg.VX360Gamepad()
        self.sink = rumble_sink

        def on_rumble(client, target, large_motor, small_motor, led_number, user_data):
            sink = self.sink
            if sink is not None:
                try:
                    if self.muted:
                        large_motor = small_motor = 0
                    sink.set(large_motor, small_motor)
                except Exception as err:
                    report_once("(vibration: could not pass the game's rumble on to Pad %d: %s - shown once)"
                                % (self.number, (str(err).strip() or err.__class__.__name__)[:100]))
        self.pad.register_notification(callback_function=on_rumble)
        self._cb = on_rumble  # keep a reference

    def stop(self):
        try:
            if self.sink:
                self.sink.close()    # motors off (a failed stop command is retried for a moment)
        except Exception:
            pass
        if self.pad is not None:
            try:
                self.pad.unregister_notification()
            except Exception:
                pass
            try:
                self.pad.reset()
                self.pad.update()
            except Exception:
                pass
            self.pad = None


class Bridge:
    def __init__(self, opts):
        self.opts = opts
        self.cfg = load_config()
        self.slots = []
        self.seen = {}      # instance id -> Slot or None (skipped)
        self.hid_out = HidOut()
        self.hh = HidHide(enabled=not opts.no_hide)
        self.only = opts.only
        self.interactive = bool(sys.stdin) and sys.stdin.isatty()
        self.wizard_done = set()
        self.closed = False
        self.close_lock = threading.Lock()
        self.warned_no_hidhide = False
        self.hinted = set()
        self.offline = opts.offline
        self.cache = load_cache()
        self.kernel = KernelLookup(self.cache, self.offline, opts.recheck)
        self.read_options()
        self._cfg_stamp = self._config_stamp()  # to notice when you edit the settings file while it runs
        self._cfg_next = 0.0
        self._told = set()                      # profile mistakes already shown
        self.paused = threading.Event()        # set from the tray: stop passing input on, un-hide the real pads
        self.is_paused_now = False
        self.quit_event = threading.Event()    # set from the tray: leave the main loop
        self.heal_requests = queue.Queue()     # (vid, pid) pairs whose vibration channel stopped answering
        self.heals = {}                        # (vid, pid) -> how many times it was opened again
        self.tray = None
        self.undo_boost = None

    def read_options(self):
        """The settings that come from both the command line and the "options" part of the config."""
        options = self.cfg.get("options", {})
        self.dpad_stick = bool(self.opts.dpad_stick or options.get("dpad_as_stick"))
        self.guide = bool(self.opts.guide or options.get("guide"))
        self.ignore = model_set(self.opts.ignore, options.get("ignore"))
        self.include = model_set(self.opts.include, options.get("include"))

    def skip(self, js, guid, vid, pid):
        return skip_reason(js, guid, vid, pid, self.ignore, self.include)

    def save_cfg(self):
        save_json(CONFIG_FILE, self.cfg)
        self._cfg_stamp = self._config_stamp()     # our own save is not "you edited the file"

    @staticmethod
    def _config_stamp():
        try:
            st = os.stat(CONFIG_FILE)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def profile_for(self, guid, vid, pid):
        """The Profile for a pad: "*" (all pads), then its model (VID:PID), then its exact GUID, each one
        adding to / overriding the one before. None if there is no profile at all (= plain behaviour)."""
        table = dict((str(k).strip().lower(), v) for k, v in self.cfg.get("profiles", {}).items())
        keys = ["*"]
        if vid and pid:
            keys.append("%04x:%04x" % (vid, pid))
        if guid:
            keys.append(guid.lower())
        layers = [table[k] for k in keys if isinstance(table.get(k), dict)]
        return Profile(merge_profile(layers)) if layers else None

    def configure_slot(self, slot):
        """Apply the profile and the dpad / guide options to one pad (at start, and after a settings reload)."""
        prof = self.profile_for(slot.guid, slot.vid, slot.pid)
        slot.profile = prof
        slot.extra = tuple(sorted(prof.extra_inputs())) if prof is not None else ()
        dpad, guide = self.dpad_stick, self.guide
        if prof is not None:
            if prof.dpad_as_stick is not None:
                dpad = prof.dpad_as_stick
            if prof.guide is not None:
                guide = prof.guide
            for text in prof.problems:        # each mistake is told once (not counted in report_once's small limit)
                line = "(profile for %s: %s - that entry is skipped)" % (slot.name, text)
                if line not in self._told:
                    self._told.add(line)
                    say(line)
        slot.dpad_stick = dpad
        if guide:
            if slot.combo is None:
                slot.combo = GuideCombo()
            slot.native_guide = True
        else:
            slot.combo, slot.native_guide = None, False
        slot.last = None       # send the state again, in case the new settings change it

    def check_config(self, now):
        """Called from the main loop about once a second: if you edited the settings file, use the new
        values (profiles, vibration tuning, options) without a restart. A file with a mistake in it is
        ignored (with a message) and the settings from before stay in use."""
        if now < self._cfg_next:
            return
        self._cfg_next = now + 1.0
        stamp = self._config_stamp()
        if stamp == self._cfg_stamp:
            return
        self._cfg_stamp = stamp
        if stamp is None:
            return
        new = load_json(CONFIG_FILE, None)      # never renames the file, you may be in the middle of editing it
        if not isinstance(new, dict):
            say("(%s cannot be read right now - there may be a typo in it. The settings from before stay in use; "
                "fix the file and save it again.)" % os.path.basename(CONFIG_FILE))
            return
        self.cfg = normalize_config(new)
        self.read_options()
        say("Settings file changed: applied.")
        for slot in self.slots:
            self.configure_slot(slot)
            if slot.sink is not None and hasattr(slot.sink, "tuning"):
                slot.sink.tuning = self.tuning_for(slot.vid, slot.pid)
            if slot.profile is not None:
                say("  Pad %d profile: %s" % (slot.number, slot.profile.describe()))

    def swap_for(self, vid, pid):
        """Should this model's adapter ports be swapped?  --swap wins, then what --test saved."""
        if self.opts.swap:
            return True
        saved = self.cfg.get("swap_by_model", {}).get("%04x:%04x" % (vid, pid))
        if saved is not None:
            return bool(saved)
        return bool(self.cfg.get("swap_ports"))

    def tuning_for(self, vid, pid):
        if not vid or not pid:
            return Tuning()
        return Tuning.from_config(self.cfg.get("tuning", {}).get("%04x:%04x" % (vid, pid)))

    # ---- what the tray icon uses --------------------------------------------
    def status_text(self):
        if self.is_paused_now:
            return "Pad Bridge - paused"
        count = len([s for s in self.slots if s.pad is not None])
        if not count:
            return "Pad Bridge - waiting for a controller"
        return "Pad Bridge - %d pad%s running" % (count, "" if count == 1 else "s")

    def is_paused(self):
        return self.paused.is_set()

    def toggle_pause(self):
        if self.paused.is_set():
            self.paused.clear()
        else:
            self.paused.set()

    def request_quit(self):
        self.quit_event.set()

    # ---- choosing a layout ------------------------------------------------
    def resolve(self, js, index, guid, vid, pid, name, interactive):
        """Pick the button layout. Order: your --setup, your saved layout, a layout checked on real
        hardware, the community list, an untested look-alike, then (only if you say so) a basic guess."""
        if self.opts.setup and guid not in self.wizard_done:
            return self.setup_pad(js, guid, name)
        saved = self.cfg["mappings"].get(guid)
        if saved == GUESS_LAYOUT:
            # Older versions saved the basic guess as if it were a real layout. Forget it, so you are
            # asked again (or the community list gets its chance).
            del self.cfg["mappings"][guid]
            self.save_cfg()
            saved = None
        if saved:
            return "joystick", saved, "saved layout"
        known = KNOWN_LAYOUTS.get((vid, pid))
        if known:
            layout = known
            if (vid, pid) == (0x0810, 0x0001):
                old = load_json(OLD_TWIN_FILE, None)
                if isinstance(old, dict) and "buttons" in old:
                    layout = old  # keep what you set up with the first version
            return "joystick", layout, "known layout"
        if sdl_knows(index):
            try:
                ctrl = sdl_controller.Controller(index)
                return "controller", ctrl, "community controller list"
            except Exception:
                pass
        untested = UNTESTED_LAYOUTS.get((vid, pid))
        if interactive:
            if untested:
                answer = input("\n'%s' has no confirmed layout yet, but it is probably wired like the Twin adapter.\n"
                               "Press Enter to try that layout (check it with --show),\n"
                               "or type S to set it up by pressing buttons (about a minute): " % name)
                if not answer.strip().lower().startswith("s"):
                    return "joystick", untested, UNTESTED_NOTE
            else:
                answer = input("\n'%s' has no known layout.\nPress Enter to set it up now (about a minute), "
                               "or type N to use a basic guess this time: " % name)
                if answer.strip().lower().startswith("n"):
                    return "joystick", GUESS_LAYOUT, GUESS_NOTE   # not saved: it asks again next time
            return self.setup_pad(js, guid, name)
        if untested:
            return "joystick", untested, UNTESTED_NOTE
        return "joystick", GUESS_LAYOUT, GUESS_NOTE

    def setup_pad(self, js, guid, name):
        self.wizard_done.add(guid)
        mapping = run_wizard(js, name)
        self.cfg["mappings"][guid] = mapping
        self.save_cfg()
        return "joystick", mapping, "your setup"

    # ---- adding and removing controllers -----------------------------------
    def consider(self, index, number, interactive, announce=True, layout=True):
        js = pygame.joystick.Joystick(index)
        iid = instance_id(js)
        if iid in self.seen:
            return None
        guid, vid, pid = guid_info(js)
        name = js.get_name()
        reason = self.skip(js, guid, vid, pid)
        if reason:
            self.seen[iid] = None
            if announce:
                say("  skipping '%s': %s" % (name, reason))
            return None
        if self.only is not None and number not in self.only:
            self.seen[iid] = None
            return None
        if len(self.slots) >= MAX_PADS:
            self.seen[iid] = None
            say("  skipping '%s': %d pads is the maximum" % (name, MAX_PADS))
            return None
        if layout:
            mode, mapping, source = self.resolve(js, index, guid, vid, pid, name, interactive)
        else:    # --test / --tune only need the vibration, not the button layout
            mode, mapping, source = "joystick", GUESS_LAYOUT, "not needed here"
        controller = mapping if mode == "controller" else None
        used = set(s.number for s in self.slots)
        number_free = next(n for n in range(1, MAX_PADS + 1) if n not in used)
        slot = Slot(js, number_free, name, guid, vid, pid, mode,
                    None if controller else mapping, source, controller)
        self.slots.append(slot)
        self.seen[iid] = slot
        return slot

    def family_for(self, vid, pid):
        if not vid or not pid:
            return None
        name = (self.cfg.get("rumble", {}).get("%04x:%04x" % (vid, pid))
                or BUILTIN_RUMBLE.get((vid, pid))
                or self.kernel.method_for(vid, pid))   # from the cached Linux driver list
        return FAMILY_BY_NAME.get(name)

    def make_sink(self, slot, tuned=True):
        tuning = self.tuning_for(slot.vid, slot.pid) if tuned else Tuning()
        fam = self.family_for(slot.vid, slot.pid)
        if fam:
            group = [s for s in self.slots if (s.vid, s.pid) == (slot.vid, slot.pid)]
            eps = list(self.hid_out.endpoints(slot.vid, slot.pid))
            if self.swap_for(slot.vid, slot.pid):
                eps.reverse()
            n = group.index(slot)
            if n < len(eps):
                if eps[n][1] - 1 >= fam.min_len:
                    key = (slot.vid, slot.pid)
                    return HidSink(eps[n], fam, slot.name, tuning,
                                   on_fail=lambda: self.heal_requests.put(key))
                say("  (this pad's vibration report is too short for method '%s'; use --learn)" % fam.name)
        obj = slot.controller if slot.mode == "controller" else slot.js
        if slot.vibration is not False and hasattr(obj, "rumble"):
            return SdlSink(obj, slot.name, tuning)
        return None

    # ---- the step-by-step vibration check ----------------------------------
    def sdl_can_rumble(self, slot):
        """True if SDL accepts a (barely felt, 30 ms) rumble command for this pad."""
        obj = slot.controller if slot.mode == "controller" else slot.js
        try:
            ok = bool(obj.rumble(0.01, 0.01, 30))
            time.sleep(0.05)
            obj.stop_rumble()
            return ok
        except Exception:
            return False

    def check_vibration(self, slot):
        """Print  Getting the ID / Vibration support / Pulling from the internet  for one pad."""
        say("\nPad %d: %s" % (slot.number, slot.name))
        has_id = bool(slot.vid and slot.pid)
        step("Getting the ID")
        if has_id:
            say(" done (%04X:%04X)" % (slot.vid, slot.pid))
        else:
            say(" not available")

        step("Vibration support")
        slot.vibration = bool(self.family_for(slot.vid, slot.pid) or self.sdl_can_rumble(slot))
        say(" yes" if slot.vibration else " no")
        if slot.vibration:
            return
        by_hand = "  (inputs only - run  %s --learn  to test it by hand)" % RUN_CMD
        if not has_id:
            say("  (no ID to look up - run  %s --learn  to test it by hand)" % RUN_CMD)
            return
        if self.offline:
            say("  (--offline is on, so nothing was pulled from the internet)")
            return

        step("Pulling from the internet")
        kind, detail = self.kernel.status(slot.vid, slot.pid)
        if kind == "need":
            slot.waiting = True
            self.kernel.start()   # a background thread: the pads keep working while it downloads
            say(" started in the background (the pad works meanwhile)")
        elif kind == "none":
            say(" skipped - checked %s and this pad is not listed (remembered for 7 days)" % days_ago(detail))
            say(by_hand)
        else:
            say(" skipped - %s" % (detail if kind == "blocked" else "this version has no such method"))
            say(by_hand)

    def poll_lookups(self):
        """Called all the time from the main loop: pick up a finished internet lookup."""
        result = self.kernel.poll()
        if result is None:
            return
        if not result["complete"]:
            say("\nInternet lookup: %s" % ("failed" if result["failed"] else "only partly worked"))
            for line in result["lines"]:
                say("    " + line)
        for slot in list(self.slots):
            if slot.waiting:
                slot.waiting = False
                self.finish_lookup(slot)

    def finish_lookup(self, slot):
        kind, detail = self.kernel.status(slot.vid, slot.pid)
        who = "Pad %d (%s)" % (slot.number, slot.name)
        if kind == "method":
            self.cfg.setdefault("rumble", {})[KernelLookup.key(slot.vid, slot.pid)] = detail
            self.save_cfg()
            slot.vibration = True
            say("%s: the Linux drivers list this pad (method '%s')." % (who, detail))
            self.attach_sink(slot)
        elif kind == "none":
            say("%s: not listed in the Linux vibration drivers (remembered for 7 days)." % who)
            say("  Inputs only - run  %s --learn  to test it by hand." % RUN_CMD)
        else:
            say("%s: could not check the internet, so inputs only for now." % who)

    def attach_sink(self, slot):
        """A vibration method turned up after the pad was already running: switch it on live."""
        if slot.pad is None:
            return   # not started yet; activate() will pick the method up
        sink = self.make_sink(slot)
        if not isinstance(sink, HidSink):
            say("  Pad %d: its vibration channel could not be opened - run  %s --learn  to check."
                % (slot.number, RUN_CMD))
            return
        old, slot.sink = slot.sink, sink
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        say("  Pad %d: vibration is now on [%s]" % (slot.number, sink.label))
        self.buzz([slot])

    def hide(self, slots):
        if self.opts.no_hide:
            return
        if not self.hh.cli:
            if not self.warned_no_hidhide:
                self.warned_no_hidhide = True
                say("HidHide is not installed, so games may also see the real controller.")
                say("  Get it (and reboot once): https://github.com/nefarius/HidHide/releases/latest")
            return
        vidpids = set((s.vid, s.pid) for s in self.slots if s.vid and s.pid)
        if self.hh.apply(vidpids):
            say("HidHide: real controllers are hidden from games (they come back when you quit).")
            # Safety net: if HidHide hid the pad from THIS program too, undo it.
            for vp in [v for v in vidpids if self.family_for(*v)]:
                if hid is not None and not self.hid_out.endpoints(*vp):
                    say("HidHide seems to block this program as well, so I switched it off again.")
                    say("  Try again with --no-hide, or tell me what Windows/Python you use.")
                    self.hh.restore()
                    self.hh.enabled = False
                    break

    def activate(self, slot):
        sink = self.make_sink(slot)
        self.configure_slot(slot)
        try:
            slot.start(sink)
        except Exception as err:
            say("  Pad %d: could not create a virtual Xbox 360 pad (%s)"
                % (slot.number, (str(err).strip() or err.__class__.__name__)[:120]))
            say("  Is the ViGEmBus driver installed?  https://github.com/nefarius/ViGEmBus/releases/latest")
            slot.stop()
            if slot in self.slots:
                self.slots.remove(slot)
            self.seen[slot.iid] = None
            return None
        vib = sink.label if sink else ("none yet (looking it up)" if slot.waiting else "none (inputs only)")
        if sink is None and not slot.waiting and slot.pid not in self.hinted:
            self.hinted.add(slot.pid)
            vib += " - run --learn to look for a vibration method"
        tuning = self.tuning_for(slot.vid, slot.pid)
        if sink is not None and not tuning.is_default:
            vib += ", " + tuning.describe()
        extras = []
        if slot.dpad_stick:
            extras.append("D-pad also steers the left stick")
        if slot.combo is not None:
            extras.append("hold Start+Back = Guide")
        if slot.profile is not None:
            extras.append("profile: " + slot.profile.describe())
        say("  Pad %d: %s -> virtual Xbox 360 pad %d   [layout: %s | vibration: %s%s]"
            % (slot.number, slot.name, slot.number, slot.source, vib,
               (" | input: " + ", ".join(extras)) if extras else ""))
        return sink

    def buzz(self, slots):
        sinks = [s.sink for s in slots if s.sink]
        if not sinks:
            return
        for sink in sinks:
            sink.set(255, 255)
            sink.tick(time.time())
        time.sleep(0.25)
        for sink in sinks:
            sink.set(0, 0)
            sink.tick(time.time())

    def remove(self, iid):
        slot = self.seen.pop(iid, None)
        if slot is None:
            return
        say("Controller unplugged: %s" % slot.name)
        slot.stop()
        if slot in self.slots:
            self.slots.remove(slot)
        if slot.vid and slot.pid and not any((s.vid, s.pid) == (slot.vid, slot.pid) for s in self.slots):
            # the last pad of this model is gone, so its HID handles are dead: a re-plug must open new ones
            self.hid_out.forget(slot.vid, slot.pid)

    # ---- shutting down -----------------------------------------------------
    def shutdown(self):
        with self.close_lock:
            if self.closed:
                return
            self.closed = True
        for slot in list(self.slots):
            slot.stop()
        self.hid_out.close()
        self.hh.restore()
        if self.tray is not None:
            self.tray.stop()
        if self.undo_boost is not None:
            self.undo_boost()

    def install_close_handler(self):
        """Restore HidHide and stop the motors if the console window is closed."""
        try:
            import ctypes
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

            def handler(event):
                self.shutdown()
                return 0

            self._handler = handler_type(handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(self._handler, True)
        except Exception:
            self._handler = None

    # ---- main --------------------------------------------------------------
    def discover(self, layouts=True):
        pump_events()
        count = pygame.joystick.get_count()
        say("Controllers found: %d" % count)
        for index in range(count):
            self.consider(index, index + 1, self.interactive, layout=layouts)

    def info(self):
        pygame.joystick.init()
        for line in version_lines():
            say(line)
        for index in range(pygame.joystick.get_count()):
            js = pygame.joystick.Joystick(index)
            guid, vid, pid = guid_info(js)
            reason = self.skip(js, guid, vid, pid)
            ids = "%04X:%04X" % (vid, pid) if vid and pid else "n/a"
            say("%d) %s" % (index + 1, js.get_name()))
            say("     id %s | guid %s | axes %d, buttons %d, hats %d"
                % (ids, guid, js.get_numaxes(), js.get_numbuttons(), js.get_numhats()))
            kind = "will be bridged" if not reason else "skipped: " + reason
            extra = []
            if self.cfg["mappings"].get(guid) not in (None, GUESS_LAYOUT):
                extra.append("saved layout")
            elif (vid, pid) in KNOWN_LAYOUTS:
                extra.append("known layout")
            elif sdl_knows(index):
                extra.append("in the community controller list")
            elif (vid, pid) in UNTESTED_LAYOUTS:
                extra.append("untested Twin layout")
            fam = self.family_for(vid, pid)
            if fam:
                extra.append("direct vibration: %s" % fam.title)
                if self.swap_for(vid, pid):
                    extra.append("adapter ports swapped")
            if vid and pid:
                tuning = self.tuning_for(vid, pid)
                if not tuning.is_default:
                    extra.append(tuning.describe())
            prof = self.profile_for(guid, vid, pid)
            if prof is not None:
                extra.append("profile: " + prof.describe())
            say("     %s%s" % (kind, (" (" + ", ".join(extra) + ")") if extra else ""))
            for text in (prof.problems if prof is not None else ()):
                say("     profile problem: %s" % text)
        known = self.cache.get("kernel")
        if known:
            say("Linux vibration list: checked %s (%d pads)"
                % (days_ago(time.time() - known.get("time", 0)), len(known.get("table", {}))))
        else:
            say("Linux vibration list: not downloaded yet (it is fetched when a pad needs it)")
        say("HidHide: %s" % (self.hh.cli or "not found"))
        say("Administrator rights: %s" % ("yes" if is_admin() else "no"))
        say("Log file: %s" % LOG_FILE)

    def learn(self):
        """Try the standard vibration commands on one pad; remember the one that works."""
        if hid is None:
            say("pywinusb is not installed. Run:  python -m pip install pywinusb")
            return
        found = []
        for i in range(pygame.joystick.get_count()):
            js = pygame.joystick.Joystick(i)
            guid, vid, pid = guid_info(js)
            if vid and pid and self.skip(js, guid, vid, pid) is None:
                found.append((js, vid, pid))
        if not found:
            say("No usable controller found. Plug it in and try again.")
            return
        choice = 0
        if len(found) > 1:
            for n, (js, vid, pid) in enumerate(found, 1):
                say("%d) %s  (%04X:%04X)" % (n, js.get_name(), vid, pid))
            try:
                choice = int(input("Which one do you want to test? ").strip()) - 1
                found[choice]
            except Exception:
                say("That is not a valid number.")
                return
        js, vid, pid = found[choice]
        eps = self.hid_out.endpoints(vid, pid)
        say("\nTesting: %s  (%04X:%04X)" % (js.get_name(), vid, pid))
        if not eps:
            say("Windows shows no vibration channel for this controller (no HID output report).")
            say("It probably needs its own driver for vibration. Inputs still work.")
            return
        say("Vibration reports found: %s" % ", ".join("id %d, %d bytes" % (e[2], e[1]) for e in eps))
        say("I will try the standard vibration commands used by Linux drivers, one at a time.")
        say("They only switch the motors on and off; nothing is changed in the controller.")
        input("Hold the controller and press Enter to start (Ctrl+C to cancel)... ")
        sinks = []
        try:
            for fam, use in learn_candidates(eps):
                for old in sinks:
                    old.close()
                sinks = [HidSink(ep, fam) for ep in use]
                say("\nTrying method '%s' (%s) on %d report(s)..." % (fam.name, fam.title, len(use)))
                for sink in sinks:
                    sink.set(255, 255)
                time.sleep(1.2)
                for sink in sinks:
                    sink.set(0, 0)
                answer = input("Did the controller vibrate? [y = yes, Enter = no, q = stop] ").strip().lower()
                if answer.startswith("y"):
                    self.cfg.setdefault("rumble", {})["%04x:%04x" % (vid, pid)] = fam.name
                    self.save_cfg()
                    say("Saved. Pads of this model (%04X:%04X) now vibrate in games." % (vid, pid))
                    return
                if answer.startswith("q"):
                    break
            say("\nNone of the known methods worked for this pad.")
            say("It may need its own driver for vibration. Inputs still work.")
        except KeyboardInterrupt:
            say("\nCancelled.")
        finally:
            for sink in sinks:
                sink.close()      # motors off, and wait until that was written
            self.hid_out.close()

    # ---- helpers for the main loop -------------------------------------------
    def neutral(self, slot):
        """Put the virtual pad back to 'nothing pressed' and forget the last state, so the next real
        state is sent again."""
        try:
            if slot.pad is not None:
                slot.pad.reset()
                slot.pad.update()
        except Exception:
            pass
        slot.last = None

    def sync_pause(self):
        """Pause / Resume from the tray icon: stop passing input on and let other programs see the real pads."""
        want = self.paused.is_set()
        if want == self.is_paused_now:
            return
        self.is_paused_now = want
        if want:
            say("Paused: the virtual pads sit idle and the real controllers are visible to other programs.")
            for slot in self.slots:
                slot.muted = True
                if slot.sink is not None:
                    slot.sink.set(0, 0)
                self.neutral(slot)
            self.hh.restore()
        else:
            say("Resumed.")
            for slot in self.slots:
                slot.muted = False
                slot.last = None
            self.hide(self.slots)

    def heal_hid(self):
        """A pad's vibration channel stopped answering (the adapter was re-plugged?): open it again.
        At most twice per model, so a pad that simply cannot be written to never causes a loop."""
        keys = set()
        while True:
            try:
                keys.add(self.heal_requests.get_nowait())
            except queue.Empty:
                break
        for key in keys:
            if self.heals.get(key, 0) >= 2:
                continue
            self.heals[key] = self.heals.get(key, 0) + 1
            group = [s for s in self.slots if (s.vid, s.pid) == key and s.pad is not None]
            if not group:
                continue
            say("Vibration to %s stopped answering - opening it again..." % group[0].name)
            for s in group:
                old, s.sink = s.sink, None
                if isinstance(old, HidSink):
                    old.close(send_stop=False, timeout=0.3)
            self.hid_out.forget(*key)
            for s in group:
                s.sink = self.make_sink(s)

    def pass_input(self, slot, now, show):
        """Read one real pad and hand its state to the virtual pad."""
        try:
            state = slot.read(now)
        except pygame.error:
            # the pad was unplugged (or is being re-plugged): park the virtual pad in neutral
            if not slot.lost:
                slot.lost = True
                self.neutral(slot)
            return
        except Exception as err:
            # NOT an unplug: a bug or a bad layout. Say so (once) instead of looking like a dead pad.
            report_once("(Pad %d: could not read '%s': %s: %s - shown once; the details are in the log)"
                        % (slot.number, slot.name, err.__class__.__name__, (str(err).strip() or "?")[:100]))
            log_exception("reading pad %d" % slot.number)
            if not slot.lost:
                slot.lost = True
                self.neutral(slot)
            return
        slot.lost = False
        if state == slot.last:
            return
        pad = slot.pad          # the window closing (or an unplug) can drop it from another thread
        if pad is None:
            return
        try:
            apply_state(pad, state)
        except Exception as err:
            report_once("(Pad %d: could not update the virtual pad: %s: %s - shown once; the details are in the log)"
                        % (slot.number, err.__class__.__name__, (str(err).strip() or "?")[:100]))
            log_exception("updating virtual pad %d" % slot.number)
            return    # slot.last stays as it was, so the next loop tries again
        slot.last = state
        if show:
            say("pad %d pressed: %s  trig %d/%d  sticks %.2f %.2f | %.2f %.2f"
                % ((slot.number, sorted(state[0]) or "-") + state[1:]))

    # ---- --test: does the right controller shake? ---------------------------------
    def _all_released(self, slots, wait=3.0):
        end = time.time() + wait
        while time.time() < end:
            busy = False
            for s in slots:
                axes, buttons, hats = snapshot(s.js)
                if any(buttons) or any(h != (0, 0) for h in hats):
                    busy = True
            if not busy:
                return
            time.sleep(0.05)

    def wait_for_press(self, slots, timeout=25.0):
        """The slot whose controller gets a button pressed first - or None (Enter pressed, or time ran out)."""
        end = time.time() + timeout
        while time.time() < end:
            if skip_requested():
                return None
            for s in slots:
                axes, buttons, hats = snapshot(s.js)
                if any(buttons) or any(h != (0, 0) for h in hats):
                    return s
            time.sleep(0.02)
        return None

    def test_mode(self):
        """Shake each pad and ask for a button press on the controller that shook. That shows whether the
        vibration of 'Pad N' really reaches the controller that is Pad N (the two ports of an adapter are
        sometimes crossed). The answer is saved, so --swap does not have to be typed again."""
        if not self.interactive:
            say("--test needs a window you can type in. Start it from the .bat file or a command prompt.")
            return
        self.discover(layouts=False)
        slots = list(self.slots)
        if not slots:
            say("No usable controller found. Plug it in and try again.")
            return
        for s in slots:
            s.sink = self.make_sink(s, tuned=False)
        say("\nVibration test. Each pad shakes once; then press any button on the controller that shook")
        say("(or press Enter on the keyboard if none of them shook).")
        results = {}
        for s in slots:
            say("\nPad %d: %s" % (s.number, s.name))
            if s.sink is None:
                say("  No vibration method is known for it yet. Start Pad Bridge normally once (it looks the pad up),")
                say("  or run  %s --learn" % RUN_CMD)
                continue
            self._all_released(slots)
            input("  Hold the controllers and press Enter: Pad %d will shake for a moment... " % s.number)
            s.sink.set(255, 255)
            time.sleep(1.2)
            s.sink.set(0, 0)
            say_part("  Now press a button on the controller that shook ...")
            hit = self.wait_for_press(slots)
            if hit is None:
                say(" nothing felt (or skipped)")
                results[s.number] = "silent"
            elif hit is s:
                say(" correct: that controller is Pad %d" % s.number)
                results[s.number] = "ok"
            else:
                say(" that controller is Pad %d, not Pad %d - the ports are crossed" % (hit.number, s.number))
                results[s.number] = "crossed"
        self._save_test_results(slots, results)

    def _save_test_results(self, slots, results):
        groups = {}
        for s in slots:
            if isinstance(s.sink, HidSink):
                groups.setdefault((s.vid, s.pid), []).append(results.get(s.number, "silent"))
        changed = False
        for (vid, pid), outcomes in sorted(groups.items()):
            if len(outcomes) < 2:
                continue          # one pad on its own cannot be crossed
            key = "%04x:%04x" % (vid, pid)
            swapped = self.swap_for(vid, pid)
            good, crossed = outcomes.count("ok"), outcomes.count("crossed")
            if good and crossed:
                say("\n%s: the answers disagree with each other, so nothing was changed. Run --test again." % key.upper())
            elif crossed:
                self.cfg.setdefault("swap_by_model", {})[key] = not swapped
                say("\n%s: the ports were crossed. Fixed and remembered - vibration now reaches the right controller."
                    % key.upper())
                changed = True
            elif good:
                self.cfg.setdefault("swap_by_model", {})[key] = swapped
                say("\n%s: vibration reaches the right controller. Remembered." % key.upper())
                changed = True
            else:
                say("\n%s: no vibration was felt, so nothing was changed. Try  %s --learn" % (key.upper(), RUN_CMD))
        if changed:
            self.save_cfg()
            say("Saved. Run  %s --test  again if you want to double-check." % RUN_CMD)

    # ---- --tune: weakest vibration the pad can feel, strength multiplier ----------------
    TUNE_LEVELS = (6, 12, 20, 30, 42, 56, 72, 92, 116, 146, 182, 220, 255)

    def _enter_within(self, seconds):
        """True if Enter was pressed on the keyboard within `seconds`."""
        end = time.time() + seconds
        while time.time() < end:
            if skip_requested():
                return True
            time.sleep(0.02)
        return False

    def _find_minimum(self, sink):
        say("The pad will vibrate from very weak to strong, one step at a time.")
        say("Hold it and press Enter on the keyboard the moment you FEEL the first vibration.")
        input("Press Enter to start... ")
        skip_requested()    # empty the keyboard buffer
        for level in self.TUNE_LEVELS:
            say_part("  strength %3d ..." % level)
            sink.set(level, level)
            felt = self._enter_within(0.8)
            sink.set(0, 0)
            if felt:
                say(" felt")
                return level
            say(" -")
            time.sleep(0.25)
        say("  (nothing was felt at any strength)")
        return None

    def _ask_gain(self, sink, gain, start):
        say("\nStrength multiplier: 1.0 keeps the game's vibration as it is, 1.5 makes it stronger, 0.5 weaker.")
        while True:
            answer = input("Type a number from 0.3 to 3 to try it, or just press Enter to keep %.1f: " % gain).strip()
            if not answer:
                return gain
            try:
                value = float(answer.replace(",", "."))
            except ValueError:
                say("That is not a number.")
                continue
            gain = max(0.3, min(3.0, value))
            large, small = Tuning(gain, start).apply(110, 110)
            say("A medium vibration with x%.1f..." % gain)
            sink.set(large, small)
            time.sleep(1.0)
            sink.set(0, 0)

    def tune_mode(self):
        """Find the weakest vibration the pad can feel and (optionally) set a strength multiplier. It is
        saved per pad model, and every game's vibration then goes through it."""
        if not self.interactive:
            say("--tune needs a window you can type in. Start it from the .bat file or a command prompt.")
            return
        self.discover(layouts=False)
        models = {}
        for slot in self.slots:
            if slot.vid and slot.pid:
                models.setdefault((slot.vid, slot.pid), slot)
        if not models:
            say("No usable controller found. Plug it in and try again.")
            return
        for (vid, pid), slot in models.items():
            key = "%04x:%04x" % (vid, pid)
            say("\n=== Vibration tuning: %s (%s) ===" % (slot.name, key.upper()))
            sink = self.make_sink(slot, tuned=False)
            if sink is None:
                say("No vibration method is known for it yet. Start Pad Bridge normally once (it looks the pad up),")
                say("or run  %s --learn." % RUN_CMD)
                continue
            current = Tuning.from_config(self.cfg.get("tuning", {}).get(key))
            try:
                start = self._find_minimum(sink)
                if start is None:
                    start = current.min_start
                    say("The old setting (%d) stays." % start)
                else:
                    say("Light vibration will now start at level %d." % start)
                gain = self._ask_gain(sink, current.gain, start)
            except KeyboardInterrupt:
                say("\nCancelled - nothing was changed.")
                return
            finally:
                sink.close()
            tuning = Tuning(gain, start)
            table = self.cfg.setdefault("tuning", {})
            if tuning.is_default:
                table.pop(key, None)
            else:
                table[key] = tuning.to_config()
            self.save_cfg()
            say("Saved for %s: %s." % (key.upper(), tuning.describe()))

    # ---- --init-profile: an editable starting point for every connected pad ---------------
    def init_profile(self):
        self.discover(layouts=False)
        if not self.slots:
            say("No usable controller found. Plug it in and try again.")
            return
        table = self.cfg.setdefault("profiles", {})
        known = set(str(k).strip().lower() for k in table)
        written = []
        for slot in self.slots:
            key = "%04x:%04x" % (slot.vid, slot.pid) if slot.vid and slot.pid else slot.guid
            if key in known or slot.guid.lower() in known:
                say("  %s (%s): already has a profile, left as it is." % (slot.name, key))
                continue
            table[key] = copy.deepcopy(DEFAULT_PROFILE)
            known.add(key)
            written.append(key)
            say("  %s (%s): profile written." % (slot.name, key))
        if written:
            self.save_cfg()
            say("\nSaved in %s under \"profiles\". Open it in any text editor and change the numbers." % CONFIG_FILE)
            say("While Pad Bridge runs, a saved change takes effect within a second. A pad that has a profile uses")
            say("the smooth round dead zone; remove the profile again if you prefer the old behaviour.")
            say("What each setting does is listed at the top of %s (or run  %s --help)." % (SCRIPT, RUN_CMD))

    # ---- the main loop -------------------------------------------------------------
    def loop(self):
        show = self.opts.show
        while not self.quit_event.is_set():
            now = time.time()
            pump_events()
            try:
                self.poll_lookups()
            except Exception as err:
                report_once("(could not use the internet lookup result: %s)" % err)
            try:
                self.check_config(now)
            except Exception as err:
                report_once("(could not apply the changed settings file: %s)" % err)
                log_exception("settings reload")
            while PENDING:
                ev = PENDING.pop(0)
                if ev.type == pygame.JOYDEVICEADDED:
                    try:
                        slot = self.consider(ev.device_index, ev.device_index + 1, False, announce=False)
                    except Exception as err:
                        say("(could not open the new controller: %s)" % err)
                        continue
                    if slot:
                        self.check_vibration(slot)
                        say("Loading...")
                        if not self.is_paused_now:
                            self.hide([slot])
                        self.activate(slot)
                        if self.is_paused_now:
                            slot.muted = True
                        else:
                            self.buzz([slot])
                else:
                    self.remove(getattr(ev, "instance_id", None))
            self.sync_pause()
            try:
                self.heal_hid()
            except Exception as err:
                report_once("(could not open a vibration channel again: %s)" % err)
            for slot in list(self.slots):
                if slot.pad is None:
                    continue
                if not self.is_paused_now:
                    self.pass_input(slot, now, show)
                if slot.sink:
                    slot.sink.tick(now)
            time.sleep(LOOP_DELAY)

    def start_tray(self):
        tray = Tray(self)
        if tray.start():
            self.tray = tray
            if tray.icon_added:
                say("Tray icon is on: right-click it (bottom right, maybe under the ^ arrow) for status, pause and quit.")
                tray.hide_console()
            else:
                say("(the taskbar is not ready yet; the tray icon appears as soon as it is. This window stays open.)")
        else:
            say("(the tray icon could not be started%s; carrying on in this window)"
                % ((": " + tray.error) if tray.error else ""))

    def run(self):
        opts = self.opts
        pygame.display.init()
        pygame.joystick.init()
        if sdl_controller is not None:
            try:
                sdl_controller.init()
            except Exception:
                pass
        if opts.mode == "info":
            self.info()
            return
        if opts.mode == "learn":
            self.learn()
            return
        if opts.mode == "test":
            self.test_mode()
            return
        if opts.mode == "tune":
            self.tune_mode()
            return
        if opts.mode == "init_profile":
            self.init_profile()
            return
        if opts.mode == "restore":
            if self.hh.ready:
                if not self.hh.restore_leftover():
                    say("Nothing to restore.")
            else:
                say("HidHide was not found.")
            return
        if not opts.no_boost:
            self.undo_boost = boost_timing()
        self.hh.restore_leftover()
        self.discover()
        if self.slots:
            for slot in self.slots:
                self.check_vibration(slot)
            say("Loading...")
            self.hide(self.slots)
            say("")
            for slot in list(self.slots):
                self.activate(slot)
            self.buzz(self.slots)
        else:
            say("No usable controller yet. Plug one in; it will be picked up automatically.")
        self.install_close_handler()
        if opts.tray:
            self.start_tray()
        say("\nVirtual Xbox 360 pads are ON. Start your game now.")
        say("Plug or unplug controllers any time. Press Ctrl+C to stop.\n")
        try:
            self.loop()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
            say("\nStopped. Your real controllers are visible to other programs again.")


# --------------------------------------------------------------------------
# Versions (printed by --info and --doctor, handy for bug reports)
# --------------------------------------------------------------------------
def pkg_version(*names):
    try:
        from importlib import metadata
    except ImportError:
        return "unknown"
    for name in names:
        try:
            return metadata.version(name)
        except Exception:
            continue
    return "unknown"


def version_lines():
    lines = ["Pad Bridge %s (%s)" % (VERSION, "exe" if FROZEN else "script"),
             "Python %s (%s) on %s" % (platform.python_version(), platform.architecture()[0], platform.platform())]
    parts = []
    if pygame is not None:
        try:
            sdl = ", SDL " + ".".join(str(x) for x in pygame.get_sdl_version())
        except Exception:
            sdl = ""
        name = "pygame-ce" if getattr(pygame, "IS_CE", False) else "pygame"
        parts.append("%s %s%s" % (name, getattr(getattr(pygame, "version", None), "ver", "?"), sdl))
    else:
        parts.append("pygame: NOT INSTALLED")
    parts.append(("vgamepad %s" % pkg_version("vgamepad")) if vg is not None else "vgamepad: NOT INSTALLED")
    parts.append(("pywinusb %s" % pkg_version("pywinusb")) if hid is not None
                 else "pywinusb: not installed (no direct vibration)")
    lines.append(" | ".join(parts))
    return lines


# --------------------------------------------------------------------------
# Start with Windows
# --------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PadBridge"
TASK_NAME = "PadBridge"

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Pad Bridge: makes generic controllers work like Xbox 360 pads</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled>%(user_id)s<Delay>PT10S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">%(user_id)s<LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>%(command)s</Command><Arguments>%(arguments)s</Arguments><WorkingDirectory>%(workdir)s</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def autostart_command(extra_args=()):
    """(program, arguments text) that Windows should start at log-on. It always starts in the tray."""
    if FROZEN:
        exe, args = sys.executable, []
    else:
        exe = sys.executable
        windowless = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(windowless):
            exe = windowless          # no console window at all
        args = [os.path.abspath(__file__)]
    args += list(extra_args) + ["--tray"]
    return exe, subprocess.list2cmdline(args)


def task_xml(exe, arguments, workdir, user):
    from xml.sax.saxutils import escape
    user_id = "<UserId>%s</UserId>" % escape(user) if user else ""
    return TASK_XML % {"user_id": user_id, "command": escape(exe), "arguments": escape(arguments),
                       "workdir": escape(workdir)}


def _task_exists():
    return run_quiet(["schtasks", "/Query", "/TN", TASK_NAME], timeout=15)[0] == 0


def _run_key_exists():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_VALUE)
        return True
    except Exception:
        return False


def autostart_status():
    """'task' (scheduled task that runs with administrator rights), 'run' (Run list), or None."""
    if sys.platform != "win32":
        return None
    if _task_exists():
        return "task"
    return "run" if _run_key_exists() else None


def _task_create(exe, arguments):
    user = "%s\\%s" % (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")) \
        if os.environ.get("USERNAME") else ""
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-16") as f:
            f.write(task_xml(exe, arguments, HERE, user))
        rc, out = run_quiet(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", path, "/F"], timeout=30)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return rc == 0, out.strip()[:200]


def _run_key_set(exe, arguments):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, '"%s" %s' % (exe, arguments))


def _run_key_remove():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except Exception:
        pass


def autostart_enable(extra_args=()):
    """(ok, message). With administrator rights it makes a scheduled task, so HidHide can work after log-on;
    otherwise (or if that fails) an entry in the Run list, which starts without those rights."""
    if sys.platform != "win32":
        return False, "Start with Windows only works on Windows."
    exe, arguments = autostart_command(extra_args)
    note = ""
    admin = is_admin()
    if admin:
        ok, why = _task_create(exe, arguments)
        if ok:
            _run_key_remove()
            return True, "Pad Bridge will start with Windows, in the tray, with administrator rights (a scheduled task)."
        note = " (a scheduled task could not be made: %s)" % why
    try:
        _run_key_set(exe, arguments)
    except Exception as err:
        return False, "Could not set up start with Windows: %s" % err
    message = "Pad Bridge will start with Windows, in the tray (your Run list)." + note
    if not admin:
        message += ("\nThis start has no administrator rights, so HidHide may not be able to hide your real pads."
                    "\nRun  %s --autostart on  once from a window opened with 'Run as administrator' to fix that." % RUN_CMD)
    return True, message


def autostart_disable():
    problems = []
    if _task_exists():
        run_quiet(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=30)
        if _task_exists():
            problems.append("the scheduled task could not be removed (open a window with 'Run as administrator' and try again)")
    if _run_key_exists():
        _run_key_remove()
        if _run_key_exists():
            problems.append("the Run list entry could not be removed")
    if problems:
        return False, "Not completely removed: " + "; ".join(problems) + "."
    return True, "Pad Bridge no longer starts with Windows."


def run_autostart(opts):
    if opts.autostart == "status":
        state = autostart_status()
        say("Start with Windows: %s" % {"task": "on (scheduled task, with administrator rights)",
                                        "run": "on (Run list, no administrator rights)", None: "off"}[state])
        return 0
    if opts.autostart == "on":
        ok, message = autostart_enable(persistent_args(opts))
    else:
        ok, message = autostart_disable()
    say(message)
    return 0 if ok else 1


# --------------------------------------------------------------------------
# System tray icon (plain Windows API through ctypes - no extra packages)
# --------------------------------------------------------------------------
class Tray:
    """An icon in the notification area. Menu: status, Pause / Resume, Show / Hide window, Open log,
    Start with Windows, Quit. It runs in its own thread; if anything fails there is simply no icon and
    Pad Bridge carries on in its window."""

    WM_TRAY = 0x0400 + 20       # our message: the mouse did something with the icon
    WM_NOTE = 0x0400 + 21       # our message: show a balloon text
    ID_STATUS, ID_PAUSE, ID_WINDOW, ID_LOG, ID_AUTOSTART, ID_QUIT = 1001, 1002, 1003, 1004, 1005, 1006

    def __init__(self, bridge):
        self.bridge = bridge
        self.thread = None
        self.ready = threading.Event()
        self.ok = False
        self.error = ""
        self.api = None
        self.hwnd = None
        self.icons = {}
        self.own_icons = []
        self.shown_icon = None
        self.icon_added = False      # True once Windows really shows the icon
        self.console = None          # the console window, if it can be hidden and shown
        self.autostart = None
        self.notes = queue.Queue()
        self.taskbar_created = 0
        self._proc = None

    # ---- called from the main thread -------------------------------------------
    def start(self):
        if sys.platform != "win32":
            self.error = "only on Windows"
            return False
        self.thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self.thread.start()
        self.ready.wait(5)
        return self.ok

    def stop(self):
        if not self.ok or self.hwnd is None:
            return
        try:
            self.api.PostMessageW(self.hwnd, 0x0010, 0, 0)       # WM_CLOSE
        except Exception:
            pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(2)

    def hide_console(self):
        if self.ok and self.icon_added and self.console:
            say("Hiding this window now. Use the tray icon (menu: Show window) to bring it back; "
                "everything is also in pad_bridge.log.")
            try:
                self.api.ShowWindow(self.console, 0)             # SW_HIDE
            except Exception:
                pass

    def notify(self, text):
        """A balloon message from the icon (from any thread)."""
        if self.ok and self.hwnd is not None:
            self.notes.put(str(text))
            try:
                self.api.PostMessageW(self.hwnd, self.WM_NOTE, 0, 0)
            except Exception:
                pass

    # ---- the Windows side ---------------------------------------------------------
    def _load_api(self):
        import ctypes
        from ctypes import wintypes
        from types import SimpleNamespace

        user32 = ctypes.WinDLL("user32", use_last_error=True)      # private copies: our function
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)    # signatures never disturb
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # other libraries
        lresult = ctypes.c_ssize_t
        wndproc = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", wndproc),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", wintypes.HANDLE), ("hIcon", wintypes.HANDLE),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                        ("hIcon", wintypes.HANDLE), ("szTip", wintypes.WCHAR * 128),
                        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                        ("guidItem", GUID), ("hBalloonIcon", wintypes.HANDLE)]

        def proto(fn, restype, *argtypes):
            fn.restype = restype
            fn.argtypes = list(argtypes)
            return fn

        hwnd_t, uint, wparam, lparam = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        handle, boolean, dword = wintypes.HANDLE, wintypes.BOOL, wintypes.DWORD
        msg_p = ctypes.POINTER(wintypes.MSG)
        return SimpleNamespace(
            WNDPROC=wndproc, WNDCLASSW=WNDCLASSW, NOTIFYICONDATAW=NOTIFYICONDATAW,
            DefWindowProcW=proto(user32.DefWindowProcW, lresult, hwnd_t, uint, wparam, lparam),
            RegisterClassW=proto(user32.RegisterClassW, wintypes.ATOM, ctypes.POINTER(WNDCLASSW)),
            CreateWindowExW=proto(user32.CreateWindowExW, hwnd_t, dword, wintypes.LPCWSTR, wintypes.LPCWSTR, dword,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  hwnd_t, handle, handle, ctypes.c_void_p),
            DestroyWindow=proto(user32.DestroyWindow, boolean, hwnd_t),
            GetMessageW=proto(user32.GetMessageW, ctypes.c_int, msg_p, hwnd_t, uint, uint),
            TranslateMessage=proto(user32.TranslateMessage, boolean, msg_p),
            DispatchMessageW=proto(user32.DispatchMessageW, lresult, msg_p),
            PostMessageW=proto(user32.PostMessageW, boolean, hwnd_t, uint, wparam, lparam),
            PostQuitMessage=proto(user32.PostQuitMessage, None, ctypes.c_int),
            CreatePopupMenu=proto(user32.CreatePopupMenu, handle),
            AppendMenuW=proto(user32.AppendMenuW, boolean, handle, uint, ctypes.c_size_t, wintypes.LPCWSTR),
            TrackPopupMenu=proto(user32.TrackPopupMenu, ctypes.c_int, handle, uint, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, hwnd_t, ctypes.c_void_p),
            DestroyMenu=proto(user32.DestroyMenu, boolean, handle),
            GetCursorPos=proto(user32.GetCursorPos, boolean, ctypes.POINTER(wintypes.POINT)),
            SetForegroundWindow=proto(user32.SetForegroundWindow, boolean, hwnd_t),
            SetTimer=proto(user32.SetTimer, ctypes.c_size_t, hwnd_t, ctypes.c_size_t, uint, ctypes.c_void_p),
            KillTimer=proto(user32.KillTimer, boolean, hwnd_t, ctypes.c_size_t),
            RegisterWindowMessageW=proto(user32.RegisterWindowMessageW, uint, wintypes.LPCWSTR),
            LoadIconW=proto(user32.LoadIconW, handle, handle, ctypes.c_void_p),
            CreateIcon=proto(user32.CreateIcon, handle, handle, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte,
                             ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_void_p),
            DestroyIcon=proto(user32.DestroyIcon, boolean, handle),
            ShowWindow=proto(user32.ShowWindow, boolean, hwnd_t, ctypes.c_int),
            IsWindowVisible=proto(user32.IsWindowVisible, boolean, hwnd_t),
            Shell_NotifyIconW=proto(shell32.Shell_NotifyIconW, boolean, dword, ctypes.POINTER(NOTIFYICONDATAW)),
            GetModuleHandleW=proto(kernel32.GetModuleHandleW, handle, wintypes.LPCWSTR),
            GetConsoleWindow=proto(kernel32.GetConsoleWindow, hwnd_t),
        )

    def _make_icon(self, rgb):
        """A small game-pad icon drawn here in code (so no .ico file is needed). None if Windows refuses."""
        import ctypes
        size = 32
        rows = [[None] * size for _ in range(size)]
        body = (rgb[2], rgb[1], rgb[0], 255)          # B, G, R, A
        white = (255, 255, 255, 255)

        def fill(x0, y0, x1, y1, color):
            for y in range(y0, y1):
                for x in range(x0, x1):
                    rows[y][x] = color
        fill(8, 6, 24, 8, body)           # the pad: narrower top, wide middle, two grips at the bottom
        fill(5, 8, 27, 10, body)
        fill(3, 10, 29, 22, body)
        fill(3, 22, 12, 26, body)
        fill(20, 22, 29, 26, body)
        fill(4, 26, 11, 28, body)
        fill(21, 26, 28, 28, body)
        fill(8, 14, 15, 16, white)        # D-pad (a plus)
        fill(10, 11, 12, 19, white)
        fill(20, 12, 23, 15, white)       # two buttons
        fill(23, 15, 26, 18, white)
        xor = bytearray(size * size * 4)
        mask = bytearray(size * size // 8)             # 1 = see-through
        for y in range(size):
            for x in range(size):
                pixel = rows[y][x]
                if pixel is None:
                    mask[y * (size // 8) + x // 8] |= 0x80 >> (x % 8)
                else:
                    xor[(y * size + x) * 4:(y * size + x) * 4 + 4] = bytes(pixel)
        xor_c = (ctypes.c_ubyte * len(xor)).from_buffer_copy(bytes(xor))
        mask_c = (ctypes.c_ubyte * len(mask)).from_buffer_copy(bytes(mask))
        icon = self.api.CreateIcon(None, size, size, 1, 32, mask_c, xor_c)
        if icon:
            self.own_icons.append(icon)
        return icon or None

    def _data(self, flags):
        import ctypes
        nid = self.api.NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _add_icon(self):
        import ctypes
        self.shown_icon = "paused" if self.bridge.is_paused_now else "run"
        nid = self._data(0x01 | 0x02 | 0x04)          # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.WM_TRAY
        nid.hIcon = self.icons.get(self.shown_icon)
        nid.szTip = self.bridge.status_text()[:120]
        if not self.api.Shell_NotifyIconW(0, ctypes.byref(nid)):       # NIM_ADD
            raise OSError("Windows would not add the tray icon")
        self.icon_added = True

    def _remove_icon(self):
        import ctypes
        try:
            self.api.Shell_NotifyIconW(2, ctypes.byref(self._data(0)))  # NIM_DELETE
        except Exception:
            pass
        for icon in self.own_icons:
            try:
                self.api.DestroyIcon(icon)
            except Exception:
                pass
        self.own_icons = []

    def _refresh(self):
        import ctypes
        want = "paused" if self.bridge.is_paused_now else "run"
        nid = self._data(0x04)                          # NIF_TIP
        if want != self.shown_icon:
            nid.uFlags |= 0x02                          # NIF_ICON
            nid.hIcon = self.icons.get(want)
            self.shown_icon = want
        nid.szTip = self.bridge.status_text()[:120]
        self.api.Shell_NotifyIconW(1, ctypes.byref(nid))               # NIM_MODIFY

    def _balloon(self):
        import ctypes
        try:
            text = self.notes.get_nowait()
        except queue.Empty:
            return
        nid = self._data(0x10)                          # NIF_INFO
        nid.szInfoTitle = "Pad Bridge"
        nid.szInfo = text[:250]
        nid.dwInfoFlags = 0x01                          # NIIF_INFO
        self.api.Shell_NotifyIconW(1, ctypes.byref(nid))

    def _toggle_window(self):
        if not self.console:
            return
        if self.api.IsWindowVisible(self.console):
            self.api.ShowWindow(self.console, 0)        # SW_HIDE
        else:
            self.api.ShowWindow(self.console, 5)        # SW_SHOW
            self.api.SetForegroundWindow(self.console)

    def _read_autostart(self):
        try:
            self.autostart = autostart_status()
        except Exception:
            self.autostart = None

    def _toggle_autostart(self):
        try:
            if autostart_status():
                ok, message = autostart_disable()
            else:
                ok, message = autostart_enable(persistent_args(self.bridge.opts))
            self.autostart = autostart_status()
            first = message.split("\n")[0]
            say("Start with Windows: " + first)
            self.notify(first)
        except Exception as err:
            log_exception("tray: start with Windows")
            self.notify("Could not change it: %s" % err)

    def _menu(self):
        import ctypes
        from ctypes import wintypes
        api, bridge = self.api, self.bridge
        menu = api.CreatePopupMenu()
        if not menu:
            return
        command = 0
        try:
            plain, grayed, checked, separator = 0x0000, 0x0001, 0x0008, 0x0800
            api.AppendMenuW(menu, plain | grayed, self.ID_STATUS, bridge.status_text())
            api.AppendMenuW(menu, separator, 0, None)
            api.AppendMenuW(menu, plain, self.ID_PAUSE, "Resume" if bridge.is_paused() else "Pause")
            if self.console:
                shown = bool(api.IsWindowVisible(self.console))
                api.AppendMenuW(menu, plain, self.ID_WINDOW, "Hide window" if shown else "Show window")
            api.AppendMenuW(menu, plain, self.ID_LOG, "Open log file")
            api.AppendMenuW(menu, plain | (checked if self.autostart else 0), self.ID_AUTOSTART, "Start with Windows")
            api.AppendMenuW(menu, separator, 0, None)
            api.AppendMenuW(menu, plain, self.ID_QUIT, "Quit")
            point = wintypes.POINT()
            api.GetCursorPos(ctypes.byref(point))
            api.SetForegroundWindow(self.hwnd)
            # TPM_RETURNCMD | TPM_RIGHTBUTTON | TPM_BOTTOMALIGN: the chosen command comes back as the result
            command = api.TrackPopupMenu(menu, 0x0100 | 0x0002 | 0x0020, point.x, point.y, 0, self.hwnd, None)
            api.PostMessageW(self.hwnd, 0, 0, 0)        # WM_NULL: lets the menu close when you click elsewhere
        finally:
            api.DestroyMenu(menu)
        if command:
            self._command(command)

    def _command(self, command):
        if command == self.ID_PAUSE:
            self.bridge.toggle_pause()
            time.sleep(0.1)
            self._refresh()
        elif command == self.ID_WINDOW:
            self._toggle_window()
        elif command == self.ID_LOG:
            try:
                os.startfile(LOG_FILE)
            except Exception:
                self.notify("Could not open " + LOG_FILE)
        elif command == self.ID_AUTOSTART:
            threading.Thread(target=self._toggle_autostart, name="autostart", daemon=True).start()
        elif command == self.ID_QUIT:
            self.bridge.request_quit()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        api = self.api
        try:
            if msg == self.WM_TRAY:
                if lparam in (0x0205, 0x007B):                  # WM_RBUTTONUP, WM_CONTEXTMENU
                    self._menu()
                elif lparam == 0x0203:                          # WM_LBUTTONDBLCLK
                    self._toggle_window()
                return 0
            if msg == 0x0113:                                   # WM_TIMER
                self._refresh()
                return 0
            if msg == self.WM_NOTE:
                self._balloon()
                return 0
            if self.taskbar_created and msg == self.taskbar_created:   # Explorer restarted: put the icon back
                self._add_icon()
                return 0
            if msg == 0x0010:                                   # WM_CLOSE
                api.DestroyWindow(hwnd)
                return 0
            if msg == 0x0002:                                   # WM_DESTROY
                api.KillTimer(hwnd, 1)
                self._remove_icon()
                api.PostQuitMessage(0)
                return 0
        except Exception:
            log_exception("tray window procedure")
        return api.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        import ctypes
        from ctypes import wintypes
        try:
            api = self.api = self._load_api()
            self._proc = api.WNDPROC(self._wndproc)            # keep a reference: Windows calls it later
            instance = api.GetModuleHandleW(None)
            wc = api.WNDCLASSW()
            wc.lpfnWndProc = self._proc
            wc.hInstance = instance
            wc.lpszClassName = "PadBridgeTray"
            if not api.RegisterClassW(ctypes.byref(wc)):
                raise OSError("could not register the tray window")
            self.hwnd = api.CreateWindowExW(0, "PadBridgeTray", "Pad Bridge", 0, 0, 0, 0, 0,
                                            None, None, instance, None)
            if not self.hwnd:
                raise OSError("could not create the tray window")
            self.taskbar_created = api.RegisterWindowMessageW("TaskbarCreated")
            stock = api.LoadIconW(None, 32512)                  # IDI_APPLICATION, the fallback
            self.icons = {"run": self._make_icon((46, 160, 67)) or stock,
                          "paused": self._make_icon((140, 140, 140)) or stock}
            try:
                self._add_icon()
            except OSError:
                # the taskbar is not there yet (right after log-on): Explorer announces itself with
                # "TaskbarCreated" later, and the icon is added then
                log_exception("tray icon not added yet")
            api.SetTimer(self.hwnd, 1, 2000, None)
            console = api.GetConsoleWindow()
            self.console = console if console and api.IsWindowVisible(console) else None
            self.ok = True
        except Exception as err:
            self.error = (str(err).strip() or err.__class__.__name__)[:100]
            log_exception("tray start")
            self.ready.set()
            return
        self.ready.set()
        threading.Thread(target=self._read_autostart, name="autostart-check", daemon=True).start()
        message = wintypes.MSG()
        while True:
            got = api.GetMessageW(ctypes.byref(message), None, 0, 0)
            if got <= 0:
                break
            api.TranslateMessage(ctypes.byref(message))
            api.DispatchMessageW(ctypes.byref(message))


# --------------------------------------------------------------------------
# --doctor
# --------------------------------------------------------------------------
def _probe(url, timeout=6):
    """(True, 'HTTP 200') if the server answers, else (False, the reason)."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "pad_bridge"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, "HTTP %d" % resp.status
    except urllib.error.HTTPError as err:
        return True, "HTTP %d" % err.code           # the server answered, so the connection itself works
    except Exception as err:
        return False, _why(err)


def vigem_state():
    return service_state("ViGEmBus")


def service_state(name):
    """'running', 'stopped', 'missing' or 'unknown' for a Windows service (a driver such as ViGEmBus)."""
    rc, out = run_quiet(["sc", "query", name], timeout=10)
    if rc == 0 and "RUNNING" in out.upper():
        return "running"
    if rc == 0:
        return "stopped"
    if "1060" in out:
        return "missing"
    return "unknown"


VIGEM_FIX = "Install ViGEmBus: https://github.com/nefarius/ViGEmBus/releases/latest (then restart Windows once)."
HIDHIDE_FIX = ("Install HidHide: https://github.com/nefarius/HidHide/releases/latest (then restart Windows once),\n"
               "or run Pad Bridge with --no-hide (games may then see the real controller as well).")


def check_system(report, opts):
    if sys.platform != "win32":
        report("fail", "Windows", "this is %s" % platform.system(),
               "Pad Bridge only works on Windows (ViGEmBus and HidHide are Windows drivers).")
        return
    report("ok", "Windows", platform.platform())
    if sys.version_info < (3, 8):
        report("warn", "Python", platform.python_version(),
               "Use Python 3.8 or newer (3.11 or newer also gives the smoothest timing).")
    else:
        report("ok", "Python", "%s, %s" % (platform.python_version(), platform.architecture()[0]))


def check_packages(report, opts):
    if FROZEN:
        report("ok", "Packages", "built into this .exe")
        return
    fix = "python -m pip install pygame-ce vgamepad pywinusb"
    if pygame is None:
        report("fail", "pygame", str(PYGAME_ERROR), fix)
    else:
        report("ok", "pygame", "%s%s" % ("pygame-ce " if getattr(pygame, "IS_CE", False) else "pygame ",
                                         getattr(getattr(pygame, "version", None), "ver", "?")))
    if vg is None:
        report("fail", "vgamepad", str(VGAMEPAD_ERROR), fix)
    else:
        report("ok", "vgamepad", pkg_version("vgamepad"))
    if hid is None:
        report("warn", "pywinusb", "not installed",
               "Needed for direct vibration on PS2 adapters:  python -m pip install pywinusb")
    else:
        report("ok", "pywinusb", pkg_version("pywinusb"))


def check_vigem(report, opts):
    if sys.platform != "win32":
        return
    state = vigem_state()
    if state == "missing":
        report("fail", "ViGEmBus driver", "not installed", VIGEM_FIX)
        return
    if state == "stopped":
        report("fail", "ViGEmBus driver", "installed but not running",
               "Restart Windows. If it is still not running, reinstall it:\n" + VIGEM_FIX)
        return
    report("ok", "ViGEmBus driver", "installed" + (" and running" if state == "running" else ""))
    if vg is None:
        return
    if not acquire_single_instance():
        report("info", "Virtual pad test", "skipped: Pad Bridge is running right now")
        return
    try:
        pad = vg.VX360Gamepad()
        pad.update()
        time.sleep(0.2)
        del pad
        report("ok", "Virtual pad test", "a test pad was created and removed")
    except Exception as err:
        report("fail", "Virtual pad test", "%s: %s" % (err.__class__.__name__, (str(err).strip() or "?")[:100]),
               VIGEM_FIX)


def check_hidhide(report, opts):
    if opts.no_hide:
        report("info", "HidHide", "skipped (--no-hide)")
        return
    hh = HidHide(enabled=True)
    if not hh.cli:
        report("fail", "HidHide", "not found", HIDHIDE_FIX)
        return
    ok, why = hh._read_state()
    if not ok:
        report("fail", "HidHide", "found (%s) but it did not answer: %s" % (hh.cli, why or "unknown error"),
               "Run Pad Bridge as administrator, and close the 'HidHide Configuration Client' window if it is open.")
        return
    report("ok", "HidHide", "%s (hiding is %s right now)" % (hh.cli, "on" if hh.cloak_was_on else "off"))
    driver = service_state("HidHide")
    if driver in ("missing", "stopped"):
        report("warn", "HidHide driver", "the driver service is %s" % ("not installed" if driver == "missing"
                                                                          else "installed but not running"),
               "Restart Windows; if it is still like this, reinstall HidHide:\n" + HIDHIDE_FIX)
    if load_json(STATE_FILE, None):
        report("warn", "HidHide leftovers", "a previous run did not clean up after itself",
               "Run  %s --restore" % RUN_CMD)


def check_admin(report, opts):
    if sys.platform != "win32":
        return
    if is_admin():
        report("ok", "Administrator rights", "yes")
    else:
        report("warn", "Administrator rights", "no",
               "HidHide normally needs them to hide the real controllers. Right-click the .bat file (or the .exe)\n"
               "and choose 'Run as administrator'.")


def check_internet(report, opts):
    if opts.offline:
        report("info", "Internet", "skipped (--offline)")
        return
    targets = [("GitHub (controller list)", DB_URLS[0]),
               ("jsDelivr (backup for the list)", DB_URLS[1]),
               ("Linux kernel sources (vibration lookup)", KERNEL_SOURCES[0] + "hid-ids.h")]
    worked = 0
    for label, url in targets:
        ok, detail = _probe(url)
        worked += 1 if ok else 0
        report("ok" if ok else "warn", label, detail)
    if not worked:
        report("warn", "Internet", "none of the sites answered",
               "Check the internet connection, firewall or proxy. Only the first download needs it;\n"
               "after that Pad Bridge also works with --offline.")


def check_files(report, opts):
    try:
        fd, path = tempfile.mkstemp(dir=HERE, prefix=".pad_bridge_test")
        os.close(fd)
        os.remove(path)
        report("ok", "Folder", "%s is writable" % HERE)
    except OSError as err:
        report("fail", "Folder", "%s is not writable (%s)" % (HERE, err),
               "Put Pad Bridge in a folder you can write to (not Program Files), for example Documents\\PadBridge.")
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("it is not a settings object")
            profiles = data.get("profiles") if isinstance(data.get("profiles"), dict) else {}
            report("ok", "Settings file", "readable (%d saved layout(s), %d profile(s))"
                   % (len(data.get("mappings") or {}), len(profiles)))
            mistakes = []
            for key, entry in profiles.items():
                if isinstance(entry, dict):
                    mistakes += ["%s: %s" % (key, text) for text in Profile(entry).problems]
                else:
                    mistakes.append("%s: a profile must be a { ... } block" % key)
            if mistakes:
                report("warn", "Profiles", "%d mistake(s): %s%s" % (len(mistakes), "; ".join(mistakes[:3]),
                                                                  " ..." if len(mistakes) > 3 else ""),
                       "Fix them in pad_bridge_config.json (entries with a mistake are skipped).")
        except Exception as err:
            report("warn", "Settings file", "damaged (%s)" % err,
                   "The next start keeps it as pad_bridge_config.json.bad-<date> and starts a new one;\n"
                   "set your pads up again with --setup.")
    else:
        report("info", "Settings file", "none yet (it is made when you first set up a pad)")
    state = autostart_status()
    report("info", "Start with Windows", {"task": "on (scheduled task, with administrator rights)",
                                          "run": "on (Run list)", None: "off"}[state])
    report("info", "Log file", LOG_FILE)


def check_pads(report, opts):
    if pygame is None:
        return
    try:
        try:
            pygame.display.init()
        except Exception:
            pass
        pygame.joystick.init()
        count = pygame.joystick.get_count()
    except Exception as err:
        report("warn", "Controllers", "could not look (%s)" % err)
        return
    if count:
        names = ", ".join(pygame.joystick.Joystick(i).get_name() for i in range(count))
        report("ok", "Controllers", "%d found: %s" % (count, names))
    else:
        report("warn", "Controllers", "none found",
               "Plug a controller in (and press its Analog / Mode button if it has one), then run this again.")


def run_doctor(opts):
    counts = {"fail": 0, "warn": 0}

    def report(level, title, detail="", fix=""):
        tag = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[ .. ]"}[level]
        say("%s %s%s" % (tag, title, (": " + detail) if detail else ""))
        if level in counts:
            counts[level] += 1
        if fix and level in ("warn", "fail"):
            for i, line in enumerate(fix.split("\n")):
                say("       %s%s" % ("fix: " if i == 0 else "     ", line))

    say("Pad Bridge doctor\n")
    for line in version_lines():
        say("  " + line)
    say("")
    for check in (check_system, check_packages, check_vigem, check_hidhide, check_admin,
                  check_internet, check_files, check_pads):
        try:
            check(report, opts)
        except Exception as err:
            report("warn", check.__name__.replace("check_", "") + " check", "could not finish: %s: %s"
                   % (err.__class__.__name__, err))
            log_exception("doctor " + check.__name__)
    say("")
    if counts["fail"]:
        extra = (" and %d warning(s)" % counts["warn"]) if counts["warn"] else ""
        say("%d problem(s) need fixing%s. The 'fix:' lines above say how." % (counts["fail"], extra))
    elif counts["warn"]:
        say("Nothing blocks Pad Bridge, but %d warning(s) above are worth a look." % counts["warn"])
    else:
        say("Everything looks fine.")
    return 1 if counts["fail"] else 0


def main():
    opts = OPTS
    if opts.mode == "doctor":
        sys.exit(run_doctor(opts))
    if opts.mode == "autostart":
        sys.exit(run_autostart(opts))
    if pygame is None or vg is None:
        say("A needed package is missing: %s" % (PYGAME_ERROR or VGAMEPAD_ERROR))
        say("Run:  python -m pip install pygame-ce vgamepad pywinusb")
        pause_before_exit()
        sys.exit(1)
    bridge = Bridge(opts)
    try:
        bridge.run()
    except KeyboardInterrupt:
        say("\nCancelled.")
    finally:
        bridge.shutdown()


if __name__ == "__main__":
    main()
