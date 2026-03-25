const path = require("path");
const { spawn } = require("child_process");

const python = path.join(__dirname, "tools", "python311", "python.exe");
const script = path.join(__dirname, "backend_server.py");
const args = [script];

if (process.argv.includes("--open")) {
  args.push("--open");
}

const child = spawn(python, args, {
  cwd: __dirname,
  stdio: "inherit",
  windowsHide: false,
});

child.on("exit", code => {
  process.exit(code ?? 0);
});

child.on("error", error => {
  console.error("Failed to launch backend:", error.message);
  process.exit(1);
});
