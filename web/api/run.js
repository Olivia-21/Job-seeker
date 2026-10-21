import { send, guard, readJson, dispatch, DAILY_WORKFLOW, ACTIONS_WORKFLOW, ACTION_TASKS } from "./_lib.js";

// Starts work on GitHub Actions: "search" = the daily discovery run, anything else = Job Seeker Actions.
export default async function handler(req, res) {
  if (req.method !== "POST") return send(res, 405, { error: "POST only" });
  if (!guard(req, res, { mutating: true })) return;
  let body = {};
  try { body = await readJson(req); } catch { return send(res, 400, { error: "Bad JSON" }); }
  const task = body.task || "search";
  try {
    if (task === "search") await dispatch(DAILY_WORKFLOW);
    else if (ACTION_TASKS.includes(task)) await dispatch(ACTIONS_WORKFLOW, { task, arg: String(body.arg || "").slice(0, 500) });
    else return send(res, 400, { error: "Unknown task" });
    send(res, 202, { ok: true, task });
  } catch (e) {
    send(res, 502, { error: String(e.message || e) });
  }
}
