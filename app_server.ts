import http from "node:http";
import { spawn } from "node:child_process";
import { readFile, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { tmpdir } from "node:os";
import crypto from "node:crypto";

const port = Number.parseInt(process.env.APP_PORT || "8790", 10);
const rootDir = process.cwd();
const dashboardPath = resolve(rootDir, "dashboard.html");
const trackingLogPath = resolve(rootDir, "tracking_log.json");

function jsonResponse(res: http.ServerResponse, status: number, payload: unknown): void {
  res.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

async function parseUpload(filename: string, contentBase64: string): Promise<Record<string, unknown>> {
  const safeName = filename.replace(/[^a-zA-Z0-9._-]/g, "_");
  const tempPath = resolve(tmpdir(), `${Date.now()}-${crypto.randomUUID()}-${safeName}`);
  try {
    await writeFile(tempPath, Buffer.from(contentBase64, "base64"));
    return await new Promise((resolvePromise, rejectPromise) => {
      const child = spawn("python3", [resolve(rootDir, "parse_upload.py"), tempPath], {
        cwd: rootDir,
        env: process.env,
        stdio: ["ignore", "pipe", "pipe"],
      });

      let stdout = "";
      let stderr = "";

      child.stdout.on("data", (chunk) => {
        stdout += chunk.toString();
      });
      child.stderr.on("data", (chunk) => {
        stderr += chunk.toString();
      });

      child.on("close", (code) => {
        if (code !== 0) {
          rejectPromise(new Error(stderr || stdout || `parser failed with code ${code}`));
          return;
        }
        resolvePromise(JSON.parse(stdout || "{}"));
      });
    });
  } finally {
    await rm(tempPath, { force: true });
  }
}

function runMailer(action: "preview" | "send", payload: Record<string, string>): Promise<Record<string, unknown>> {
  return new Promise((resolvePromise, rejectPromise) => {
    const env = {
      ...process.env,
      MAIL_PROFILE_URL: payload.profileUrl || "",
      MAIL_NAME_CH: payload.nameCh || "",
      MAIL_NAME_EN: payload.nameEn || "",
      MAIL_DIRECTION: payload.direction || "",
      MAIL_RECIPIENT: payload.recipient || "",
      MAIL_MODE: action,
      UTM_SOURCE: payload.utmSource || "",
      UTM_MEDIUM: payload.utmMedium || "",
      UTM_CAMPAIGN: payload.utmCampaign || "",
    };

    const child = spawn("node", ["--experimental-strip-types", resolve(rootDir, "send_email.ts")], {
      cwd: rootDir,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });

    child.on("close", (code) => {
      if (code !== 0) {
        rejectPromise(new Error(stderr || stdout || `mailer failed with code ${code}`));
        return;
      }

      const previewMatch = stdout.match(/Preview saved to (.+)/);
      resolvePromise({
        ok: true,
        mode: action,
        previewPath: previewMatch?.[1]?.trim() || "",
        output: stdout.trim(),
      });
    });
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || "/", `http://${req.headers.host || `127.0.0.1:${port}`}`);

  if (req.method === "GET" && url.pathname === "/") {
    const html = await readFile(dashboardPath, "utf-8");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(html);
    return;
  }

  if (req.method === "GET" && url.pathname === "/api/logs") {
    const raw = await readFile(trackingLogPath, "utf-8");
    jsonResponse(res, 200, JSON.parse(raw));
    return;
  }

  if (req.method === "GET" && url.pathname === "/preview") {
    const previewPath = url.searchParams.get("path");
    if (!previewPath || !previewPath.startsWith(rootDir)) {
      jsonResponse(res, 400, { error: "invalid preview path" });
      return;
    }
    const html = await readFile(previewPath, "utf-8");
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(html);
    return;
  }

  if (req.method === "POST" && (url.pathname === "/api/preview" || url.pathname === "/api/send")) {
    try {
      let body = "";
      for await (const chunk of req) {
        body += chunk.toString();
      }
      const payload = JSON.parse(body || "{}");
      const mode = url.pathname.endsWith("/send") ? "send" : "preview";
      const result = await runMailer(mode, payload);
      jsonResponse(res, 200, result);
    } catch (error) {
      jsonResponse(res, 500, { error: String(error) });
    }
    return;
  }

  if (req.method === "POST" && url.pathname === "/api/upload") {
    try {
      let body = "";
      for await (const chunk of req) {
        body += chunk.toString();
      }
      const payload = JSON.parse(body || "{}") as { filename?: string; contentBase64?: string };
      if (!payload.filename || !payload.contentBase64) {
        jsonResponse(res, 400, { error: "filename and contentBase64 are required" });
        return;
      }
      const result = await parseUpload(payload.filename, payload.contentBase64);
      jsonResponse(res, 200, result);
    } catch (error) {
      jsonResponse(res, 500, { error: String(error) });
    }
    return;
  }

  jsonResponse(res, 404, { error: "not_found" });
});

server.listen(port, "127.0.0.1", () => {
  console.log(`App console listening on http://127.0.0.1:${port}`);
});
