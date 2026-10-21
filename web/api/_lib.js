// Shared helpers for the hosted dashboard (Vercel serverless functions).
// Files starting with "_" inside /api are not exposed as routes.
import crypto from "node:crypto";

export const STATUSES = [
  "Pending", "Needs Contact", "Approved", "Sent", "Followup-Sent", "Replied",
  "Interview", "Offer", "Rejected", "Bounced", "Failed", "Skipped",
];
export const EDITABLE_FIELDS = ["status", "track", "notes", "email", "recipient_name"];
export const DAILY_WORKFLOW = "daily-outreach-prep.yml";
export const ACTIONS_WORKFLOW = "job-seeker-actions.yml";
export const ACTION_TASKS = ["send", "followups", "check-replies", "generate", "backfill-sources", "refilter", "test-send"];

// ---------------------------------------------------------------- config
export function cfg() {
  const repo = process.env.GITHUB_REPO || "";
  return {
    repo,
    branch: process.env.GITHUB_BRANCH || "main",
    token: process.env.GITHUB_TOKEN || "",
    password: process.env.DASHBOARD_PASSWORD || "",
    secret: process.env.SESSION_SECRET || "",
  };
}

export function missingConfig() {
  const c = cfg();
  return ["GITHUB_REPO", "GITHUB_TOKEN", "DASHBOARD_PASSWORD", "SESSION_SECRET"].filter(
    (k) => !{ GITHUB_REPO: c.repo, GITHUB_TOKEN: c.token, DASHBOARD_PASSWORD: c.password, SESSION_SECRET: c.secret }[k],
  );
}

// ---------------------------------------------------------------- http helpers
export function send(res, status, body, headers = {}) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Cache-Control", "no-store");
  for (const [k, v] of Object.entries(headers)) res.setHeader(k, v);
  res.end(JSON.stringify(body));
}

export async function readJson(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const c of req) chunks.push(c);
  const raw = Buffer.concat(chunks).toString("utf8");
  return raw ? JSON.parse(raw) : {};
}

function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

// ---------------------------------------------------------------- auth
const COOKIE = "jh_session";
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function sign(value) {
  return crypto.createHmac("sha256", cfg().secret).update(value).digest("base64url");
}

export function sessionCookie() {
  const exp = String(Math.floor(Date.now() / 1000) + MAX_AGE);
  return `${COOKIE}=${exp}.${sign(exp)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${MAX_AGE}`;
}

export function clearCookie() {
  return `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`;
}

export function isAuthed(req) {
  if (!cfg().secret) return false;
  const v = parseCookies(req)[COOKIE] || "";
  const [exp, sig] = v.split(".");
  if (!exp || !sig) return false;
  const good = sign(exp);
  if (sig.length !== good.length || !crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(good))) return false;
  return Number(exp) > Date.now() / 1000;
}

export function passwordMatches(given) {
  const a = crypto.createHash("sha256").update(String(given || "")).digest();
  const b = crypto.createHash("sha256").update(cfg().password).digest();
  return cfg().password.length > 0 && crypto.timingSafeEqual(a, b);
}

/** Returns true if the request may proceed; otherwise sends the error response. */
export function guard(req, res, { mutating = false } = {}) {
  const missing = missingConfig();
  if (missing.length) {
    send(res, 500, { error: `Dashboard not configured. Missing env vars: ${missing.join(", ")}` });
    return false;
  }
  if (!isAuthed(req)) {
    send(res, 401, { error: "Not signed in" });
    return false;
  }
  if (mutating) {
    // Basic CSRF defence: state-changing calls must come from this site.
    const origin = req.headers.origin;
    const host = req.headers["x-forwarded-host"] || req.headers.host;
    if (origin && host && new URL(origin).host !== host) {
      send(res, 403, { error: "Cross-site request blocked" });
      return false;
    }
  }
  return true;
}

// ---------------------------------------------------------------- GitHub API
async function gh(path, init = {}) {
  const { token } = cfg();
  const resp = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "job-hunt-dashboard",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers || {}),
    },
  });
  return resp;
}

function contentsPath(path) {
  return `/repos/${cfg().repo}/contents/${path.split("/").map(encodeURIComponent).join("/")}`;
}

export async function getFile(path, { binary = false } = {}) {
  const r = await gh(`${contentsPath(path)}?ref=${cfg().branch}`);
  if (r.status === 404) return { text: "", buffer: null, sha: null };
  if (!r.ok) throw new Error(`GitHub read ${path} failed: ${r.status} ${await r.text()}`);
  const j = await r.json();
  let buffer;
  if (j.content || j.size === 0) buffer = Buffer.from(j.content || "", "base64");
  else { // files over 1 MB come without inline content
    const raw = await gh(`${contentsPath(path)}?ref=${cfg().branch}`, { headers: { Accept: "application/vnd.github.raw" } });
    buffer = Buffer.from(await raw.arrayBuffer());
  }
  return { text: binary ? "" : buffer.toString("utf8"), buffer, sha: j.sha };
}

export async function putFile(path, data, sha, message) {
  const content = (Buffer.isBuffer(data) ? data : Buffer.from(data, "utf8")).toString("base64");
  const body = { message, content, branch: cfg().branch };
  if (sha) body.sha = sha;
  const r = await gh(contentsPath(path), { method: "PUT", body: JSON.stringify(body) });
  if (r.status === 409 || r.status === 422) return { conflict: true };
  if (!r.ok) throw new Error(`GitHub write ${path} failed: ${r.status} ${await r.text()}`);
  return { conflict: false };
}

export async function deleteFile(path, message) {
  const { sha } = await getFile(path, { binary: true });
  if (!sha) return false;
  const r = await gh(contentsPath(path), { method: "DELETE", body: JSON.stringify({ message, sha, branch: cfg().branch }) });
  return r.ok;
}

export async function listDir(path) {
  const r = await gh(`${contentsPath(path)}?ref=${cfg().branch}`);
  if (!r.ok) return [];
  const j = await r.json();
  return Array.isArray(j) ? j.map((f) => f.name) : [];
}

/** Same rule as common.py's letter_filename(). */
export function letterFilename(filePrefix, companyName) {
  const safe = String(companyName).replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "Company";
  return `${filePrefix}_Cover_Letter_${safe}.pdf`;
}

export async function recentRuns(limit = 8) {
  const { repo } = cfg();
  try {
    const r = await gh(`/repos/${repo}/actions/runs?per_page=${limit}`);
    if (!r.ok) return [];
    return ((await r.json()).workflow_runs || []).map((run) => ({
      name: run.display_title || run.name, workflow: run.name, status: run.status, conclusion: run.conclusion,
      created_at: run.created_at, updated_at: run.updated_at, url: run.html_url, event: run.event,
    }));
  } catch {
    return [];
  }
}

export async function dispatch(workflowFile, inputs) {
  const { repo, branch } = cfg();
  const body = { ref: branch, ...(inputs ? { inputs } : {}) };
  const r = await gh(`/repos/${repo}/actions/workflows/${workflowFile}/dispatches`, { method: "POST", body: JSON.stringify(body) });
  if (r.status !== 204) throw new Error(`Could not start it on GitHub: ${r.status} ${await r.text()}`);
}

// ---------------------------------------------------------------- CSV (RFC 4180, matches Python's csv module)
export function parseCsv(text) {
  const rows = [];
  let row = [], field = "", i = 0, quoted = false;
  text = text.replace(/^﻿/, "");
  while (i < text.length) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
        quoted = false; i++; continue;
      }
      field += c; i++; continue;
    }
    if (c === '"') { quoted = true; i++; continue; }
    if (c === ",") { row.push(field); field = ""; i++; continue; }
    if (c === "\r") { i++; continue; }
    if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; i++; continue; }
    field += c; i++;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  const header = rows.shift() || [];
  const records = rows
    .filter((r) => r.some((v) => v !== ""))
    .map((r) => Object.fromEntries(header.map((h, k) => [h, r[k] ?? ""])));
  return { header, records };
}

function csvField(v) {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(header, records) {
  const lines = [header.map(csvField).join(",")];
  for (const r of records) lines.push(header.map((h) => csvField(r[h])).join(","));
  return lines.join("\r\n") + "\r\n"; // Python's csv module writes \r\n
}
