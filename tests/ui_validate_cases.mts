// Runs tests/fixtures/ui_contract_cases.json through the page's validator
// (frontend/src/uiValidate.ts) and prints each case's verdict as JSON, for
// tests/test_ui_contract.py to compare with the Python module's. Run with
// `node --experimental-strip-types tests/ui_validate_cases.mts <cases.json>`.
import { readFileSync } from "node:fs";
import { argsProblem, operationProblem, targetProblem } from "../frontend/src/uiValidate.ts";

const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = {
  args: cases.args.map((c: any) => argsProblem(c.command, c.args)),
  targets: cases.targets.map((c: any) => targetProblem(c.ref)),
  operations: cases.operations.map((c: any) => operationProblem(c.op)),
};
process.stdout.write(JSON.stringify(out));
