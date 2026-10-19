import io
import json
import os
import socket
import ssl
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from azure_classifier import classify_images, validate_response, connection_error
from eol_reporter import main


def response(image, category='third_party'):
    return json.dumps({'classifications': [
        {'image': image, 'category': category, 'reason': 'Recognized upstream project'}]})


class AzureTests(unittest.TestCase):
    def test_network_diagnostics_are_specific_and_redact_exception_text(self):
        cases = [(socket.gaierror(-2, 'secret-host'), 'DNS lookup failed'),
                 (ssl.SSLCertVerificationError(1, 'secret-certificate'), 'TLS certificate verification failed'),
                 (TimeoutError('secret-timeout'), 'timed out after 90 seconds'),
                 (ConnectionRefusedError('secret-proxy'), 'connection was refused'),
                 (ConnectionResetError('secret-reset'), 'connection was interrupted'),
                 ('proxy https://user:secret-password@proxy', 'connection failed')]
        for reason, expected in cases:
            with self.subTest(expected=expected):
                message = connection_error(URLError(reason), 90)
                self.assertIn(expected, message)
                self.assertNotIn('secret-', message)

    @patch('eol_reporter.classify_images')
    @patch('eol_reporter.subprocess.run')
    def test_azure_check_does_not_access_cluster(self, run, classify):
        classify.return_value = {'docker.io/library/nginx:latest': {'category': 'third_party'}}
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['--check-azure']), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'ok')

    @patch.dict(os.environ, {'AZURE_OPENAI_ENDPOINT': 'https://test.openai.azure.com',
                            'AZURE_OPENAI_DEPLOYMENT': 'deployment',
                            'AZURE_OPENAI_API_KEY': 'secret-key',
                            'AZURE_OPENAI_TIMEOUT_SECONDS': '120'}, clear=True)
    @patch('azure_classifier.urlopen')
    def test_timeout_configuration_and_transport_error(self, send):
        send.side_effect = URLError(TimeoutError('secret-proxy'))
        with self.assertRaisesRegex(ValueError, 'timed out after 120 seconds'):
            classify_images(['nginx'])
        self.assertEqual(send.call_args.kwargs['timeout'], 120)
        for value in ('nan', 'inf', '0', '-1', '301', 'not-a-number'):
            with patch.dict(os.environ, {'AZURE_OPENAI_TIMEOUT_SECONDS': value}):
                with self.assertRaisesRegex(ValueError, 'AZURE_OPENAI_TIMEOUT_SECONDS'):
                    classify_images(['nginx'])

    @patch.dict(os.environ, {'AZURE_OPENAI_ENDPOINT': 'https://test.openai.azure.com',
                            'AZURE_OPENAI_DEPLOYMENT': 'deployment',
                            'AZURE_OPENAI_API_KEY': 'secret-key',
                            'AZURE_OPENAI_CA_BUNDLE': 'trusted.pem'}, clear=True)
    @patch('azure_classifier.ssl.create_default_context')
    @patch('azure_classifier.urlopen')
    def test_trusted_ca_bundle_is_passed_to_tls(self, send, create_context):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        create_context.return_value = context
        send.side_effect = URLError(socket.gaierror(-2, 'secret-proxy'))
        with self.assertRaisesRegex(ValueError, 'DNS lookup failed'):
            classify_images(['nginx'])
        self.assertEqual(create_context.call_args.kwargs, {'cafile': 'trusted.pem'})
        self.assertIs(send.call_args.kwargs['context'], context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    @patch('eol_reporter.classify_images')
    def test_cli_applies_model_decision_before_category_filter(self, classify):
        image = 'corp.azurecr.io/nginx:1.21.1'
        classify.return_value = {image: {'category': 'third_party',
            'classification_reason': 'Upstream nginx mirrored in private registry',
            'classification_source': 'azure_openai'}}
        document = {'items': [{'kind': 'Pod', 'metadata': {'name': 'web'},
            'spec': {'containers': [{'name': 'web', 'image': image}]},
            'status': {'phase': 'Running', 'containerStatuses': [
                {'name': 'web', 'state': {'running': {}}}]}}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_text(json.dumps(document))
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(['--input', str(path), '--category', 'third_party'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())[0]['classification_source'], 'azure_openai')
        classify.assert_called_once_with([image], '', 50)

    def test_missing_duplicate_extra_and_invalid_decisions_rejected(self):
        for content in ('{}', response('other'), response('a', 'maybe'),
                        '{"classifications": []}', 'not JSON'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_response(content, ['a'])
        item = json.loads(response('a'))['classifications'][0]
        with self.assertRaises(ValueError):
            validate_response(json.dumps({'classifications': [item, item]}), ['a'])

    @patch.dict(os.environ, {'AZURE_OPENAI_ENDPOINT': 'https://test.openai.azure.com',
                            'AZURE_OPENAI_DEPLOYMENT': 'deployment',
                            'AZURE_OPENAI_API_KEY': 'test-key'}, clear=True)
    @patch('azure_classifier.urlopen')
    def test_batching_deduplication_and_payload(self, send):
        def reply(request, timeout):
            payload = json.loads(request.data)
            user = json.loads(payload['messages'][1]['content'])
            self.assertEqual(user['company_context'], 'Company background')
            self.assertNotIn('pods', user)
            image = user['images'][0]
            self.assertEqual(request.get_header('Api-key'), 'test-key')
            return io.BytesIO(json.dumps({'choices': [{'finish_reason': 'stop',
                'message': {'content': response(image)}}]}).encode())
        send.side_effect = reply
        decisions = classify_images(['b', 'a', 'a'], 'Company background', batch_size=1)
        self.assertEqual(set(decisions), {'a', 'b'})
        self.assertEqual(send.call_count, 2)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_configuration_and_empty_inventory(self):
        self.assertEqual(classify_images([]), {})
        with self.assertRaisesRegex(ValueError, 'Missing Azure'):
            classify_images(['nginx'])

    @patch.dict(os.environ, {'AZURE_OPENAI_ENDPOINT': 'https://test.openai.azure.com',
                            'AZURE_OPENAI_DEPLOYMENT': 'deployment',
                            'AZURE_OPENAI_API_KEY': 'secret-key'}, clear=True)
    @patch('azure_classifier.urlopen')
    def test_api_error_does_not_become_success_or_expose_key(self, send):
        send.side_effect = HTTPError('https://test', 401, 'secret-key', {}, None)
        with self.assertRaisesRegex(ValueError, 'HTTP 401') as caught:
            classify_images(['nginx'])
        self.assertNotIn('secret-key', str(caught.exception))
