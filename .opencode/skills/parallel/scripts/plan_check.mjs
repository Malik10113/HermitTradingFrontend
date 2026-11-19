#!/usr/bin/env node
// plan_check.mjs — validate a /parallel dispatch plan before launching executors.
//
// Usage: node plan_check.mjs <plan.tsv> [--help]
//
// The plan is tab-separated, one task per line, five fields:
//
//   id <TAB> wave <TAB> kind <TAB> imports <TAB> write_set
//
//   id        task id, e.g. F1-E3
//   wave      1, 2 or 3
//   kind      skeleton | atom | facade | docs
//   imports   comma-separated task ids, or "-" for none
//   write_set space-separated paths (no spaces in paths)
//
// Blank lines and lines starting with # are ignored. CRLF plans are accepted.
//
// Rules:
//   R1  a wave-1 task imports nothing another task owns
//   R2  a facade (3+ imports) is wave 2 and never carries documentation
//   R3  write sets are disjoint across all tasks
//   R4  a docs task runs in wave 1
//
// Exit: 0 clean, 1 rule violation, 2 usage or malformed plan.
//
// Zero dependencies, so the skill exports as a directory and runs on Windows, macOS and
// Linux under the one runtime OpenCode itself needs.

import { readFileSync } from "node:fs";

const FACADE_IMPORTS = 3;

export function isDoc(path) {
  const text = String(path).trim().replace(/\\/g, "/").toLowerCase();
  return /\.(md|mdx|rst|txt)$/.test(text) || /^(docs?|documentation)\//.test(text);
}

export function validate(text) {
  const problems = [];
  let malformed = 0;
  const owners = new Map();
  const seen = new Set();

  const lines = String(text).replace(/^\uFEFF/, "").split(/\r?\n/);
  lines.forEach((raw, index) => {
    const lineNo = index + 1;
    if (!raw.trim() || raw.trim().startsWith("#")) return;

    const fields = raw.split("\t").map((field) => field.trim());
    if (fields.length < 5) {
      problems.push(`line ${lineNo}: expected 5 tab-separated fields, got ${fields.length}`);
      malformed += 1;
      return;
    }

    const [id, wave, kind, importsRaw, writeRaw] = fields;
    if (!id) {
      problems.push(`line ${lineNo}: empty task id`);
      malformed += 1;
      return;
    }
    if (seen.has(id)) {
      problems.push(`${id}: duplicate task id`);
      malformed += 1;
    }
    seen.add(id);

    if (!["1", "2", "3"].includes(wave)) {
      problems.push(`${id}: wave must be 1, 2 or 3 (got "${wave}")`);
    }

    const imports = importsRaw && importsRaw !== "-" ? importsRaw.split(",").map((s) => s.trim()).filter(Boolean) : [];
    const paths = writeRaw ? writeRaw.split(/\s+/).filter(Boolean) : [];
    const docs = paths.filter(isDoc);

    if (wave === "1" && imports.length > 0) {
      problems.push(
        `${id}: R1 wave-1 task imports ${imports.length} task module(s) (${imports.join(",")}); ` +
          "it can only start after they merge"
      );
    }

    if (imports.length >= FACADE_IMPORTS) {
      if (wave === "1") {
        problems.push(`${id}: R2 facade atom imports ${imports.length} tasks, so it cannot be wave 1`);
      }
      if (docs.length > 0) {
        problems.push(
          `${id}: R2 facade atom imports ${imports.length} tasks and must not write documentation ` +
            `(${docs.join(", ")}); split docs into a wave-1 task`
        );
      }
    }

    if (kind === "docs" && wave !== "1") {
      problems.push(`${id}: R4 docs task must run in wave 1 (got wave ${wave})`);
    } else if (docs.length > 0 && docs.length === paths.length && wave !== "1") {
      problems.push(`${id}: R4 docs-only write set must run in wave 1 (got wave ${wave})`);
    }

    for (const path of paths) {
      if (owners.has(path)) {
        problems.push(`${id}: R3 write set overlaps ${owners.get(path)} on ${path}`);
      } else {
        owners.set(path, id);
      }
    }
  });

  return { problems, malformed };
}

const USAGE = `plan_check — validate a /parallel plan

Usage: node plan_check.mjs <plan.tsv>

Five tab-separated fields per task:
  id  wave  kind  imports  write_set

Rules: R1 wave-1 imports nothing, R2 facade is wave 2 and docs-free,
R3 write sets disjoint, R4 docs in wave 1.

Exit: 0 clean, 1 rule violation, 2 usage or malformed plan.`;

export function main(argv) {
  if (argv.includes("-h") || argv.includes("--help")) {
    process.stdout.write(`${USAGE}\n`);
    return 0;
  }
  if (argv.length !== 1) {
    process.stderr.write("plan_check: usage: node plan_check.mjs <plan.tsv>\n");
    return 2;
  }
  let text;
  try {
    text = readFileSync(argv[0], "utf8");
  } catch (error) {
    process.stderr.write(`plan_check: cannot read ${argv[0]}: ${error.message}\n`);
    return 2;
  }
  const { problems, malformed } = validate(text);
  for (const problem of problems) process.stdout.write(`${problem}\n`);
  if (malformed > 0) return 2;
  if (problems.length > 0) return 1;
  process.stdout.write("plan ok\n");
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exitCode = main(process.argv.slice(2));
}
