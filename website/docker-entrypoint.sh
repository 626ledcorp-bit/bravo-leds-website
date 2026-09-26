#!/bin/sh
# Docker entrypoint: persist the SQLite DB and uploaded product images on
# Render's persistent disk (mounted at /srv/data). Without this, every
# redeploy would wipe products, orders, and uploads.
set -e

if [ -d /srv/data ]; then
  mkdir -p /srv/data/uploads
  # First run: carry over any images baked into the image.
  if [ -z "$(ls -A /srv/data/uploads 2>/dev/null)" ] \
     && [ -d /srv/static/img/products ] \
     && [ ! -L /srv/static/img/products ]; then
    cp -r /srv/static/img/products/. /srv/data/uploads/ 2>/dev/null || true
  fi
  rm -rf /srv/static/img/products
  ln -sfn /srv/data/uploads /srv/static/img/products
fi

exec "$@"
