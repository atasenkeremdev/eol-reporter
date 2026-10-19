import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eol_reporter import canonical_image, inventory


def fixture():
    return {'items': [
        {'kind': 'ReplicaSet', 'metadata': {'uid': 'rs', 'name': 'web-123',
         'namespace': 'prod', 'ownerReferences': [
             {'controller': True, 'kind': 'Deployment', 'name': 'web', 'uid': 'deploy'}]}},
        {'kind': 'Pod', 'metadata': {'name': 'web-123-abc', 'namespace': 'prod',
         'ownerReferences': [{'controller': True, 'kind': 'ReplicaSet', 'name': 'web-123', 'uid': 'rs'}]},
         'spec': {'containers': [{'name': 'web', 'image': 'nginx:1.27'},
                                 {'name': 'waiting', 'image': 'busybox'}],
                  'initContainers': [{'name': 'init', 'image': 'busybox'}]},
         'status': {'phase': 'Running', 'containerStatuses': [
             {'name': 'web', 'state': {'running': {}}, 'imageID': 'docker-pullable://nginx@sha256:abc'},
             {'name': 'waiting', 'state': {'waiting': {'reason': 'ImagePullBackOff'}}}],
             'initContainerStatuses': [{'name': 'init', 'state': {'terminated': {'exitCode': 0}}}]}}
    ]}


class InventoryTests(unittest.TestCase):
    def test_running_only_and_workload_resolution(self):
        rows = inventory(fixture())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['workload'], 'web')
        self.assertEqual(rows[0]['workload_kind'], 'Deployment')
        self.assertEqual(rows[0]['category'], 'unknown')
        self.assertEqual(rows[0]['image'], 'docker.io/library/nginx:1.27')
        self.assertEqual(len(rows[0]['image_ids']), 1)

    def test_normalization(self):
        self.assertEqual(canonical_image('team/app:v1'), 'docker.io/team/app:v1')
        self.assertEqual(canonical_image('localhost:5000/app@sha256:abc'),
                         'localhost:5000/app@sha256:abc')

    def test_aggregation_and_terminal_pods(self):
        doc = fixture()
        second = copy.deepcopy(doc['items'][1])
        second['metadata']['name'] = 'web-123-def'
        doc['items'].append(second)
        terminal = copy.deepcopy(second)
        terminal['status']['phase'] = 'Succeeded'
        doc['items'].append(terminal)
        rows = inventory(doc)
        self.assertEqual(rows[0]['container_count'], 2)
        self.assertEqual(len(rows[0]['pods']), 2)

    def test_cli_json_and_csv(self):
        script = Path(__file__).resolve().parents[1] / 'eol_reporter.py'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'pods.json'
            path.write_text(json.dumps(fixture()))
            for output_format in ('json', 'csv'):
                result = subprocess.run([sys.executable, str(script), '--input', str(path),
                                         '--format', output_format, '--inventory-only'], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if output_format == 'json':
                    self.assertEqual(json.loads(result.stdout)[0]['workload'], 'web')
                else:
                    self.assertIn('namespace,workload_kind,workload', result.stdout)
                    self.assertIn('docker.io/library/nginx:1.27', result.stdout)

    def test_invalid_input_fails(self):
        script = Path(__file__).resolve().parents[1] / 'eol_reporter.py'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invalid.json'
            path.write_text('{}')
            result = subprocess.run([sys.executable, str(script), '--input', str(path)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('items array', result.stderr)


if __name__ == '__main__':
    unittest.main()
