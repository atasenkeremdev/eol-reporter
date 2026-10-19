"""Local web interface for the AKS image reporter."""

import argparse
import json
import secrets
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from config import DEFAULT_ENV, load_env

ROOT = Path(__file__).resolve().parent


def run_scan(data):
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    for name in ('context', 'namespace', 'company_context', 'inventory_json'):
        if not isinstance(data.get(name, ''), str):
            raise ValueError(f'{name} must be text')
    if not isinstance(data.get('inventory_only', False), bool):
        raise ValueError('inventory_only must be boolean')
    with tempfile.TemporaryDirectory(prefix='image-reporter-') as directory:
        command = [sys.executable, str(ROOT / 'eol_reporter.py')]
        for name in ('context', 'namespace'):
            if data.get(name):
                command.append(f'--{name}={data[name]}')
        for name, flag in (('company_context', '--company-context'), ('inventory_json', '--input')):
            if data.get(name):
                path = Path(directory) / name
                path.write_text(data[name], encoding='utf-8')
                command += [flag, str(path)]
        if data.get('inventory_only'):
            command.append('--inventory-only')
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, cwd=ROOT)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Scan failed')
        return json.loads(result.stdout)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, body, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.valid_host():
            return self.respond(403, b'{"error":"Invalid host"}')
        if self.path == '/':
            html = (ROOT / 'ui.html').read_text().replace('__CSRF_TOKEN__', self.server.csrf_token)
            return self.respond(200, html.encode(), 'text/html')
        self.respond(404, b'{"error":"Not found"}')

    def do_POST(self):
        if (not self.valid_host() or
                self.headers.get('X-CSRF-Token') != self.server.csrf_token):
            return self.respond(403, b'{"error":"Invalid request token"}')
        if self.path != '/api/scan':
            return self.respond(404, b'{"error":"Not found"}')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 2_000_000:
                return self.respond(413, b'{"error":"Request must be between 1 byte and 2 MB"}')
            data = json.loads(self.rfile.read(size))
            rows = run_scan(data)
            self.respond(200, json.dumps({'rows': rows}).encode())
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            self.respond(400, json.dumps({'error': str(error)}).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--env-file', default=str(DEFAULT_ENV))
    args = parser.parse_args()
    load_env(args.env_file)
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.csrf_token = secrets.token_urlsafe(32)
    print(f'Image reporter UI listening on 127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
