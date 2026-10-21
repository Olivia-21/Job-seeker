import { send, guard, getFile } from "./_lib.js";

// Streams a PDF from the repo: ?kind=letter&name=<file>  or  ?kind=cv
export default async function handler(req, res) {
  if (!guard(req, res)) return;
  const url = new URL(req.url, "http://x");
  const kind = url.searchParams.get("kind");
  try {
    let path;
    if (kind === "cv") {
      const profile = JSON.parse((await getFile("profile.json")).text || "{}");
      path = profile.cv_file || "CV.pdf";
    } else if (kind === "letter") {
      const name = (url.searchParams.get("name") || "").split("/").pop();
      if (!/^[\w.-]+\.pdf$/i.test(name)) return send(res, 400, { error: "Bad file name" });
      path = `generated_letters/${name}`;
    } else return send(res, 400, { error: "Unknown kind" });
    const { buffer } = await getFile(path, { binary: true });
    if (!buffer) return send(res, 404, { error: "Not found. It may not have been generated yet." });
    res.statusCode = 200;
    res.setHeader("Content-Type", "application/pdf");
    res.setHeader("Content-Disposition", `inline; filename="${path.split("/").pop()}"`);
    res.setHeader("Cache-Control", "private, no-store");
    res.end(buffer);
  } catch (e) {
    send(res, 502, { error: String(e.message || e) });
  }
}
