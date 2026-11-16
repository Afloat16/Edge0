import { execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const repository = path.resolve(process.env.EDGE0_VALIDATION_REPOSITORY ?? 'edge0-working');
const desktop = path.join(repository, 'edge0-infer/examples/desktop');
const require = createRequire(path.join(desktop, 'package.json'));
const { createServer: createViteServer, transformWithOxc } = await import(pathToFileURL(require.resolve('vite')));
const original = execFileSync('git', ['show', 'f6bfa5546bcf89919f86946ec669bcc7f4cd526d:edge0-infer/examples/desktop/src/chat.ts'], { cwd: repository, encoding: 'utf8' });
const vite = await createViteServer({
  root: desktop,
  server: { middlewareMode: true },
  plugins: [{
    name: 'original-chat-source',
    resolveId(id) { if (id === 'validation:before.ts') return '\0validation:before.ts'; },
    async load(id) {
      if (id === '\0validation:before.ts') {
        const source = original.replace("'../../shared/app.json'", JSON.stringify(path.join(desktop, '../shared/app.json')));
        return (await transformWithOxc(source, path.join(desktop, 'src/original.ts'), { lang: 'ts' })).code;
      }
    },
  }],
});
const event = value => `data: ${JSON.stringify(value)}\n\n`;
const body = event({ choices: [{ delta: { content: 'complete answer' } }] }) +
  event({ choices: [], usage: { prompt_tokens: 7, completion_tokens: 3 } }) + 'data: [DONE]\n\n';
const server = createServer((request, response) => {
  response.writeHead(200, { 'Content-Type': 'text/event-stream' });
  response.write(body);
  // Deliberately leave the HTTP response open after its terminal SSE event.
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const results = [];
try {
  for (const [label, id] of [['before', 'validation:before.ts'], ['after', '/src/chat.ts']]) {
    const client = await vite.ssrLoadModule(id);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 500);
    let message, error = null;
    const started = performance.now();
    try {
      await client.streamChat(fetch, base, client.buildRequest([], false, 'fixture'), controller.signal, next => { message = next; });
    } catch (failure) { error = failure.name + ': ' + failure.message; }
    finally { clearTimeout(timer); }
    results.push({ label, elapsed_ms: performance.now() - started, error, message });
  }
  console.log(JSON.stringify({ transport: 'native Node HTTP server and fetch', server_sent_done_without_eof: true, results }, null, 2));
  if (!results[0].error?.startsWith('AbortError:') || results[1].error || results[1].message.content !== 'complete answer' || results[1].message.metrics.generated_count !== 3) process.exitCode = 1;
} finally {
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
  await vite.close();
}
