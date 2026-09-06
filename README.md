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
