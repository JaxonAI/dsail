/**
 * Compile the expense policy, fetch its prompt pack, check one claim
 * dictionary, and print what the rules concluded — from TypeScript, against the
 * same hosted objects the Python client uses.
 *
 *   DSAIL_URL=http://127.0.0.1:8710 npm run demo
 */

import { readFileSync } from "node:fs";
import { DsailClient, DsailError } from "./client.ts";

const url = process.env.DSAIL_URL ?? "https://agents.jaxon.ai";
const client = new DsailClient(url, process.env.DSAIL_CREDENTIAL || undefined);
// run.sh copies ../policies/expenses.dsail beside this file.
const source = readFileSync(new URL("./expenses.dsail", import.meta.url), "utf8");

try {
  const compiled = await client.compile({ source });
  console.log(`ruleset ${compiled.ruleset_hash.slice(0, 12)}… asks ${compiled.manifest.claims.length} questions`);

  const pack = await client.promptPack(compiled.ruleset_hash);
  for (const prompt of pack.prompts) console.log(`  ? ${prompt.claim}: ${prompt.question}`);

  const result = await client.check({
    ruleset_hash: compiled.ruleset_hash,
    claims: { amount: "1899 USD", has_receipt: true, category: "equipment", manager_approved: false },
  });
  for (const a of DsailClient.assertions(result)) {
    console.log(`  ${a.check.padEnd(10)} ${a.name}${a.counterexample ? `   counterexample: ${a.counterexample}` : ""}`);
  }
  const violated = DsailClient.assertions(result).filter((a) => a.check === "FALSE").map((a) => a.name);
  console.log(JSON.stringify({ ok: true, ruleset_hash: result.ruleset_hash, violated }));
} catch (error) {
  if (error instanceof DsailError) {
    console.error(JSON.stringify({ ok: false, status: error.status, error: error.payload.error }));
    process.exit(2);
  }
  throw error;
}
