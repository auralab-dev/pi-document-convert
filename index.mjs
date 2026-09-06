import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const PACKAGE_DIR = dirname(fileURLToPath(import.meta.url));
const PYTHON_HELPER = join(PACKAGE_DIR, "python", "pdf_convert.py");
const MAX_CAPTURE = 64_000;

function configDir(cwd = process.cwd()) {
  const explicit = process.env.PI_CODING_AGENT_DIR?.trim();
  return explicit ? resolve(explicit) : resolve(cwd, ".pi");
}

export function documentConvertConfigPath(cwd = process.cwd()) {
  return join(configDir(cwd), "document-convert.json");
}

export function loadDocumentConvertConfig(cwd = process.cwd()) {
  const path = documentConvertConfigPath(cwd);
  if (!existsSync(path)) return {};
  const value = JSON.parse(readFileSync(path, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Invalid document converter config: ${path}`);
  }
  return value;
}

function resolvePython(cwd = process.cwd()) {
  const explicit = process.env.PI_DOCUMENT_CONVERT_PYTHON?.trim();
  if (explicit) return explicit;
  const base = join(configDir(cwd), "document-convert", "venv");
  const local = process.platform === "win32"
    ? join(base, "Scripts", "python.exe")
    : join(base, "bin", "python");
  return existsSync(local) ? local : (process.platform === "win32" ? "python" : "python3");
}

function capture(current, chunk) {
  if (current.length >= MAX_CAPTURE) return current;
  const text = String(chunk);
  return current + text.slice(0, MAX_CAPTURE - current.length);
}

async function runPython(args, { cwd, signal } = {}) {
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(resolvePython(cwd), [PYTHON_HELPER, ...args], {
      cwd: cwd ?? process.cwd(),
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout = capture(stdout, chunk); });
    child.stderr.on("data", (chunk) => { stderr = capture(stderr, chunk); });
    const abort = () => child.kill("SIGTERM");
    if (signal) {
      if (signal.aborted) abort();
      else signal.addEventListener("abort", abort, { once: true });
    }
    child.on("error", reject);
    child.on("close", (code) => {
      signal?.removeEventListener("abort", abort);
      if (signal?.aborted) return reject(new Error("Document conversion aborted"));
      if (code !== 0) {
        const detail = stderr.trim() || stdout.trim() || `exit ${code}`;
        return reject(new Error(`Document conversion failed: ${detail.slice(-4000)}`));
      }
      resolvePromise(stdout);
    });
  });
}

export async function convertPdfFile(inputPath, options = {}) {
  const cwd = options.cwd ?? process.cwd();
  const outputPath = options.outputPath ?? `${inputPath}.md`;
  const metadataPath = options.metadataPath ?? `${outputPath}.conversion.json`;
  mkdirSync(dirname(outputPath), { recursive: true, mode: 0o700 });
  const args = [
    "--input", resolve(inputPath),
    "--output", resolve(outputPath),
    "--metadata", resolve(metadataPath),
    "--config", documentConvertConfigPath(cwd),
  ];
  if (options.sourceUrl) args.push("--source-url", options.sourceUrl);
  if (Number.isInteger(options.maxPages) && options.maxPages > 0) {
    args.push("--max-pages", String(options.maxPages));
  }
  const stdout = await runPython(args, { cwd, signal: options.signal });
  const trimmed = stdout.trim();
  const lines = trimmed.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  let result;
  let lastError;
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      result = JSON.parse(lines[i]);
      break;
    } catch (err) {
      lastError = err;
    }
  }
  if (!result) {
    try {
      result = JSON.parse(trimmed);
    } catch (err) {
      lastError = err;
    }
  }
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    const detail = lastError instanceof Error ? ` (${lastError.message})` : "";
    throw new Error(`Document converter returned invalid JSON${detail}: ${stdout.slice(-1000)}`);
  }
  return result;
}

export async function convertPdfBuffer(buffer, options = {}) {
  const cwd = options.cwd ?? process.cwd();
  const outputDir = resolve(options.outputDir ?? join(configDir(cwd), "document-convert", "tmp"));
  mkdirSync(outputDir, { recursive: true, mode: 0o700 });
  const id = options.id ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const sourcePath = join(outputDir, `${id}.pdf`);
  const outputPath = join(outputDir, `${id}.md`);
  const metadataPath = join(outputDir, `${id}.conversion.json`);
  writeFileSync(sourcePath, Buffer.from(buffer), { mode: 0o600, flag: "wx" });
  const result = await convertPdfFile(sourcePath, {
    cwd,
    outputPath,
    metadataPath,
    sourceUrl: options.sourceUrl,
    maxPages: options.maxPages,
    signal: options.signal,
  });
  return { ...result, sourcePath, outputPath, metadataPath };
}
