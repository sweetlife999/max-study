import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createClient } from '@hey-api/openapi-ts';

const webRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const input = join(webRoot, '..', 'docs', 'openapi.json');
const output = join(webRoot, 'src', 'api', 'generated');

async function generate(outputPath) {
  await createClient({
    input,
    output: {
      path: outputPath,
      clean: true,
      entryFile: false,
      postProcess: [
        {
          name: 'Prettier',
          command: 'prettier',
          args: ['--config', join(webRoot, '.prettierrc.json'), '--write', '{{path}}'],
        },
      ],
      tsConfigPath: join(webRoot, 'tsconfig.app.json'),
    },
    plugins: ['@hey-api/typescript'],
    logs: { file: false, level: 'silent' },
  });
}

async function filesIn(directory, current = directory) {
  const files = new Map();
  let entries;
  try {
    entries = await readdir(current, { withFileTypes: true });
  } catch (error) {
    if (error.code === 'ENOENT') return files;
    throw error;
  }

  for (const entry of entries) {
    const path = join(current, entry.name);
    if (entry.isDirectory()) {
      for (const [name, contents] of await filesIn(directory, path)) files.set(name, contents);
    } else if (entry.isFile()) {
      files.set(relative(directory, path), await readFile(path));
    }
  }
  return files;
}

async function check() {
  const temporaryOutput = await mkdtemp(join(tmpdir(), 'campus-api-types-'));
  try {
    await generate(temporaryOutput);
    const [expected, actual] = await Promise.all([filesIn(temporaryOutput), filesIn(output)]);
    const stale = [...new Set([...expected.keys(), ...actual.keys()])].sort().filter((name) => {
      const expectedContents = expected.get(name);
      const actualContents = actual.get(name);
      return (
        expectedContents === undefined ||
        actualContents === undefined ||
        !expectedContents.equals(actualContents)
      );
    });

    if (stale.length > 0) {
      throw new Error(
        `Generated API types are stale (${stale.join(', ')}). Run \`npm run api:types\`.`,
      );
    }
  } finally {
    await rm(temporaryOutput, { force: true, recursive: true });
  }
}

const mode = process.argv[2];
if (mode === undefined) {
  await generate(output);
} else if (mode === '--check') {
  await check();
} else {
  throw new Error(`Unknown argument: ${mode}`);
}
