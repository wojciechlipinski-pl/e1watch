# e1watch

Console monitor for **E1/T1 PRI links and ISDN B-channels** on Asterisk + DAHDI.
One screen shows the line state per span, the D-channel, error counters and every B-channel (free / busy / ringing / connected) together with both call parties.

Polish version: [README.pl.md](README.pl.md)

```
 ● E1 Watch 0.1.3             E1 link and ISDN channel monitor  [DEMO: ok]             host    08:25:56

 Clock: line   Interrupts: 1000/s   Calls: 5 (ringing 2, connected 2)   Free channels: 25   Number mask:

┌─ ● SPAN 1  Carrier PRI ─────────────────────────────────────────────────────────────────── LINK OK ──┐
│ Alarm  OK     D-channel Up, Active   BPV 0   CRC4 0                                                  │
│ Busy    5/30 ███░░░░░░░░░░░░░░░░░  16%  recent minutes  ▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂▂                             │
│   1○   2○   3●   4○   5○   6○   7◐   8○   9○  10○  11○  12◐  13○  14○  15○                           │
│  17○  18●  19○  20○  21○  22●  23○  24○  25○  26○  27○  28○  29○  30○  31○                           │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ ● SPAN 2  Test link ─────────────────────────────────────────────────────────────────── NO SIGNAL ──┐
│ Alarm  RED     D-channel In Alarm, Down, Active   BPV -   CRC4 -                                     │
│ no link: channels unavailable                                                                        │
│  94·  95·  96·  97·  98·  99· 100· 101· 102· 103· 104· 105· 106· 107· 108·                           │
│ 110· 111· 112· 113· 114· 115· 116· 117· 118· 119· 120· 121· 122· 123· 124·                           │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
 ○ free  ● busy  ◐ ringing (blinks)  ● connected  · no link

 Chan   Direction  State         Party A            Party B             Time
───────────────────────────────────────────────────────────────────────────────────────────────────────
     3  PSTN→PBX  ● CONNECTED    491*******123     → 1001              00:03:06
     7  PBX→PSTN  ◐ RINGING      1002              → 491*******456     00:01:07

 Events
───────────────────────────────────────────────────────────────────────────────────────────────────────
 08:25:55 chan 22    PSTN→PBX  - → -                           NEW BUSY
 08:25:55 chan 18    PBX→PSTN  1004 → 494*******099            NEW CONNECTED
 08:25:55 chan 12    PSTN→PBX  493*******078 → 1003            NEW RINGING
 08:25:55 chan 7     PBX→PSTN  1002 → 491*******456            NEW RINGING
 F1 Help  F2 Mask  F3 Spans  F4 Events  F5 Refresh  F10 Quit
```

## Features

- **Link colour = state**: green (signal OK, ISDN active), yellow (signal OK, D-channel inactive), red (no signal / alarm). Red is reserved for failures; a connected call is blue.
- B-channel grid with spinner and blinking row while ringing, load bar, history chart, event log with flashing new entries.
- Both call parties, direction (PSTN→PBX / PBX→PSTN), call duration; external numbers masked by default.
- Spans and B-channels autodetected from `/proc/dahdi` (D-channel excluded); names and channel lists can be overridden.
- Works over SSH and on the Linux console (ASCII fallback); F-keys decoded for xterm, PuTTY, Linux console, Windows Terminal; `Esc`+digit and letter keys as alternatives.
- English and Polish UI. No dependencies beyond Python 3.8+ and the standard library.

## Install

```bash
pip install .            # or: python3 -m e1watch from a checkout with PYTHONPATH=src
```

## Asterisk setup

`CoreShowChannels` needs an AMI user with `write` permission. In `manager.conf` (keep AMI on localhost):

```ini
[e1watch]
secret = <choose-a-secret>
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
read = system,reporting
write = system,reporting
```

Copy [examples/e1watch.conf](examples/e1watch.conf) to `/etc/e1watch.conf` (mode 600 if it contains the secret, or use `secret_file`). Run as a user that may read `/proc/dahdi` and call `asterisk -rx` (usually root).

## Usage

```bash
sudo e1watch                 # live screen
e1watch --once               # one text frame (--size 120x35, --ascii)
e1watch --demo [ok|alarm|d]  # simulated data, no Asterisk needed
e1watch --lang pl --no-mask
```

Keys: `F1`/`?` help, `F2`/`m` number mask, `F3`/`s` extra spans, `F4`/`z` events, `F5`/`r` refresh, `F10`/`q` quit; `Esc`+`1..0` = F1..F10.

## How the state is derived

| Item | Source |
|---|---|
| Alarm, BPV, CRC4, channels | `/proc/dahdi/N` |
| D-channel (Up/Down/Alarm) | `asterisk -rx "pri show spans"` |
| Calls, parties, duration | AMI `CoreShowChannels` |
| Direction | DAHDI leg that started the call (`Uniqueid == Linkedid`) = inbound; or `inbound_contexts` |
| Interrupt rate | `/proc/interrupts` (`irq_pattern`) |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).
