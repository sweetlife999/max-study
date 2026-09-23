import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');

function braceDepthAt(source: string, index: number): number {
  let depth = 0;
  for (const character of source.slice(0, index)) {
    if (character === '{') depth += 1;
    if (character === '}') depth -= 1;
  }
  return depth;
}

it('keeps the mobile event-form media query at the stylesheet top level', () => {
  const mobileMedia = /@media\s*\(\s*max-width\s*:\s*600px\s*\)/;
  const index = styles.search(mobileMedia);

  expect(index).toBeGreaterThanOrEqual(0);
  expect(braceDepthAt(styles, index)).toBe(0);
});
