import { send, readJson, passwordMatches, sessionCookie, missingConfig } from "./_lib.js";

export default async function handler(req, res) {
  if (req.method !== "POST") return send(res, 405, { error: "POST only" });
  const missing = missingConfig();
  if (missing.length) return send(res, 500, { error: `Missing env vars: ${missing.join(", ")}` });
  let body = {};
  try { body = await readJson(req); } catch { return send(res, 400, { error: "Bad request" }); }
  if (!passwordMatches(body.password)) {
    await new Promise((r) => setTimeout(r, 800)); // slow down guessing
    return send(res, 401, { error: "Wrong password" });
  }
  send(res, 200, { ok: true }, { "Set-Cookie": sessionCookie() });
}
