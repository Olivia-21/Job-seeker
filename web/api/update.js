import { send, guard, readJson, getFile, putFile, deleteFile, dispatch, letterFilename, parseCsv, toCsv,
  STATUSES, EDITABLE_FIELDS, ACTIONS_WORKFLOW } from "./_lib.js";

const QUEUE = "outreach_queue.csv";

export default async function handler(req, res) {
  if (req.method !== "POST") return send(res, 405, { error: "POST only" });
  if (!guard(req, res, { mutating: true })) return;

  let body;
  try { body = await readJson(req); } catch { return send(res, 400, { error: "Bad JSON" }); }
  const names = (body.companies || (body.company_name ? [body.company_name] : [])).map((n) => String(n).toLowerCase());
  const changes = Object.fromEntries(EDITABLE_FIELDS.filter((k) => k in body).map((k) => [k, String(body[k])]));
  if (!names.length || !Object.keys(changes).length) return send(res, 400, { error: "Nothing to update" });
  if (changes.status && !STATUSES.includes(changes.status)) return send(res, 400, { error: "Bad status" });

  try {
    let tracks = null, profile = null;
    if (changes.track) {
      profile = JSON.parse((await getFile("profile.json")).text || "{}");
      tracks = profile.tracks || {};
      if (!tracks[changes.track]) return send(res, 400, { error: "Bad track" });
    }
    // Read-modify-write with a retry: if the bot committed in between, GitHub rejects the stale sha.
    for (let attempt = 0; attempt < 3; attempt++) {
      const { text, sha } = await getFile(QUEUE);
      const { header, records } = parseCsv(text);
      let hit = 0;
      for (const r of records) {
        if (names.includes((r.company_name || "").toLowerCase())) {
          Object.assign(r, changes);
          if (tracks && changes.track) r.role_title = tracks[changes.track].role_title || r.role_title;
          hit++;
        }
      }
      if (!hit) return send(res, 404, { error: "Company not found" });
      const label = changes.status ? `${changes.status}` : Object.keys(changes).join(", ");
      const who = hit === 1 ? records.find((r) => names.includes(r.company_name.toLowerCase())).company_name : `${hit} companies`;
      const { conflict } = await putFile(QUEUE, toCsv(header, records), sha, `Dashboard: ${label} - ${who}`);
      if (!conflict) {
        let regenerating = false;
        if (changes.email && changes.email.includes("@") && !changes.track) {
          // A contact was added by hand: build the cover letter for it right away.
          const touched = records.filter((r) => names.includes(r.company_name.toLowerCase())).map((r) => r.company_name);
          await dispatch(ACTIONS_WORKFLOW, { task: "generate", arg: touched.join("|") }).then(() => { regenerating = true; }).catch(() => {});
        }
        if (changes.track) {
          // The cover letter depends on the role track: drop the old one and rebuild it on GitHub.
          const touched = records.filter((r) => names.includes(r.company_name.toLowerCase())).map((r) => r.company_name);
          for (const n of touched) await deleteFile(`generated_letters/${letterFilename(profile.file_prefix || "CV", n)}`, `Dashboard: role changed - ${n}`).catch(() => {});
          await dispatch(ACTIONS_WORKFLOW, { task: "generate", arg: touched.join("|") }).then(() => { regenerating = true; }).catch(() => {});
        }
        return send(res, 200, { updated: hit, regenerating });
      }
    }
    send(res, 409, { error: "The queue changed while saving. Please try again." });
  } catch (e) {
    send(res, 502, { error: String(e.message || e) });
  }
}
