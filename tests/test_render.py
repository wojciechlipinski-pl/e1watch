import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from e1watch import app


def frame(scen, lang='en', unicode_ok=True, w=110, h=34, help=False):
    conf = app.load_config('/nonexistent'); conf['lang'] = lang
    m = app.Model(conf, True, scen); m.refresh()
    ui = app.Ui(unicode_ok, True, lang); ui.help = help
    c = app.TextCanvas(w, h, unicode_ok); app.draw(c, m, ui, None, 0, 0)
    return c.text()


class Render(unittest.TestCase):
    def test_scenarios(self):
        self.assertIn('LINK OK', frame('ok')); self.assertIn('NO SIGNAL', frame('alarm')); self.assertIn('ISDN INACTIVE', frame('d'))

    def test_ascii_has_no_non_ascii(self):
        for s in ('ok', 'alarm', 'd'):
            self.assertTrue(frame(s, 'pl', False).isascii())

    def test_polish(self):
        self.assertIn('ŁĄCZE SPRAWNE', frame('ok', 'pl')); self.assertIn('Pomoc', frame('ok', 'pl'))

    def test_help_and_small_terminal(self):
        self.assertIn('Link colour', frame('ok', help=True))
        for w, h in ((60, 20), (80, 24), (200, 60)): frame('ok', w=w, h=h); frame('alarm', w=w, h=h, help=True)


if __name__ == '__main__':
    unittest.main()
