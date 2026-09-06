#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Invalid converter config: {path}")
    return value


def pdf_cfg(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("pdf", {})
    return value if isinstance(value, dict) else {}


def import_pymupdf():
    try:
        import pymupdf as fitz  # type: ignore
    except ImportError:
        try:
            import fitz  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF is not installed. Run the bundle install script."
            ) from exc
    return fitz


def probe_native_text(pdf_path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    fitz = import_pymupdf()
    doc = fitz.open(str(pdf_path))
    total = len(doc)
    sample_limit = max(1, int(cfg.get("probePages", 12)))
    min_chars = max(1, int(cfg.get("minTextCharsPerPage", 80)))
    min_ratio = float(cfg.get("minTextPageRatio", 0.60))

    if total <= sample_limit:
        indices = list(range(total))
    else:
        indices = sorted({round(i * (total - 1) / (sample_limit - 1)) for i in range(sample_limit)})

    usable = 0
    chars = 0
    samples: list[dict[str, Any]] = []
    for idx in indices:
        text = doc[idx].get_text("text") or ""
        clean = re.sub(r"\s+", " ", text).strip()
        alnum = sum(ch.isalnum() for ch in clean)
        density = (alnum / len(clean)) if clean else 0.0
        good = len(clean) >= min_chars and density >= 0.35
        usable += 1 if good else 0
        chars += len(clean)
        samples.append({"page": idx + 1, "chars": len(clean), "alnumRatio": round(density, 3), "usable": good})
    doc.close()
    ratio = usable / max(1, len(indices))
    return {
        "pages": total,
        "sampledPages": len(indices),
        "usableTextPages": usable,
        "usableTextPageRatio": round(ratio, 3),
        "sampleChars": chars,
        "nativeText": ratio >= min_ratio,
        "samples": samples,
    }


def safe_call_to_markdown(pdf_path: Path, max_pages: int | None) -> tuple[str, int]:
    try:
        # Some PyMuPDF4LLM versions print parser/OCR diagnostics to stdout.
        # stdout is reserved for the machine-readable JSON protocol, so route
        # third-party chatter to stderr.
        with redirect_stdout(sys.stderr):
            import pymupdf4llm  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "pymupdf4llm is not installed. Run the bundle install script."
        ) from exc

    fitz = import_pymupdf()
    doc = fitz.open(str(pdf_path))
    pages_total = len(doc)
    doc.close()
    pages = list(range(min(pages_total, max_pages))) if max_pages else None

    signature = inspect.signature(pymupdf4llm.to_markdown)
    supported = signature.parameters
    kwargs: dict[str, Any] = {}
    if "pages" in supported and pages is not None:
        kwargs["pages"] = pages
    if "page_chunks" in supported:
        kwargs["page_chunks"] = True
    if "show_progress" in supported:
        kwargs["show_progress"] = False
    if "use_ocr" in supported:
        kwargs["use_ocr"] = False
    if "table_output" in supported:
        kwargs["table_output"] = "html"
    elif "table_strategy" in supported:
        kwargs["table_strategy"] = "lines_strict"

    with redirect_stdout(sys.stderr):
        result = pymupdf4llm.to_markdown(str(pdf_path), **kwargs)
    if isinstance(result, str):
        return add_page_marker_if_needed(result, 1), pages_total

    chunks: list[str] = []
    if isinstance(result, list):
        for pos, item in enumerate(result, start=1):
            if isinstance(item, str):
                text = item
                page = pos
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("markdown") or "")
                meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                page = int(meta.get("page") or meta.get("page_number") or item.get("page") or pos)
                if page == 0:
                    page = pos
            else:
                continue
            chunks.append(f"<!-- Page {page} -->\n\n{soft_wrap(text.strip())}")
    if not chunks:
        raise RuntimeError("PyMuPDF4LLM returned no Markdown content")
    return "\n\n".join(chunks), pages_total


def add_page_marker_if_needed(text: str, page: int) -> str:
    text = soft_wrap(text.strip())
    return text if "<!-- Page " in text else f"<!-- Page {page} -->\n\n{text}"


def soft_wrap(text: str, width: int = 900) -> str:
    """Split pathological one-line prose while leaving Markdown/HTML tables intact."""
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if len(line) <= width or stripped.startswith("|") or stripped.startswith("<") or stripped.endswith(">"):
            out.append(line)
            continue
        remaining = line
        while len(remaining) > width:
            window = remaining[: width + 120]
            cuts = [window.rfind(token) for token in (". ", "; ", ": ", " ")]
            cut = max(cuts)
            if cut < width // 2:
                cut = width
            else:
                cut += 1
            out.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            out.append(remaining)
    return "\n".join(out)


def recursively_find_markdown(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        return found
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"markdown", "markdown_result", "md"} and isinstance(child, str) and child.strip():
                found.append(child)
            else:
                found.extend(recursively_find_markdown(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(recursively_find_markdown(child))
    return found


def paddle_to_markdown(pdf_path: Path, cfg: dict[str, Any], work_dir: Path) -> tuple[str, int]:
    try:
        from paddleocr import PPStructureV3  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is not installed. Run ./install.sh --paddle or select another OCR backend."
        ) from exc

    kwargs: dict[str, Any] = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "use_table_recognition": True,
        "use_formula_recognition": False,
        "use_chart_recognition": False,
    }
    paddle_cfg = cfg.get("paddle", {}) if isinstance(cfg.get("paddle"), dict) else {}
    model = paddle_cfg.get("textRecognitionModel")
    if isinstance(model, str) and model.strip():
        kwargs["text_recognition_model_name"] = model.strip()
    # Keep footnotes because they can change legal/procedural meaning.
    kwargs["markdown_ignore_labels"] = ["number", "header_image", "footer_image", "aside_text"]

    try:
        pipeline = PPStructureV3(**kwargs)
    except TypeError:
        kwargs.pop("markdown_ignore_labels", None)
        pipeline = PPStructureV3(**kwargs)

    results = list(pipeline.predict(input=str(pdf_path)))
    markdown_parts: list[str] = []
    save_dir = work_dir / "paddle-markdown"
    save_dir.mkdir(parents=True, exist_ok=True)

    for idx, result in enumerate(results, start=1):
        obj = None
        if hasattr(result, "json"):
            try:
                obj = result.json
                if callable(obj):
                    obj = obj()
            except Exception:
                obj = None
        if obj is not None:
            md = recursively_find_markdown(obj)
            if md:
                markdown_parts.append(f"<!-- Page {idx} -->\n\n{soft_wrap('\n\n'.join(md))}")
                continue
        if hasattr(result, "save_to_markdown"):
            before = set(save_dir.rglob("*.md"))
            result.save_to_markdown(save_path=str(save_dir))
            after = set(save_dir.rglob("*.md"))
            created = sorted(after - before)
            if created:
                body = "\n\n".join(p.read_text(encoding="utf-8") for p in created)
                markdown_parts.append(f"<!-- Page {idx} -->\n\n{soft_wrap(body)}")
                continue
        raise RuntimeError("PP-StructureV3 result did not expose Markdown output")

    if not markdown_parts:
        raise RuntimeError("PP-StructureV3 returned no Markdown content")
    fitz = import_pymupdf()
    doc = fitz.open(str(pdf_path)); pages = len(doc); doc.close()
    return "\n\n".join(markdown_parts), pages


def glm_command_to_markdown(pdf_path: Path, cfg: dict[str, Any], output_path: Path) -> tuple[str, int]:
    command = cfg.get("glmOcrCommand") or os.environ.get("PI_GLM_OCR_COMMAND")
    if not isinstance(command, str) or not command.strip():
        raise RuntimeError(
            "GLM-OCR backend requires pdf.glmOcrCommand or PI_GLM_OCR_COMMAND. "
            "Use {input} and {output} placeholders; the command must write Markdown to {output}."
        )
    rendered = command.replace("{input}", shlex.quote(str(pdf_path))).replace("{output}", shlex.quote(str(output_path)))
    completed = subprocess.run(rendered, shell=True, text=True, capture_output=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "GLM-OCR command failed").strip()
        raise RuntimeError(detail[-4000:])
    if not output_path.exists():
        raise RuntimeError("GLM-OCR command completed but did not create the requested Markdown output")
    body = output_path.read_text(encoding="utf-8")
    fitz = import_pymupdf()
    doc = fitz.open(str(pdf_path)); pages = len(doc); doc.close()
    return soft_wrap(body), pages


def title_for(pdf_path: Path) -> str:
    fitz = import_pymupdf()
    doc = fitz.open(str(pdf_path))
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip() if isinstance(meta, dict) else ""
    doc.close()
    return title or pdf_path.stem


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--source-url", default="")
    parser.add_argument("--max-pages", type=int, default=None)
    args = parser.parse_args()

    source = Path(args.input).resolve()
    output = Path(args.output).resolve()
    metadata = Path(args.metadata).resolve()
    config = load_config(Path(args.config))
    cfg = pdf_cfg(config)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True, exist_ok=True)

    probe = probe_native_text(source, cfg)
    native_backend = str(cfg.get("nativeBackend", "pymupdf4llm"))
    ocr_backend = str(cfg.get("ocrBackend", "none"))

    if probe["nativeText"]:
        if native_backend != "pymupdf4llm":
            raise RuntimeError(f"Unsupported native PDF backend: {native_backend}")
        body, pages = safe_call_to_markdown(source, args.max_pages)
        converter = "pymupdf4llm"
        used_ocr = False
    else:
        if ocr_backend == "paddle":
            body, pages = paddle_to_markdown(source, cfg, output.parent)
            converter = "paddle-pp-structure-v3"
        elif ocr_backend in {"glm-ocr", "glm"}:
            body, pages = glm_command_to_markdown(source, cfg, output)
            converter = "glm-ocr"
        elif ocr_backend == "none":
            raise RuntimeError(
                "PDF has no sufficiently usable native text layer and OCR is disabled. "
                "Set pdf.ocrBackend to 'paddle' or 'glm-ocr'."
            )
        else:
            raise RuntimeError(f"Unsupported OCR backend: {ocr_backend}")
        used_ocr = True

    title = title_for(source)
    header = [f"# {title}", ""]
    if args.source_url:
        header += [f"> Source: {args.source_url}"]
    header += [f"> Pages: {pages}", f"> Converter: {converter}", "", "---", ""]
    content = "\n".join(header) + body.strip() + "\n"
    output.write_text(content, encoding="utf-8")

    meta = {
        "converter": converter,
        "nativeText": bool(probe["nativeText"]),
        "ocr": used_ocr,
        "pages": pages,
        "chars": len(content),
        "sourceUrl": args.source_url or None,
        "probe": probe,
    }
    metadata.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "title": title,
        "pages": pages,
        "chars": len(content),
        "converter": converter,
        "nativeText": bool(probe["nativeText"]),
        "ocr": used_ocr,
        "outputPath": str(output),
        "metadataPath": str(metadata),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
