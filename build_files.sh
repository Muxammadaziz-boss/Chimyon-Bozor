#!/bin/bash
echo "Building project assets for Vercel deployment..."
python3.11 -m pip install -r requirements.txt
python3.11 manage.py collectstatic --noinput --clear
python3.11 manage.py migrate --noinput
echo "Build complete!"
