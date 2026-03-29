import http from "node:http";
import { appendTrackingEvent, initializeTrackingLog } from "./tracker.ts";

const config = {
  host: process.env.TRACKING_HOST || "127.0.0.1",
  port: Number.parseInt(process.env.TRACKING_PORT || "8787", 10),
  logPath: process.env.TRACKING_LOG_PATH || "./tracking_log.json",
};

const transparentGif = Buffer.from(
  "R0lGODlhAQABAAAAACwAAAAAAQABAAA=",
  "base64",
);

/**
 * Extract a best-effort client IP from headers/socket.
 */
function getClientIp(req: http.IncomingMessage): string {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string" && forwarded.trim()) {
    return forwarded.split(",")[0].trim();
  }
  return req.socket.remoteAddress || "";
}

/**
 * Write an open event and return a 1x1 transparent pixel.
 */
async function handleOpen(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<void> {
  const scholarId = url.searchParams.get("id") || "";
  const ts = url.searchParams.get("ts") || "";

  await appendTrackingEvent(config.logPath, "opened", {
    scholar_id: scholarId,
    opened_at: new Date().toISOString(),
    sent_at_hint: ts,
    ip: getClientIp(req),
    user_agent: req.headers["user-agent"] || "",
  });

  res.writeHead(200, {
    "Content-Type": "image/gif",
    "Content-Length": transparentGif.length,
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
  });
  res.end(transparentGif);
}

/**
 * Write a click event and redirect to the final target.
 */
async function handleClick(req: http.IncomingMessage, res: http.ServerResponse, url: URL): Promise<void> {
  const scholarId = url.searchParams.get("id") || "";
  const targetUrl = url.searchParams.get("target") || "https://www.bohrium.com";

  await appendTrackingEvent(config.logPath, "clicked", {
    scholar_id: scholarId,
    clicked_at: new Date().toISOString(),
    target_url: targetUrl,
    ip: getClientIp(req),
    user_agent: req.headers["user-agent"] || "",
  });

  res.writeHead(302, {
    Location: targetUrl,
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
  });
  res.end();
}

/**
 * Start the tracking server.
 */
async function main(): Promise<void> {
  await initializeTrackingLog(config.logPath);

  const server = http.createServer(async (req, res) => {
    try {
      const host = req.headers.host || `127.0.0.1:${config.port}`;
      const url = new URL(req.url || "/", `http://${host}`);

      if (req.method === "GET" && url.pathname === "/track/open") {
        await handleOpen(req, res, url);
        return;
      }

      if (req.method === "GET" && url.pathname === "/track/click") {
        await handleClick(req, res, url);
        return;
      }

      if (req.method === "GET" && url.pathname === "/healthz") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true }));
        return;
      }

      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "not_found" }));
    } catch (error) {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: String(error) }));
    }
  });

  server.listen(config.port, config.host, () => {
    console.log(`Tracking server listening on http://${config.host}:${config.port}`);
  });
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
