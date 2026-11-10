#!/usr/bin/env node
// Drive the MCP server over stdio against a running FreeCAD — the end-to-end check
// that pytest cannot do, because the addon lives inside the GUI process.
//
//   node scripts/smoke_test.mjs                 battery, screenshots into out/
//   node scripts/smoke_test.mjs list            tool names
//   node scripts/smoke_test.mjs call get_view '{"view_name":"Top","focus_object":"Box"}'
//   node scripts/smoke_test.mjs code 'import FreeCAD; print(list(FreeCAD.listDocuments()))'
//
// Server command defaults to this checkout; override with FREECAD_MCP_CMD.
// FreeCAD must be running with the RPC server started.
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const WIN = process.platform === "win32";
const ROOT = path.resolve(import.meta.dirname, "..");
const CMD = process.env.FREECAD_MCP_CMD ?? "uv run --locked freecad-mcp";
const OUT = path.join(process.cwd(), "out");
const TIMEOUT = Number(process.env.SMOKE_TIMEOUT ?? 300000);

const proc = WIN
  ? spawn(CMD, { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"], shell: true })
  : spawn("sh", ["-c", CMD], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] });

let buffer = "", stderr = "", id = 0;
const waiting = new Map();
proc.stdout.setEncoding("utf8");
proc.stdout.on("data", (chunk) => {
  buffer += chunk;
  for (;;) {
    const at = buffer.indexOf("\n");
    if (at < 0) break;
    const line = buffer.slice(0, at).trim();
    buffer = buffer.slice(at + 1);
    if (!line) continue;
    let message; try { message = JSON.parse(line); } catch { continue; }
    if (message.jsonrpc === "2.0" && waiting.has(message.id)) {
      const settle = waiting.get(message.id); waiting.delete(message.id);
      message.error ? settle.reject(new Error(JSON.stringify(message.error))) : settle.resolve(message.result);
    }
  }
});
proc.stderr.setEncoding("utf8");
proc.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-3000); });

const rpc = (method, params, ms = TIMEOUT) => new Promise((resolve, reject) => {
  const mid = ++id;
  waiting.set(mid, { resolve, reject });
  setTimeout(() => { if (waiting.has(mid)) { waiting.delete(mid); reject(new Error(`${method} timed out after ${ms}ms`)); } }, ms);
  proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: mid, method, params }) + "\n");
});

async function call(tool, params = {}, ms) {
  const result = await rpc("tools/call", { name: tool, arguments: params }, ms);
  const text = (result.content ?? []).filter((c) => c.type === "text").map((c) => c.text).join("\n");
  const files = (result.content ?? []).filter((c) => c.type === "image").map((image, i) => {
    mkdirSync(OUT, { recursive: true });
    const file = path.join(OUT, `${tool}_${Date.now()}_${i}.${(image.mimeType || "image/png").split("/")[1]}`);
    writeFileSync(file, Buffer.from(image.data, "base64"));
    return file;
  });
  return { text, files, isError: !!result.isError };
}

const report = (label, r, limit = 900) => {
  console.log(`\n### ${label}${r.isError ? "  [ERROR]" : ""}`);
  console.log(r.text.slice(0, limit) + (r.text.length > limit ? "\n…(truncated)" : ""));
  if (r.files.length) console.log("image saved: " + r.files.join(", "));
};

const mode = process.argv[2];
let failures = 0;
const step = async (label, tool, params, ms) => {
  const r = await call(tool, params, ms);
  if (r.isError) failures += 1;
  report(label, r);
};

try {
  const init = await rpc("initialize", { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "smoke", version: "0" } });
  proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) + "\n");
  const tools = (await rpc("tools/list", {})).tools;
  console.log(`server=${init.serverInfo?.name} tools=${tools.length}`);

  if (mode === "list") {
    for (const t of tools) console.log(` - ${t.name}(${Object.keys(t.inputSchema?.properties ?? {}).join(", ")})`);
  } else if (mode === "call") {
    report(`call ${process.argv[3]}`, await call(process.argv[3], JSON.parse(process.argv[4] ?? "{}")));
  } else if (mode === "code") {
    report("execute_code", await call("execute_code", { code: process.argv[3], include_screenshot: false }));
  } else {
    const doc = "Smoke" + Date.now().toString().slice(-4);
    await step("get_rpc_status", "get_rpc_status");
    await step("create_document", "create_document", { name: doc });
    await step("create_object", "create_object", {
      doc_name: doc, obj_type: "Part::Box", obj_name: "Box",
      obj_properties: { Length: 30, Width: 20, Height: 10 }, include_screenshot: false,
    });
    await step("edit_object", "edit_object", { doc_name: doc, obj_name: "Box", obj_properties: { Length: 45 }, include_screenshot: false });
    await step("get_object", "get_object", { doc_name: doc, obj_name: "Box", include_screenshot: false });
    await step("get_view Isometric", "get_view", { view_name: "Isometric", width: 800, height: 600 });
    await step("execute_code", "execute_code", {
      code: `import FreeCAD\nd = FreeCAD.getDocument('${doc}')\nprint('objects', [o.Name for o in d.Objects], 'length', d.getObject('Box').Length)`,
      include_screenshot: false,
    });
    // Headless spawns a whole second FreeCAD process; budget for it.
    await step("execute_code_headless", "execute_code_headless", {
      code: "import FreeCAD\nd = FreeCAD.newDocument('HeadlessCheck')\nd.addObject('Part::Box', 'HB').Length = 7\nd.recompute()\nprint('headless ok', [o.Name for o in d.Objects])",
      timeout: 240,
    }, 300000);
    await step("cleanup", "execute_code", {
      code: `import FreeCAD\nfor n in list(FreeCAD.listDocuments()):\n    if n.startswith('Smoke') or n.startswith('HeadlessCheck'): FreeCAD.closeDocument(n)\nprint('remaining', list(FreeCAD.listDocuments()))`,
      include_screenshot: false,
    });
    console.log(`\n${failures === 0 ? "all calls succeeded" : failures + " call(s) failed"}`);
  }
} catch (error) {
  console.log("FAIL: " + error.message);
  if (stderr) console.log("stderr: " + stderr.split("\n").slice(-6).join(" | "));
  failures += 1;
} finally {
  try { proc.stdin?.end(); } catch {}
  if (WIN) { try { spawnSync("taskkill", ["/F", "/T", "/PID", String(proc.pid)], { stdio: "ignore" }); } catch {} }
  else proc.kill();
}
process.exitCode = failures ? 1 : 0;
