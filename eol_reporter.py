"""Read-only inventory of container images in running Kubernetes pods."""

import argparse
import csv
import json
import subprocess
import sys

from azure_classifier import classify_images
from config import DEFAULT_ENV, load_env


def canonical_image(image):
    first = image.split('/')[0]
    if '/' not in image or not ('.' in first or ':' in first or first == 'localhost'):
        image = 'docker.io/' + image
    if image.startswith('docker.io/') and image.count('/') == 1:
        image = image.replace('docker.io/', 'docker.io/library/', 1)
    return image


def controller(obj):
    return next((ref for ref in obj.get('metadata', {}).get('ownerReferences', [])
                 if ref.get('controller')), None)


def inventory(document):
    items = document.get('items', [])
    owners = {(item.get('metadata', {}).get('namespace', 'default'),
               item.get('metadata', {}).get('uid')): item for item in items
              if item.get('metadata', {}).get('uid')}
    groups = {}
    for pod in items:
        if pod.get('kind') != 'Pod' or pod.get('status', {}).get('phase') != 'Running':
            continue
        metadata = pod['metadata']
        namespace = metadata.get('namespace', 'default')
        workload = pod
        seen = set()
        while (owner := controller(workload)) and owner.get('uid') not in seen:
            seen.add(owner.get('uid'))
            parent = owners.get((namespace, owner.get('uid')))
            if parent is None:
                workload = {'kind': owner['kind'], 'metadata': {'name': owner['name']}}
                break
            workload = parent
        spec, status = pod.get('spec', {}), pod.get('status', {})
        for spec_key, status_key, container_type in (
            ('containers', 'containerStatuses', 'container'),
            ('initContainers', 'initContainerStatuses', 'init'),
            ('ephemeralContainers', 'ephemeralContainerStatuses', 'ephemeral'),
        ):
            statuses = {entry['name']: entry for entry in status.get(status_key, [])}
            for container in spec.get(spec_key, []):
                runtime = statuses.get(container['name'], {})
                if 'running' not in runtime.get('state', {}):
                    continue
                image = canonical_image(container['image'])
                key = (namespace, workload['kind'], workload['metadata']['name'], image, container_type)
                if key not in groups:
                    groups[key] = dict(namespace=namespace, workload_kind=key[1], workload=key[2],
                                       image=image, category="unknown",
                                       classification_reason="Not classified",
                                       classification_source="none",
                                       container_type=container_type, container_count=0,
                                       pods=set(), image_ids=set())
                row = groups[key]
                row['container_count'] += 1
                row['pods'].add(metadata['name'])
                if runtime.get('imageID'):
                    row['image_ids'].add(runtime['imageID'])
    return [dict(row, pods=sorted(row['pods']), image_ids=sorted(row['image_ids']))
            for _, row in sorted(groups.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default=str(DEFAULT_ENV), help='Environment file (default: project .env)')
    parser.add_argument('--check-azure', action='store_true', help='Test Azure classification with one public image; no cluster access')
    parser.add_argument('--input', help='Read a Kubernetes List JSON file instead of a cluster')
    parser.add_argument('--company-context', help='Company ownership context text file for the model')
    parser.add_argument('--inventory-only', action='store_true', help='Skip Azure OpenAI; all images remain unknown')
    parser.add_argument('--batch-size', type=int, default=50)
    parser.add_argument('--context', help='Explicit kubeconfig context (otherwise current context)')
    parser.add_argument('--namespace', help='Limit cluster query to one namespace')
    parser.add_argument('--format', choices=['json', 'csv'], default='json')
    parser.add_argument('--category', choices=['all', 'third_party', 'internal', 'unknown'], default='all')
    args = parser.parse_args(argv)
    try:
        load_env(args.env_file)
        if args.check_azure:
            decisions = classify_images(['docker.io/library/nginx:latest'])
            json.dump({'status': 'ok', 'message': 'Azure OpenAI classification request succeeded',
                       'classification': decisions['docker.io/library/nginx:latest']}, sys.stdout, indent=2)
            print()
            return 0
        if args.input:
            with open(args.input, encoding='utf-8-sig') as stream:
                document = json.load(stream)
        else:
            command = ['kubectl']
            if args.context:
                command += ['--context', args.context]
            command += ['get', 'pods,replicasets.apps,jobs.batch', '-o', 'json']
            command += ['--namespace', args.namespace] if args.namespace else ['--all-namespaces']
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=90, check=True)
            document = json.loads(result.stdout)
        if not isinstance(document, dict) or not isinstance(document.get('items'), list):
            raise ValueError('Input must be a Kubernetes List with an items array')
        rows = inventory(document)
        if not args.inventory_only:
            company_context = ''
            if args.company_context:
                with open(args.company_context, encoding='utf-8-sig') as stream:
                    company_context = stream.read()
            decisions = classify_images([row['image'] for row in rows], company_context, args.batch_size)
            for row in rows:
                row.update(decisions[row['image']])
        if args.category != 'all':
            rows = [row for row in rows if row['category'] == args.category]
        if args.format == 'json':
            json.dump(rows, sys.stdout, indent=2)
            print()
        else:
            fields = ['namespace', 'workload_kind', 'workload', 'image', 'category',
                      'classification_reason', 'classification_source',
                      'container_type', 'container_count', 'pods', 'image_ids']
            writer = csv.DictWriter(sys.stdout, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value) if isinstance(value, list) else value
                                 for key, value in row.items()})
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f'Inventory failed: {detail}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
