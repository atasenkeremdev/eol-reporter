import json
import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

from config import load_env
from web_ui import Handler


class EnvironmentTests(unittest.TestCase):
    @patch.dict(os.environ, {'EXISTING': 'runtime'}, clear=True)
    def test_dotenv_quotes_comments_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('# Comment\nEXISTING=file\nexport NEW="file value"\nLITERAL=$(echo nope)\n')
            load_env(path)
        self.assertEqual(os.environ['EXISTING'], 'runtime')
        self.assertEqual(os.environ['NEW'], 'file value')
        self.assertEqual(os.environ['LITERAL'], '$(echo nope)')

    @patch.dict(os.environ, {}, clear=True)
    def test_invalid_file_does_not_partially_change_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('VALID=ok\nINVALID\n')
            with self.assertRaises(ValueError):
                load_env(path)
        self.assertNotIn('VALID', os.environ)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), Handler)
        cls.server.csrf_token = 'test-token'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method, path, data=None, headers=None):
        connection = HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        connection.request(method, path, body=data, headers=headers or {})
        response = connection.getresponse()
        result = response.status, response.read().decode()
        connection.close()
        return result

    def test_html_and_secret_files_not_served(self):
        status, body = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('Image Observatory', body)
        self.assertIn("const token = 'test-token'", body)
        self.assertNotIn('__CSRF_TOKEN__', body)
        self.assertEqual(self.request('GET', '/.env')[0], 404)

    def test_scan_requires_token_and_valid_host(self):
        self.assertEqual(self.request('POST', '/api/scan', '{}')[0], 403)
        self.assertEqual(self.request('GET', '/', headers={'Host': 'attacker.example'})[0], 403)

    def test_saved_inventory_http_scan_and_error(self):
        document = {'items': [{'kind': 'Pod', 'metadata': {'name': 'web', 'namespace': 'prod'},
            'spec': {'containers': [{'name': 'web', 'image': 'nginx:1.21.1'}]},
            'status': {'phase': 'Running', 'containerStatuses': [
                {'name': 'web', 'state': {'running': {}}}]}}]}
        headers = {'X-CSRF-Token': 'test-token', 'Content-Type': 'application/json'}
        status, body = self.request('POST', '/api/scan', json.dumps({
            'inventory_json': json.dumps(document), 'inventory_only': True}), headers)
        self.assertEqual(status, 200, body)
        row = json.loads(body)['rows'][0]
        self.assertEqual(row['image'], 'docker.io/library/nginx:1.21.1')
        self.assertEqual(row['namespace'], 'prod')
        self.assertEqual(row['classification_source'], 'none')
        status, body = self.request('POST', '/api/scan', json.dumps({
            'inventory_json': '{}', 'inventory_only': True}), headers)
        self.assertEqual(status, 400)
        self.assertIn('items array', json.loads(body)['error'])

    def test_invalid_request_types_rejected(self):
        status, _ = self.request('POST', '/api/scan', '[]', {'X-CSRF-Token': 'test-token'})
        self.assertEqual(status, 400)
