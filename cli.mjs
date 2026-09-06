#!/usr/bin/env node
import { convertPdfFile } from "./index.mjs";

function parse(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i];
    if (!key.startsWith("--")) continue;
    out[key.slice(2)] = argv[++i];
  }
  return out;
}

const args = parse(process.argv.slice(2));
if (!args.input || !args.output) {
  console.error("Usage: pi-document-convert --input FILE.pdf --output content.md [--metadata conversion.json] [--source-url URL] [--max-pages N]");
  process.exit(2);
}

try {
  const result = await convertPdfFile(args.input, {
    cwd: process.cwd(),
    outputPath: args.output,
    metadataPath: args.metadata,
    sourceUrl: args["source-url"],
    maxPages: args["max-pages"] ? Number(args["max-pages"]) : undefined,
  });
  process.stdout.write(JSON.stringify(result));
} catch (err) {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
}
