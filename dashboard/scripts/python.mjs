import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

export function pythonCommand(projectRoot) {
  const isWindows = process.platform === 'win32';
  if (process.env.PYTHON) {
    return { command: process.env.PYTHON, prefix: [] };
  }

  const venvPython = resolve(
    projectRoot,
    isWindows ? '.venv/Scripts/python.exe' : '.venv/bin/python',
  );
  if (existsSync(venvPython)) {
    return { command: venvPython, prefix: [] };
  }

  return {
    command: isWindows ? 'py' : 'python3',
    prefix: isWindows ? ['-3'] : [],
  };
}

export function projectVenvPython(projectRoot) {
  return resolve(
    projectRoot,
    process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python',
  );
}

export function torchInstallArgs() {
  // PyPI's Linux wheel pulls the CUDA runtime even on machines without an
  // NVIDIA GPU. Desktop development should work everywhere by default, so use
  // PyTorch's CPU wheel on Linux and Windows. macOS ships its appropriate
  // CPU/MPS wheel through the default index. Set OPENSHORTS_TORCH_INDEX to a
  // CUDA or ROCm index when building for a machine that needs acceleration.
  const indexUrl = process.env.OPENSHORTS_TORCH_INDEX
    ?? (process.platform === 'darwin' ? null : 'https://download.pytorch.org/whl/cpu');
  const args = ['-m', 'pip', 'install'];
  if (indexUrl) args.push('--index-url', indexUrl);
  args.push('torch==2.11.0', 'torchvision==0.26.0');
  return args;
}
