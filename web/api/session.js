import { send, isAuthed, missingConfig } from "./_lib.js";

export default function handler(req, res) {
  const missing = missingConfig();
  send(res, 200, { authed: !missing.length && isAuthed(req), mode: "hosted", missing });
}
