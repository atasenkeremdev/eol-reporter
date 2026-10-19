"""Discover local contexts and Azure AKS clusters without switching global context."""

import json
import os
import shutil
import subprocess
from pathlib import Path

LOCAL_CONFIG = Path(__file__).resolve().parent / '.local' / 'kubeconfig'


def tool_environment():
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    if LOCAL_CONFIG.exists():
        existing = environment.get('KUBECONFIG') or str(Path.home() / '.kube' / 'config')
        paths = existing.split(os.pathsep)
        if str(LOCAL_CONFIG) not in paths:
            paths.append(str(LOCAL_CONFIG))
        environment['KUBECONFIG'] = os.pathsep.join(paths)
    return environment


def run_json(command, timeout=30):
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                timeout=timeout, check=True, env=tool_environment())
    except FileNotFoundError:
        raise ValueError(f'{command[0]} is not installed or is not on PATH') from None
    except subprocess.CalledProcessError as error:
        raise ValueError(error.stderr.strip() or f'{command[0]} command failed') from None
    except subprocess.TimeoutExpired:
        raise ValueError(f'{command[0]} command timed out') from None
    return json.loads(result.stdout) if result.stdout.strip() else {}


def list_contexts():
    # Extract names only. Never return users, certificates or tokens from kubeconfig.
    document = run_json(['kubectl', 'config', 'view', '-o', 'json'])
    current = document.get('current-context', '')
    contexts = sorted(entry['name'] for entry in document.get('contexts', []))
    return {'contexts': contexts, 'current_context': current}


def list_subscriptions():
    subscriptions = run_json(['az', 'account', 'list', '-o', 'json'])
    return [{'id': entry['id'], 'name': entry['name'], 'is_default': entry.get('isDefault', False)}
            for entry in subscriptions if entry.get('state') == 'Enabled']


def list_clusters(subscription):
    clusters = run_json(['az', 'aks', 'list', '--subscription', subscription, '-o', 'json'], timeout=60)
    return [{'name': entry['name'], 'resource_group': entry['resourceGroup']}
            for entry in clusters]


def connect_cluster(data):
    for name in ('subscription', 'resource_group', 'name'):
        if not isinstance(data.get(name), str) or not data[name].strip() or data[name].startswith('-'):
            raise ValueError(f'{name} is required')
    context = f"{data['subscription']}_{data['resource_group']}_{data['name']}"
    LOCAL_CONFIG.parent.mkdir(mode=0o700, exist_ok=True)
    # Pre-create with owner-only permissions before Azure CLI writes credentials.
    descriptor = os.open(LOCAL_CONFIG, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(descriptor)
    LOCAL_CONFIG.chmod(0o600)
    run_json(['az', 'aks', 'get-credentials', '--subscription', data['subscription'],
              '--resource-group', data['resource_group'], '--name', data['name'],
              '--context', context, '--overwrite-existing', '--file', str(LOCAL_CONFIG),
              '--output', 'none'], timeout=90)
    if shutil.which('kubelogin'):
        run_json(['kubelogin', 'convert-kubeconfig', '-l', 'azurecli',
                  '--context', context, '--kubeconfig', str(LOCAL_CONFIG)])
    return {'context': context, **list_contexts()}
