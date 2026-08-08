import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { pythonCommand, torchInstallArgs } from './python.mjs';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptsDir, '../..');
const python = pythonCommand(projectRoot);

function run(args) {
  const result = spawnSync(python.command, [...python.prefix, ...args], {
    cwd: projectRoot,
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

run(torchInstallArgs());
run(['-m', 'pip', 'install', '-r', 'desktop/requirements-build.txt']);
run([resolve(projectRoot, 'desktop/build_sidecar.py')]);
