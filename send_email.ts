import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tls from "node:tls";

import {
  appendTrackingEvent,
  initializeTrackingLog,
} from "./tracker.ts";

type ScholarInput = {
  profileUrl: string;
  nameCh: string;
  nameEn: string;
  direction: string;
  instituteCh: string;
  instituteEn: string;
};

type ScholarPaperApiItem = {
  jumpUrl?: string;
  paperUrl?: string;
  enName?: string;
  zhName?: string;
  title?: string;
  publicationEnName?: string;
  publicationZhName?: string;
  coverDateStart?: string;
  authors?: string[];
  citationNums?: number;
};

type PaperCard = {
  title: string;
  journal: string;
  publicationDate: string;
  authors: string;
  citationCount: number;
  paperUrl: string;
};

type FetchScholarPapersResult = {
  papers: PaperCard[];
  total: number;
};

type ScholarStats = {
  hIndex: string;
  citations: string;
  papers: string;
};

const currentDir = dirname(fileURLToPath(import.meta.url));
const trackingLogPath = resolve(currentDir, "tracking_log.json");
const templatePath = resolve(currentDir, "email_template.html");
const env = process.env;
const commonChineseSurnames = new Set([
  "ai", "an", "bai", "bao", "ben", "bi", "bian", "cao", "cen", "cha", "chai", "chang", "chen", "cheng",
  "chi", "chong", "chu", "cui", "dai", "deng", "ding", "dong", "du", "duan", "fan", "fang", "feng", "fu",
  "gao", "gong", "gu", "guan", "guo", "han", "hao", "he", "hong", "hou", "hu", "hua", "huang", "hui", "huo",
  "ji", "jia", "jian", "jiang", "jin", "jing", "kang", "ke", "kong", "kuang", "lai", "lan", "lang", "lei",
  "li", "liang", "liao", "lin", "liu", "long", "lu", "luo", "lv", "ma", "mao", "meng", "mo", "ni", "nie",
  "ou", "pan", "pang", "peng", "qi", "qian", "qin", "qiu", "qu", "ran", "ren", "ruan", "shao", "she", "shen",
  "sheng", "shi", "song", "su", "sun", "sui", "tan", "tang", "tao", "tian", "tong", "tu", "wan", "wang", "wei",
  "wen", "wu", "xi", "xia", "xian", "xiao", "xie", "xin", "xing", "xiong", "xu", "xue", "yan", "yang", "yao",
  "ye", "yi", "yin", "ying", "you", "yu", "yuan", "yue", "yun", "zeng", "zha", "zhai", "zhan", "zhang", "zhao",
  "zhe", "zheng", "zhi", "zhong", "zhou", "zhu", "zhuang", "zou", "zuo",
]);
const commonPinyinTokens = new Set([
  "an", "ang", "ao", "ba", "bai", "ban", "bang", "bao", "bei", "ben", "biao", "bin", "bo", "cai", "can", "chang",
  "chen", "cheng", "chong", "chu", "chuan", "chun", "cong", "cun", "da", "dai", "dan", "dao", "de", "deng", "di",
  "dong", "du", "duo", "en", "er", "fan", "fang", "fei", "fen", "feng", "fu", "gai", "gang", "gao", "ge", "gen",
  "gong", "gu", "guang", "gui", "guo", "hai", "han", "hao", "he", "heng", "hong", "hou", "hu", "hua", "huai",
  "hui", "huo", "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jiong", "ju", "juan", "jun", "kai",
  "kan", "kang", "ke", "kong", "ku", "kun", "la", "lai", "lan", "lang", "lao", "lei", "li", "lian", "liang",
  "liao", "lie", "lin", "ling", "liu", "long", "lu", "luan", "lun", "luo", "lv", "ma", "mai", "man", "mao", "mei",
  "meng", "mi", "mian", "min", "ming", "mo", "mou", "na", "nan", "nao", "nei", "nian", "ning", "nuo", "ou", "pan",
  "peng", "qi", "qian", "qiang", "qiao", "qin", "qing", "qiong", "qiu", "quan", "que", "qun", "ran", "rang", "ren",
  "rong", "rui", "run", "san", "sang", "shan", "shao", "she", "shen", "sheng", "shi", "shou", "shu", "shuan",
  "shui", "si", "song", "su", "sui", "sun", "ta", "tai", "tan", "tang", "tao", "te", "tian", "ting", "tong", "tuo",
  "wan", "wang", "wei", "wen", "wu", "xi", "xia", "xian", "xiang", "xiao", "xie", "xin", "xing", "xiong", "xiu",
  "xu", "xuan", "xue", "xun", "yan", "yang", "yao", "ye", "yi", "yin", "ying", "yong", "you", "yu", "yuan", "yue",
  "yun", "zai", "zan", "zang", "ze", "zei", "zeng", "zhan", "zhang", "zhao", "zhe", "zhen", "zheng", "zhi", "zhong",
  "zhou", "zhu", "zhuang", "zi", "zong", "zou", "zu",
]);

const config = {
  scholarApi: {
    endpoint: `${env.SCHOLAR_API_BASE_URL || "https://your-scholar-api.example.com"}${env.SCHOLAR_API_PATH || "/api/v1/paper/scholar/paper"}`,
    body: {
      sort: 1,
      random: 0,
      isShowCitationNum: true,
    },
    webBaseUrl: env.SCHOLAR_API_WEB_BASE_URL || "https://www.bohrium.com",
  },
  smtp: {
    server: env.SMTP_SERVER || "YOUR_SMTP_SERVER",
    port: Number.parseInt(env.SMTP_PORT || "465", 10),
    user: env.SMTP_USER || "YOUR_SMTP_USER",
    password: env.SMTP_PASSWORD || "YOUR_SMTP_PASSWORD",
    fromName: env.SMTP_FROM_NAME || "Bohrium",
    replyTo: env.SMTP_REPLY_TO || "support@example.com",
  },
  tracking: {
    baseUrl: "",
  },
  utm: {
    source: process.env.UTM_SOURCE || "gkx",
    medium: process.env.UTM_MEDIUM || "scholar",
    campaign: process.env.UTM_CAMPAIGN || "edm1",
  },
  recipient: process.env.MAIL_RECIPIENT || "test@example.com",
  scholar: {
    profileUrl: process.env.MAIL_PROFILE_URL || "https://www.bohrium.com/scholar/EXAMPLE_ID",
    nameCh: process.env.MAIL_NAME_CH || "示例学者",
    nameEn: process.env.MAIL_NAME_EN || "Example Scholar",
    direction:
      process.env.MAIL_DIRECTION ||
      "AI for Science, Materials Science, Automation",
    instituteCh: "示例机构",
    instituteEn:
      "Example Institute",
  } satisfies ScholarInput,
};
const mailMode = process.env.MAIL_MODE || "send";

/**
 * Extract the Bohrium scholar ID from the profile URL.
 */
function extractScholarId(profileUrl: string): string {
  const parts = profileUrl.replace(/\/+$/, "").split("/").filter(Boolean);
  const scholarIndex = parts.findIndex((part) => part === "scholar");
  if (scholarIndex >= 0 && parts[scholarIndex + 1]) {
    return parts[scholarIndex + 1];
  }
  return parts[parts.length - 1] ?? "";
}

/**
 * Keep the open pixel as a harmless inline placeholder now that tracking is disabled.
 */
function resolveOpenTrackingUrl(): string {
  return "data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA=";
}

/**
 * Convert the English name into the Bohrium path slug format.
 */
function buildScholarSlug(nameEn: string, profileUrl: string): string {
  const normalizedName = nameEn
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^A-Za-z0-9_-]/g, "");
  if (normalizedName) {
    return normalizedName;
  }

  try {
    const url = new URL(profileUrl);
    const parts = url.pathname.split("/").filter(Boolean);
    const scholarIndex = parts.findIndex((part) => part === "scholar");
    if (scholarIndex >= 0 && parts[scholarIndex + 2]) {
      return parts[scholarIndex + 2];
    }
  } catch {
    return "";
  }
  return "";
}

/**
 * Decide whether the scholar should use the Chinese Bohrium page.
 */
function isLikelyChineseScholar(nameCh: string, nameEn: string): boolean {
  const tokens = nameEn
    .trim()
    .split(/\s+/)
    .map((token) => token.toLowerCase().replace(/[^a-z]/g, ""))
    .filter(Boolean);

  if (tokens.length >= 2) {
    const hasCamelCaseToken = nameEn
      .trim()
      .split(/\s+/)
      .some((token) => /^[A-Z][a-z]+[A-Z][A-Za-z]+$/.test(token));
    if (hasCamelCaseToken) {
      return false;
    }

    const surnameMatch =
      commonChineseSurnames.has(tokens[0] || "") || commonChineseSurnames.has(tokens[tokens.length - 1] || "");
    const pinyinCount = tokens.filter((token) => commonPinyinTokens.has(token) || commonChineseSurnames.has(token)).length;

    if (surnameMatch && pinyinCount >= Math.max(2, tokens.length - 1)) {
      return true;
    }

    if (pinyinCount >= 3 && tokens.length >= 3) {
      return true;
    }
  }

  if (nameCh.trim() && !nameEn.trim()) {
    return true;
  }

  return false;
}

/**
 * Normalize scholar profile URLs by scholar language and expected Bohrium path format.
 */
function normalizeProfileUrl(profileUrl: string, scholarId: string, nameCh: string, nameEn: string): string {
  const slug = buildScholarSlug(nameEn, profileUrl);
  const useChinesePage = isLikelyChineseScholar(nameCh, nameEn);
  const basePath = useChinesePage ? "/scholar" : "/en/scholar";
  const suffix = slug ? `/${slug}` : "";
  return `https://www.bohrium.com${basePath}/${scholarId}${suffix}`;
}

/**
 * Add UTM parameters directly to Bohrium URLs to avoid redirect-based click tracking.
 */
function addUtmParams(targetUrl: string, content: string): string {
  const url = new URL(targetUrl);
  url.searchParams.set("utm_source", config.utm.source);
  url.searchParams.set("utm_medium", config.utm.medium);
  url.searchParams.set("utm_campaign", config.utm.campaign);
  url.searchParams.set("utm_term", content);
  return url.toString();
}

/**
 * Keep only the first three research directions for tag display.
 */
function buildTags(direction: string): string[] {
  const tags = direction
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 3);

  while (tags.length < 3) {
    tags.push("Research Focus");
  }

  return tags;
}

/**
 * Convert API paper items into the shape used by the email renderer.
 */
function normalizePaper(item: ScholarPaperApiItem): PaperCard {
  const paperUrl = item.jumpUrl
    ? `https://${item.jumpUrl.replace(/^https?:\/\//, "").replace(/^\/+/, "")}`
    : item.paperUrl || config.scholarApi.webBaseUrl;

  return {
    title: item.enName || item.zhName || item.title || "Untitled Paper",
    journal: item.publicationEnName || item.publicationZhName || "Unknown Journal",
    publicationDate: item.coverDateStart || "Unknown Date",
    authors: (item.authors || []).join(", "),
    citationCount: item.citationNums || 0,
    paperUrl,
  };
}

/**
 * Fetch papers for the selected scholar from production.
 */
async function fetchScholarPapers(scholarId: string): Promise<FetchScholarPapersResult> {
  const response = await fetch(config.scholarApi.endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      scholarIds: [scholarId],
      ...config.scholarApi.body,
    }),
  });

  if (!response.ok) {
    throw new Error(`Scholar paper API failed with HTTP ${response.status}`);
  }

  const payload = (await response.json()) as {
    code?: number;
    data?: { items?: ScholarPaperApiItem[]; total?: number };
  };

  if (payload.code !== 0 || !payload.data?.items?.length) {
    throw new Error(`Scholar paper API returned no usable items: ${JSON.stringify(payload).slice(0, 400)}`);
  }

  return {
    papers: payload.data.items.map(normalizePaper),
    total: payload.data.total || payload.data.items.length,
  };
}

/**
 * Fetch scholar-level stats from the public Bohrium scholar page JSON-LD block.
 */
async function fetchScholarStats(profileUrl: string): Promise<ScholarStats> {
  const response = await fetch(profileUrl);
  if (!response.ok) {
    throw new Error(`Scholar profile page failed with HTTP ${response.status}`);
  }

  const html = await response.text();
  const match = html.match(/<script type="application\/ld\+json">({.*?"@type":"Person".*?})<\/script>/);
  if (!match) {
    return { hIndex: "--", citations: "--", papers: "--" };
  }

  const personJson = JSON.parse(match[1]) as {
    description?: string;
    additionalProperty?: Array<{ name?: string; value?: string }>;
  };

  const properties = new Map<string, string>();
  for (const item of personJson.additionalProperty || []) {
    if (item.name && item.value) {
      properties.set(item.name.toLowerCase(), item.value);
    }
  }

  const hIndex = properties.get("h-index") || "--";
  const citationsRaw = properties.get("citations") || "";
  const papersRaw = properties.get("papers") || "";

  const formatCompact = (value: string): string => {
    const num = Number.parseInt(value, 10);
    if (!Number.isFinite(num) || num <= 0) {
      return "--";
    }
    if (num >= 100000) {
      return `${Math.round(num / 1000)}K+`;
    }
    if (num >= 1000) {
      return `${Math.round(num / 100) / 10}K+`;
    }
    return String(num);
  };

  return {
    hIndex,
    citations: formatCompact(citationsRaw),
    papers: formatCompact(papersRaw),
  };
}

/**
 * Build a short personalized profile summary from directions and recent papers.
 */
function buildProfileSummary(tags: string[], papers: PaperCard[]): string {
  const firstPaper = papers[0];
  const secondJournal = papers[1]?.journal;

  if (firstPaper) {
    return [
      `主页已收录您在${tags[0]}、${tags[1]}等方向的代表性成果，`,
      `包括近期发表于《${firstPaper.journal}》的 ${firstPaper.title} 工作`,
      secondJournal ? `及《${secondJournal}》等权威期刊研究，` : "，",
      "可直接用于对外展示研究概况。",
    ].join(" ");
  }

  return `主页已整合您在${tags[0]}、${tags[1]}等方向的代表性成果，可直接用于对外展示研究概况。`;
}

/**
 * Build the opening paragraph of the email.
 */
function buildHeaderText(): string {
  return "玻尔已为您创建专属学者主页，系统整合了您的研究方向与代表性成果，帮助同行、学生及潜在合作者更便捷地了解您的学术贡献。";
}

/**
 * Render HTML paper cards. The first card gets a "Latest" badge.
 */
function renderPaperCards(papers: PaperCard[]): string {
  return papers
    .slice(0, 3)
    .map((paper, index) => {
      const latestBadge = index === 0 ? '<span class="badge-new">最新</span>' : "";
      const citation = paper.citationCount > 0 ? ` <span style="margin:0 4px;color:#d0d8e4;">·</span><span>引用 ${paper.citationCount}</span>` : "";
      const paperUrlWithUtm = addUtmParams(paper.paperUrl, `paper_card_${index + 1}`);

      return `
        <a href="${paperUrlWithUtm}" target="_blank" class="paper-card">
          <div class="paper-meta">
            <span>${paper.publicationDate}</span>
            <span style="margin:0 4px;color:#d0d8e4;">·</span>
            <span class="journal">${paper.journal}</span>
            ${latestBadge}
            ${citation}
          </div>
          <div class="paper-title">${paper.title}</div>
          <div class="paper-authors">${paper.authors}</div>
        </a>
      `;
    })
    .join("");
}

/**
 * Simple template renderer for the HTML email file.
 */
async function renderEmailTemplate(replacements: Record<string, string>): Promise<string> {
  const template = await readFile(templatePath, "utf-8");
  return Object.entries(replacements).reduce((html, [key, value]) => {
    return html.replaceAll(`{{${key}}}`, value);
  }, template);
}

/**
 * Build the RFC-822 message and send it over SMTP with AUTH LOGIN.
 */
async function sendHtmlEmail(params: { to: string; subject: string; html: string }): Promise<void> {
  const { server, port, user, password, fromName, replyTo } = config.smtp;
  const messageId = `<${Date.now()}.${Math.random().toString(16).slice(2)}@dp.tech>`;
  const headers = [
    `From: ${fromName} <${user}>`,
    `To: ${params.to}`,
    `Subject: ${params.subject}`,
    "MIME-Version: 1.0",
    'Content-Type: text/html; charset="UTF-8"',
    "Content-Transfer-Encoding: 8bit",
    `Reply-To: ${replyTo}`,
    `Message-ID: ${messageId}`,
    `Date: ${new Date().toUTCString()}`,
  ].join("\r\n");
  const rawMessage = `${headers}\r\n\r\n${params.html}\r\n.`;

  await new Promise<void>((resolvePromise, rejectPromise) => {
    const socket = tls.connect(port, server, { servername: server }, () => undefined);
    socket.setEncoding("utf8");

    const queue: Array<{ command: string; expect: number[] }> = [];
    let buffer = "";

    const enqueue = (command: string, expect: number[]) => {
      queue.push({ command, expect });
      if (queue.length === 1) {
        socket.write(command);
      }
    };

    const fail = (error: Error) => {
      socket.destroy();
      rejectPromise(error);
    };

    socket.on("error", fail);
    socket.on("data", (chunk: string) => {
      buffer += chunk;
      if (!/\r\n$/.test(buffer)) {
        return;
      }

      const lines = buffer.trim().split(/\r\n/);
      const lastLine = lines[lines.length - 1] || "";
      if (/^\d{3}-/.test(lastLine)) {
        return;
      }

      const code = Number.parseInt(lastLine.slice(0, 3), 10);
      buffer = "";

      if (queue.length === 0) {
        if (code !== 220) {
          fail(new Error(`SMTP handshake failed: ${lastLine}`));
          return;
        }

        enqueue(`EHLO dp.tech\r\n`, [250]);
        enqueue(`AUTH LOGIN\r\n`, [334]);
        enqueue(`${Buffer.from(user).toString("base64")}\r\n`, [334]);
        enqueue(`${Buffer.from(password).toString("base64")}\r\n`, [235]);
        enqueue(`MAIL FROM:<${user}>\r\n`, [250]);
        enqueue(`RCPT TO:<${params.to}>\r\n`, [250, 251]);
        enqueue("DATA\r\n", [354]);
        enqueue(`${rawMessage}\r\n`, [250]);
        enqueue("QUIT\r\n", [221]);
        return;
      }

      const current = queue.shift();
      if (!current) {
        return;
      }

      if (!current.expect.includes(code)) {
        fail(new Error(`SMTP command failed (${code}): ${lastLine}`));
        return;
      }

      if (queue.length > 0) {
        socket.write(queue[0].command);
      } else {
        socket.end();
        resolvePromise();
      }
    });
  });
}

/**
 * Main flow: fetch production papers, render HTML, send mail, and record the send event.
 */
async function main(): Promise<void> {
  await initializeTrackingLog(trackingLogPath);

  const scholar = config.scholar;
  const scholarId = extractScholarId(scholar.profileUrl);
  const timestamp = new Date().toISOString();
  const normalizedProfileUrl = normalizeProfileUrl(
    scholar.profileUrl,
    scholarId,
    scholar.nameCh,
    scholar.nameEn,
  );

  try {
    const [{ papers, total }, stats] = await Promise.all([
      fetchScholarPapers(scholarId),
      fetchScholarStats(normalizedProfileUrl),
    ]);
    const tags = buildTags(scholar.direction);
    const openTrackingUrl = resolveOpenTrackingUrl();
    const profileUrlWithUtm = addUtmParams(normalizedProfileUrl, "main_cta");
    const html = await renderEmailTemplate({
      GREETING: `${scholar.nameCh}教授，您好！`,
      HEADER_TEXT: buildHeaderText(),
      NAME_CH: scholar.nameCh,
      TAG_1: tags[0],
      TAG_2: tags[1],
      TAG_3: tags[2],
      PROFILE_SUMMARY: buildProfileSummary(tags, papers),
      OPEN_TRACKING_URL: openTrackingUrl,
      CLICK_TRACKING_URL: profileUrlWithUtm,
      PAPER_CARDS: renderPaperCards(papers),
      TOTAL_CITATIONS: stats.citations,
      H_INDEX: stats.hIndex,
      PAPER_COUNT: stats.papers !== "--" ? stats.papers : total > 999 ? `${Math.round(total / 100) / 10}K+` : String(total),
    });

    const outputPreviewPath = resolve(currentDir, "outputs", "preview_huiming_cheng_prod.html");
    await writeFile(outputPreviewPath, html, "utf-8");

    if (mailMode === "send") {
      await sendHtmlEmail({
        to: config.recipient,
        subject: `${scholar.nameCh}教授，您的玻尔专属学者主页已生成`,
        html,
      });

      await appendTrackingEvent(trackingLogPath, "sent", {
        scholar_id: scholarId,
        email: config.recipient,
        sent_at: timestamp,
        status: "success",
        error_msg: "",
      });

      console.log(`Email sent successfully to ${config.recipient}`);
    } else {
      console.log("Preview generated only (MAIL_MODE=preview)");
    }
    console.log(`Preview saved to ${outputPreviewPath}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (mailMode === "send") {
      await appendTrackingEvent(trackingLogPath, "sent", {
        scholar_id: extractScholarId(config.scholar.profileUrl),
        email: config.recipient,
        sent_at: timestamp,
        status: "fail",
        error_msg: message,
      });
    }
    throw error;
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
