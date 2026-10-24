import io
import unittest
from types import SimpleNamespace
from bot.launch import wait_url


class TunnelTests(unittest.TestCase):
    def test_reads_only_cloudflare_https_address(self):
        proc=SimpleNamespace(stdout=io.StringIO('info https://example.com\nTunnel: https://sample-test.trycloudflare.com\n'))
        self.assertEqual(wait_url(proc,1),'https://sample-test.trycloudflare.com')

    def test_stopped_tunnel_reports_clear_error(self):
        with self.assertRaisesRegex(RuntimeError,'туннель остановился'):
            wait_url(SimpleNamespace(stdout=io.StringIO('no tunnel URL\n')),1)


if __name__=='__main__':
    unittest.main()
