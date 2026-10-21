import { send, guard, readJson, getFile, putFile } from "./_lib.js";

// Replaces the CV in the repo. Body: { data: "<base64 PDF>" }
export default async function handler(req, res) {
  if (req.method !== "POST") return send(res, 405, { error: "POST only" });
  if (!guard(req, res, { mutating: true })) return;
  let body;
  try { body = await readJson(req); } catch { return send(res, 400, { error: "Bad JSON" }); }
  const buf = Buffer.from(String(body.data || ""), "base64");
  if (buf.length < 1000 || buf.subarray(0, 5).toString() !== "%PDF-") return send(res, 400, { error: "That isn't a PDF file." });
  if (buf.length > 3 * 1024 * 1024) return send(res, 413, { error: "CV must be under 3 MB." });
  try {
    const profile = JSON.parse((await getFile("profile.json")).text || "{}");
    const path = profile.cv_file || "CV.pdf";
    for (let i = 0; i < 3; i++) {
      const { sha } = await getFile(path, { binary: true });
      const { conflict } = await putFile(path, buf, sha, "Dashboard: replace CV");
      if (!conflict) return send(res, 200, { ok: true, bytes: buf.length });
    }
    send(res, 409, { error: "Please try again." });
  } catch (e) {
    send(res, 502, { error: String(e.message || e) });
  }
}
