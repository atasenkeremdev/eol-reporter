# AKS third-party image reporter

Collects running container images from AKS and sends a deduplicated image list to
Azure OpenAI for classification as `internal`, `third_party`, or `unknown`.
No static image lists or matching rules are used. Requires Python 3.10+ with no
additional Python packages.

## Configuration

Set the following environment variables in your runtime environment:

Alternatively, copy `.env.example` to `.env` in the project root and fill in your
settings. Both the CLI and web UI load this file automatically, regardless of the
working directory. Existing process environment variables take precedence.
The `.env` file is ignored by Git. Keep credentials out of `.env.example`.
Use `--env-file /path/to/settings.env` to select another file. Restart the web UI
after changing settings.

The supported file format is one `NAME=value` assignment per line, with optional
`export`, surrounding single/double quotes, blank lines, and full-line comments.
Shell commands, variable interpolation, multiline values, and inline comments
are not evaluated or expanded.

| Variable | Description |
| --- | --- |
| `AZURE_OPENAI_ENDPOINT` | Resource base HTTPS URL, e.g. `https://YOUR-RESOURCE.openai.azure.com` |
| `AZURE_OPENAI_DEPLOYMENT` | Model deployment supporting chat completions and JSON mode |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key; inject as a secret |
| `AZURE_OPENAI_API_VERSION` | Optional; defaults to `2024-10-21` |

Do not put the key in source code or command-line arguments. The application uses
an existing Azure resource; it does not create resources or deployments.
Key-based authentication is supported. Entra ID and managed identity are not yet
supported.

Live scanning requires `kubectl` and a kubeconfig with AKS access:

```bash
python eol_reporter.py --context YOUR_AKS_CONTEXT
python eol_reporter.py --context YOUR_AKS_CONTEXT --category third_party --format csv
python eol_reporter.py --context YOUR_AKS_CONTEXT --category unknown
```

The scan requires permission to list pods, ReplicaSets, and Jobs across namespaces.
Use `--namespace NAME` to narrow the scope. Cluster access is read-only.
Deployment and CronJob owner references are followed. Only currently running
regular, init sidecar, and ephemeral containers in pods with phase `Running` are
included. Completed init containers and waiting or crashed containers are excluded.

## Company context and classification

The model is instructed to recognize upstream software such as nginx as third
party even when hosted in a private registry. A private registry or unfamiliar
name alone is not evidence of internal ownership. Images that cannot be
classified using the model's product knowledge and supplied context should remain
`unknown`.

Optionally supply a text file describing your company's products and naming
conventions:

```bash
python eol_reporter.py --company-context /path/to/company-context.txt
```

This text and the image references are sent to your Azure OpenAI endpoint.
Namespaces, pod names, and workload metadata are not sent. Model output does not
verify ownership: it can misclassify images and does not inspect registry
contents. Each decision includes `classification_reason` and
`classification_source`. Without company context, some internal applications may
remain `unknown`.

Images are deduplicated and sent in batches of 50 by default (`--batch-size`).
Each scan makes fresh model calls and may incur charges. API errors, missing or
duplicate images, invalid categories, and truncated responses cause the operation
to fail; partial reports are not emitted as successful results. Automatic retries
are not implemented; retry later after a 429 response.

## Local data and tests

```bash
python -m unittest discover -s tests -v
kubectl --context YOUR_AKS_CONTEXT get pods,replicasets.apps,jobs.batch -A -o json > /tmp/inventory.json
python eol_reporter.py --input /tmp/inventory.json
python eol_reporter.py --input /tmp/inventory.json --inventory-only
```

`--input` skips cluster access but still uses Azure OpenAI by default.
`--inventory-only` skips Azure calls and marks every record as `unknown` with
source `none`. JSON and CSV output includes namespace, workload, image, container
type, container count, pod names, and runtime image IDs.

Tests mock Azure HTTP calls; they do not validate live AKS or Azure access.
EOL and vulnerability analysis are outside the current scope.

## Web UI

```bash
cp .env.example .env
# Edit .env with your Azure OpenAI settings, then start the UI.
python web_ui.py
```

Open `http://127.0.0.1:8080` on the machine running the application. Change the
port with `python web_ui.py --port 8081`. The UI provides context and namespace
inputs, optional company context, saved Kubernetes List JSON upload, an inventory
only mode, unique-image counts, search, category filters, classification reasons,
and JSON export of the filtered results. Requests are limited to 2 MB. Saved
inventories are scanned as supplied; context and namespace only affect live scans.

Credentials stay on the server and are not returned to the browser. Temporary
uploaded inventories and company context files are removed after each scan.
Results remain in the page until a new scan or reload; there is no database.
The server handles one scan at a time, with a maximum scan time of ten minutes.
It binds only to loopback and uses request tokens and Host validation. This is a
local development UI without user authentication; do not expose it as a public
service. HTTPS and authentication are required before a shared deployment.

The test suite exercises HTTP page delivery, request protection, saved-inventory
scans through the CLI subprocess, errors, and dotenv loading. Live cluster and
Azure OpenAI validation still require your credentials and network access.
