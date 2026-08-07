// Opening a posting is the last thing this app sees before the user applies
// somewhere else, and until now it saw nothing at all: every link was a plain
// <a target="_blank">, so the archive could not tell "never looked at it" from
// "applied three weeks ago". Recording the open is what lets a confirmation
// email be matched back to an offer later — the alternative is searching a whole
// mailbox for anything that looks like a confirmation.
//
// One delegated listener instead of a handler per rendering site. The five
// places that render a posting link only carry a data-job-link tag; everything
// that happens next is here.

import { api } from "./helpers.js";

let _deps = {
  onOpened: () => {},
};

export function initApplyWatch(deps) {
  _deps = { ..._deps, ...deps };
}

export function wireApplyWatch() {
  // Capture phase: the jobs table attaches its own row handlers, and a future
  // stopPropagation there would silently stop the tracking without breaking
  // anything visible — the worst kind of regression to find.
  document.addEventListener("click", _onOpen, true);
  // Middle-click opens in a new tab and never fires "click".
  document.addEventListener("auxclick", _onOpen, true);
}

function _onOpen(event) {
  if (event.type === "auxclick" && event.button !== 1) return;
  const anchor = event.target && event.target.closest ? event.target.closest("a[data-job-link]") : null;
  if (!anchor) return;
  const jobId = Number(anchor.dataset.jobLink);
  if (!jobId) return;
  // No preventDefault and no await before returning: the browser must open the
  // tab in this same task, or the popup blocker takes it. The request is
  // fire-and-forget, and the page is not unloading (target="_blank"), so a
  // normal fetch survives without needing sendBeacon.
  api(`/api/jobs/${jobId}/link-opened`, { method: "POST", body: "{}" })
    .then(() => _deps.onOpened(jobId))
    // Deliberately silent: failing to note an open must never put an error in
    // front of someone who just clicked a link, and the e2e suite asserts that
    // no view logs a console error.
    .catch(() => {});
}
