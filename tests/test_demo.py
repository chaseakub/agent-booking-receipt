import unittest
from importlib.util import find_spec

from booking_receipt.demo import run_demo


class LostResponseDemoTest(unittest.TestCase):
    @unittest.skipUnless(find_spec("radicale"), "install the demo extra for the real CalDAV test")
    def test_lost_confirmation_and_fresh_process_replay(self):
        evidence = run_demo()
        self.assertEqual(evidence["first_attempt"], "unknown")
        self.assertEqual(evidence["recovery"], "confirmed")
        self.assertEqual(evidence["replay"], "confirmed")
        self.assertEqual(evidence["provider_events"], 1)
        self.assertEqual(evidence["provider_puts"], 1)
        self.assertEqual(evidence["dropped_put_replies"], 1)
        self.assertEqual(evidence["unavailable_reads"], 1)
        self.assertIn(evidence["committed_put_http_status"], (200, 201, 204))
