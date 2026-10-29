from http.client import HTTPConnection
from threading import Event, Thread
import unittest
from ottv2.local_api import LocalHandler, LocalHTTPServer


class ConcurrencyTests(unittest.TestCase):
    def test_slow_client_does_not_block_health_and_saturation_returns_503(self):
        entered, release = Event(), Event()
        class Handler(LocalHandler):
            def do_GET(self):
                if self.path == '/slow':
                    entered.set(); release.wait(5)
                self.reply(200, {'ok':True})
        server = LocalHTTPServer(('127.0.0.1',0),Handler,max_handlers=2)
        thread = Thread(target=server.serve_forever,kwargs={'poll_interval':.01}); thread.start()
        def request(path):
            client = HTTPConnection('127.0.0.1',server.server_port,timeout=2)
            try:
                client.request('GET',path)
                response = client.getresponse(); response.read()
                return response.status
            finally: client.close()
        slow = Thread(target=lambda:request('/slow')); slow.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(request('/health'),200)
            release.set(); slow.join(3)
            # Deterministically fill admission after both real handlers finish.
            for _ in range(8): self.assertTrue(server._admission.acquire(timeout=2))
            try: self.assertEqual(request('/health'),503)
            finally:
                for _ in range(8): server._admission.release()
        finally:
            release.set(); slow.join(3)
            server.shutdown(); thread.join(3); server.server_close()

