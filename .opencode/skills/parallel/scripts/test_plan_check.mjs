// test_plan_check.mjs — tests for plan_check.mjs. Zero dependencies: node:test + node:assert.
//
// Usage: node test_plan_check.mjs
// Exit: 0 all pass, non-zero on failure.

import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { isDoc, validate, main } from "./plan_check.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CHECK = join(HERE, "plan_check.mjs");

function run(plan, args = []) {
  const dir = mkdtempSync(join(tmpdir(), "plan-check-"));
  try {
    const path = join(dir, "plan.tsv");
    if (plan !== null) writeFileSync(path, plan);
    const result = spawnSync(process.execPath, [CHECK, path, ...args], { encoding: "utf8" });
    return { code: result.status, out: `${result.stdout}${result.stderr}` };
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const plan = (...rows) => `${rows.join("\n")}\n`;
const okExit = 0;
const violation = 1;
const malformed = 2;

const valid = plan(
  "F1-E1\t1\tatom\t-\tpl/sources.py tests/test_sources.py",
  "F1-E2\t1\tatom\t-\tpl/transforms.py tests/test_transforms.py",
  "F1-DOC\t1\tdocs\t-\tREADME.md",
  "F1-E5\t2\tfacade\tF1-E1,F1-E2,F1-E3\tpl/cli.py tests/test_cli.py"
);

test("valid plan passes", () => {
  const { code, out } = run(valid);
  assert.equal(code, okExit, out);
  assert.match(out, /plan ok/);
});

test("R1 wave-1 task with imports", () => {
  const { code, out } = run(plan("F1-E5\t1\tfacade\tF1-E1\tpl/cli.py"));
  assert.equal(code, violation, out);
  assert.match(out, /R1/);
});

test("R2 facade carrying docs", () => {
  const { code, out } = run(
    plan("F1-E5\t2\tfacade\tF1-E1,F1-E2,F1-E3,F1-E4\tpl/cli.py README.md")
  );
  assert.equal(code, violation, out);
  assert.match(out, /R2/);
  assert.match(out, /README\.md/);
});

test("R2 facade in wave 1", () => {
  const { code, out } = run(plan("F1-E5\t1\tfacade\tF1-E1,F1-E2,F1-E3\tpl/cli.py"));
  assert.equal(code, violation, out);
  assert.match(out, /R2/);
});

test("two imports is not a facade", () => {
  const { code, out } = run(plan("F1-E4\t2\tatom\tF1-E1,F1-E2\tpl/engine.py"));
  assert.equal(code, okExit, out);
});

test("R3 overlapping write sets", () => {
  const { code, out } = run(
    plan(
      "F1-E1\t1\tatom\t-\tpl/core.py tests/test_a.py",
      "F1-E2\t1\tatom\t-\tpl/core.py"
    )
  );
  assert.equal(code, violation, out);
  assert.match(out, /R3/);
});

test("R4 docs task in wave 2", () => {
  const { code, out } = run(plan("F1-DOC\t2\tdocs\t-\tREADME.md"));
  assert.equal(code, violation, out);
  assert.match(out, /R4/);
});

test("R4 docs-only write set in wave 2", () => {
  const { code, out } = run(plan("F1-E9\t2\tatom\t-\tdocs/usage.md"));
  assert.equal(code, violation, out);
  assert.match(out, /R4/);
});

test("docs task in wave 1 is fine", () => {
  const { code, out } = run(plan("F1-DOC\t1\tdocs\t-\tREADME.md"));
  assert.equal(code, okExit, out);
});

test("comments and blank lines are ignored", () => {
  const { code, out } = run(plan("# id\twave\tkind\timports\twrite_set", "", "F1-E1\t1\tatom\t-\tpl/core.py"));
  assert.equal(code, okExit, out);
});

test("malformed line exits 2", () => {
  const { code, out } = run(plan("F1-E1\t1\tatom\tpl/core.py"));
  assert.equal(code, malformed, out);
  assert.match(out, /expected 5 tab-separated fields/);
});

test("bad wave value", () => {
  const { code, out } = run(plan("F1-E1\t9\tatom\t-\tpl/core.py"));
  assert.equal(code, violation, out);
  assert.match(out, /wave must be/);
});

test("duplicate task id exits 2", () => {
  const { code, out } = run(plan("F1-E1\t1\tatom\t-\tpl/a.py", "F1-E1\t1\tatom\t-\tpl/b.py"));
  assert.equal(code, malformed, out);
  assert.match(out, /duplicate/);
});

test("CRLF plans parse (Windows)", () => {
  const crlf = valid.replace(/\n/g, "\r\n");
  const { code, out } = run(crlf);
  assert.equal(code, okExit, out);
});

test("UTF-8 BOM is ignored", () => {
  const { code, out } = run(`\uFEFF${valid}`);
  assert.equal(code, okExit, out);
});

test("backslash paths are treated as docs by suffix", () => {
  const { code, out } = run(plan("F1-DOC\t2\tdocs\t-\tdocs\\usage.md"));
  assert.equal(code, violation, out);
  assert.match(out, /R4/);
});

test("regression: original pipeline-lab run is flagged", () => {
  const { code, out } = run(
    plan(
      "F1-E1\t1\tatom\t-\tpl/sources.py tests/test_sources.py",
      "F1-E2\t1\tatom\t-\tpl/transforms.py tests/test_transforms.py",
      "F1-E3\t1\tatom\t-\tpl/sinks.py tests/test_sinks.py",
      "F1-E4\t1\tatom\t-\tpl/engine.py pl/report.py tests/test_engine.py",
      "F1-E5\t2\tfacade\tF1-E1,F1-E2,F1-E3,F1-E4\tpl/cli.py tests/test_cli.py README.md"
    )
  );
  assert.equal(code, violation, out);
  assert.match(out, /F1-E5: R2/);
  assert.match(out, /README\.md/);
});

test("regression: corrected run passes", () => {
  const { code, out } = run(
    plan(
      "F1-E1\t1\tatom\t-\tpl/sources.py tests/test_sources.py",
      "F1-E2\t1\tatom\t-\tpl/transforms.py tests/test_transforms.py",
      "F1-E3\t1\tatom\t-\tpl/sinks.py tests/test_sinks.py",
      "F1-E4\t1\tatom\t-\tpl/engine.py pl/report.py tests/test_engine.py",
      "F1-DOC\t1\tdocs\t-\tREADME.md",
      "F1-E5\t2\tfacade\tF1-E1,F1-E2,F1-E3,F1-E4\tpl/cli.py tests/test_cli.py"
    )
  );
  assert.equal(code, okExit, out);
});

test("usage errors exit 2", () => {
  const noArgs = spawnSync(process.execPath, [CHECK], { encoding: "utf8" });
  assert.equal(noArgs.status, 2);
  assert.match(`${noArgs.stdout}${noArgs.stderr}`, /usage/);

  const missing = spawnSync(process.execPath, [CHECK, join(HERE, "nope.tsv")], { encoding: "utf8" });
  assert.equal(missing.status, 2);
});

test("--help exits 0", () => {
  const result = spawnSync(process.execPath, [CHECK, "--help"], { encoding: "utf8" });
  assert.equal(result.status, 0);
  assert.match(result.stdout, /R1/);
});

test("isDoc classification", () => {
  assert.equal(isDoc("README.md"), true);
  assert.equal(isDoc("docs/design.md"), true);
  assert.equal(isDoc("pl/cli.py"), false);
});

test("validate returns problems without exiting", () => {
  const { problems, malformed } = validate(plan("F1-E1\t1\tatom\t-\tpl/a.py"));
  assert.deepEqual(problems, []);
  assert.equal(malformed, 0);
  assert.equal(main(["-h"]), 0);
});
