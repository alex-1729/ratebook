'use strict';
// Run by Claude Code (through Electron's own Node: ELECTRON_RUN_AS_NODE=1), never by the app itself.
//   bridge.js mcp            an MCP server on stdin/stdout that offers the app's tools and passes each call to the app
//   bridge.js hook prompt    UserPromptSubmit hook: prints what the window shows, which Claude Code adds to the message
//   bridge.js hook pretool   PreToolUse hook for Read/Glob/Grep: asks the app whether the path is one the assistant may read
// The app is reached on 127.0.0.1:$IRV_PORT with $IRV_TOKEN; nothing else is contacted.
// Adapted from the bridge in the user's Tessera app.
const http = require('http');

function post(pathname, body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body || {}));
    const req = http.request({ host: '127.0.0.1', port: Number(process.env.IRV_PORT), path: pathname, method: 'POST',
      headers: { 'content-type': 'application/json', 'content-length': data.length, 'x-irv-token': process.env.IRV_TOKEN || '' } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8');
        if (res.statusCode !== 200) return reject(new Error(text || `The app answered ${res.statusCode}.`));
        try { resolve(JSON.parse(text)); } catch { reject(new Error('The app sent an answer that is not JSON.')); }
      });
    });
    req.on('error', () => reject(new Error('The app is not running, so its tools are not available.')));
    req.end(data);
  });
}

async function mcp() {
  const send = (msg) => process.stdout.write(JSON.stringify(msg) + '\n');
  const handle = async (msg) => {
    if (msg.id === undefined || msg.id === null) return; // a notification: nothing to answer
    const ok = (result) => send({ jsonrpc: '2.0', id: msg.id, result });
    try {
      if (msg.method === 'initialize') {
        return ok({ protocolVersion: (msg.params && msg.params.protocolVersion) || '2024-11-05', capabilities: { tools: {} },
          serverInfo: { name: 'rates', version: '0.1.0' } });
      }
      if (msg.method === 'ping') return ok({});
      if (msg.method === 'tools/list') return ok({ tools: (await post('/chat/tools')).tools });
      if (msg.method === 'tools/call') {
        return ok(await post('/chat/call', { name: msg.params && msg.params.name, args: (msg.params && msg.params.arguments) || {} }));
      }
      send({ jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: `Unknown method ${msg.method}` } });
    } catch (e) {
      if (msg.method === 'tools/call') ok({ content: [{ type: 'text', text: String((e && e.message) || e) }], isError: true });
      else send({ jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: String((e && e.message) || e) } });
    }
  };
  let buf = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    buf += chunk;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      let msg; try { msg = JSON.parse(line); } catch { continue; }
      void handle(msg);
    }
  });
  process.stdin.on('end', () => process.exit(0));
}

// What Claude Code passes a hook on stdin (JSON); empty if nothing arrives.
function stdinJson() {
  return new Promise((resolve) => {
    let buf = '';
    const done = () => { try { resolve(JSON.parse(buf)); } catch { resolve({}); } };
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (c) => { buf += c; });
    process.stdin.on('end', done);
    setTimeout(done, 1500);
  });
}

async function hook(kind) {
  const input = await stdinJson();
  if (kind === 'prompt') {
    try {
      const r = await post('/chat/hook/prompt', {});
      if (r.text) process.stdout.write(r.text + '\n');
    } catch { /* the app is gone: the message goes without the view line */ }
    process.exit(0);
  }
  // pretool: the app decides; if it cannot be asked, the read is refused.
  let decision = { allow: false, reason: 'The app could not be reached to check this path.' };
  try {
    decision = await post('/chat/hook/pretool', { tool: input.tool_name || '', input: input.tool_input || {} });
  } catch { /* refused */ }
  process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse',
    permissionDecision: decision.allow ? 'allow' : 'deny', permissionDecisionReason: decision.reason || '' } }) + '\n');
  process.exit(0);
}

const [mode, arg] = process.argv.slice(2);
if (mode === 'mcp') void mcp();
else if (mode === 'hook' && (arg === 'prompt' || arg === 'pretool')) void hook(arg);
else { process.stderr.write('usage: bridge.js mcp | hook prompt | hook pretool\n'); process.exit(2); }
