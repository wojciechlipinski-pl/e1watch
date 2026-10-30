# Key tests for e1watch in a pseudo-terminal (xterm, PuTTY, Linux console, Esc+digit, letters): help, number mask, lone Esc, F10.
import fcntl, os, pty, re, select, signal, struct, sys, termios, time, unittest

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')


def start(term):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(TERM=term, LANG='C.UTF-8', PYTHONPATH=SRC)
        os.execv(sys.executable, [sys.executable, '-m', 'e1watch', '--demo', '-c', '/nonexistent'])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 110, 0, 0))
    return pid, fd


def read(fd, t=1.0):
    out = b''; end = time.time() + t
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try: d = os.read(fd, 65536)
            except OSError: break
            if not d: break
            out += d
    t = out.decode(errors='replace')
    t = re.sub(r'\x1b\[(\d*)C', lambda m: ' ' * int(m.group(1) or 1), t)
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\([A-Z0-9]|\x1b[=>]', '', t)


def alive(pid):
    try: return os.waitpid(pid, os.WNOHANG)[0] == 0
    except ChildProcessError: return False


class Keys(unittest.TestCase):
    def run_case(self, term, f1, f2, f10):
        pid, fd = start(term)
        try:
            read(fd, 1.5)
            os.write(fd, f1); self.assertIn('Link colour', read(fd, 0.9), 'help opens')
            os.write(fd, b'x'); read(fd, 0.6)
            os.write(fd, f2); self.assertIn('123', read(fd, 0.9), 'mask toggled')
            os.write(fd, b'\x1b'); read(fd, 0.6); self.assertTrue(alive(pid), 'lone Esc must not quit')
            os.write(fd, f10); read(fd, 0.8); time.sleep(0.3); self.assertFalse(alive(pid), 'F10 quits')
        finally:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
                try: os.waitpid(pid, 0)
                except Exception: pass

    def test_xterm_ss3(self): self.run_case('xterm', b'\x1bOP', b'\x1bOQ', b'\x1b[21~')
    def test_putty(self): self.run_case('xterm', b'\x1b[11~', b'\x1b[12~', b'\x1b[21~')
    def test_linux_console(self): self.run_case('linux', b'\x1b[[A', b'\x1b[[B', b'\x1b[21~')
    def test_esc_digit(self): self.run_case('xterm', b'\x1b1', b'\x1b2', b'\x1b0')
    def test_letters(self): self.run_case('xterm-256color', b'h', b'm', b'q')


if __name__ == '__main__':
    unittest.main()
