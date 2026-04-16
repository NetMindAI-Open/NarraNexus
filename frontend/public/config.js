// Runtime configuration — injected by the deploy pipeline at container start.
//
// The deploy repo's frontend entrypoint overwrites this file with the
// real values before nginx starts serving requests. Keeping a default
// here means the same built bundle works in dev (this empty config →
// user picks mode) and in cloud deploys (deploy script fills it in).
//
// Shape:
//   mode:   null  → let the user choose (desktop / dev)
//           "cloud" → force cloud-web, block mode-select
//           "local" → force local mode
//   apiUrl: ""    → same-origin (nginx proxy)
//           absolute URL (e.g. "https://api.example.com") → call that host
window.__NARRANEXUS_CONFIG__ = {
  mode: null,
  apiUrl: ""
};
