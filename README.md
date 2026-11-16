# Pad Bridge

Make cheap generic controllers (PS2-to-USB adapters, no-name USB pads, old DirectInput gamepads) work like **Xbox 360 pads** on Windows, **with vibration**.

Pad Bridge reads every connected controller, creates one virtual Xbox 360 pad for each (through the ViGEmBus driver), passes the game's vibration back to the real controller, and hides the real controller from games with HidHide so the game only sees one pad.

> **Status: early, lightly tested.** Only one device has been confirmed on real hardware: the **Twin USB Joystick PS2 adapter (VID 0810 / PID 0001)**. Everything else is based on the community controller list and on documented protocols, and has **not** been tested on real devices. Please report what works and what doesn't (see [Reporting a problem](#reporting-a-problem)).

---

## What it does

- Finds every connected controller (both ports of a PS2 adapter, USB pads, ...) and gives each one its own virtual Xbox 360 pad.
- **Vibration for cheap pads.** Many generic adapters show no vibration in Windows. For known chips, Pad Bridge sends the vibration commands directly to the adapter (see [Vibration support](#vibration-support)). For other pads it tries SDL rumble.
- Looks unknown pads up in the Linux kernel's force-feedback driver lists and remembers the answer.
- Button layouts come from the community [SDL_GameControllerDB](https://github.com/mdqinc/SDL_GameControllerDB) (thousands of pads), from a built-in layout for the Twin adapter, or from a one-minute "press each button" setup.
- Hides the real controllers with [HidHide](https://github.com/nefarius/HidHide) and un-hides them when you quit.
- Hot-plug: plug or unplug controllers while it runs.
- Optional extras: per-pad profiles (dead zones, response curves, remapping, rapid fire, toggle, shift layer), tray icon, start with Windows, a Guide-button combo, D-pad-as-stick for pads stuck in digital mode.

---

## Requirements

- Windows 10 or 11 (other Windows versions are untested)
- Python 3.8 or newer (3.11+ gives the smoothest timing)
- Python packages: `pygame-ce`, `vgamepad`, `pywinusb`
- The **ViGEmBus** driver (installed automatically by `pip install vgamepad`; restart Windows once if no virtual pad appears)
- **HidHide** (optional, but recommended; without it games may see both the real and the virtual pad): https://github.com/nefarius/HidHide/releases/latest

`pywinusb` is only needed for direct vibration on PS2-style adapters. Without it, everything else still works.

---

## Install

```
python -m pip install pygame-ce vgamepad pywinusb
```

Put `pad_bridge.py` in a folder you can write to (for example `Documents\PadBridge`, **not** Program Files). It saves its settings and log next to itself.

Check that everything is in place:

```
python pad_bridge.py --doctor
```

`--doctor` checks ViGEmBus, HidHide, administrator rights, packages and internet access, and tells you how to fix each problem.

---

## Quick start

1. Plug in your controller (press its Analog / Mode button if it has one).
2. Open a terminal **as administrator** (HidHide normally needs it) and run:
   ```
   python pad_bridge.py
   ```
3. If the pad is not in the community list, Pad Bridge offers a one-minute setup: press each button when asked.
4. **Start Pad Bridge before the game.** Press `Ctrl+C` or close the window to stop. Your real controllers become visible again.

Only one copy can run at a time.

---

## Options

| Option | What it does |
|---|---|
| `--setup` | Redo the button setup (press each button once) for the connected controllers |
| `--info` | List what was found (and the versions of everything), then exit |
| `--learn` | Try the standard vibration commands on a pad and remember the one that works |
| `--test` | Shake each pad and check that the right controller shakes; remembers the answer |
| `--tune` | Find the weakest vibration your pad can feel and set a strength multiplier (saved per pad model) |
| `--doctor` | Check ViGEmBus, HidHide, admin rights, packages and internet; says how to fix problems |
| `--only 1,2` | Bridge only these joystick numbers from the list |
| `--swap` | Swap which adapter port gets which vibration (this run only; `--test` remembers it) |
| `--no-hide` | Do not touch HidHide |
| `--offline` | Never look anything up on the internet |
| `--restore` | Undo anything this program left in HidHide, then exit |
| `--show` | Print what is pressed (for checking a layout) |
| `--update-db` | Download the community controller list now (otherwise: once a week) |
| `--recheck` | Ask the internet again about pads that were "not listed" (otherwise: once a week) |
| `--dpad-stick` | For pads stuck in digital mode: the D-pad also moves the left stick |
| `--guide` | Hold Start+Back for half a second to press the Xbox Guide button |
| `--tray` | Run from an icon in the system tray (status, pause, quit) and hide the window |
| `--autostart on\|off\|status` | Start with Windows (always starts in the tray) |
| `--no-boost` | Do not ask Windows for 1 ms timer accuracy and a slightly higher priority |
| `--ignore LIST` | Never bridge these controllers (`VID:PID` such as `046d:c24f`, or part of the name) |
| `--include LIST` | Bridge these even though they look like a wheel / flight stick / not a gamepad |
| `--init-profile` | Write an editable profile for each connected pad into `pad_bridge_config.json` |
| `--version` | Print the version number and exit |

Racing wheels, flight sticks, keyboards and mice are skipped automatically, so Pad Bridge does not hide them from the games that want them. Use `--include` if you really want one bridged. Controllers that are already Xbox pads are skipped too.

---

## Vibration support

Direct vibration commands are built in for these adapter families. The commands follow the protocols of the matching Linux kernel HID drivers.

| Method | Family | Built-in USB IDs | Confirmed on hardware |
|---|---|---|---|
| `pl` | Twin / GreenAsia 0x03 | `0810:0001`, `0e8f:0003` | `0810:0001` only |
| `ga` | GreenAsia 0x12 | `0e8f:0012` | no |
| `dr` | DragonRise | `0079:0006` | no |
| `sj` | SmartJoy PLUS | `0925:0005` | no |
| `zp` | Zeroplus | none built in (found through the kernel lookup) | no |

For other pads, Pad Bridge:

1. Asks SDL whether it can rumble the pad (works for many Xbox / PlayStation / Switch-style pads).
2. Otherwise downloads the Linux force-feedback driver lists once (and caches them for a week) to see whether the pad's USB ID is listed.
3. Otherwise you can run `--learn`, which tries each known command on the pad and asks you whether it vibrated.

If a PS2 adapter has two ports and the wrong controller shakes, run `--test`. It finds out whether the ports are crossed and remembers the fix.

---

## Profiles

A profile gives one pad (or every pad) its own dead zones, response curves, remapping and more, a small version of what remapping tools such as reWASD offer. Run:

```
python pad_bridge.py --init-profile
```

then open `pad_bridge_config.json` in any text editor and change the numbers. While Pad Bridge runs, the file is checked once a second, so a saved change takes effect within a second. A mistake in the file never stops Pad Bridge; the wrong entry is skipped and reported.

A profile can be given for `"*"` (every pad), for a model (`"0810:0001"`) or for one exact pad (its GUID, shown by `--info`). The more specific one wins.

```json
"profiles": {
  "0810:0001": {
    "left_stick":  {"deadzone": 0.10, "anti_deadzone": 0.15, "curve": 1.4, "sensitivity": 1.0,
                    "outer": 1.0, "invert_x": false, "invert_y": false, "radial": true},
    "triggers":    {"deadzone": 0.05, "max": 0.9, "curve": 1.0, "digital": false, "threshold": 0.5},
    "swap_sticks": false,
    "remap":  {"A": "B", "B": "A", "LT": "LB", "X": "none", "B12": "Y"},
    "turbo":  {"X": 10},
    "toggle": ["L3"],
    "shift":  {"hold": "BACK", "remap": {"A": "X", "B": "Y"}}
  }
}
```

Remapping outputs: `A B X Y LB RB LT RT BACK START L3 R3 UP DOWN LEFT RIGHT GUIDE`, or `none`. `B<n>` means the raw button number *n* of the pad (use `--show` to find it). Rapid fire (`turbo`) is in Hz, 1 to 30.

Run `python pad_bridge.py --help` for the full list of settings and what each one does.

---

## Files it creates

All of them are next to the script:

| File | What it is |
|---|---|
| `pad_bridge_config.json` | Your settings: saved layouts, vibration methods, profiles, tuning. A damaged file is renamed to `pad_bridge_config.json.bad-<date>`, never overwritten |
| `pad_bridge.log` | Everything the program prints (the previous log is kept as `pad_bridge.log.1`) |
| `pad_bridge_cache.json` | Downloaded lookups; safe to delete |
| `gamecontrollerdb_community.txt`, `gamecontrollerdb_active.txt` | The downloaded community controller list and the merged list SDL reads; safe to delete |
| `gamecontrollerdb.txt` | **Optional, yours.** Add your own SDL controller lines here; they win over the community list |
| `pad_bridge_hidhide_state.json` | What Pad Bridge changed in HidHide, so it can undo exactly that |

---

## What it changes on your PC, and what it connects to

- **HidHide:** it registers itself and hides your real controller from other programs while it runs. On exit it removes only what it added. If it ever crashes and your controller stays hidden, run `python pad_bridge.py --restore` (or open the HidHide Configuration Client).
- **Administrator rights:** HidHide normally needs them. Pad Bridge itself works without, but your real pad may then stay visible to games.
- **Internet:** it downloads the community controller list from GitHub (jsDelivr as a backup) about once a week, and the Linux kernel's HID driver sources (GitHub, kernel.org as a backup) to look up vibration methods. Use `--offline` to stop all of it.
- **Start with Windows:** only if you turn it on with `--autostart on` (or from the tray menu). `--autostart off` removes it.

---

## Troubleshooting

| Problem | Try |
|---|---|
| Nothing works / no virtual pad | `--doctor`, then restart Windows once after installing ViGEmBus |
| The game sees two pads | Install HidHide and run as administrator (or check with `--doctor`) |
| Buttons are wrong | `--setup`, and `--show` to see what is pressed |
| Sticks do nothing | Press the pad's Analog / Mode button; if it is stuck in digital mode, use `--dpad-stick` |
| No vibration | Make sure `pywinusb` is installed, then `--learn` |
| The wrong controller shakes | `--test` |
| Vibration is too weak | `--tune` |
| Real controller still hidden after a crash | `--restore` |
| Anything else | Look in `pad_bridge.log` |

---

## Reporting a problem

Open an issue and include:

1. The output of `python pad_bridge.py --info` (it lists your pads, their IDs and the versions of everything).
2. Your `pad_bridge.log`.
3. What you expected and what happened.

Reports that say "my adapter (VID:PID) works / does not work" are especially useful, because most hardware here has not been tested.

---

## Credits

- [vgamepad](https://github.com/yannbouteiller/vgamepad) and the [ViGEmBus](https://github.com/nefarius/ViGEmBus) driver: the virtual Xbox 360 pad
- [HidHide](https://github.com/nefarius/HidHide): hiding the real controllers
- [pygame-ce](https://github.com/pygame-community/pygame-ce) / SDL: reading controllers
- [pywinusb](https://github.com/rene-aguirre/pywinusb): direct vibration commands
- [SDL_GameControllerDB](https://github.com/mdqinc/SDL_GameControllerDB): community controller layouts
- The Linux kernel HID force-feedback drivers (`hid-pl`, `hid-gaff`, `hid-dr`, `hid-sjoy`, `hid-zpff`): the vibration protocols and the lists of supported USB IDs

## About this project

Pad Bridge was written with the help of an AI assistant (Claude), with testing on real hardware by the author. It is a hobby project and comes with no guarantees.

## License

MIT. See [LICENSE](LICENSE).
