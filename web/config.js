// Public configuration for the dashboard. Nothing here is secret.
//
// The username and password are NOT stored in this file or anywhere in the code.
// They are GitHub secrets (DASHBOARD_USERNAME / DASHBOARD_PASSWORD) used at deploy time
// to encrypt the leads into data.enc.json; the browser decrypts it with what you type.
window.APP_CONFIG = {
  // Shown on the login screen and in the header.
  APP_NAME: "Influencer Lead Finder",

  // Encrypted data file produced by `python -m pipeline.export_site` (relative URL).
  DATA_URL: "data.enc.json",

  // Lock the dashboard (wipe decrypted data from memory) after this many idle minutes.
  IDLE_LOCK_MINUTES: 60,
};
