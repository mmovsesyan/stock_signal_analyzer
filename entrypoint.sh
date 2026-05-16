#!/bin/bash
set -e

if ! python -c "import tinkoff.invest" 2>/dev/null; then
  echo "Installing T-Bank SDK..."
  pip install -q tinkoff-investments 2>/dev/null || \
    pip install -q --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --extra-index-url https://pypi.org/simple t-tech-investments || true
fi

exec "$@"
