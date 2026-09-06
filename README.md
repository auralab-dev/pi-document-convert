# pi-document-convert

Project-local document conversion helper used automatically by the bundled
`pi-web-access` fork. It registers **no model-facing Pi tool**.

## PDF policy

- Usable native PDF text -> PyMuPDF4LLM, OCR disabled when the installed API supports that switch.
- Scanned / unusable text layer -> configured OCR backend.
- `paddle`: PP-StructureV3 (layout/table-aware), with Polish-friendly Latin recognition configurable.
- `glm-ocr`: external command adapter. Set `pdf.glmOcrCommand` (or `PI_GLM_OCR_COMMAND`) with `{input}` and `{output}` placeholders.

The converter keeps page markers, avoids the old 100-page default cutoff, and
soft-wraps pathological one-line prose while leaving Markdown/HTML table lines
untouched.

Configuration is read from `.pi/document-convert.json`.

## Install (split repo)

Previously installed by the shared `pi-harness/install.sh`. Standalone equivalent:

```bash
# Node deps (no runtime deps, Node >=22.19)
pnpm install

# Python native-text backend (Python 3.11+)
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c 'import pymupdf, pymupdf4llm; print("PyMuPDF OK")'
```

Optional PaddleOCR backend:

```bash
.venv/bin/python -m pip install -r requirements-paddle.txt
.venv/bin/python -c 'import paddleocr; print("PaddleOCR OK")'
```

Point the harness at this checkout with `PI_DOCUMENT_CONVERT_CMD=<repo>/cli.mjs` and `PI_DOCUMENT_CONVERT_PYTHON=<repo>/.venv/bin/python`.
