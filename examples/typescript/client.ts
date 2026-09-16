/**
 * A DSAIL REST client in TypeScript, typed from the bundled OpenAPI document.
 *
 * `dsail-api.d.ts` is GENERATED (`npm run generate`) from the same
 * `openapi.json` the Python package ships, so every request and response shape
 * here is the service's own contract rather than a retyping of it. The client
 * does what the Python one does: sends the credential header, obtains an
 * evaluation credential on a 401 and retries once, and returns typed payloads.
 * It holds no parser, compiler or solver.
 */

import type { components, paths } from "./dsail-api.d.ts";

type Json<T> = T extends { content: { "application/json": infer B } } ? B : never;

export type CompileRequest = components["schemas"]["CompileRequest"];
export type CheckRequest = components["schemas"]["CheckRequest"];
export type CompileResponse = Json<paths["/v1/compile"]["post"]["responses"]["200"]>;
export type CheckResponse = Json<paths["/v1/check"]["post"]["responses"]["200"]>;
export type PromptPackResponse = Json<paths["/v1/prompt-pack"]["post"]["responses"]["200"]>;
export type CredentialResponse = Json<
  paths["/v1/credentials/evaluation"]["post"]["responses"]["200"]
>;
export type ErrorResponse = Json<paths["/v1/check"]["post"]["responses"]["422"]>;

export type Check = "TRUE" | "FALSE" | "UNKNOWN" | "AMBIGUOUS";

export class DsailError extends Error {
  constructor(public readonly status: number, public readonly payload: ErrorResponse) {
    super(`${payload.error.code}: ${payload.error.message}`);
  }
}

export class DsailClient {
  private credential: string | undefined;

  constructor(
    private readonly baseUrl: string,
    credential?: string,
    private readonly autoCredential = true,
  ) {
    this.credential = credential;
  }

  private async request<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
    const headers: Record<string, string> = { accept: "application/json", "user-agent": "dsail-typescript-example/0.1" };
    if (body !== undefined) headers["content-type"] = "application/json";
    if (this.credential) headers["x-jaxon-credential"] = this.credential;
    const response = await fetch(this.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const payload = JSON.parse(text);
    if (response.status === 401 && this.autoCredential && !retried) {
      const code = payload?.error?.code;
      if (code === "CREDENTIAL_REQUIRED" || code === "CREDENTIAL_INVALID") {
        await this.acquireEvaluationCredential();
        return this.request<T>(method, path, body, true);
      }
    }
    if (!response.ok || payload?.ok === false) throw new DsailError(response.status, payload as ErrorResponse);
    return payload as T;
  }

  async acquireEvaluationCredential(): Promise<CredentialResponse> {
    const issued = await this.request<CredentialResponse>("POST", "/v1/credentials/evaluation", {
      client: "dsail-typescript-example/0.1",
    });
    this.credential = issued.credential;
    return issued;
  }

  compile(body: CompileRequest): Promise<CompileResponse> {
    return this.request<CompileResponse>("POST", "/v1/compile", body);
  }

  check(body: CheckRequest): Promise<CheckResponse> {
    return this.request<CheckResponse>("POST", "/v1/check", body);
  }

  promptPack(rulesetHash: string): Promise<PromptPackResponse> {
    return this.request<PromptPackResponse>("GET", `/v1/prompt-pack/${encodeURIComponent(rulesetHash)}`);
  }

  /** Every assertion across every rule, flattened, in the engine's own words. */
  static assertions(result: CheckResponse): Array<{ rule: string; name: string; check: Check; counterexample?: string }> {
    return result.rules.flatMap((rule) =>
      rule.assertions.map((a) => ({
        rule: rule.rule,
        name: a.name,
        check: a.check as Check,
        counterexample: a.counterexample,
      })),
    );
  }
}
