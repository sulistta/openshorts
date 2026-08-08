import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { projectVenvPython, pythonCommand, torchInstallArgs } from './python.mjs';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptsDir, '../..');
const venvPython = projectVenvPython(projectRoot);

function run(command, args) {
  const result = spawnSync(command, args, { cwd: projectRoot, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}

if (!existsSync(venvPython)) {
  const systemPython = pythonCommand(projectRoot);
  run(systemPython.command, [...systemPython.prefix, '-m', 'venv', '.venv']);
}

run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip']);
run(venvPython, torchInstallArgs());
run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt']);
