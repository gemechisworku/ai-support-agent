#!/bin/bash
# Renders each docs/design/*.html to a PDF in docs/ using headless Chrome.
set -e
cd "$(dirname "$0")"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
for f in 0*.html; do
  out="../${f%.html}.pdf"
  "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --allow-file-access-from-files \
    --print-to-pdf="$out" "file://$PWD/$f" 2>/dev/null
  echo "built $out"
done
