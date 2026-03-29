#!/usr/bin/env python3
"""Batch scrape scholar emails from an XLSX and export review-friendly CSV files.

The script avoids third-party dependencies so it can run in restricted environments.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
XML_NS = {"a": NS_MAIN, "r": NS_REL}

EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

OBFUSCATED_PATTERNS = [
    (re.compile(r"(?i)\s*\(at\)\s*|\s*\[at\]\s*|\s+at\s+"), "@"),
    (re.compile(r"(?i)\s*\(dot\)\s*|\s*\[dot\]\s*|\s+dot\s+"), "."),
]

BAD_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "example",
    "sample",
    "privacy",
    "webmaster",
    "support",
    "contact",
    "service",
    "admin",
    "info",
    "email",
    "name",
    "firstname",
    "lastname",
    "error-lite",
}

HIGH_VALUE_HINTS = {
    "faculty",
    "teacher",
    "people",
    "person",
    "staff",
    "profile",
    "team",
    "about",
    "contact",
    "member",
    "cv",
}

BAD_EMAIL_DOMAINS = {
    "bohrium.com",
    "deepmd.net",
    "duckduckgo.com",
}

# High-frequency institution -> domain hints used for targeted site queries.
INSTITUTION_DOMAIN_HINTS = {
    "sun yat-sen university": "sysu.edu.cn",
    "sunyat-sen university": "sysu.edu.cn",
    "中山大学": "sysu.edu.cn",
    "southern university of science and technology": "sustech.edu.cn",
    "南方科技大学": "sustech.edu.cn",
    "peking university": "pku.edu.cn",
    "北京大学": "pku.edu.cn",
    "tsinghua university": "tsinghua.edu.cn",
    "清华大学": "tsinghua.edu.cn",
    "shenzhen university": "szu.edu.cn",
    "深圳大学": "szu.edu.cn",
    "the chinese university of hong kong shenzhen": "cuhk.edu.cn",
    "香港中文大学深圳": "cuhk.edu.cn",
}


@dataclass
class Candidate:
    email: str
    source_url: str
    score: int


@dataclass
class ScholarResult:
    row: Dict[str, str]
    status: str
    extracted_email: str
    source_url: str
    confidence: int
    note: str
    candidates: List[Candidate]


class XlsxReader:
    def __init__(self, xlsx_path: Path):
        self.xlsx_path = xlsx_path

    def read_first_sheet(self) -> List[Dict[str, str]]:
        with zipfile.ZipFile(self.xlsx_path) as zf:
            shared_strings = self._read_shared_strings(zf)
            sheet_path = self._get_first_sheet_path(zf)
            rows = self._read_sheet_rows(zf, sheet_path, shared_strings)

        if not rows:
            return []

        headers = [str(v).strip() for v in rows[0]]
        records: List[Dict[str, str]] = []
        for row in rows[1:]:
            if not any(str(cell).strip() for cell in row):
                continue
            item: Dict[str, str] = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                value = row[idx] if idx < len(row) else ""
                item[header] = str(value).strip()
            records.append(item)
        return records

    def _read_shared_strings(self, zf: zipfile.ZipFile) -> List[str]:
        if "xl/sharedStrings.xml" not in zf.namelist():
            return []
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        strings: List[str] = []
        for si in root.findall("a:si", XML_NS):
            text = "".join((node.text or "") for node in si.findall(".//a:t", XML_NS))
            strings.append(text)
        return strings

    def _get_first_sheet_path(self, zf: zipfile.ZipFile) -> str:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        first_sheet = wb.find(".//a:sheets/a:sheet", XML_NS)
        if first_sheet is None:
            raise ValueError("XLSX has no sheets")
        rid = first_sheet.attrib.get(f"{{{NS_REL}}}id")
        if not rid or rid not in rid_to_target:
            raise ValueError("Cannot resolve first worksheet path")
        path = rid_to_target[rid]
        return path if path.startswith("xl/") else f"xl/{path}"

    def _read_sheet_rows(
        self,
        zf: zipfile.ZipFile,
        sheet_path: str,
        shared_strings: Sequence[str],
    ) -> List[List[str]]:
        root = ET.fromstring(zf.read(sheet_path))
        rows: List[List[str]] = []
        for row in root.findall(".//a:sheetData/a:row", XML_NS):
            row_data: Dict[int, str] = {}
            max_idx = -1
            for cell in row.findall("a:c", XML_NS):
                ref = cell.attrib.get("r", "A1")
                idx = self._col_idx(ref)
                value = self._cell_value(cell, shared_strings)
                row_data[idx] = value
                if idx > max_idx:
                    max_idx = idx
            if max_idx < 0:
                rows.append([])
            else:
                rows.append([row_data.get(i, "") for i in range(max_idx + 1)])
        return rows

    @staticmethod
    def _col_idx(cell_ref: str) -> int:
        letters = ""
        for ch in cell_ref:
            if ch.isalpha():
                letters += ch
            else:
                break
        idx = 0
        for ch in letters.upper():
            idx = idx * 26 + (ord(ch) - 64)
        return max(idx - 1, 0)

    @staticmethod
    def _cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
        cell_type = cell.attrib.get("t", "")
        if cell_type == "inlineStr":
            is_node = cell.find("a:is", XML_NS)
            if is_node is None:
                return ""
            return "".join((node.text or "") for node in is_node.findall(".//a:t", XML_NS))

        val_node = cell.find("a:v", XML_NS)
        if val_node is None or val_node.text is None:
            return ""
        raw = val_node.text

        if cell_type == "s":
            if raw.isdigit():
                idx = int(raw)
                return shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
            return ""

        return raw


class EmailScraper:
    def __init__(self, timeout: float, retries: int, delay: float, max_pages: int, user_agent: str):
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.max_pages = max_pages
        self.user_agent = user_agent

    def scrape_scholar(self, row: Dict[str, str], enable_search: bool) -> ScholarResult:
        homepage = self._safe_str(row.get("学者主页", ""))
        current_email = self._safe_str(row.get("邮箱", ""))
        zh_name = self._safe_str(row.get("中文姓名", ""))
        en_name = self._safe_str(row.get("英文姓名", ""))
        inst_cn = self._safe_str(row.get("中文机构", ""))
        inst_en = self._safe_str(row.get("英文机构", ""))
        inst = f"{inst_cn} {inst_en}".strip()

        if current_email:
            return ScholarResult(
                row=row,
                status="skip_existing",
                extracted_email=current_email,
                source_url="",
                confidence=100,
                note="原表已存在邮箱",
                candidates=[Candidate(email=current_email, source_url="", score=100)],
            )

        candidate_map: Dict[str, Candidate] = {}
        queue: List[str] = []

        if homepage:
            queue.append(homepage)
        queue.extend(self._build_institution_seed_urls(inst_cn=inst_cn, inst_en=inst_en))

        if enable_search and (en_name or zh_name):
            for query in self._build_search_queries(en_name=en_name, zh_name=zh_name, inst_cn=inst_cn, inst_en=inst_en):
                ddg_url = self._duckduckgo_lite_query(query)
                bing_url = self._bing_query(query)
                if ddg_url:
                    queue.append(ddg_url)
                if bing_url:
                    queue.append(bing_url)

        visited: Set[str] = set()
        pages_attempted = 0
        pages_scanned = 0

        while queue and pages_attempted < self.max_pages:
            url = queue.pop(0)
            normalized = self._normalize_url(url)
            if not normalized or normalized in visited:
                continue
            visited.add(normalized)

            pages_attempted += 1
            html_text = self._fetch(normalized)
            if not html_text:
                continue

            pages_scanned += 1
            page_emails = self._extract_emails_from_page(html_text)
            for email in page_emails:
                score = self._score_email(email, en_name, inst, normalized)
                existing = candidate_map.get(email)
                if existing is None or score > existing.score:
                    candidate_map[email] = Candidate(email=email, source_url=normalized, score=score)

            # If listing pages include target-name anchors, prioritize those detail links first.
            for link in self._extract_name_matched_links(
                base_url=normalized,
                html_text=html_text,
                zh_name=zh_name,
                en_name=en_name,
            ):
                if link in visited:
                    continue
                queue.insert(0, link)

            current_domain = urllib.parse.urlparse(normalized).netloc.lower()
            for link in self._extract_candidate_links(normalized, html_text):
                if link in visited or len(queue) >= self.max_pages * 4:
                    continue
                link_domain = urllib.parse.urlparse(link).netloc.lower()
                # Prioritize cross-domain pages, they are more likely to contain contact info.
                if link_domain and link_domain != current_domain:
                    queue.insert(0, link)
                else:
                    queue.append(link)

            if self.delay > 0:
                time.sleep(self.delay)

        candidates = sorted(candidate_map.values(), key=lambda x: x.score, reverse=True)
        usable_candidates = [c for c in candidates if c.score >= 25]
        if not usable_candidates:
            return ScholarResult(
                row=row,
                status="not_found",
                extracted_email="",
                source_url="",
                confidence=0,
                note="未检出邮箱",
                candidates=candidates[:10],
            )

        name_matched = [c for c in usable_candidates if self._is_name_matched_email(c.email, en_name)]
        best = name_matched[0] if name_matched else usable_candidates[0]
        confidence = min(max(best.score, 5), 100)
        note = ""
        # Guardrail: avoid assigning unrelated list-page emails when no name match exists.
        if (
            not name_matched
            and confidence < 45
            and any(token in best.source_url.lower() for token in ("/faculty", "/people", "/staff", "/team"))
        ):
            return ScholarResult(
                row=row,
                status="not_found",
                extracted_email="",
                source_url="",
                confidence=0,
                note="仅命中列表页低置信候选，未采用",
                candidates=candidates[:10],
            )
        if confidence < 45:
            note = "低置信度，建议复核"
        return ScholarResult(
            row=row,
            status="ok" if confidence >= 45 else "needs_review",
            extracted_email=best.email,
            source_url=best.source_url,
            confidence=confidence,
            note=note,
            candidates=candidates,
        )

    @staticmethod
    def _safe_str(value: object) -> str:
        """Convert mixed XLSX cell values to a clean string."""
        if value is None:
            return ""
        if isinstance(value, float):
            # NaN check without importing math.
            if value != value:
                return ""
        return str(value).strip()

    @staticmethod
    def _is_name_matched_email(email: str, en_name: str) -> bool:
        if not email or "@" not in email or not en_name:
            return False
        local = email.split("@", 1)[0].lower()
        tokens = [t.lower() for t in re.split(r"[^a-zA-Z]+", en_name) if len(t) >= 3]
        if not tokens:
            return False
        return any(t in local for t in tokens[:4])

    def _fetch(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        attempt = 0
        while attempt <= self.retries:
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    charset = resp.headers.get_content_charset() or "utf-8"
                    body = resp.read()
                    return body.decode(charset, errors="ignore")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout):
                attempt += 1
                if attempt > self.retries:
                    return ""
                time.sleep(0.8 + random.random() * 0.6)
        return ""

    def _extract_emails_from_page(self, html_text: str) -> Set[str]:
        sanitized = html_text
        for pattern, replacement in OBFUSCATED_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)

        plain_text = html.unescape(TAG_RE.sub(" ", sanitized))
        plain_text = SPACE_RE.sub(" ", plain_text)

        found = set(EMAIL_RE.findall(sanitized)) | set(EMAIL_RE.findall(plain_text))
        cleaned: Set[str] = set()
        for email in found:
            normalized = email.strip(" .,;:<>\"'()[]{}")
            if not normalized:
                continue
            if "/" in normalized:
                continue
            local_part = normalized.split("@")[0].lower()
            domain_part = normalized.split("@")[1].lower() if "@" in normalized else ""
            if local_part in BAD_LOCAL_PARTS or local_part.startswith("email"):
                continue
            if domain_part in BAD_EMAIL_DOMAINS:
                continue
            if normalized.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
                continue
            cleaned.add(normalized)
        return cleaned

    def _extract_candidate_links(self, base_url: str, html_text: str) -> List[str]:
        hrefs = re.findall(r'(?i)href=[\"\']([^\"\']+)[\"\']', html_text)
        raw_urls = re.findall(r'https?:\\\\/\\\\/[^\"\\\'\\s<>]+', html_text)
        raw_urls = [u.replace("\\/", "/") for u in raw_urls]
        out: List[str] = []
        base_domain = urllib.parse.urlparse(base_url).netloc.lower()
        is_search_page = "duckduckgo.com" in base_domain or "bing.com" in base_domain
        if is_search_page:
            return self._extract_search_result_links(base_url, html_text)
        for href in hrefs:
            if href.startswith("javascript:") or href.startswith("mailto:"):
                continue
            abs_url = urllib.parse.urljoin(base_url, href)
            abs_url = self._unwrap_redirect(abs_url)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            if "/paper-details/" in parsed.path:
                continue
            if "scholar.google." in base_domain and "google." in parsed.netloc.lower():
                continue
            if any(token in abs_url.lower() for token in HIGH_VALUE_HINTS):
                out.append(abs_url)
                continue
            # Keep same-domain shallow links.
            if parsed.netloc.lower() == base_domain and parsed.path.count("/") <= 3:
                out.append(abs_url)
        if "bohrium.com" in base_domain:
            filtered: List[str] = []
            for u in out:
                p = urllib.parse.urlparse(u)
                if "bohrium.com" not in p.netloc.lower():
                    filtered.append(u)
                    continue
                if "/scholar/" in p.path.lower():
                    filtered.append(u)
            out = filtered
            # Bohrium pages often embed external profile links in JSON, not href tags.
            for raw in raw_urls:
                p = urllib.parse.urlparse(raw)
                if p.scheme not in {"http", "https"}:
                    continue
                if not p.netloc or "bohrium.com" in p.netloc.lower() or "deepmd.net" in p.netloc.lower():
                    continue
                if ".edu" in p.netloc.lower() or any(tok in raw.lower() for tok in HIGH_VALUE_HINTS):
                    out.append(raw)
        # Preserve order and deduplicate.
        dedup: List[str] = []
        seen: Set[str] = set()
        for u in out:
            n = self._normalize_url(u)
            if n and n not in seen:
                seen.add(n)
                dedup.append(n)
        return dedup[:30]

    @staticmethod
    def _unwrap_redirect(url: str) -> str:
        """Unwrap redirect wrappers (e.g. DuckDuckGo's /l/?uddg=...)."""
        parsed = urllib.parse.urlparse(url)
        if "duckduckgo.com" not in parsed.netloc.lower():
            return url
        q = urllib.parse.parse_qs(parsed.query)
        uddg = q.get("uddg")
        if uddg and uddg[0]:
            return urllib.parse.unquote(uddg[0])
        return url

    def _extract_search_result_links(self, base_url: str, html_text: str) -> List[str]:
        # Prioritize actual SERP result anchors to avoid crawling static assets.
        base_domain = urllib.parse.urlparse(base_url).netloc.lower()
        links: List[str] = []
        if "duckduckgo.com" in base_domain:
            links = re.findall(r'(?is)class=[\"\'][^\"\']*result__a[^\"\']*[\"\'][^>]*href=[\"\']([^\"\']+)[\"\']', html_text)
        elif "bing.com" in base_domain:
            links = re.findall(r'(?is)<li[^>]*class=[\"\'][^\"\']*b_algo[^\"\']*[\"\'][^>]*>.*?<h2>.*?<a[^>]+href=[\"\']([^\"\']+)[\"\']', html_text)
        if not links:
            links = re.findall(r'(?i)href=[\"\']([^\"\']+)[\"\']', html_text)

        out: List[str] = []
        blocked_suffix = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".xml", ".webp")
        for href in links:
            abs_url = urllib.parse.urljoin(base_url, href)
            abs_url = self._unwrap_redirect(abs_url)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme not in {"http", "https"}:
                continue
            low = abs_url.lower()
            if low.endswith(blocked_suffix):
                continue
            if "duckduckgo.com" in parsed.netloc.lower() or "bing.com" in parsed.netloc.lower():
                continue
            out.append(abs_url)

        dedup: List[str] = []
        seen: Set[str] = set()
        for u in out:
            n = self._normalize_url(u)
            if n and n not in seen:
                seen.add(n)
                dedup.append(n)
        return dedup[:15]

    def _extract_name_matched_links(self, base_url: str, html_text: str, zh_name: str, en_name: str) -> List[str]:
        if not html_text:
            return []
        zh_name = (zh_name or "").strip()
        en_tokens = [t.lower() for t in re.split(r"[^a-zA-Z]+", (en_name or "").strip()) if len(t) >= 2]
        anchors = re.findall(r'(?is)<a[^>]+href=[\"\']([^\"\']+)[\"\'][^>]*>(.*?)</a>', html_text)
        out: List[str] = []
        for href, inner_html in anchors:
            text = SPACE_RE.sub(" ", TAG_RE.sub(" ", html.unescape(inner_html))).strip().lower()
            if not text:
                continue
            matched = False
            if zh_name and zh_name in text:
                matched = True
            elif en_tokens and sum(1 for t in en_tokens[:3] if t in text) >= 1:
                matched = True
            if not matched:
                continue
            abs_url = urllib.parse.urljoin(base_url, href)
            abs_url = self._unwrap_redirect(abs_url)
            n = self._normalize_url(abs_url)
            if n:
                out.append(n)
        dedup: List[str] = []
        seen: Set[str] = set()
        for u in out:
            if u not in seen:
                seen.add(u)
                dedup.append(u)
        return dedup[:20]

    @staticmethod
    def _score_email(email: str, en_name: str, institution: str, source_url: str) -> int:
        score = 20
        local, _, domain = email.lower().partition("@")
        if not domain or "." not in domain:
            return 0

        name_tokens = [t for t in re.split(r"[^a-zA-Z]+", en_name.lower()) if len(t) >= 2]
        for token in name_tokens[:4]:
            if token in local:
                score += 15

        inst_tokens = [t for t in re.split(r"[^a-zA-Z]+", institution.lower()) if len(t) >= 4]
        hit = any(tok in domain for tok in inst_tokens[:8])
        if hit:
            score += 20

        role_based = {"admission", "office", "service", "support", "contact", "news"}
        if any(part in local for part in role_based):
            score -= 20

        if domain in BAD_EMAIL_DOMAINS:
            score -= 30

        source_domain = urllib.parse.urlparse(source_url).netloc.lower()
        if source_domain and domain in source_domain:
            score += 10
        elif source_domain and source_domain.endswith(domain):
            score += 15

        if domain.endswith((".edu", ".edu.cn", ".ac.cn", ".org")):
            score += 10

        return max(min(score, 100), 0)

    @staticmethod
    def _normalize_url(url: str) -> str:
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            return ""
        clean = parsed._replace(fragment="")
        return urllib.parse.urlunparse(clean)

    @staticmethod
    def _duckduckgo_lite_query(query: str) -> str:
        # Prefer Chinese locale for better hit rate on CN university staff pages.
        return "https://duckduckgo.com/html/?kl=cn-zh&q=" + urllib.parse.quote(query)

    @staticmethod
    def _bing_query(query: str) -> str:
        return "https://www.bing.com/search?q=" + urllib.parse.quote(query)

    @staticmethod
    def _build_search_queries(en_name: str, zh_name: str, inst_cn: str, inst_en: str) -> List[str]:
        queries: List[str] = []
        en_name = en_name.strip()
        zh_name = zh_name.strip()
        inst_cn = inst_cn.strip()
        inst_en = inst_en.strip()

        inst_en_short = inst_en.split(";")[0].split(",")[0].strip()
        inst_cn_short = inst_cn.split(";")[0].split("；")[0].strip()
        domain_hints = EmailScraper._infer_domain_hints(inst_cn=inst_cn, inst_en=inst_en)

        if en_name:
            queries.append(f'"{en_name}" email')
            queries.append(f'"{en_name}" contact')
            queries.append(f'"{en_name}" "{inst_en_short}" email' if inst_en_short else f'"{en_name}" email edu')
            queries.append(f'"{en_name}" "{inst_cn_short}" 邮箱' if inst_cn_short else f'"{en_name}" 邮箱')
        if zh_name:
            queries.append(f'"{zh_name}" 邮箱')
            if inst_cn_short:
                queries.append(f'"{zh_name}" "{inst_cn_short}" 邮箱')
                queries.append(f'"{zh_name}" "{inst_cn_short}" 教师')
                queries.append(f'"{zh_name}" "{inst_cn_short}" 导师 邮箱')
            if inst_en_short:
                # Key optimization: English institution + Chinese name often performs better.
                queries.append(f'"{zh_name}" "{inst_en_short}" email')
                queries.append(f'"{zh_name}" "{inst_en_short}" faculty')
                queries.append(f'"{zh_name}" "{inst_en_short}" 教师 邮箱')
        if en_name:
            queries.append(f'"{en_name}" site:.edu.cn')
            if inst_cn_short:
                queries.append(f'"{en_name}" "{inst_cn_short}" site:.edu.cn')
        if zh_name:
            queries.append(f'"{zh_name}" site:.edu.cn 邮箱')
            if inst_en_short:
                queries.append(f'"{zh_name}" "{inst_en_short}" site:.edu.cn')
                queries.append(f'"{zh_name}" "{inst_en_short}" profile')
                queries.append(f'"{zh_name}" "{inst_en_short}" contact')
        if inst_en_short and en_name:
            queries.append(f'"{inst_en_short}" "{en_name}" profile')
            queries.append(f'"{inst_en_short}" "{en_name}" faculty email')
        if inst_en_short and zh_name:
            queries.append(f'"{inst_en_short}" "{zh_name}" profile')
            queries.append(f'"{inst_en_short}" "{zh_name}" 教师')
        if inst_cn_short and zh_name:
            queries.append(f'"{inst_cn_short}" "{zh_name}" 联系方式')
            queries.append(f'"{inst_cn_short}" "{zh_name}" 邮件')
        for domain in domain_hints:
            if zh_name:
                queries.append(f'"{zh_name}" site:{domain}')
                queries.append(f'"{zh_name}" 邮箱 site:{domain}')
            if en_name:
                queries.append(f'"{en_name}" site:{domain}')
                queries.append(f'"{en_name}" email site:{domain}')
            if zh_name and en_name:
                queries.append(f'"{zh_name}" "{en_name}" site:{domain}')

        dedup: List[str] = []
        seen: Set[str] = set()
        for q in queries:
            q = SPACE_RE.sub(" ", q).strip()
            if q and q not in seen:
                seen.add(q)
                dedup.append(q)
        return dedup[:28]

    @staticmethod
    def _infer_domain_hints(inst_cn: str, inst_en: str) -> List[str]:
        text = f"{inst_cn} {inst_en}".strip().lower()
        hints: List[str] = []
        for key, domain in INSTITUTION_DOMAIN_HINTS.items():
            if key in text and domain not in hints:
                hints.append(domain)
        return hints

    @staticmethod
    def _build_institution_seed_urls(inst_cn: str, inst_en: str) -> List[str]:
        """Generate direct institution URLs to reduce search-engine dependency."""
        domain_hints = EmailScraper._infer_domain_hints(inst_cn=inst_cn, inst_en=inst_en)
        if not domain_hints:
            return []

        # Build acronym from the first EN institution segment (e.g., School of Public Health -> phs).
        inst_en_short = inst_en.split(";")[0].split(",")[0].strip().lower()
        words = re.findall(r"[a-zA-Z]+", inst_en_short)
        stop = {"school", "of", "the", "and", "for", "at", "in", "shenzhen", "university"}
        acronym = "".join(w[0] for w in words if w and w not in stop)
        subdomain_prefixes: Set[str] = set()
        if 2 <= len(acronym) <= 6:
            subdomain_prefixes.add(acronym)
            # Many school subdomains append trailing "s" (e.g., phs).
            if "school" in words and len(acronym) <= 5:
                subdomain_prefixes.add(f"{acronym}s")

        base_paths = ["", "/teacher", "/faculty", "/people", "/contact", "/zh-hans/teacher"]
        urls: List[str] = []
        for domain in domain_hints:
            urls.append(f"https://{domain}")
            for sub in subdomain_prefixes:
                urls.append(f"https://{sub}.{domain}")
            for p in base_paths:
                urls.append(f"https://{domain}{p}")
                for sub in subdomain_prefixes:
                    urls.append(f"https://{sub}.{domain}{p}")

        dedup: List[str] = []
        seen: Set[str] = set()
        for u in urls:
            n = EmailScraper._normalize_url(u)
            if n and n not in seen:
                seen.add(n)
                dedup.append(n)
        return dedup[:20]


def write_results(out_dir: Path, results: Sequence[ScholarResult]) -> Tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    main_csv = out_dir / f"scholar_emails_{ts}.csv"
    candidate_csv = out_dir / f"scholar_email_candidates_{ts}.csv"
    summary_json = out_dir / f"scrape_summary_{ts}.json"

    main_fields = [
        "学者主页",
        "中文姓名",
        "英文姓名",
        "原邮箱",
        "抓取邮箱",
        "邮箱来源",
        "置信度",
        "状态",
        "备注",
        "研究方向",
        "中文机构",
        "英文机构",
        "抓取时间",
    ]
    with main_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=main_fields)
        writer.writeheader()
        now = dt.datetime.now().isoformat(timespec="seconds")
        for res in results:
            row = res.row
            writer.writerow(
                {
                    "学者主页": row.get("学者主页", ""),
                    "中文姓名": row.get("中文姓名", ""),
                    "英文姓名": row.get("英文姓名", ""),
                    "原邮箱": row.get("邮箱", ""),
                    "抓取邮箱": res.extracted_email,
                    "邮箱来源": res.source_url,
                    "置信度": res.confidence,
                    "状态": res.status,
                    "备注": res.note,
                    "研究方向": row.get("研究方向", ""),
                    "中文机构": row.get("中文机构", ""),
                    "英文机构": row.get("英文机构", ""),
                    "抓取时间": now,
                }
            )

    candidate_fields = ["中文姓名", "英文姓名", "候选邮箱", "候选分数", "来源URL"]
    with candidate_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=candidate_fields)
        writer.writeheader()
        for res in results:
            for cand in res.candidates:
                writer.writerow(
                    {
                        "中文姓名": res.row.get("中文姓名", ""),
                        "英文姓名": res.row.get("英文姓名", ""),
                        "候选邮箱": cand.email,
                        "候选分数": cand.score,
                        "来源URL": cand.source_url,
                    }
                )

    total = len(results)
    ok = sum(1 for r in results if r.status == "ok")
    needs_review = sum(1 for r in results if r.status == "needs_review")
    not_found = sum(1 for r in results if r.status == "not_found")
    skipped = sum(1 for r in results if r.status == "skip_existing")
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "total": total,
        "ok": ok,
        "needs_review": needs_review,
        "not_found": not_found,
        "skip_existing": skipped,
        "main_csv": str(main_csv),
        "candidate_csv": str(candidate_csv),
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return main_csv, candidate_csv, summary_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scrape scholar emails from XLSX")
    parser.add_argument("--input", required=True, help="Path to XLSX input")
    parser.add_argument("--out-dir", default="outputs", help="Output directory")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N records (0 = all)")
    parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    parser.add_argument("--retries", type=int, default=1, help="Retry count per URL")
    parser.add_argument("--delay", type=float, default=0.15, help="Delay between requests")
    parser.add_argument("--max-pages", type=int, default=4, help="Maximum pages to crawl per scholar")
    parser.add_argument("--workers", type=int, default=8, help="Parallel workers for scholar rows")
    parser.add_argument("--enable-search", action="store_true", help="Enable search-engine fallback")
    parser.add_argument(
        "--user-agent",
        default=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        help="User-Agent header",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    rows = XlsxReader(input_path).read_first_sheet()
    if not rows:
        raise SystemExit("No rows found in input XLSX")

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    scraper = EmailScraper(
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        max_pages=args.max_pages,
        user_agent=args.user_agent,
    )

    indexed_rows = list(enumerate(rows, start=1))
    results_by_idx: Dict[int, ScholarResult] = {}

    def _task(item: Tuple[int, Dict[str, str]]) -> Tuple[int, str, ScholarResult]:
        idx, row = item
        name = row.get("英文姓名") or row.get("中文姓名") or f"row-{idx}"
        res = scraper.scrape_scholar(row=row, enable_search=args.enable_search)
        return idx, name, res

    workers = max(1, int(args.workers))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_task, item): item[0] for item in indexed_rows}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                row_idx, name, result = future.result()
                results_by_idx[row_idx] = result
                print(
                    f"[{row_idx}/{len(rows)}] {name} -> status={result.status}, "
                    f"email={result.extracted_email or '-'}, confidence={result.confidence}"
                )
            except Exception as exc:
                row = rows[idx - 1]
                name = row.get("英文姓名") or row.get("中文姓名") or f"row-{idx}"
                print(f"[{idx}/{len(rows)}] {name} -> status=error, detail={exc}")
                results_by_idx[idx] = ScholarResult(
                    row=row,
                    status="not_found",
                    extracted_email="",
                    source_url="",
                    confidence=0,
                    note=f"抓取异常: {exc}",
                    candidates=[],
                )

    results: List[ScholarResult] = [results_by_idx[i] for i in range(1, len(rows) + 1)]

    main_csv, candidate_csv, summary_json = write_results(Path(args.out_dir), results)
    print("\\nDone")
    print(f"main_csv: {main_csv}")
    print(f"candidate_csv: {candidate_csv}")
    print(f"summary_json: {summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
