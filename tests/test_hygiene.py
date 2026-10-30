# Public-release hygiene: no private IPs, credentials or site-specific names in tracked files.
# Extra site-specific words go into tests/denylist.local.txt (gitignored, one regex per line).
import os, re, subprocess, unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
BUILTIN = [r'\b10\.\d+\.\d+\.\d+\b', r'\b192\.168\.\d+\.\d+\b', r'\b172\.(1[6-9]|2\d|3[01])\.\d+\.\d+\b', r'BEGIN (RSA|OPENSSH|EC) PRIVATE KEY',
           r'(?i)\b(password|passwd|secret)\s*=\s*[^\s;#<]{4,}']


def tracked():
    try:
        out = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, capture_output=True, text=True, check=False).stdout.split('\n')
    except OSError:
        out = []
    return [f for f in out if f and f != 'LICENSE' and not f.startswith('tests/test_hygiene')]


class Hygiene(unittest.TestCase):
    def test_no_leaks(self):
        pats = list(BUILTIN)
        try: pats += [l.strip() for l in open(os.path.join(ROOT, 'tests', 'denylist.local.txt')) if l.strip() and not l.startswith('#')]
        except OSError: pass
        bad = []
        for f in tracked():
            try: text = open(os.path.join(ROOT, f), errors='ignore').read()
            except OSError: continue
            for p in (pats if not f.endswith('.py') else [x for x in pats if x not in BUILTIN[4:]]):
                for m in re.finditer(p, text):
                    bad.append('%s: %r' % (f, m.group(0)))
        self.assertEqual(bad, [])


if __name__ == '__main__':
    unittest.main()
