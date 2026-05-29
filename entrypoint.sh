#!/bin/bash
set -e

# gRPC timeout для T-Bank API (предотвращает зависания при UNAVAILABLE)
export GRPC_PYTHON_DEFAULT_TIMEOUT_SECONDS=5

if ! python -c "import tinkoff.invest" 2>/dev/null; then
  if python -c "import t_tech.invest" 2>/dev/null; then
    # t-tech-investments установлен, но импортируется как t_tech.invest
    # Создаём symlink tinkoff → t_tech для обратной совместимости
    SITE=$(python -c "import site; print(site.getsitepackages()[0])")
    if [ -d "$SITE/t_tech" ] && [ ! -d "$SITE/tinkoff" ]; then
      ln -s "$SITE/t_tech" "$SITE/tinkoff"
      echo "T-Bank SDK linked: tinkoff → t_tech"
    fi
  else
    echo "Installing T-Bank SDK..."
    pip install -q tinkoff-investments 2>/dev/null || \
      pip install -q --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple --extra-index-url https://pypi.org/simple t-tech-investments || true
    # Повторная попытка symlink
    if python -c "import t_tech.invest" 2>/dev/null; then
      SITE=$(python -c "import site; print(site.getsitepackages()[0])")
      if [ -d "$SITE/t_tech" ] && [ ! -d "$SITE/tinkoff" ]; then
        ln -s "$SITE/t_tech" "$SITE/tinkoff"
        echo "T-Bank SDK linked: tinkoff → t_tech"
      fi
    fi
  fi
fi

# Run DB migrations
python /app/scripts/migrate_add_last_notify.py || true

exec "$@"
