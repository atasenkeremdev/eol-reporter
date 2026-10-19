import io
import json
import os
import unittest
import tempfile
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError

from azure_classifier import classify_images, validate_response
from eol_reporter import main


def response(image, category='third_party'):
    return json.dumps({'classifications': [
        {'image': image, 'category': category, 'reason': 'Recognized upstream project'}]})


class AzureTests(unittest.TestCase):
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
