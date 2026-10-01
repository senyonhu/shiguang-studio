import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { Compiler } from 'inkjs/full';
const story = new Compiler(readFileSync('story/tower.ink', 'utf8')).Compile();
mkdirSync('src/generated', { recursive: true });
writeFileSync('src/generated/story.json', story.ToJson());
console.log('Ink story compiled using the same inkjs version as the runtime.');
