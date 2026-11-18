'use strict';
// A local server for the bridge only: 127.0.0.1, a random port, and a token made for this launch.
const http = require('http');

// handlers: { tools(), call(name, args), promptLine(), pretool(tool, input) }
function startServer(token, handlers) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const answer = (code, obj) => { const b = Buffer.from(JSON.stringify(obj)); res.writeHead(code, { 'content-type': 'application/json', 'content-length': b.length }); res.end(b); };
      if (req.method !== 'POST' || req.headers['x-irv-token'] !== token) return answer(403, { error: 'Forbidden' });
      const chunks = [];
      let size = 0;
      req.on('data', (c) => { size += c.length; if (size > 1e6) req.destroy(); else chunks.push(c); });
      req.on('end', async () => {
        let body = {};
        try { body = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'); } catch { return answer(400, { error: 'Bad request' }); }
        try {
          if (req.url === '/chat/tools') return answer(200, { tools: handlers.tools() });
          if (req.url === '/chat/call') return answer(200, await handlers.call(String(body.name), body.args));
          if (req.url === '/chat/hook/prompt') return answer(200, { text: handlers.promptLine() });
          if (req.url === '/chat/hook/pretool') return answer(200, handlers.pretool(String(body.tool), body.input));
          answer(404, { error: 'Not found' });
        } catch (e) {
          answer(500, { error: String((e && e.message) || e) });
        }
      });
    });
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({ server, port: server.address().port }));
  });
}
module.exports = { startServer };
