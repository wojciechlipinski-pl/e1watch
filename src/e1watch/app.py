#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""e1watch: a console monitor for E1/T1 PRI links and ISDN B-channels on Asterisk + DAHDI.

Shows per span: line alarm state, D-channel status, BPV/CRC4 counters and a colour-coded grid of B-channels
(free / busy / ringing / connected) with both call parties, a load bar with history and an event log.
Data sources: Asterisk AMI (CoreShowChannels), /proc/dahdi/N, /proc/interrupts and `asterisk -rx "pri show spans"`.
Link colour: GREEN = signal OK and ISDN active, YELLOW = signal OK but ISDN (D-channel) inactive, RED = no signal / E1 alarm.
"""
import argparse, collections, configparser, curses, glob, html, math, os, random, re, socket, subprocess, sys, textwrap, time, unicodedata

VERSION = '0.1.0'
CONF_PATHS = ('/etc/e1watch.conf', os.path.expanduser('~/.config/e1watch.ini'))
STATE_COLOR = {'BUSY': 'yellow', 'RINGING': 'cyan', 'CONNECTED': 'blue'}   # red is reserved for failures (alarm, no link)
IRQ_DEFAULT = r'dahdi|wct|wcte|tor3|tormenta|t[24]xxp|te[0-9]xx'
ALARMS = r'\b(RED|BLUE|YELLOW|RECOVER|NOTOPEN|LOOPBACK)\b'

PL = {
    'E1 link and ISDN channel monitor': 'Monitor łącza E1 i kanałów ISDN',
    'Clock: ': 'Takt: ', 'free-run': 'własny', 'line': 'z łącza', 'Interrupts: ': 'Przerwania: ', 'Calls: ': 'Rozmowy: ',
    ' (ringing ': ' (wołanie ', ', connected ': ', połączone ', ')   Free channels: ': ')   Wolne kanały: ',
    'Number mask: ': 'Maska numerów: ', 'Mask: ': 'Maska: ', 'on': 'wł.', 'off': 'wył.',
    'NO SIGNAL': 'BRAK SYGNAŁU', 'NO DATA': 'BRAK DANYCH', 'ALARM ': 'ALARM ', 'SIGNAL OK · ISDN INACTIVE': 'SYGNAŁ OK · ISDN NIEAKTYWNY', 'LINK OK': 'ŁĄCZE SPRAWNE',
    'Alarm ': 'Alarm ', '   D-channel ': '   Kanał D ', 'Busy ': 'Zajęte ', 'recent minutes ': 'ostatnie minuty ', 'no link: channels unavailable': 'brak łącza: kanały niedostępne',
    'BUSY': 'ZAJĘTY', 'RINGING': 'WOŁANIE', 'CONNECTED': 'POŁĄCZONY', 'NEW': 'NOWE', 'ENDED': 'KONIEC', 'Interrupts: ': 'Przerwania: ',
    'free': 'wolny', 'busy': 'zajęty', 'ringing (blinks)': 'wołanie (miga)', 'connected': 'połączony', 'no link': 'brak łącza',
    ' Chan   Direction  State         Party A            Party B             Time': ' Kanał  Kierunek   Stan          Numer A            Numer B             Czas',
    '(no active calls)': '(brak aktywnych połączeń)', ' Events': ' Zdarzenia', '(no events since start)': '(brak zdarzeń od uruchomienia)', 'chan %-4d': 'kanał %-4d',
    'Help': 'Pomoc', 'Mask': 'Maska', 'Spans': 'Spany', 'Events': 'Zdarzenia', 'Refresh': 'Odśwież', 'Quit': 'Wyjście', ' Help ': ' Pomoc ',
    'E1/ISDN link and channel monitor': 'Monitor łącza E1/ISDN i kanałów',
    'Link colour:  GREEN   signal OK and ISDN active': 'Kolor łącza:  ZIELONY  sygnał OK i ISDN aktywny',
    '              YELLOW  signal OK, but ISDN (D-channel) inactive or errors': '              ŻÓŁTY    sygnał OK, ale ISDN (kanał D) nieaktywny lub błędy',
    '              RED     no signal / E1 alarm (failures only)': '              CZERWONY brak sygnału / alarm E1 (tylko awarie)',
    'free   busy (no ringing)': 'wolny   zajęty (bez dzwonka)', 'ringing (cyan, blinks)   connected (blue, call in progress)': 'wołanie (cyan, miga)   połączony (niebieski, rozmowa w toku)',
    'no link (E1 alarm)': 'brak łącza (alarm E1)',
    'BPV/CRC4: green = no new errors, yellow/red = growing.': 'BPV/CRC4: zielone = bez nowych błędów, żółte/czerwone = rosnące.',
    'F2 or m  mask external numbers (middle digits)': 'F2 lub m  maskowanie numerów zewnętrznych (środek cyfr)',
    'F3 or s  show/hide spans after the first    F4 or z  events': 'F3 lub s  pokaż/ukryj spany po pierwszym    F4 lub z  zdarzenia',
    'F5 or r  refresh     F10 or q  quit     F1, ? or h  help': 'F5 lub r  odśwież     F10 lub q  wyjście     F1, ? lub h  pomoc',
    'If your terminal captures F-keys use letters or Esc + digit (Esc 1 = F1, Esc 0 = F10).    Any key closes help.':
        'Gdy terminal przechwytuje klawisze F: użyj liter albo Esc + cyfra (Esc 1 = F1, Esc 0 = F10).    Dowolny klawisz zamyka pomoc.',
}


def tr(s, lang):
    if lang != 'pl': return s
    if s in PL: return PL[s]
    core = s.strip()   # same phrase with different padding
    if core in PL: return s[:len(s) - len(s.lstrip())] + PL[core] + s[len(s.rstrip()):]
    for k, v in PL.items():
        if k.strip() == core: return s[:len(s) - len(s.lstrip())] + v.strip() + s[len(s.rstrip()):]
    return s


# ------------------------------------------------------------------ configuration
def parse_ranges(text):
    out = []
    for part in re.split(r'[,\s]+', text or ''):
        m = re.fullmatch(r'(\d+)(?:-(\d+))?', part)
        if m: out.extend(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return out


def load_config(path=None):
    cp = configparser.ConfigParser(interpolation=None)
    paths = [path] if path else [os.environ.get('E1WATCH_CONF')] + list(CONF_PATHS)
    cp.read([p for p in paths if p])
    g = lambda sec, key, default='': cp.get(sec, key, fallback=default).strip()
    secret = g('ami', 'secret')
    if not secret and g('ami', 'secret_file'):
        try: secret = open(g('ami', 'secret_file')).read().strip()
        except OSError: pass
    conf = {'ami_host': g('ami', 'host', '127.0.0.1'), 'ami_port': int(g('ami', 'port', '5038')), 'ami_user': g('ami', 'user', 'e1watch'), 'ami_secret': secret,
            'lang': g('e1watch', 'lang', 'en'), 'mask': g('e1watch', 'mask', 'yes').lower() in ('1', 'yes', 'true', 'on'),
            'inbound_contexts': tuple(x for x in re.split(r'[,\s]+', g('e1watch', 'inbound_contexts')) if x),
            'irq_pattern': g('e1watch', 'irq_pattern', IRQ_DEFAULT), 'clock_state_file': g('e1watch', 'clock_state_file'),
            'keep_digits': int(g('e1watch', 'unmasked_max_digits', '6')), 'only_spans': parse_ranges(g('e1watch', 'spans')), 'spans': {}}
    for sec in cp.sections():
        m = re.fullmatch(r'span\.(\d+)', sec)
        if m: conf['spans'][int(m.group(1))] = {'name': g(sec, 'name'), 'channels': parse_ranges(g(sec, 'channels'))}
    return conf


# ------------------------------------------------------------------ data
class AMI:
    def __init__(self, conf):
        self.conf = conf; self.s = None; self.buf = b''

    def connect(self):
        self.s = socket.create_connection((self.conf['ami_host'], self.conf['ami_port']), timeout=3)
        self.s.settimeout(3); self.buf = b''
        self._read_until(b'\r\n')
        self._send({'Action': 'Login', 'Username': self.conf['ami_user'], 'Secret': self.conf['ami_secret'], 'Events': 'off'})
        r = self._block()
        if 'Success' not in r.get('Response', ''):
            raise RuntimeError('AMI login: ' + r.get('Message', '?'))

    def _send(self, d):
        self.s.sendall(('\r\n'.join('%s: %s' % kv for kv in d.items()) + '\r\n\r\n').encode())

    def _read_until(self, sep):
        while sep not in self.buf:
            d = self.s.recv(65536)
            if not d: raise RuntimeError('AMI closed the connection')
            self.buf += d
        i = self.buf.index(sep) + len(sep); out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def _block(self):
        t = self._read_until(b'\r\n\r\n').decode(errors='replace')
        return dict(l.split(': ', 1) for l in t.split('\r\n') if ': ' in l)

    def channels(self):
        self._send({'Action': 'CoreShowChannels'})
        out = []
        while True:
            b = self._block()
            if b.get('Event') == 'CoreShowChannel': out.append(b)
            elif b.get('Event') == 'CoreShowChannelsComplete': return out
            elif b.get('Response') == 'Error': raise RuntimeError('AMI: ' + b.get('Message', '?'))


def read_file(path):
    try:
        with open(path) as f: return f.read()
    except OSError:
        return ''


def parse_span(text):
    """Parses /proc/dahdi/N: returns dict(name, alarm, bpv, crc, channels) or None when the span does not exist.
    B-channels exclude the HDLC (D) channel."""
    if not text: return None
    head = text.split('\n', 1)[0]
    name = re.search(r'"([^"]*)"', head)
    alarm = re.search(ALARMS, head)
    bpv = re.search(r'BPV count: (\d+)', text); crc = re.search(r'CRC4 error count: (\d+)', text)
    chans = []
    for line in text.split('\n')[1:]:
        m = re.match(r'\s+(\d+)\s+(\S+)\s*(.*)$', line)
        if m and 'HDLC' not in m.group(3).upper() and 'HDLC' not in m.group(2).upper(): chans.append(int(m.group(1)))
    return {'name': name.group(1) if name else '', 'alarm': alarm.group(1) if alarm else 'OK', 'bpv': int(bpv.group(1)) if bpv else None,
            'crc': int(crc.group(1)) if crc else None, 'channels': chans}


def detect_spans(proc='/proc/dahdi'):
    found = {}
    for p in glob.glob(os.path.join(proc, '[0-9]*')):
        n = int(os.path.basename(p)); info = parse_span(read_file(p))
        if info: found[n] = info
    return found


def interrupts(pattern):
    rx = re.compile(pattern, re.I)
    for line in read_file('/proc/interrupts').split('\n'):
        if rx.search(line): return sum(int(x) for x in line.split()[1:] if x.isdigit())
    return None


def pri_spans():
    try:
        out = subprocess.run(['asterisk', '-rx', 'pri show spans'], capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return {}
    return {int(m.group(1)): m.group(2).strip() for m in re.finditer(r'PRI span (\d+)/\d+: (.+)', out)}


def call_state(desc):
    d = (desc or '').lower()
    if d == 'up': return 'CONNECTED'
    if d in ('ring', 'ringing', 'dialing', 'dialing offhook', 'pre-ring'): return 'RINGING'
    return 'BUSY'


def channel_number(name):
    m = re.match(r'DAHDI/(?:[gGrRiI]\d+/)?(\d+)-\d+', name or '')
    return int(m.group(1)) if m else None


def to_seconds(d):
    m = re.fullmatch(r'(\d+):(\d+):(\d+)', d or '')
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else 0


def fmt_time(s):
    s = int(s); return '%02d:%02d:%02d' % (s // 3600, s % 3600 // 60, s % 60)


def calls_from_channels(chans, inbound_contexts=()):
    """Maps AMI CoreShowChannels rows to {dahdi channel: call}. Direction: a DAHDI leg that started the call
    (Uniqueid == Linkedid) is inbound from the PSTN; inbound_contexts, when configured, overrides the heuristic."""
    out = {}
    for c in chans:
        n = channel_number(c.get('Channel'))
        if n is None: continue
        if inbound_contexts: inbound = c.get('Context') in inbound_contexts
        elif c.get('Uniqueid') and c.get('Linkedid'): inbound = c['Uniqueid'] == c['Linkedid']
        else: inbound = False
        st = call_state(c.get('ChannelStateDesc'))
        a = c.get('CallerIDNum', ''); b = c.get('ConnectedLineNum', '') or c.get('Exten', '')
        if inbound and st != 'CONNECTED': b = c.get('Exten', '') or b
        out[n] = {'dir': 'IN' if inbound else 'OUT', 'state': st, 'a': a, 'b': b, 'dur': c.get('Duration', ''), 'sec': to_seconds(c.get('Duration', ''))}
    return out


def demo_channels(t, scenario):
    """Simulated data. scenario: 'ok' (link OK with calls), 'alarm' (no signal), 'd' (signal OK, D-channel inactive)."""
    random.seed(int(t / 7))
    rows = [(3, 'IN', 'Up', '4915550100123', '1001'), (7, 'OUT', 'Ringing', '1002', '4915550100456'), (12, 'IN', 'Ring', '4930555010078', '1003'),
            (18, 'OUT', 'Up', '1004', '4940555010099'), (22, 'IN', 'Down', '', '')]
    if scenario != 'ok': return []
    return [{'Channel': 'DAHDI/%d-1' % n, 'Uniqueid': '1.1' if k == 'IN' else '2.2', 'Linkedid': '1.1', 'ChannelStateDesc': s, 'CallerIDNum': a, 'Exten': b,
             'ConnectedLineNum': b if k == 'OUT' else '', 'Duration': '00:%02d:%02d' % (random.randint(0, 5), random.randint(0, 59))} for n, k, s, a, b in rows[:random.randint(3, 5)]]


def mask_number(num, mask, keep=6):
    num = (num or '').strip()
    if not num: return '-'
    if not mask or len(num) <= keep: return num
    return num[:3] + '*' * (len(num) - 6) + num[-3:]


class Model:
    def __init__(self, conf, demo=False, scenario='ok'):
        self.conf = conf; self.demo = demo; self.scen = scenario; self.ami = None; self.calls = {}; self.error = ''
        self.pri = {}; self.t_pri = 0; self.irq = None; self.irq_t = None; self.irq_s = None
        self.prev = {}; self.events = collections.deque(maxlen=60); self.t_poll = time.time(); self.t_hist = 0
        self.spans = {}; self.info = {}; self.last = {}; self.hist = {}
        self._load_spans()

    def _load_spans(self):
        if self.demo:
            ch = list(range(1, 16)) + list(range(17, 32))
            det = {1: {'name': 'Carrier PRI', 'channels': ch}, 2: {'name': 'Test link', 'channels': [c + 93 for c in ch]}}
        else:
            det = detect_spans()
        if self.conf['only_spans'] and not self.demo: det = {n: d for n, d in det.items() if n in self.conf['only_spans']}
        for n, d in self.conf['spans'].items():
            base = det.get(n, {'name': '', 'channels': []})
            det[n] = {'name': d['name'] or base['name'], 'channels': d['channels'] or base['channels']}
        self.spans = {n: (d['name'] or 'span %d' % n, d['channels']) for n, d in sorted(det.items())}
        self.hist = {n: collections.deque(maxlen=48) for n in self.spans}
        self.info = {n: {'alarm': 'NODATA', 'bpv': None, 'crc': None} for n in self.spans}
        if self.demo:
            for n in self.spans:
                for i in range(48): self.hist[n].append(int(2 + 2 * (1 + math.sin(i / 5.0)) + (i % 7 == 0)) if self.scen == 'ok' else 0)

    def _log(self, ch, call, kind, dur=''):
        self.events.appendleft((time.strftime('%H:%M:%S'), ch, call['dir'], call['a'], call['b'], kind, call['state'], dur, time.time()))

    def _track(self, new):
        for ch, r in new.items():
            p = self.prev.get(ch)
            if p is None: self._log(ch, r, 'NEW')
            elif p['state'] != r['state']: self._log(ch, r, 'STATE')
        for ch, p in self.prev.items():
            if ch not in new: self._log(ch, p, 'ENDED', fmt_time(p['sec'] + (time.time() - self.t_poll)))
        self.prev = {c: dict(r) for c, r in new.items()}

    def _demo_spans(self):
        first, second = list(self.spans)[:2] if len(self.spans) > 1 else (list(self.spans)[0], None)
        if self.scen == 'alarm': st = {first: ('RED', 'In Alarm, Down, Active'), second: ('RED', 'In Alarm, Down, Active')}
        elif self.scen == 'd': st = {first: ('OK', 'Down, Active'), second: ('OK', 'Down, Active')}
        else: st = {first: ('OK', 'Up, Active'), second: ('RED', 'In Alarm, Down, Active')}
        for n, (al, pri) in st.items():
            self.info[n] = {'alarm': al, 'bpv': 9e7 if al != 'OK' else 0, 'crc': 9e7 if al != 'OK' else 0}; self.pri[n] = pri
        self.irq_s = 1000

    def refresh(self):
        t = time.time()
        if self.demo: self._demo_spans()
        else:
            for n in self.spans:
                i = parse_span(read_file('/proc/dahdi/%d' % n))
                self.info[n] = {'alarm': i['alarm'], 'bpv': i['bpv'], 'crc': i['crc']} if i else {'alarm': 'NODATA', 'bpv': None, 'crc': None}
            cnt = interrupts(self.conf['irq_pattern'])
            if cnt is not None and self.irq is not None and t > self.irq_t: self.irq_s = int((cnt - self.irq) / (t - self.irq_t))
            self.irq, self.irq_t = cnt, t
            if t - self.t_pri > 3: self.pri = pri_spans(); self.t_pri = t
        try:
            if self.demo: new = calls_from_channels(demo_channels(t, self.scen), self.conf['inbound_contexts'])
            else:
                if self.ami is None: self.ami = AMI(self.conf); self.ami.connect()
                new = calls_from_channels(self.ami.channels(), self.conf['inbound_contexts'])
            self.error = ''
        except Exception as e:
            self.ami = None; new = {}; self.error = 'AMI: %s' % e
        self._track(new); self.calls = new; self.t_poll = time.time()
        if self.t_poll - self.t_hist >= 2:
            self.t_hist = self.t_poll
            for n, (_, chans) in self.spans.items(): self.hist[n].append(sum(1 for ch in new if ch in chans))


# ------------------------------------------------------------------ canvases (curses and text/HTML)
class Attr:
    """Text attributes: fg (None/green/yellow/red/cyan/blue/key/sel), bold, dim, rev (reverse: the fg colour becomes the background)."""
    __slots__ = ('fg', 'bold', 'dim', 'rev')
    def __init__(self, fg=None, bold=False, dim=False, rev=False): self.fg, self.bold, self.dim, self.rev = fg, bold, dim, rev
    def key(self): return (self.fg, self.bold, self.dim, self.rev)


def ascii_fold(s):
    s = s.replace('ł', 'l').replace('Ł', 'L').replace('→', '>').replace('←', '<').replace('·', '-').replace('▸', '>').replace('▾', 'v')
    return unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode()


class TextCanvas:
    def __init__(self, w, h, unicode_ok):
        self.w, self.h, self.u = w, h, unicode_ok
        self.g = [[(' ', (None, False, False, False))] * w for _ in range(h)]
        self.box = dict(h='─', v='│', tl='┌', tr='┐', bl='└', br='┘') if unicode_ok else dict(h='-', v='|', tl='+', tr='+', bl='+', br='+')

    def put(self, y, x, text, a=None):
        a = a or Attr()
        if y < 0 or y >= self.h: return
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w: self.g[y][x + i] = (ch, a.key())

    def glyph(self, y, x, name, a): self.put(y, x, self.box[name], a)
    def hline(self, y, x, n, a): self.put(y, x, self.box['h'] * n, a)
    def vline(self, y, x, n, a):
        for i in range(n): self.put(y + i, x, self.box['v'], a)

    def text(self):
        return '\n'.join(''.join(c for c, _ in row).rstrip() for row in self.g)

    def html(self):
        css = {'green': '#3ddc5a', 'yellow': '#f0c93a', 'red': '#ff4d4d', 'cyan': '#3dd6e0', 'blue': '#4f9dff'}
        out = []
        for row in self.g:
            line = []
            for ch, (fg, bold, dim, rev) in row:
                col, bg, extra = '#d8d8d8', '#101010', []
                if fg == 'key': col, bg = '#0a8a8a', '#e8e8e8'
                elif fg == 'sel': col, bg = '#000000', css['cyan']
                elif fg in css:
                    if rev: col, bg = '#101010', css[fg]
                    else: col = css[fg]
                elif rev: col, bg = '#101010', '#e8e8e8'
                if dim: extra.append('opacity:.6')
                if bold: extra.append('font-weight:bold')
                line.append('<span style="color:%s;background:%s;%s">%s</span>' % (col, bg, ';'.join(extra), html.escape(ch)))
            out.append(''.join(line))
        return '\n'.join(out)

    @staticmethod
    def html_anim(f0, f1):
        st = "font:15px/1.2 'DejaVu Sans Mono',Menlo,monospace;color:#d8d8d8;margin:0;position:absolute;top:12px;left:12px"
        return ('<!doctype html><meta charset="utf-8"><style>@keyframes a{0%%{opacity:1}50%%{opacity:0}}@keyframes b{0%%{opacity:0}50%%{opacity:1}}'
                '#f0{animation:a 1s steps(1) infinite}#f1{animation:b 1s steps(1) infinite}</style>'
                '<body style="background:#101010;margin:0"><pre id="f0" style="%s">%s</pre><pre id="f1" style="%s">%s</pre>' % (st, f0, st, f1))


class CursesCanvas:
    def __init__(self, win, unicode_ok):
        self.win, self.u = win, unicode_ok; self.h, self.w = win.getmaxyx()
        curses.start_color(); curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1); curses.init_pair(2, curses.COLOR_YELLOW, -1); curses.init_pair(3, curses.COLOR_RED, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1); curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(6, 30 if curses.COLORS >= 256 else curses.COLOR_CYAN, curses.COLOR_WHITE)
        curses.init_pair(7, 39 if curses.COLORS >= 256 else curses.COLOR_BLUE, -1)   # light blue readable on black
        self.pairs = {'green': 1, 'yellow': 2, 'red': 3, 'cyan': 4, 'sel': 5, 'key': 6, 'blue': 7}

    def _a(self, a):
        v = curses.A_NORMAL
        if a.fg in self.pairs: v |= curses.color_pair(self.pairs[a.fg])
        if a.bold: v |= curses.A_BOLD
        if a.dim: v |= curses.A_DIM
        if a.rev: v |= curses.A_REVERSE
        return v

    def put(self, y, x, text, a=None):
        a = a or Attr()
        if y < 0 or y >= self.h or x >= self.w: return
        try: self.win.addnstr(y, x, text, max(0, self.w - x - (1 if y == self.h - 1 else 0)), self._a(a))
        except curses.error: pass

    def _acs(self, n): return dict(h=curses.ACS_HLINE, v=curses.ACS_VLINE, tl=curses.ACS_ULCORNER, tr=curses.ACS_URCORNER, bl=curses.ACS_LLCORNER, br=curses.ACS_LRCORNER)[n]
    def glyph(self, y, x, name, a):
        try: self.win.addch(y, x, self._acs(name), self._a(a))
        except curses.error: pass
    def hline(self, y, x, n, a):
        for i in range(n): self.glyph(y, x + i, 'h', a)
    def vline(self, y, x, n, a):
        for i in range(n): self.glyph(y + i, x, 'v', a)


# ------------------------------------------------------------------ screen
class Ui:
    def __init__(self, unicode_ok, mask=True, lang='en'):
        self.u = unicode_ok; self.mask = mask; self.lang = lang; self.more_spans = True; self.events = True; self.help = False

    def T(self, s):
        s = tr(s, self.lang)
        return s if self.u else ascii_fold(s)

    def sym(self, kind):
        if self.u: return {'free': '○', 'BUSY': '●', 'RINGING': '●', 'CONNECTED': '●', 'none': '·', 'dot': '●'}[kind]
        return {'free': '.', 'BUSY': 'B', 'RINGING': 'R', 'CONNECTED': 'C', 'none': 'x', 'dot': '*'}[kind]


def frame(c, ui, y, x, h, w, color, title_parts):
    r = Attr(color, bold=bool(color), dim=not color)
    c.glyph(y, x, 'tl', r); c.hline(y, x + 1, w - 2, r); c.glyph(y, x + w - 1, 'tr', r)
    for i in range(1, h - 1): c.glyph(y + i, x, 'v', r); c.glyph(y + i, x + w - 1, 'v', r)
    c.glyph(y + h - 1, x, 'bl', r); c.hline(y + h - 1, x + 1, w - 2, r); c.glyph(y + h - 1, x + w - 1, 'br', r)
    cx = x + 2
    for text, a in title_parts:
        text = ui.T(text); c.put(y, cx, text, a); cx += len(text)


def table_header(c, ui, y, w, text):
    c.put(y, 0, ui.T(text), Attr('cyan', bold=True)); c.hline(y + 1, 0, w - 1, Attr(dim=True))


def span_state(info, pri):
    """(colour, label): green = signal OK and ISDN active, yellow = signal OK but ISDN inactive, red = E1 alarm."""
    if info['alarm'] != 'OK': return 'red', {'RED': 'NO SIGNAL', 'NODATA': 'NO DATA'}.get(info['alarm'], 'ALARM ' + info['alarm'])
    if pri == '?': return 'green', 'LINK OK'
    if 'Up' not in pri or 'Down' in pri: return 'yellow', 'SIGNAL OK · ISDN INACTIVE'
    return 'green', 'LINK OK'


def bar(ui, n, maxn, width=20):
    k = 0 if maxn == 0 else int(round(width * n / maxn))
    return ('█' * k + '░' * (width - k)) if ui.u else ('#' * k + '-' * (width - k))


def chart(ui, data, maxn, width=30):
    g = '▁▂▃▄▅▆▇█' if ui.u else '.:-=+*#%'
    d = list(data)[-width:]
    return ''.join(g[min(len(g) - 1, int(v * (len(g) - 1) / maxn + 0.5))] if maxn else g[0] for v in d).rjust(width, g[0])


def draw(c, m, ui, now=None, phase=0, spin=0):
    now = now or time.time()
    w, h = c.w, c.h
    pulse = ('●○' if ui.u else '*o')[phase]
    left = ' %s E1 Watch %s ' % (pulse, VERSION); right = ' %s  %s ' % (socket.gethostname(), time.strftime('%H:%M:%S'))
    mid = (ui.T('E1 link and ISDN channel monitor') + ('  [DEMO: %s]' % m.scen if m.demo else '')).center(max(0, w - len(left) - len(right)))
    c.put(0, 0, (left + mid + right).ljust(w), Attr(bold=True, rev=True))
    # summary
    items = []
    clock_file = m.conf['clock_state_file']
    clock = read_file(clock_file).strip() if clock_file else ('line' if m.demo and m.scen == 'ok' else '')
    if clock: items.append(('Clock: ', clock if clock not in ('free', 'sync') else {'free': 'free-run', 'sync': 'line'}[clock], Attr({'free': 'yellow', 'sync': 'green', 'line': 'green'}.get(clock, 'yellow'), bold=True)))
    irq = m.irq_s
    items.append(('   Interrupts: ' if items else 'Interrupts: ', '%s/s' % ('?' if irq is None else irq), Attr('green' if (irq or 0) >= 500 else 'red', bold=True)))
    ringing = sum(1 for r in m.calls.values() if r['state'] == 'RINGING'); conn = sum(1 for r in m.calls.values() if r['state'] == 'CONNECTED')
    alive = sum(len(ch) for n, (_, ch) in m.spans.items() if m.info[n]['alarm'] == 'OK')
    free = '-' if not alive else str(alive - len(m.calls))
    items += [('   Calls: ', str(len(m.calls)), Attr(bold=True)), (' (ringing ', str(ringing), Attr('cyan', bold=True, rev=bool(ringing) and phase == 0)),
              (', connected ', str(conn), Attr('blue', bold=True)), (')   Free channels: ', free, Attr('green', bold=True)),
              ('   Number mask: ' if w >= 100 else '   Mask: ', 'on' if ui.mask else 'off', Attr())]
    x = 1
    for label, val, a in items:
        c.put(2, x, ui.T(label), Attr(dim=True)); x += len(ui.T(label)); c.put(2, x, ui.T(val), a); x += len(ui.T(val))
    if m.error: c.put(3, 1, m.error, Attr('red', bold=True))
    y = 4
    width = min(w, 80) if w < 100 else w
    spinner = ('◐◓◑◒' if ui.u else '|/-\\')[spin % 4]
    for idx, (n, (name, chans)) in enumerate(m.spans.items()):
        if idx > 0 and not ui.more_spans: continue
        info = m.info[n]; pri = m.pri.get(n, '?')
        color, label = span_state(info, pri); ok = info['alarm'] == 'OK'
        rows = (len(chans) + 14) // 15
        hgt = 4 + rows
        if idx > 0 and y + hgt + 8 > h: break
        blink = color == 'red' and phase == 1   # alarm badge blinks; yellow and green stay steady
        frame(c, ui, y, 0, hgt, width, color, [(' %s SPAN %d ' % (ui.sym('dot'), n), Attr(color, bold=True, rev=True)), (' %s ' % name, Attr(bold=True))])
        badge = ' ' + ui.T(label) + ' '
        c.put(y, width - len(badge) - 3, badge, Attr(color, bold=True, rev=not blink))

        def counter(key, val):
            if not ok or val is None: return '-', None
            p0 = m.last.get((n, key)); m.last[(n, key)] = (val, now)
            if not p0 or val < p0[0]: return str(int(val)), 0.0
            rate = (val - p0[0]) / max(now - p0[1], 0.5)
            return str(int(val)) + (' (+%.1f/s)' % rate), rate
        bpv, rb = counter('bpv', info['bpv']); crc, rc = counter('crc', info['crc'])
        rate_color = lambda r: None if r is None else ('green' if r == 0 else 'yellow' if r < 5 else 'red')
        x = 2
        pri_color = 'green' if 'Up' in pri and 'Down' not in pri else 'red' if 'Alarm' in pri else 'yellow'
        for label2, val, a in (('Alarm ', ' %s  ' % info['alarm'], Attr('green' if ok else 'red', bold=True, rev=True)),
                               ('   D-channel ', pri, Attr(pri_color, bold=True)),
                               ('   BPV ', bpv, Attr(rate_color(rb), bold=True, dim=rb is None)), ('   CRC4 ', crc, Attr(rate_color(rc), bold=True, dim=rc is None))):
            c.put(y + 1, x, ui.T(label2), Attr(dim=True)); x += len(ui.T(label2)); c.put(y + 1, x, ui.T(str(val)), a); x += len(ui.T(str(val)))
        busy = sum(1 for ch in m.calls if ch in chans); pct = int(100 * busy / len(chans)) if chans else 0
        if ok and chans:
            pc = 'green' if pct < 50 else 'yellow' if pct < 80 else 'red'
            c.put(y + 2, 2, ui.T('Busy '), Attr(dim=True)); c.put(y + 2, 9, '%2d/%d' % (busy, len(chans)), Attr(bold=True))
            c.put(y + 2, 15, bar(ui, busy, len(chans)), Attr(pc, bold=True)); c.put(y + 2, 36, '%3d%%' % pct, Attr(pc, bold=True))
            c.put(y + 2, 42, ui.T('recent minutes '), Attr(dim=True)); c.put(y + 2, 58, chart(ui, m.hist[n], len(chans), 16), Attr('cyan'))
        elif not ok:
            c.put(y + 2, 2, ui.T('no link: channels unavailable'), Attr('red', dim=True))
        for i in range(0, len(chans), 15):
            xx = 2
            for ch in chans[i:i + 15]:
                r = m.calls.get(ch); yy = y + 3 + i // 15
                if not ok and not r: a, an, sy = Attr('red', dim=True), Attr(dim=True), ui.sym('none')
                elif not r: a, an, sy = Attr('green', bold=True), Attr('green', dim=True), ui.sym('free')
                elif r['state'] == 'RINGING': sy = spinner; a = an = Attr('cyan', bold=True, rev=phase == 0)   # blinks and spins
                elif r['state'] == 'CONNECTED': a = an = Attr('blue', bold=True, rev=True); sy = ui.sym('CONNECTED')
                else: a = an = Attr('yellow', bold=True, rev=True); sy = ui.sym('BUSY')
                c.put(yy, xx, '%3d' % ch, an); c.put(yy, xx + 3, sy, a); xx += 5
        y += hgt
    # legend
    legend = [('free', 'free', 'green', False), ('BUSY', 'busy', 'yellow', False), ('RINGING', 'ringing (blinks)', 'cyan', False), ('CONNECTED', 'connected', 'blue', False), ('none', 'no link', 'red', True)]
    x = 1
    for kind, text, fg, dim in legend:
        c.put(y, x, spinner if kind == 'RINGING' else ui.sym(kind), Attr(fg, bold=not dim, dim=dim, rev=kind in ('CONNECTED', 'BUSY'))); c.put(y, x + 2, ui.T(text), Attr(dim=True)); x += 4 + len(ui.T(text))
    y += 2
    # calls
    table_header(c, ui, y, w, ' Chan   Direction  State         Party A            Party B             Time'); y += 2
    max_rows = max(2, h - y - 1 - (8 if ui.events else 0))
    if not m.calls: c.put(y, 2, ui.T('(no active calls)'), Attr(dim=True)); y += 1
    for ch in sorted(m.calls)[:max_rows]:
        r = m.calls[ch]; fg = STATE_COLOR[r['state']]
        direction = 'PSTN→PBX' if r['dir'] == 'IN' else 'PBX→PSTN'
        ring = r['state'] == 'RINGING'; hl = ring and phase == 0
        if hl: c.put(y, 0, ' ' * (w - 1), Attr('cyan', bold=True, rev=True))   # whole row lights up while ringing
        A = (lambda **k: Attr('cyan', bold=True, rev=True)) if hl else (lambda **k: Attr(**k))
        keep = m.conf['keep_digits']
        c.put(y, 1, '%5d' % ch, A(bold=True)); c.put(y, 8, ui.T(direction), A()); c.put(y, 18, spinner if ring else ui.sym(r['state']), Attr(fg, bold=True, rev=hl))
        c.put(y, 20, ui.T(r['state']).ljust(11), Attr(fg, bold=True, rev=r['state'] == 'CONNECTED' or hl))
        c.put(y, 33, mask_number(r['a'], ui.mask, keep).ljust(18), A(bold=True)); c.put(y, 51, ui.T('→ ') + mask_number(r['b'], ui.mask, keep).ljust(16), A(bold=True))
        c.put(y, 71, fmt_time(r['sec'] + now - m.t_poll), A(dim=True) if not hl else A()); y += 1
    # event log (new entries flash for 3 s)
    if ui.events and y + 4 < h - 1:
        y += 1
        table_header(c, ui, y, w, ' Events'); y += 2
        room = h - y - 1
        if not m.events: c.put(y, 2, ui.T('(no events since start)'), Attr(dim=True))
        for t, ch, d, a, b, kind, state, dur, ts in list(m.events)[:max(0, room)]:
            fg = None if kind == 'ENDED' else STATE_COLOR[state]
            text = ui.T('ENDED') + (' (%s)' % dur if dur else '') if kind == 'ENDED' else (ui.T('NEW') + ' ' if kind == 'NEW' else '') + ui.T(state)
            flash = now - ts < 3
            keep = m.conf['keep_digits']
            c.put(y, 1, t, Attr(dim=not flash, bold=flash)); c.put(y, 10, ui.T('chan %-4d' % ch), Attr(bold=flash))
            c.put(y, 21, ui.T('PSTN→PBX' if d == 'IN' else 'PBX→PSTN'), Attr(bold=flash)); c.put(y, 31, '%s %s %s' % (mask_number(a, ui.mask, keep), ui.T('→'), mask_number(b, ui.mask, keep)), Attr(bold=flash))
            c.put(y, 62, ' ' + text + ' ', Attr(fg, bold=True, rev=flash)); y += 1
    # footer bar in the Midnight Commander / ZoneCTL style
    actions = (('F1', 'Help'), ('F2', 'Mask'), ('F3', 'Spans'), ('F4', 'Events'), ('F5', 'Refresh'), ('F10', 'Quit'))
    c.put(h - 1, 0, ' ' * (w - 1), Attr(rev=True)); x = 1
    for k, t in actions:
        seg = ui.T(' %s  ' % t)
        if x + len(k) + len(seg) >= w: break
        c.put(h - 1, x, k, Attr('key', dim=True)); x += len(k); c.put(h - 1, x, seg, Attr(rev=True)); x += len(seg)
    if ui.help: help_window(c, ui)


def help_window(c, ui):
    lines = ['E1/ISDN link and channel monitor', '',
             'Link colour:  GREEN   signal OK and ISDN active', '              YELLOW  signal OK, but ISDN (D-channel) inactive or errors', '              RED     no signal / E1 alarm (failures only)', '',
             ui.sym('free') + '  ' + 'free   busy (no ringing)'.replace('free   busy', 'free   ' + ui.sym('BUSY') + '  busy'), ui.sym('RINGING') + '  ' + 'ringing (cyan, blinks)   connected (blue, call in progress)',
             ui.sym('none') + '  ' + 'no link (E1 alarm)', '',
             'BPV/CRC4: green = no new errors, yellow/red = growing.', '',
             'F2 or m  mask external numbers (middle digits)', 'F3 or s  show/hide spans after the first    F4 or z  events', 'F5 or r  refresh     F10 or q  quit     F1, ? or h  help', '',
             'If your terminal captures F-keys use letters or Esc + digit (Esc 1 = F1, Esc 0 = F10).    Any key closes help.']
    limit = max(30, c.w - 8)
    wrapped = []
    for l in lines: wrapped.extend(textwrap.wrap(ui.T(l), limit, subsequent_indent='  ') or [''])
    wd = min(c.w, max(len(l) for l in wrapped) + 4); ht = min(c.h, len(wrapped) + 2)
    y0 = max(1, (c.h - ht) // 2); x0 = max(0, (c.w - wd) // 2)
    for i in range(ht): c.put(y0 + i, x0, ' ' * wd, Attr())
    frame(c, ui, y0, x0, ht, wd, 'cyan', [(' Help ', Attr('cyan', bold=True))])
    for i, l in enumerate(wrapped): c.put(y0 + 1 + i, x0 + 2, l, Attr())


def unicode_ok(args):
    if args.ascii: return False
    return 'utf' in (sys.stdout.encoding or '').lower() and os.environ.get('TERM') != 'linux'


# F-key sequences of various terminals (xterm, PuTTY, Linux console, Windows Terminal); terminfo does not always match $TERM
F_SEQUENCES = {b'OP': curses.KEY_F1, b'OQ': curses.KEY_F2, b'OR': curses.KEY_F3, b'OS': curses.KEY_F4,
               b'[[A': curses.KEY_F1, b'[[B': curses.KEY_F2, b'[[C': curses.KEY_F3, b'[[D': curses.KEY_F4, b'[[E': curses.KEY_F5,
               b'[11~': curses.KEY_F1, b'[12~': curses.KEY_F2, b'[13~': curses.KEY_F3, b'[14~': curses.KEY_F4, b'[15~': curses.KEY_F5,
               b'[17~': curses.KEY_F6, b'[18~': curses.KEY_F7, b'[19~': curses.KEY_F8, b'[20~': curses.KEY_F9, b'[21~': curses.KEY_F10,
               b'[1;2P': curses.KEY_F1, b'[1;2Q': curses.KEY_F2}
# Esc + digit = F1..F10 (as in Midnight Commander, for terminals that capture the F-keys)
ESC_DIGIT = {ord(c): getattr(curses, 'KEY_F%d' % (i + 1)) for i, c in enumerate('1234567890')}


def read_key(win, normal_timeout=250):
    """Reads a key; after ESC collects up to 5 bytes (80 ms) and decodes F-key sequences. A lone ESC returns 27 (does not quit)."""
    k = win.getch()
    if k != 27: return k
    seq = []
    try:
        win.timeout(80)
        for _ in range(5):
            n = win.getch()
            if n == -1: break
            seq.append(n)
    finally:
        win.timeout(normal_timeout)
    if seq:
        f = F_SEQUENCES.get(bytes(seq))
        if f is not None: return f
        if len(seq) == 1 and seq[0] in ESC_DIGIT: return ESC_DIGIT[seq[0]]
        for n in reversed(seq):
            try: curses.ungetch(n)
            except curses.error: break
    return 27


def curses_main(stdscr, m, ui):
    curses.curs_set(0); stdscr.timeout(250); stdscr.keypad(True)
    last = 0
    while True:
        now = time.time()
        if now - last >= 1: m.refresh(); last = now
        stdscr.erase()
        draw(CursesCanvas(stdscr, ui.u), m, ui, now, int(now * 2) % 2, int(now * 4) % 4); stdscr.refresh()
        k = read_key(stdscr)
        if k == -1: continue
        if ui.help: ui.help = False; continue
        if k in (ord('q'), ord('Q'), curses.KEY_F10): return
        elif k in (ord('m'), ord('M'), curses.KEY_F2): ui.mask = not ui.mask
        elif k in (ord('s'), ord('S'), curses.KEY_F3): ui.more_spans = not ui.more_spans
        elif k in (ord('z'), ord('Z'), curses.KEY_F4): ui.events = not ui.events
        elif k in (ord('r'), ord('R'), curses.KEY_F5): m.refresh(); last = time.time()
        elif k in (curses.KEY_F1, ord('?'), ord('h'), ord('H')): ui.help = True


def main(argv=None):
    ap = argparse.ArgumentParser(prog='e1watch', description='Console monitor for E1/T1 PRI links and ISDN channels (Asterisk + DAHDI).')
    ap.add_argument('-c', '--config', help='config file (default: $E1WATCH_CONF, /etc/e1watch.conf, ~/.config/e1watch.ini)')
    ap.add_argument('--once', action='store_true', help='print one frame as text and exit')
    ap.add_argument('--demo', nargs='?', const='ok', choices=['ok', 'alarm', 'd'], help='simulated data (ok | alarm | d)')
    ap.add_argument('--no-mask', action='store_true', help='do not mask external numbers'); ap.add_argument('--lang', choices=['en', 'pl'])
    ap.add_argument('--ascii', action='store_true'); ap.add_argument('--size', default='120x34', help='frame size for --once/--html')
    ap.add_argument('--html', metavar='FILE', help='write a blinking HTML preview'); ap.add_argument('--help-screen', action='store_true', help='render the help window in --once')
    ap.add_argument('--phase', type=int, default=0, choices=[0, 1]); ap.add_argument('--version', action='version', version='e1watch ' + VERSION)
    a = ap.parse_args(argv)
    conf = load_config(a.config)
    m = Model(conf, bool(a.demo), a.demo or 'ok'); ui = Ui(unicode_ok(a), conf['mask'] and not a.no_mask, a.lang or conf['lang'])
    if a.once or a.html:
        m.refresh(); time.sleep(1); m.refresh(); ui.help = a.help_screen
        w, h = (int(v) for v in a.size.split('x')); ui.u = unicode_ok(a) or (a.html is not None and not a.ascii)
        now = time.time()
        if a.html:
            frames = []
            for phase in (0, 1):
                c = TextCanvas(w, h, ui.u); draw(c, m, ui, now, phase, phase * 2); frames.append(c.html())
            with open(a.html, 'w') as f: f.write(TextCanvas.html_anim(*frames))
            print('written', a.html)
        else:
            c = TextCanvas(w, h, ui.u); draw(c, m, ui, now, a.phase, a.phase * 2); print(c.text())
        return 0
    os.environ.setdefault('ESCDELAY', '25')
    curses.wrapper(lambda s: curses_main(s, m, ui))
    return 0


if __name__ == '__main__':
    sys.exit(main())
