"""Load a small, explicit dotenv format without executing shell expressions."""

import os
import re
from pathlib import Path


DEFAULT_ENV = Path(__file__).resolve().parent / '.env'


def load_env(path=DEFAULT_ENV):
    path = Path(path)
    if not path.exists():
        return
    values = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        name, separator, value = line.partition('=')
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'Invalid environment assignment on line {number}')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'Unclosed environment value on line {number}')
            value = value[1:-1]
        values[name] = value
    for name, value in values.items():
        os.environ.setdefault(name, value)
