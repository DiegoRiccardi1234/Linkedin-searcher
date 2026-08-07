const { defineConfig } = require("@playwright/test");
const path = require("path");

const pythonExe = process.platform === "win32"
  ? `"${path.join(__dirname, ".venv", "Scripts", "python.exe")}"`
  : "python3";

// NOT 8000: that is the port the INSTALLED app listens on. With
// reuseExistingServer, a suite launched while Job Finder was open silently
// tested the installed bundle instead of this working tree — passing or failing
// for reasons that had nothing to do with the code under test, and writing test
// data into the user's real archive.
const PORT = 8123;

module.exports = defineConfig({
  testDir: path.join("tests", "e2e"),
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: `${pythonExe} -m uvicorn app.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
