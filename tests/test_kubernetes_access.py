import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kubernetes_access as access


class KubernetesAccessTests(unittest.TestCase):
    @patch('kubernetes_access.run_json')
    def test_contexts_include_current_but_no_credentials(self, run):
        run.return_value = {'current-context': 'prod', 'contexts': [
            {'name': 'prod'}, {'name': 'dev'}], 'users': [{'token': 'not-for-browser'}]}
        result = access.list_contexts()
        self.assertEqual(result, {'contexts': ['dev', 'prod'], 'current_context': 'prod'})
        self.assertNotIn('not-for-browser', json.dumps(result))

    @patch('kubernetes_access.subprocess.run')
    def test_commands_decode_utf8_and_missing_tools_are_actionable(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, '{"contexts": []}', '')
        access.list_contexts()
        self.assertEqual(run.call_args.kwargs['encoding'], 'utf-8')
        run.side_effect = FileNotFoundError()
        with self.assertRaisesRegex(ValueError, 'kubectl is not installed'):
            access.list_contexts()

    @patch('kubernetes_access.shutil.which', return_value='/bin/kubelogin')
    @patch('kubernetes_access.run_json')
    def test_connect_writes_only_isolated_config_and_returns_context(self, run, which):
        run.side_effect = [{}, {}, {'contexts': [], 'current-context': ''}]
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / '.local' / 'kubeconfig'
            with patch.object(access, 'LOCAL_CONFIG', local):
                result = access.connect_cluster({'subscription': 'sub', 'resource_group': 'rg', 'name': 'aks'})
                self.assertEqual(result['context'], 'sub_rg_aks')
                self.assertIn(str(local), run.call_args_list[0].args[0])
                self.assertIn('--kubeconfig', run.call_args_list[1].args[0])
                self.assertNotIn('use-context', str(run.call_args_list))
                if os.name != 'nt':
                    self.assertEqual(local.stat().st_mode & 0o777, 0o600)

    @patch('kubernetes_access.run_json')
    def test_subscriptions_and_clusters_are_projected(self, run):
        run.return_value = [{'id': 'sub', 'name': 'Production', 'isDefault': True, 'state': 'Enabled'},
                            {'id': 'off', 'name': 'Disabled', 'state': 'Disabled'}]
        self.assertEqual(access.list_subscriptions(), [{'id': 'sub', 'name': 'Production', 'is_default': True}])
        run.return_value = [{'name': 'aks', 'resourceGroup': 'rg', 'other': 'not-required'}]
        self.assertEqual(access.list_clusters('sub'), [{'name': 'aks', 'resource_group': 'rg'}])
