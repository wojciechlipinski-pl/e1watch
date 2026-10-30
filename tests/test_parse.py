import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from e1watch import app

SPAN = '''Span 1: TE2/0/1 "Test Card Span 1" (MASTER) HDB3/CCS/CRC4 ClockSource RED
	IRQ misses: 0
	BPV count: 12
	CRC4 error count: 3
	1 PRI/1/1 Clear (In use)
	2 PRI/1/2 Clear
	16 PRI/1/16 HDLCFCS (In use)
	17 PRI/1/17 Clear
'''


class Parse(unittest.TestCase):
    def test_span(self):
        s = app.parse_span(SPAN)
        self.assertEqual(s['name'], 'Test Card Span 1'); self.assertEqual(s['alarm'], 'RED')
        self.assertEqual((s['bpv'], s['crc']), (12, 3)); self.assertEqual(s['channels'], [1, 2, 17])

    def test_span_ok_and_missing(self):
        self.assertEqual(app.parse_span(SPAN.replace(' RED', ''))['alarm'], 'OK')
        self.assertIsNone(app.parse_span(''))

    def test_alarm_word_in_card_name_ignored(self):
        self.assertEqual(app.parse_span(SPAN.replace('Test Card', 'RED Card').replace(' RED\n', '\n'))['alarm'], 'OK')

    def test_ranges(self):
        self.assertEqual(app.parse_ranges('1-3, 5,7-8'), [1, 2, 3, 5, 7, 8])

    def test_channel_number(self):
        self.assertEqual(app.channel_number('DAHDI/17-1'), 17); self.assertEqual(app.channel_number('DAHDI/i1/17-1'), 17)
        self.assertIsNone(app.channel_number('PJSIP/1001-0000'))

    def test_direction_heuristic(self):
        rows = [{'Channel': 'DAHDI/3-1', 'Uniqueid': '1', 'Linkedid': '1', 'ChannelStateDesc': 'Up', 'CallerIDNum': '555', 'Exten': '1001'},
                {'Channel': 'DAHDI/4-1', 'Uniqueid': '3', 'Linkedid': '2', 'ChannelStateDesc': 'Ringing', 'CallerIDNum': '1001', 'Exten': '555'}]
        c = app.calls_from_channels(rows)
        self.assertEqual((c[3]['dir'], c[3]['state']), ('IN', 'CONNECTED')); self.assertEqual((c[4]['dir'], c[4]['state']), ('OUT', 'RINGING'))

    def test_inbound_contexts_override(self):
        rows = [{'Channel': 'DAHDI/3-1', 'Uniqueid': '1', 'Linkedid': '2', 'Context': 'from-pstn', 'ChannelStateDesc': 'Up'}]
        self.assertEqual(app.calls_from_channels(rows, ('from-pstn',))[3]['dir'], 'IN')

    def test_mask(self):
        self.assertEqual(app.mask_number('4915550100123', True), '491*******123')
        self.assertEqual(app.mask_number('1001', True), '1001'); self.assertEqual(app.mask_number('4915550100123', False), '4915550100123')
        self.assertEqual(app.mask_number('', True), '-')

    def test_span_state(self):
        ok = {'alarm': 'OK'}
        self.assertEqual(app.span_state(ok, 'Up, Active')[0], 'green'); self.assertEqual(app.span_state(ok, 'Down, Active')[0], 'yellow')
        self.assertEqual(app.span_state({'alarm': 'RED'}, 'Up')[0], 'red'); self.assertEqual(app.span_state(ok, '?')[0], 'green')


if __name__ == '__main__':
    unittest.main()
