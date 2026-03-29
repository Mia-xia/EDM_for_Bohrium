import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

export type TrackingKind = "sent" | "opened" | "clicked";

export interface TrackingLog {
  sent: Array<Record<string, unknown>>;
  opened: Array<Record<string, unknown>>;
  clicked: Array<Record<string, unknown>>;
}

/**
 * Ensure the tracking log exists with the expected top-level structure.
 */
export async function initializeTrackingLog(logPath: string): Promise<void> {
  await mkdir(dirname(logPath), { recursive: true });
  try {
    await readFile(logPath, "utf-8");
  } catch {
    const initialLog: TrackingLog = { sent: [], opened: [], clicked: [] };
    await writeFile(logPath, JSON.stringify(initialLog, null, 2), "utf-8");
  }
}

/**
 * Append a tracking event to the local JSON log.
 */
export async function appendTrackingEvent(
  logPath: string,
  kind: TrackingKind,
  payload: Record<string, unknown>,
): Promise<void> {
  await initializeTrackingLog(logPath);
  const raw = await readFile(logPath, "utf-8");
  const parsed = JSON.parse(raw) as TrackingLog;
  parsed[kind].push(payload);
  await writeFile(logPath, JSON.stringify(parsed, null, 2), "utf-8");
}

/**
 * Build the transparent pixel URL used to track opens.
 */
export function buildOpenTrackingUrl(baseUrl: string, scholarId: string, timestamp: string): string {
  const url = new URL("/track/open", baseUrl);
  url.searchParams.set("id", scholarId);
  url.searchParams.set("ts", timestamp);
  return url.toString();
}

/**
 * Build the redirect URL used to track CTA clicks.
 */
export function buildClickTrackingUrl(baseUrl: string, scholarId: string, targetUrl: string): string {
  const url = new URL("/track/click", baseUrl);
  url.searchParams.set("id", scholarId);
  url.searchParams.set("target", targetUrl);
  return url.toString();
}
