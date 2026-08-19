#!/usr/bin/env bash
# Installs an NER model for BioLink.
#
# Usage:
#   ./scripts/download_models.sh            # tries scispacy first, falls back to spaCy
#   ./scripts/download_models.sh --spacy-only
#
# BioLink runs with zero extra setup using its built-in dictionary/regex
# NER fallback (see backend/app/services/ner.py), but a real biomedical
# model gives noticeably better entity recognition on real papers.
set -e

cd "$(dirname "$0")/.."

if [ "$1" != "--spacy-only" ]; then
  echo "Installing scispacy + a small biomedical NER model..."
  pip install scispacy==0.5.4 || echo "scispacy install failed, will fall back to spaCy."
  pip install "https://s3-us-west-2.amazonaws.com/ai2-s2-research-public/scispacy/20230811/en_core_sci_sm-0.5.3.tar.gz" \
    || echo "scispacy model download failed (needs outbound access to amazonaws.com). Falling back to spaCy."
fi

echo "Installing spaCy fallback model (en_core_web_sm)..."
python -m spacy download en_core_web_sm

echo "Done. Set SCISPACY_MODEL / SPACY_FALLBACK_MODEL in .env if you used different model names."
