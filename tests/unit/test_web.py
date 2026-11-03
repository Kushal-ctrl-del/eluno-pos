import json
import threading
import unittest
from urllib.request import urlopen

from app.entrypoints.web import PosHandler
from http.server import ThreadingHTTPServer


class WebShellTests(unittest.TestCase):
    def test_static_frontend_is_served(self):
        class FakeApplication:
            pass

        PosHandler.application = FakeApplication()
        server = ThreadingHTTPServer(("127.0.0.1", 0), PosHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/") as response:
                body = response.read().decode("utf-8")
            self.assertIn("Eluno POS", body)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_not_found_api_is_json(self):
        class FakeApplication:
            pass

        PosHandler.application = FakeApplication()
        server = ThreadingHTTPServer(("127.0.0.1", 0), PosHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with self.assertRaises(Exception) as context:
                urlopen(f"http://127.0.0.1:{server.server_port}/api/missing")
            error = context.exception
            body = json.loads(error.read().decode("utf-8"))
            self.assertEqual(body["error"]["code"], "not_found")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
