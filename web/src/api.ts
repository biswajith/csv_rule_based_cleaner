import type {
  Change, Config, Flag, Page, Plan, PlanResult, PreviewResult, Profile, RunOptions, RunStatus, SourceInfo,
} from "./types";
import { mock } from "./mock";
import { ApiError } from "./apiError";

export { ApiError };

async function request<T>(method: string, path: string, body?: unknown, form?: FormData): Promise<T> {
  const init: RequestInit = { method };
  if (form) {
    init.body = form;
  } else if (body !== undefined) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the Scrubber server. Is it running?");
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let errors: string[] | undefined;
    try {
      const j = await res.json();
      if (typeof j.detail === "string") detail = j.detail;
      else if (j.detail !== undefined) detail = JSON.stringify(j.detail);
      if (Array.isArray(j.errors)) errors = j.errors.map(String);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, errors);
  }
  return (await res.json()) as T;
}

const real = {
  config: () => request<Config>("GET", "/api/config"),
  upload: (file: File, delimiter: string, encoding: string) => {
    const f = new FormData();
    f.append("file", file);
    if (delimiter) f.append("delimiter", delimiter);
    if (encoding) f.append("encoding", encoding);
    return request<SourceInfo>("POST", "/api/uploads", undefined, f);
  },
  addSource: (spec: Record<string, unknown>) => request<SourceInfo>("POST", "/api/sources", spec),
  profile: (id: string) => request<Profile>("GET", `/api/sources/${encodeURIComponent(id)}/profile`),
  plan: (source_id: string, rules: string[]) =>
    request<PlanResult>("POST", "/api/plan", { source_id, rules }),
  preview: (source_id: string, plan: Plan) =>
    request<PreviewResult>("POST", "/api/preview", { source_id, plan }),
  approvePlan: (plan: Plan, by: string) =>
    request<{ plan: Plan }>("POST", "/api/plans/approve", { plan, by }),
  startRun: (source_id: string, plan: Plan, o: RunOptions) =>
    request<{ run_id: string }>("POST", "/api/runs", { source_id, plan, ...o }),
  run: (id: string) => request<RunStatus>("GET", `/api/runs/${encodeURIComponent(id)}`),
  cancel: (id: string) => request<RunStatus>("POST", `/api/runs/${encodeURIComponent(id)}/cancel`),
  changes: (id: string, column: string, offset: number, limit: number) =>
    request<Page<Change>>("GET", `/api/runs/${encodeURIComponent(id)}/changes?${qs(column, offset, limit)}`),
  flags: (id: string, column: string, offset: number, limit: number) =>
    request<Page<Flag>>("GET", `/api/runs/${encodeURIComponent(id)}/flags?${qs(column, offset, limit)}`),
};

function qs(column: string, offset: number, limit: number): string {
  const p = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (column) p.set("column", column);
  return p.toString();
}

export function fileUrl(runId: string, name: string): string {
  return `/api/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(name)}`;
}

// The mock is only reachable when built with VITE_MOCK=1; the condition is a
// compile-time constant so production bundles tree-shake it away.
export const api: typeof real = import.meta.env.VITE_MOCK === "1" ? mock : real;
