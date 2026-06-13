#!/bin/bash
set -e

echo "Running migrations..."
alembic upgrade head

echo "Starting application..."
exec "$@"
