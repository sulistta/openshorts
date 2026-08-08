import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { pythonCommand } from './python.mjs';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const dashboardDir = resolve(scriptsDir, '..');
const projectRoot = resolve(dashboardDir, '..');
const isWindows = process.platform === 'win32';
const python = pythonCommand(projectRoot);
const dependencyCheck = spawnSync(
  python.command,
  [...python.prefix, '-c', 'import dotenv, uvicorn'],
  { cwd: projectRoot, encoding: 'utf8' },
);

if (dependencyCheck.status !== 0) {
  console.error('\nOpenShorts backend dependencies are not installed.');
  console.error('Run: npm run setup:backend\n');
  process.exit(dependencyCheck.status ?? 1);
}

async function viteIsAlreadyRunning() {
  try {
    const response = await fetch('http://127.0.0.1:1420/@vite/client', {
      signal: AbortSignal.timeout(750),
    });
    return response.ok;
  } catch {
    return false;
  }
}

const backend = spawn(
  python.command,
  [...python.prefix, resolve(projectRoot, 'desktop/backend.py'), '--port', '37831', '--data-dir', projectRoot],
  { cwd: projectRoot, stdio: 'inherit' },
);
const npm = isWindows ? 'npm.cmd' : 'npm';
const reuseVite = await viteIsAlreadyRunning();
const vite = reuseVite
  ? null
  : spawn(
    npm,
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', '1420', '--strictPort'],
    { cwd: dashboardDir, stdio: 'inherit' },
  );

if (reuseVite) {
  console.log('Reusing the Vite server already running at http://127.0.0.1:1420');
}

let stopping = false;
function stop(exitCode = 0) {
  if (stopping) return;
  stopping = true;
  backend.kill();
  vite?.kill();
  process.exitCode = exitCode;
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => stop());
}

backend.on('exit', (code) => {
  if (!stopping) stop(code || 1);
});
vite?.on('exit', (code) => {
  if (!stopping) stop(code || 1);
});
