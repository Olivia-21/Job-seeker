import { send, guard, getFile, listDir, parseCsv, recentRuns, letterFilename, STATUSES } from "./_lib.js";

export default async function handler(req, res) {
  if (!guard(req, res)) return;
  try {
    const [profileF, queueF, logF, repliesF, runs, letters] = await Promise.all([
      getFile("profile.json"), getFile("outreach_queue.csv"), getFile("outreach_log.csv"),
      getFile("replies_log.csv"), recentRuns(), listDir("generated_letters"),
    ]);
    const profile = JSON.parse(profileF.text || "{}");
    const letterSet = new Set(letters);
    const queue = parseCsv(queueF.text).records.map((r) => {
      const fn = letterFilename(profile.file_prefix || "CV", r.company_name);
      let status = r.status || "Pending";
      // Same rule as common.load_queue(): website checked but no email -> Needs Contact
      if (r.email === "no_email_found" && (["Pending", "Approved"].includes(status) ||
          (status === "Skipped" && !(r.notes || "").includes("Auto-skipped")))) status = "Needs Contact";
      return { ...r, status, letter: letterSet.has(fn) ? fn : "" };
    });
    send(res, 200, {
      generated_at: new Date().toISOString(),
      mode: "hosted",
      capabilities: { letters: true, runSearch: true, actions: true, cv: true },
      bot_run: runs[0] || null,
      runs,
      profile: {
        name: profile.name, email: profile.email, headline: profile.headline, cv_file: profile.cv_file,
        limits: profile.limits || {},
        tracks: Object.fromEntries(Object.entries(profile.tracks || {}).map(
          ([k, t]) => [k, { label: t.label, priority: t.priority || "", role_title: t.role_title }])),
      },
      statuses: STATUSES,
      queue,
      log: parseCsv(logF.text).records,
      replies: parseCsv(repliesF.text).records,
    });
  } catch (e) {
    send(res, 502, { error: String(e.message || e) });
  }
}
