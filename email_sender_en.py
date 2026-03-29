#!/usr/bin/env python3
"""
自动化邮件发送脚本
功能：根据教师信息生成科学问题，获取研究概述和文献，批量发送邮件

作者：Devin AI
日期：2025-08-15
"""

import json
import requests
import time
import smtplib
import pandas as pd
import re
import logging
import os
import threading
import dns.resolver
import socket
import random
from urllib.parse import urljoin
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('email_automation.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TerminalStatusError(Exception):
    """用于指示远端状态已失败（如 SecurityRisk），应立即终止当前轮询与处理。"""
    pass


def normalize_base_url(base_url: str) -> str:
    """规范化 API base URL，允许传入不带协议的域名。"""
    if not base_url:
        return ""
    base_url = base_url.strip()
    if not re.match(r'^https?://', base_url, re.IGNORECASE):
        base_url = f"https://{base_url}"
    return base_url.rstrip('/')


def normalize_external_url(url: str, default_base: str = "") -> str:
    """补全外部链接协议或域名。"""
    if not url:
        return ""
    url = str(url).strip()
    if re.match(r'^https?://', url, re.IGNORECASE):
        return url
    if url.startswith('//'):
        return f"https:{url}"
    if re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$', url):
        return f"https://{url}"
    if default_base:
        return urljoin(f"{normalize_base_url(default_base)}/", url.lstrip('/'))
    return f"https://{url.lstrip('/')}"


class GeminiAPI:
    """Gemini API 客户端，用于生成科学问题"""
    
    def __init__(self, credentials_path: str = "YOUR_GOOGLE_APPLICATION_CREDENTIALS.json"):
        """初始化 Gemini API 客户端"""
        try:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
            from google import genai
            from google.genai.types import GenerateContentConfig, GoogleSearch, Tool
            
            self.client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_PROJECT_ID", "YOUR_GOOGLE_PROJECT_ID"),
                location=os.getenv("GOOGLE_LOCATION", "us-central1")
            )
            self.GenerateContentConfig = GenerateContentConfig
            self.GoogleSearch = GoogleSearch
            self.Tool = Tool
            logger.info("Gemini API 客户端初始化成功")
        except Exception as e:
            logger.error(f"Gemini API 初始化失败: {e}")
            raise
    
    def generate_scientific_question(self, name: str, department: str, interests: str = "") -> str:
        """
        根据教师信息生成科学问题
        
        规则：
        1. 仅生成1个问题
        2. 契合教师的研究方向与兴趣，结合近三年的研究成果
        3. 不以"如何"开头，不以"研究"结尾，不为疑问句
        4. 不超过20个汉字
        5. 仅输出问题本身，无标点符号
        """
        teacher_info = f"{name} {department}"
        
        prompt = f"""
Generate a specific scientific question with given Teacger Info, requirements:
1. Only one question.
2. The question must correspond to the teacher's research interests and current research status/results, more focusing on those in the last three years.
3. The question cannot start with "What" and other interrogative words, or end with "research" and similar words, or in the form of a question.
4. Every word, its first letter must be capitalized.
5. Output in English and the words must be less than 20.
6. Any punctuation and content except scientific question is forbidden.

Teacher Info：{teacher_info}
"""
        
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                response = self.client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config=self.GenerateContentConfig(
                        tools=[self.Tool(google_search=self.GoogleSearch())],
                    ),
                )
                
                field_text = ""
                if hasattr(response, "text") and response.text:
                    field_text = response.text.strip()
                elif hasattr(response, "candidates") and response.candidates:
                    if hasattr(response.candidates[0].content.parts[0], 'text'):
                        field_text = response.candidates[0].content.parts[0].text.strip()
                else:
                    field_text = str(response).strip()
                
                field_text = re.sub(r'[，。！？；：""''（）【】《》、.,!?;:()\[\]<>"\']', '', field_text)
                field_text = field_text.strip()
                
                # English rule: no more than 20 words
                words_count = len(field_text.split())
                if words_count <= 20:
                    logger.info(f"为 {name} 生成科学问题: {field_text}")
                    return field_text
                else:
                    logger.warning(f"生成的问题超过20个单词({words_count}词): {field_text}，正在重试...")
                    if attempt == max_attempts - 1:
                        # Still over 20 words on last attempt -> fail
                        raise Exception("生成的问题超过20个单词")
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"第{attempt+1}次生成问题失败: {e}")
                if attempt == max_attempts - 1:
                    raise Exception(f"生成科学问题失败: {e}")
                time.sleep(1)
        
        return ""


class BohriumAPI:
    """Bohrium API 客户端，用于获取研究概述和文献"""
    
    def __init__(self, access_key: str):
        """初始化 Bohrium API 客户端"""
        self.access_key = access_key
        self.base_url = os.getenv(
            "BOHRIUM_API_BASE_URL",
            "https://bohrium.example.com/bohrapi/v1/sigma-search/api/v1"
        )
        self.headers = {
            "Content-Type": "application/json",
            "accessKey": access_key,
            "content-language": "en-us"
        }
        logger.info("Bohrium API 客户端初始化成功")
    
    def clean_summary(self, text: str, max_length: int = 350) -> str:
        """Clean and normalize summary text (strip Markdown/HTML; single paragraph)."""
        if not text:
            return ""
        
        # 移除引用标记
        text = re.sub(r'\[citation:[^\]]+\]', '', text)
        
        # 移除 Markdown 标题
        text = re.sub(r'^#+\s+.*$', '', text, flags=re.MULTILINE)
        
        # 移除 Markdown 格式
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # 加粗
        text = re.sub(r'\*(.+?)\*', r'\1', text)      # 斜体
        text = re.sub(r'__(.+?)__', r'\1', text)      # 加粗
        text = re.sub(r'_(.+?)_', r'\1', text)        # 斜体
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)   # 图片
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # 链接
        text = re.sub(r'`([^`]+)`', r'\1', text)      # 行内代码
        text = re.sub(r'```[\s\S]*?```', '', text)    # 代码块
        text = re.sub(r'<[^>]+>', '', text)           # HTML标签
        
        # 移除列表/表格/引用等符号
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)  # 无序列表
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)  # 有序列表
        text = re.sub(r'^\s*>\s+', '', text, flags=re.MULTILINE)      # 引用块
        text = re.sub(r'^\s*\|.*\|.*$', '', text, flags=re.MULTILINE) # 表格行
        text = re.sub(r'^\s*[-=]+\s*$', '', text, flags=re.MULTILINE) # 分隔线
        text = re.sub(r'~~(.+?)~~', r'\1', text)      # 删除线
        text = re.sub(r'\^\^(.+?)\^\^', r'\1', text)  # 上标
        text = re.sub(r'==(.+?)==', r'\1', text)      # 高亮
        
        # 合并空格和换行
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        # 长度控制 + 提前返回
        if len(text) > max_length:
            return text[:max_length] + '...<div style="text-align:right;">Read Full Answer</div>'
        return text
    
    def create_session(self, query: str) -> Dict:
        """创建搜索会话"""
        url = f"{self.base_url}/ai_search/sessions"
        data = {
            "query": query,
            "model": "qwen",
            "discipline": "All",
            "resource_id_list": []
        }
        response = requests.post(url, headers=self.headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_session_details(self, uuid: str) -> Dict:
        """获取会话详情"""
        url = f"{self.base_url}/ai_search/sessions/{uuid}"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def get_question_summary(self, question_id: str) -> Dict:
        """获取问题总结"""
        url = f"{self.base_url}/ai_search/questions/{question_id}"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def set_session_share(self, uuid: str) -> Dict:
        """设置会话为可分享状态"""
        url = f"{self.base_url}/ai_search/sessions_extended/{uuid}"
        data = {
            "share": 1
        }
        response = requests.patch(url, headers=self.headers, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    
    def wait_for_completion(self, question_id: str, max_retries: int = 30, interval: int = 10) -> Dict:
        """等待问题处理完成"""
        for retry in range(max_retries):
            try:
                question_summary = self.get_question_summary(question_id)
                if question_summary.get("code") != 0:
                    raise Exception("获取问题总结失败")
                
                status = question_summary["data"]["status"]
                logger.debug(f"问题处理状态: {status}")
                
                if status == "succeeded":
                    return question_summary
                elif status == "failed":
                    # 失败状态直接终止，让外层逻辑决定是否跳过该条
                    raise TerminalStatusError(f"处理失败: {question_summary['data'].get('reason', '未知原因')}")
                
                time.sleep(interval)
                
            except Exception as e:
                # 对终止类错误直接抛出，不在内层循环里继续等待
                if isinstance(e, TerminalStatusError):
                    raise
                if retry == max_retries - 1:
                    raise
                logger.warning(f"获取问题状态失败，重试中: {e}")
                time.sleep(interval)
        
        raise Exception(f"等待超时: {max_retries * interval}秒后仍未完成")
    
    def get_papers_by_question_id(self, question_id: str, citation_list: List[str]) -> List[Dict]:
        """获取问题相关的论文列表"""
        url = f"{self.base_url}/ai_search/questions/{question_id}/papers"
        params = {"sort": "RelevanceScore"}
        
        try:
            response = requests.get(url, params=params, headers=self.headers, timeout=30)
            response.raise_for_status()
            response_data = response.json()
            
            if response_data.get("code") != 0:
                logger.warning(f"获取论文列表失败: {response_data}")
                return []
            
            papers = response_data.get("data", {}).get("list", [])
            
            # 创建 sequenceId 到论文的映射
            paper_map = {str(paper.get("sequenceId")): paper for paper in papers}
            
            # 按照 citation_list 的顺序返回论文
            ordered_papers = []
            for citation_id in citation_list:
                if citation_id in paper_map:
                    paper = paper_map[citation_id]
                    formatted_paper = {
                        "title": paper.get("title", ""),
                        "journal": paper.get("journal", ""),
                        "publicationDate": paper.get("publicationDate", ""),
                        "author": ', '.join(paper.get("author", [])),
                        "abstract": paper.get("abstract", "")[:100] + "..." if paper.get("abstract") else ""
                    }
                    ordered_papers.append(formatted_paper)
                else:
                    logger.warning(f"未找到ID为{citation_id}的论文")
            
            return ordered_papers
            
        except Exception as e:
            logger.error(f"获取论文列表时发生错误: {e}")
            return []
    
    def check_recent_papers(self, papers: List[Dict], days: int = 30) -> bool:
        """检查是否有最近的论文"""
        now = datetime.now()
        for paper in papers:
            pub_date = paper.get('publicationDate', '')
            try:
                date_str = pub_date.split()[0].replace('/', '-')
                try:
                    pub_dt = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    try:
                        pub_dt = datetime.strptime(date_str, '%Y-%m')
                    except ValueError:
                        pub_dt = datetime.strptime(date_str, '%Y')
                
                if (now - pub_dt).days <= days:
                    return True
            except Exception:
                continue
        return False
    
    def get_research_overview(self, field: str, max_retries: int = 5) -> Tuple[Dict, str]:
        """获取研究领域概述"""
        logger.info(f"开始获取 {field} 领域的研究概述...")
        
        for retry in range(max_retries):
            try:
                # 创建会话
                session_response = self.create_session(f"The latest development of {field}")
                if session_response.get("code") != 0:
                    raise Exception("创建会话失败")
                
                uuid = session_response["data"]["uuid"]
                logger.debug(f"会话创建成功，UUID: {uuid}")
                time.sleep(2)
                
                # 获取会话详情
                session_details = self.get_session_details(uuid)
                if session_details.get("code") != 0:
                    raise Exception("获取会话详情失败")
                
                question_id = session_details["data"]["questions"][0]["id"]
                logger.debug(f"问题ID: {question_id}")
                
                share_response = self.set_session_share(uuid)
                if share_response.get("code") != 0:
                    logger.warning(f"设置分享状态失败: {share_response}")
                else:
                    logger.debug(f"会话分享状态设置成功: {uuid}")
                
                question_summary = self.wait_for_completion(question_id)
                
                summary = question_summary["data"]["summary"]
                if not summary:
                    raise Exception("未能获取到有效的研究概述")
                
                # 如果生成的内容是中文，重新生成（进入下一次重试）
                if re.search(r"[\u4e00-\u9fff]", summary):
                    logger.warning("检测到中文摘要内容，尝试重新生成英文概述...")
                    time.sleep(1)
                    continue
                
                citation_list = re.findall(r'\[citation:(\d+)\]', summary)
                seen = set()
                unique_citations = []
                for citation in citation_list:
                    if citation not in seen:
                        seen.add(citation)
                        unique_citations.append(citation)
                        if len(unique_citations) == 8:
                            break
                
                logger.debug(f"提取到的citations: {unique_citations}")
                
                papers = self.get_papers_by_question_id(question_id, unique_citations)
                
                # 回退策略（两阶段）：先近30天最多3次，再近3个月最多2次，总尝试不超过 max_retries
                days_30_attempts = min(3, max_retries)
                days_90_attempts = max_retries - days_30_attempts
                
                if retry < days_30_attempts:
                    has_recent_30 = self.check_recent_papers(papers, 30)
                    if has_recent_30:
                        logger.info("成功检索到近30天文献")
                    else:
                        remaining = days_30_attempts - (retry + 1)
                        if remaining > 0:
                            logger.info(f"未检索到近30天文献，将重试（剩余{remaining}次）...")
                            time.sleep(1)
                            continue
                        else:
                            logger.info("近30天多次未命中，将切换检索近3个月文献")
                            time.sleep(1)
                            continue
                else:
                    # 第二阶段：检查近3个月
                    has_recent_90 = self.check_recent_papers(papers, 90)
                    if has_recent_90:
                        logger.info("成功检索到近3个月文献")
                    else:
                        # 还剩多少次用于90天阶段
                        phase_retry_index = retry - days_30_attempts
                        remaining_90 = max(0, days_90_attempts - (phase_retry_index + 1))
                        if remaining_90 > 0:
                            logger.warning(f"未检索到近3个月文献，将重试（剩余{remaining_90}次）...")
                            time.sleep(1)
                            continue
                        else:
                            logger.warning("未检索到近3个月文献，但将继续使用当前结果")
                
                # 清理和格式化摘要
                cleaned_summary = self.clean_summary(summary, 500)
                
                template_data = {
                    "summary": cleaned_summary,
                    "papers": papers
                }
                
                logger.info(f"成功获取 {field} 的研究概述，包含 {len(papers)} 篇论文")
                return template_data, uuid
                
            except TerminalStatusError as e:
                # 遇到终止错误（如 SecurityRisk），直接跳过该条
                logger.error(f"终止错误，跳过该条: {e}")
                raise
            except Exception as e:
                logger.error(f"第{retry+1}次获取研究概述失败: {e}")
                if retry == max_retries - 1:
                    raise Exception(f"获取研究概述失败: {e}")
                time.sleep(5)
        
        raise Exception("获取研究概述失败")


class ScholarPaperAPI:
    """学者论文 API 客户端。"""

    def __init__(self, api_config: Dict):
        self.base_url = normalize_base_url(api_config.get('base_url', ''))
        self.path = api_config.get('path', '/api/v1/paper/scholar/paper')
        self.timeout = int(api_config.get('timeout', 20))
        self.random = int(api_config.get('random', 0))
        self.is_show_citation_num = bool(api_config.get('isShowCitationNum', True))
        self.web_base_url = api_config.get('web_base_url', self.base_url)
        self.sort_mapping = api_config.get('sort_mapping', {
            'latest': 0,
            'highly_cited': 1
        })
        self.headers = {
            "Content-Type": "application/json"
        }
        self.headers.update(api_config.get('headers', {}) or {})
        logger.info(f"学者论文 API 初始化完成: {self.base_url}{self.path}")

    def _build_url(self) -> str:
        if not self.base_url:
            raise ValueError("scholar_api.base_url 未配置")
        return urljoin(f"{self.base_url}/", self.path.lstrip('/'))

    def _extract_paper_items(self, payload: Dict) -> List[Dict]:
        if not isinstance(payload, dict):
            return []

        data = payload.get('data', payload)
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            for key in ('list', 'items', 'records', 'papers', 'rows'):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def _pick_first(self, paper: Dict, keys: List[str], default: str = "") -> str:
        for key in keys:
            value = paper.get(key)
            if value is None:
                continue
            if isinstance(value, list):
                value = ', '.join(str(item) for item in value if item)
            value = str(value).strip()
            if value:
                return value
        return default

    def _normalize_paper(self, paper: Dict) -> Dict:
        citation_count = paper.get('citationCount')
        if citation_count is None:
            citation_count = paper.get('citationNum')
        if citation_count is None:
            citation_count = paper.get('citationNums')
        if citation_count is None:
            citation_count = paper.get('citedByCount')
        if citation_count is None:
            citation_count = 0

        authors = paper.get('author') or paper.get('authors') or paper.get('authorNames') or []
        if isinstance(authors, str):
            author_text = authors
        elif isinstance(authors, list):
            author_text = ', '.join(str(item) for item in authors if item)
        else:
            author_text = ""

        return {
            "title": self._pick_first(paper, ['title', 'paperTitle', 'name']),
            "journal": self._pick_first(paper, ['journal', 'venue', 'conference', 'source', 'publisher'], "未提供期刊/会议"),
            "publicationDate": self._pick_first(
                paper,
                ['publicationDate', 'publishDate', 'pubDate', 'year', 'publishedAt'],
                "未提供时间"
            ),
            "author": author_text or "作者信息待补充",
            "citationCount": citation_count,
            "paperUrl": normalize_external_url(
                self._pick_first(paper, ['jumpUrl', 'paperUrl', 'url', 'doiUrl', 'link']),
                self.web_base_url
            ),
            "abstract": self._pick_first(paper, ['abstract', 'summary'])
        }

    def fetch_papers(self, scholar_id: str, sort_key: str = 'latest', top_n: int = 3) -> List[Dict]:
        sort_value = self.sort_mapping.get(sort_key, self.sort_mapping.get('latest', 0))
        payload = {
            "scholarIds": [scholar_id],
            "sort": sort_value,
            "random": self.random,
            "isShowCitationNum": self.is_show_citation_num
        }

        response = requests.post(
            self._build_url(),
            headers=self.headers,
            json=payload,
            timeout=self.timeout
        )
        response.raise_for_status()
        response_payload = response.json() if response.text else {}

        code = response_payload.get('code')
        if code not in (None, 0, "0"):
            raise ValueError(f"学者论文接口返回失败: code={code}, body={str(response_payload)[:300]}")

        items = self._extract_paper_items(response_payload)
        normalized = [self._normalize_paper(item) for item in items]
        normalized = [paper for paper in normalized if paper.get('title')]
        return normalized[:top_n]


class EmailSender:
    """邮件发送器 - 支持连接复用和并发限流"""
    
    def __init__(self, smtp_config: Dict, max_smtp_workers: int = 10):
        """初始化邮件发送器"""
        self.smtp_config = smtp_config
        self.max_smtp_workers = max_smtp_workers
        self.smtp_semaphore = threading.Semaphore(max_smtp_workers)
        self.connection_pool = {}
        self.connection_lock = threading.Lock()
        logger.info(f"邮件发送器初始化成功，SMTP并发限制: {max_smtp_workers}")
    
    def _get_connection(self) -> Optional[smtplib.SMTP_SSL]:
        """从连接池获取SMTP连接"""
        thread_id = threading.get_ident()
        
        with self.connection_lock:
            if thread_id in self.connection_pool:
                conn = self.connection_pool[thread_id]
                try:
                    # 测试连接是否还有效
                    conn.noop()
                    return conn
                except:
                    # 连接已断开，移除
                    del self.connection_pool[thread_id]
            
            # 创建新连接
            try:
                conn = smtplib.SMTP_SSL(
                    self.smtp_config['server'], 
                    self.smtp_config['port'], 
                    timeout=30
                )
                conn.login(self.smtp_config['user'], self.smtp_config['password'])
                self.connection_pool[thread_id] = conn
                logger.debug(f"创建新的SMTP连接，线程ID: {thread_id}")
                return conn
            except Exception as e:
                logger.error(f"创建SMTP连接失败: {e}")
                return None
    
    def _release_connection(self, conn: smtplib.SMTP_SSL):
        """释放连接回连接池"""
        thread_id = threading.get_ident()
        with self.connection_lock:
            if thread_id in self.connection_pool:
                # 保持连接在池中，不关闭
                pass
    
    def _close_connection(self):
        """关闭当前线程的连接"""
        thread_id = threading.get_ident()
        with self.connection_lock:
            if thread_id in self.connection_pool:
                try:
                    self.connection_pool[thread_id].quit()
                except:
                    pass
                del self.connection_pool[thread_id]
    
    def send_email(self, recipient: str, subject: str, html_content: str, max_retries: int = 1) -> bool:
        """发送邮件 - 使用连接池和并发限流"""
        msg = MIMEText(html_content, 'html', 'utf-8')
        msg['Subject'] = Header(subject, 'utf-8')
        msg['From'] = formataddr(("Bohrium AI", self.smtp_config['user']))
        msg['To'] = Header(recipient, 'utf-8')
        msg['Reply-To'] = 'admin@bohrium.com'
        
        # 使用信号量限制并发
        with self.smtp_semaphore:
            for retry in range(max_retries):
                conn = None
                try:
                    conn = self._get_connection()
                    if not conn:
                        raise Exception("无法获取SMTP连接")
                    
                    conn.sendmail(self.smtp_config['user'], [recipient], msg.as_string())
                    self._release_connection(conn)
                    logger.info(f"邮件发送成功给 {recipient}（尝试次数：{retry + 1}）")
                    return True
                    
                except smtplib.SMTPAuthenticationError as e:
                    logger.error(f"SMTP认证失败：{e}")
                    if conn:
                        self._close_connection()
                    return False
                except smtplib.SMTPRecipientsRefused as e:
                    logger.error(f"收件人被拒绝：{e}")
                    if conn:
                        self._release_connection(conn)
                    return False
                except Exception as e:
                    logger.warning(f"第{retry+1}次发送邮件失败: {e}")
                    if conn:
                        # 连接出错，关闭并重新创建
                        self._close_connection()
                    # 单次尝试策略：直接返回失败，不再重试
                    logger.error(f"邮件发送失败，停止重试：{e}")
                    return False
        
        return False
    
    def __del__(self):
        """析构函数，清理所有连接"""
        with self.connection_lock:
            for conn in self.connection_pool.values():
                try:
                    conn.quit()
                except:
                    pass
            self.connection_pool.clear()


class InvitationApiSender:
    """邀请链接接口发送器"""

    def __init__(self, api_config: Dict):
        self.enabled = bool(api_config.get('enabled', False))
        self.url = api_config.get(
            'url',
            'https://your-api.example.com/api/activity/assistance/email_invitation_url'
        )
        self.language = api_config.get('language', 'en-us')
        self.utm_source = api_config.get('utm_source', 'test_invitation')
        self.timeout = int(api_config.get('timeout', 15))
        self.extra_headers = api_config.get('headers', {})
        logger.info(f"邀请接口发送器初始化完成，enabled={self.enabled}, url={self.url}")

    def send_invitation(self, email: str) -> Tuple[bool, str, str]:
        """调用邀请链接接口，返回 (success, invitation_url, error_message)"""
        if not self.enabled:
            return False, "", "邀请接口未启用（invitation_api.enabled=false）"

        payload = {
            "email": email,
            "language": self.language,
            "utmSource": self.utm_source
        }
        headers = {
            "User-Agent": "pre-edm/1.0",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }
        headers.update(self.extra_headers or {})

        try:
            response = requests.post(self.url, json=payload, headers=headers, timeout=self.timeout)
            if not response.ok:
                return False, "", f"HTTP {response.status_code}: {response.text[:200]}"

            data = response.json() if response.text else {}
            if isinstance(data, dict) and data.get('code') not in (None, 0, "0"):
                err = data.get('error', {})
                err_msg = err.get('msg') if isinstance(err, dict) else ""
                return False, "", f"业务失败(code={data.get('code')}): {err_msg or str(data)[:200]}"

            invitation_url = ""
            if isinstance(data, dict):
                data_field = data.get('data')
                invitation_url = (
                    data_field if isinstance(data_field, str) else
                    (data_field.get('url', '') if isinstance(data_field, dict) else '')
                ) or data.get('url', '') or data.get('invitationUrl', '')

            if not invitation_url:
                return False, "", f"接口未返回 invitation url: {str(data)[:200]}"

            logger.info(f"邀请接口调用成功: {email}")
            return True, invitation_url, ""
        except Exception as e:
            return False, "", str(e)


class TemplateProcessor:
    """模板处理器"""
    
    def __init__(self, template_path: str):
        """初始化模板处理器"""
        with open(template_path, 'r', encoding='utf-8') as f:
            self.template = f.read()
        logger.info(f"模板加载成功: {template_path}")
    
    def replace_variables(self, data: Dict) -> str:
        """替换模板变量"""
        html_content = self.template

        papers_html = ""
        papers = data.get("papers", [])
        is_latest_section = str(data.get("paperSectionTitle", "")).strip() == "最新论文"
        for idx, paper in enumerate(papers):
            paper_url = str(paper.get('paperUrl', '')).strip() or '#'
            citation_count = paper.get('citationCount')
            citation_html = ""
            try:
                if citation_count is not None and str(citation_count).strip() not in ("", "0"):
                    citation_html = (
                        '<span style="margin:0 4px;color:#d0d8e4;">·</span>'
                        f'<span>引用 {citation_count}</span>'
                    )
            except Exception:
                citation_html = ""

            latest_badge_html = ""
            if is_latest_section and idx == 0:
                latest_badge_html = '<span class="badge-new">最新</span>'

            paper_html = f"""
            <a href="{paper_url}" target="_blank" class="paper-card">
                <div class="paper-meta">
                    <span>{paper.get('publicationDate', '')}</span>
                    <span style="margin:0 4px;color:#d0d8e4;">·</span>
                    <span class="journal">{paper.get('journal', '')}</span>
                    {latest_badge_html}
                    {citation_html}
                </div>
                <div class="paper-title">{paper.get('title', '')}</div>
                <div class="paper-authors">{paper.get('author', '')}</div>
            </a>
            """
            papers_html += paper_html

        html_content = re.sub(r'{{#papers}}.*?{{/papers}}', papers_html, html_content, flags=re.DOTALL)

        def replace_placeholder(match: re.Match) -> str:
            key = match.group(1).strip()
            if key == 'papers':
                return match.group(0)
            value = data.get(key, "")
            if value is None:
                return ""
            return str(value)

        return re.sub(r'{{\s*([a-zA-Z0-9_]+)\s*}}', replace_placeholder, html_content)


class EmailDomainValidator:
    """邮箱域名验证器 - 检查域名MX记录"""
    
    def __init__(self):
        """初始化域名验证器"""
        self.domain_cache = {}
        logger.info("邮箱域名验证器初始化完成")
    
    def extract_domain(self, email: str) -> str:
        """从邮箱地址提取域名"""
        try:
            return email.split('@')[1].lower().strip()
        except (IndexError, AttributeError):
            return ""
    
    def check_mx_record(self, domain: str) -> bool:
        """检查域名是否有有效的MX记录"""
        if not domain:
            return False
        
        if domain in self.domain_cache:
            return self.domain_cache[domain]
        
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            if mx_records:
                self.domain_cache[domain] = True
                logger.debug(f"域名 {domain} 有有效的MX记录")
                return True
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.Timeout, Exception) as e:
            logger.debug(f"域名 {domain} MX记录查询失败: {e}")
        
        try:
            a_records = dns.resolver.resolve(domain, 'A')
            if a_records:
                self.domain_cache[domain] = True
                logger.debug(f"域名 {domain} 有A记录，可能支持邮件")
                return True
        except Exception as e:
            logger.debug(f"域名 {domain} A记录查询也失败: {e}")
        
        self.domain_cache[domain] = False
        return False
    
    def validate_email_domain(self, email: str) -> Tuple[bool, str]:
        """验证单个邮箱域名
        
        Returns:
            Tuple[bool, str]: (是否有效, 错误信息)
        """
        if not email or '@' not in email:
            return False, "邮箱格式无效"
        
        domain = self.extract_domain(email)
        if not domain:
            return False, "无法提取域名"
        
        if self.check_mx_record(domain):
            return True, ""
        else:
            return False, f"域名 {domain} 无有效MX记录"
    
    def batch_validate_domains(self, emails: List[str]) -> Dict[str, Tuple[bool, str]]:
        """批量验证邮箱域名
        
        Returns:
            Dict[str, Tuple[bool, str]]: {email: (是否有效, 错误信息)}
        """
        logger.info(f"开始批量验证 {len(emails)} 个邮箱域名...")
        
        results = {}
        unique_domains = set()
        
        for email in emails:
            domain = self.extract_domain(email)
            if domain:
                unique_domains.add(domain)
        
        logger.info(f"发现 {len(unique_domains)} 个唯一域名需要验证")
        
        for domain in unique_domains:
            self.check_mx_record(domain)
        
        for email in emails:
            is_valid, error_msg = self.validate_email_domain(email)
            results[email] = (is_valid, error_msg)
        
        valid_count = sum(1 for valid, _ in results.values() if valid)
        invalid_count = len(results) - valid_count
        
        logger.info(f"域名验证完成: {valid_count} 个有效, {invalid_count} 个无效")
        
        return results


class AutomatedEmailSender:
    """自动化邮件发送主类"""
    
    def __init__(
        self,
        config: Dict,
        max_smtp_workers: int = 10,
        delivery_mode: str = "smtp",
        campaign_mode: str = "classic",
        template_variant: str = "A",
        paper_sort: str = "latest",
        top_papers: int = 3
    ):
        """初始化自动化邮件发送器"""
        self.config = config
        self.delivery_mode = delivery_mode
        self.campaign_mode = campaign_mode
        self.template_variant = template_variant.upper()
        self.paper_sort = paper_sort
        self.top_papers = top_papers
        self.invitation_sender = InvitationApiSender(config.get('invitation_api', {}))
        self.domain_validator = EmailDomainValidator()
        self.scholar_api = None

        # invitation_api 模式仅调用新接口，不依赖 Gemini/Bohrium/SMTP
        if self.delivery_mode == "invitation_api":
            self.gemini_api = None
            self.bohrium_api = None
            self.email_sender = None
            self.template_processor = None
        else:
            self.gemini_api = None
            self.bohrium_api = None
            if self.campaign_mode == 'classic':
                self.gemini_api = GeminiAPI(config['gemini']['credentials_path'])
                self.bohrium_api = BohriumAPI(config['bohrium']['access_key'])
            else:
                self.scholar_api = ScholarPaperAPI(config.get('scholar_api', {}))
            self.email_sender = EmailSender(config['smtp'], max_smtp_workers)
            template_path = self._resolve_template_path(config)
            self.template_processor = TemplateProcessor(template_path)
        
        self.stats = {
            'total': 0,
            'success': 0,
            'failed': 0,
            'invalid_domain': 0,
            'errors': []
        }
        
        logger.info(
            f"自动化邮件发送器初始化完成，SMTP并发限制: {max_smtp_workers}, "
            f"发送模式: {delivery_mode}, campaign_mode: {campaign_mode}, "
            f"template_variant: {self.template_variant}, paper_sort: {self.paper_sort}"
        )

    def _resolve_template_path(self, config: Dict) -> str:
        if self.campaign_mode == 'scholar':
            scholar_templates = config.get('templates', {}).get('scholar_cn', {})
            variant_path = scholar_templates.get(self.template_variant)
            if variant_path:
                return variant_path
        return config['template']['path']

    def _pick_name(self, teacher_data: Dict) -> str:
        return (
            teacher_data.get('name')
            or teacher_data.get('中文姓名')
            or teacher_data.get('english_name')
            or teacher_data.get('英文姓名')
            or "老师"
        )

    def _extract_tags(self, research_focus: str) -> List[str]:
        if not research_focus:
            return ["学者主页", "代表成果", "研究方向"]
        parts = [item.strip() for item in re.split(r'[;,，、]+', str(research_focus)) if item.strip()]
        tags = parts[:3]
        while len(tags) < 3:
            tags.append("研究方向")
        return tags

    def _build_scholar_summary(self, teacher_data: Dict, papers: List[Dict]) -> str:
        research_focus = teacher_data.get('interests') or teacher_data.get('research_focus') or "相关方向"
        tags = self._extract_tags(research_focus)
        journals = []
        for paper in papers:
            journal = str(paper.get('journal', '')).strip()
            if journal and journal not in journals:
                journals.append(journal)
        latest_title = ""
        if papers:
            latest_title = str(papers[0].get('title', '')).strip()

        if journals and latest_title:
            return (
                f"主页已收录您在{tags[0]}、{tags[1]}等方向的代表性成果，"
                f"包括近期发表于《{journals[0]}》的 {latest_title} 工作"
                f"{'及《' + journals[1] + '》等权威期刊研究' if len(journals) > 1 else ''}，"
                "可直接用于对外展示研究概况。"
            )

        return (
            f"主页已整合您在{tags[0]}、{tags[1]}等方向的代表性成果，"
            "帮助同行、学生及潜在合作者更便捷地了解您的学术贡献。"
        )

    def _build_scholar_subject(self, name: str) -> str:
        if self.template_variant == 'B':
            return f"{name}教授，您的研究亮点与代表成果已整理完成"
        return f"{name}教授，您的 Bohrium 专属学者主页已生成"

    def _build_scholar_template_data(self, teacher_data: Dict, papers: List[Dict], tracking_open_url: str = "") -> Dict:
        name = self._pick_name(teacher_data)
        institution = (
            teacher_data.get('department')
            or teacher_data.get('中文机构')
            or teacher_data.get('英文机构')
            or "机构信息待补充"
        )
        research_focus = teacher_data.get('interests') or teacher_data.get('研究方向') or "研究方向待补充"
        scholar_id = teacher_data.get('scholarId') or teacher_data.get('scholar_id') or ""
        scholar_page_url = teacher_data.get('scholar_homepage') or teacher_data.get('学者主页') or ""
        tags = self._extract_tags(research_focus)
        paper_section_title = "最新论文" if self.paper_sort == 'latest' else "高被引论文"
        email = teacher_data.get('email', '')
        if not tracking_open_url and email:
            tracking_open_url = (
                "https://your-tracking-host.example.com/api/statistics/email_send"
                f"?source=scholar_cn_{self.template_variant.lower()}&name=EDM&email={email}&sender=science"
            )

        return {
            "name": name,
            "institution": institution,
            "researchFocus": research_focus,
            "scholarId": scholar_id,
            "scholarPageUrl": scholar_page_url,
            "summary": self._build_scholar_summary(teacher_data, papers),
            "paperSectionTitle": paper_section_title,
            "profileActionUrl": scholar_page_url,
            "trackingOpenUrl": tracking_open_url,
            "unsubscribeUrl": "https://www.bohrium.com/settings/notice",
            "tag1": tags[0],
            "tag2": tags[1],
            "tag3": tags[2],
            "totalCitations": teacher_data.get('total_citations') or teacher_data.get('totalCitations') or "--",
            "hIndex": teacher_data.get('h_index') or teacher_data.get('hIndex') or "--",
            "paperCount": teacher_data.get('paper_count') or teacher_data.get('paperCount') or "--",
            "papers": papers,
            "email": email
        }
    
    def read_data(self, file_path: str) -> pd.DataFrame:
        """读取教师数据"""
        logger.info(f"读取数据文件: {file_path}")
        
        try:
            if file_path.endswith('.csv'):
                for encoding in ['utf-8', 'gbk', 'gb2312', 'latin1']:
                    try:
                        df = pd.read_csv(file_path, encoding=encoding)
                        if not df.empty:
                            break
                    except (UnicodeDecodeError, pd.errors.EmptyDataError):
                        continue
                else:
                    raise Exception("无法读取CSV文件")
            else:
                df = pd.read_excel(file_path)
            
            column_mapping = {
                '姓名': 'name',
                '中文姓名': 'name',
                '英文姓名': 'name',
                '邮箱': 'email', 
                '部门': 'department',
                '中文机构': 'department',
                '英文机构': 'department',
                '研究方向': 'interests',
                '兴趣': 'interests',
                '学者主页': 'scholar_homepage',
                'scholarId': 'scholarId',
                'ScholarId': 'scholarId',
                'scholar_id': 'scholarId',
                'source_type': 'source_type',
                'third_author_link': 'third_author_link',
                'Name': 'name',
                'Email': 'email',
                'Department': 'department',
                'Interests': 'interests'
            }
            
            for old_col, new_col in column_mapping.items():
                if old_col not in df.columns:
                    continue
                if new_col not in df.columns:
                    df[new_col] = df[old_col]
                else:
                    # 多来源字段按顺序补空值，避免仅命中第一列导致数据丢失
                    df[new_col] = df[new_col].where(
                        df[new_col].notna() & (df[new_col].astype(str).str.strip() != ''),
                        df[old_col]
                    )

            if '英文姓名' in df.columns and 'english_name' not in df.columns:
                df['english_name'] = df['英文姓名']

            # 对仅有机构字段的数据源做兜底，保证 department 可用
            if 'department' not in df.columns:
                if '英文机构' in df.columns:
                    df['department'] = df['英文机构']
                elif '中文机构' in df.columns:
                    df['department'] = df['中文机构']
            
            required_cols = ['name', 'email', 'department']
            if self.campaign_mode == 'scholar':
                required_cols.extend(['scholarId', 'scholar_homepage'])
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                raise Exception(f"缺少必需的列：{missing_cols}")
            
            df = df.dropna(subset=['name', 'email', 'department'])
            
            logger.info(f"成功读取 {len(df)} 条教师数据")
            return df
            
        except Exception as e:
            logger.error(f"读取数据文件失败: {e}")
            raise
    
    def process_teacher(self, teacher_data: Dict, domain_validation_result: Tuple[bool, str] = None) -> Dict:
        """处理单个教师的数据"""
        name = teacher_data['name']
        email = teacher_data['email']
        department = teacher_data['department']
        interests = teacher_data.get('interests', '')
        
        result = {
            'name': name,
            'email': email,
            'success': False,
            'error': None,
            'field': '',
            'uuid': '',
            'share_link': '',
            'scholar_id': teacher_data.get('scholarId', ''),
            'template_variant': self.template_variant,
            'paper_sort': self.paper_sort,
            'invitation_url': '',
            'status': '',
            'status_emoji': ''
        }
        
        if domain_validation_result:
            is_valid, error_msg = domain_validation_result
            if not is_valid:
                result['error'] = f"🚫 无效域名: {error_msg}"
                result['status'] = 'invalid_domain'
                result['status_emoji'] = '🚫'
                logger.warning(f"🚫 跳过无效域名邮箱: {name} ({email}) - {error_msg}")
                return result
        
        try:
            logger.info(f"开始处理教师: {name}")

            if self.delivery_mode == 'invitation_api':
                api_success, invitation_url, api_error = self.invitation_sender.send_invitation(email)
                result['invitation_url'] = invitation_url
                if api_success:
                    result['success'] = True
                    result['status'] = 'api_sent_success'
                    result['status_emoji'] = '✅'
                    logger.info(f"✅ 邀请接口发送成功: {name}")
                else:
                    result['error'] = f"❌ 邀请接口发送失败: {api_error}"
                    result['status'] = 'api_failed'
                    result['status_emoji'] = '❌'
                    logger.error(f"❌ 邀请接口发送失败: {name} - {api_error}")
                return result
            
            if self.campaign_mode == 'scholar':
                scholar_id = teacher_data.get('scholarId') or teacher_data.get('scholar_id')
                if not scholar_id:
                    raise Exception("缺少 scholarId")

                logger.info(f"为 {name} 获取学者论文: scholarId={scholar_id}, sort={self.paper_sort}")
                papers = self.scholar_api.fetch_papers(
                    scholar_id=scholar_id,
                    sort_key=self.paper_sort,
                    top_n=self.top_papers
                )
                template_data = self._build_scholar_template_data(teacher_data, papers)
                html_content = self.template_processor.replace_variables(template_data)
                subject = self._build_scholar_subject(name)
                result['share_link'] = template_data.get('scholarPageUrl', '')
            else:
                logger.info(f"为 {name} 生成科学问题...")
                field = self.gemini_api.generate_scientific_question(name, department, interests)
                if not field:
                    raise Exception("生成科学问题失败")

                result['field'] = field

                logger.info(f"为 {name} 获取研究概述...")
                template_data, uuid = self.bohrium_api.get_research_overview(field)
                result['uuid'] = uuid

                share_link = f"https://www.bohrium.com/ai-search/share/{uuid}?utm_source=email&utm_term=science&utm_campaign=edm"
                result['share_link'] = share_link

                template_data.update({
                    'name': name,
                    'field': field,
                    'uuid': uuid,
                    'email': email
                })

                html_content = self.template_processor.replace_variables(template_data)
                subject = f'''Explore New Advances in {field}'''

            smtp_success = self.email_sender.send_email(email, subject, html_content)
            api_success = True
            api_error = ""
            invitation_url = ""

            if self.delivery_mode == 'both':
                api_success, invitation_url, api_error = self.invitation_sender.send_invitation(email)
                result['invitation_url'] = invitation_url

            if smtp_success and api_success:
                result['success'] = True
                result['status'] = 'sent_success' if self.delivery_mode == 'smtp' else 'both_success'
                result['status_emoji'] = '✅'
                logger.info(f"✅ 成功处理教师: {name}")
            else:
                if self.delivery_mode == 'both':
                    if smtp_success and not api_success:
                        result['error'] = f"❌ SMTP成功，但邀请接口失败: {api_error}"
                        result['status'] = 'smtp_success_api_failed'
                    elif (not smtp_success) and api_success:
                        result['error'] = "❌ 邀请接口成功，但SMTP发送失败"
                        result['status'] = 'smtp_failed_api_success'
                    else:
                        result['error'] = f"❌ SMTP与邀请接口均失败: {api_error}"
                        result['status'] = 'both_failed'
                else:
                    result['error'] = "❌ 邮件发送失败 (SMTP错误)"
                    result['status'] = 'smtp_failed'
                result['status_emoji'] = '❌'
                logger.error(f"❌ 邮件发送失败: {name}")
            
        except Exception as e:
            result['error'] = f"❌ 处理失败: {str(e)}"
            result['status'] = 'processing_failed'
            result['status_emoji'] = '❌'
            logger.error(f"❌ 处理教师失败 {name}: {e}")
        
        return result
    
    def process_batch(self, df: pd.DataFrame, max_workers: int = 25) -> List[Dict]:
        """批量处理教师数据"""
        self.stats['total'] = len(df)
        results = []
        
        logger.info(f"开始批量处理 {len(df)} 位教师，并发数: {max_workers}")
        
        emails = df['email'].tolist()
        domain_validation_results = self.domain_validator.batch_validate_domains(emails)
        
        valid_emails = []
        invalid_emails = []
        
        for email, (is_valid, error_msg) in domain_validation_results.items():
            if is_valid:
                valid_emails.append(email)
            else:
                invalid_emails.append((email, error_msg))
        
        logger.info(f"域名验证结果: {len(valid_emails)} 个有效邮箱, {len(invalid_emails)} 个无效邮箱")
        
        for _, row in df.iterrows():
            email = row['email']
            if email in [invalid_email for invalid_email, _ in invalid_emails]:
                error_msg = next(msg for e, msg in invalid_emails if e == email)
                result = {
                    'name': row['name'],
                    'email': email,
                    'success': False,
                    'error': f"🚫 无效域名: {error_msg}",
                    'field': '',
                    'uuid': '',
                    'share_link': '',
                    'status': 'invalid_domain',
                    'status_emoji': '🚫'
                }
                results.append(result)
                self.stats['invalid_domain'] += 1
                self.stats['errors'].append({
                    'name': row['name'],
                    'error': result['error']
                })
                logger.warning(f"🚫 跳过无效域名: {row['name']} ({email}) - {error_msg}")
        
        valid_df = df[df['email'].isin(valid_emails)]
        logger.info(f"开始处理 {len(valid_df)} 位有效邮箱教师...")
        
        processed_emails = set()
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_teacher = {}
            for _, row in valid_df.iterrows():
                email = row['email']
                # 跳过重复邮箱
                if email in processed_emails:
                    logger.warning(f"⚠️ 跳过重复邮箱: {row['name']} ({email})")
                    self.stats['errors'].append({
                        'name': row['name'],
                        'error': f"⚠️ 重复邮箱，已跳过: {email}"
                    })
                    continue
                processed_emails.add(email)
                validation_result = domain_validation_results[email]
                future = executor.submit(self.process_teacher, row.to_dict(), validation_result)
                future_to_teacher[future] = row['name']
            
            for future in as_completed(future_to_teacher):
                teacher_name = future_to_teacher[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    if result['success']:
                        self.stats['success'] += 1
                    elif result.get('status') == 'invalid_domain':
                        self.stats['invalid_domain'] += 1
                    else:
                        self.stats['failed'] += 1
                        self.stats['errors'].append({
                            'name': teacher_name,
                            'error': result['error']
                        })
                        
                except Exception as e:
                    logger.error(f"处理教师 {teacher_name} 时发生异常: {e}")
                    self.stats['failed'] += 1
                    self.stats['errors'].append({
                        'name': teacher_name,
                        'error': str(e)
                    })
                    results.append({
                        'name': teacher_name,
                        'email': '',
                        'success': False,
                        'error': f"❌ 处理异常: {str(e)}",
                        'field': '',
                        'uuid': '',
                        'share_link': '',
                        'status': 'processing_failed',
                        'status_emoji': '❌'
                    })
        
        return results
    
    def save_results(self, results: List[Dict], output_path: str):
        """保存处理结果"""
        df_results = pd.DataFrame(results)
        df_results.to_excel(output_path, index=False)
        logger.info(f"处理结果已保存到: {output_path}")
    
    def print_summary(self):
        """打印处理摘要"""
        logger.info("=" * 50)
        logger.info("处理摘要:")
        logger.info(f"总计: {self.stats['total']}")
        logger.info(f"✅ 发送成功: {self.stats['success']}")
        logger.info(f"❌ 发送失败: {self.stats['failed']}")
        logger.info(f"🚫 无效域名 (提前过滤): {self.stats['invalid_domain']}")
        
        if self.stats['total'] > 0:
            success_rate = self.stats['success'] / self.stats['total'] * 100
            logger.info(f"成功率: {success_rate:.1f}%")
            
            valid_emails = self.stats['total'] - self.stats['invalid_domain']
            if valid_emails > 0:
                valid_success_rate = self.stats['success'] / valid_emails * 100
                logger.info(f"有效邮箱成功率: {valid_success_rate:.1f}%")
        
        if self.stats['errors']:
            logger.info("\n详细错误信息:")
            for error in self.stats['errors']:
                logger.info(f"  {error['name']}: {error['error']}")
        
        logger.info("=" * 50)


def load_config(config_path: str = "config_en.json") -> Dict:
    """加载配置文件"""
    def deep_merge(base: Dict, override: Dict) -> Dict:
        merged = dict(base)
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    default_config = {
        "gemini": {
            "credentials_path": os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "YOUR_GOOGLE_APPLICATION_CREDENTIALS.json")
        },
        "bohrium": {
            "access_key": os.getenv("BOHRIUM_ACCESS_KEY", "YOUR_BOHRIUM_ACCESS_KEY")
        },
        "smtp": {
            "server": os.getenv("SMTP_SERVER", "YOUR_SMTP_SERVER"),
            "port": int(os.getenv("SMTP_PORT", "465")),
            "user": os.getenv("SMTP_USER", "YOUR_SMTP_USER"),
            "password": os.getenv("SMTP_PASSWORD", "YOUR_SMTP_PASSWORD")
        },
        "invitation_api": {
            "enabled": False,
            "url": os.getenv("INVITATION_API_URL", "https://your-api.example.com/api/activity/assistance/email_invitation_url"),
            "language": os.getenv("INVITATION_API_LANGUAGE", "en-us"),
            "utm_source": os.getenv("INVITATION_API_UTM_SOURCE", "test_invitation"),
            "timeout": 15,
            "headers": {}
        },
        "template": {
            "path": "index_en.html"
        },
        "templates": {
            "scholar_cn": {
                "A": "index_scholar_cn_vA.html",
                "B": "index_scholar_cn_vB.html"
            }
        },
        "scholar_api": {
            "base_url": os.getenv("SCHOLAR_API_BASE_URL", "https://your-scholar-api.example.com"),
            "path": os.getenv("SCHOLAR_API_PATH", "/api/v1/paper/scholar/paper"),
            "timeout": 20,
            "random": 8,
            "isShowCitationNum": True,
            "sort_mapping": {
                "latest": 1,
                "highly_cited": 2
            },
            "headers": {}
        }
    }
    
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config = deep_merge(default_config, config)
            logger.info(f"配置文件加载成功: {config_path}")
            return config
        except Exception as e:
            logger.warning(f"配置文件加载失败，使用默认配置: {e}")
    else:
        logger.info("配置文件不存在，使用默认配置")
    
    return default_config


def get_next_sequential_output_path(base_path: str, prefix: str = 'result_', ext: str = '.xlsx', min_pad: int = 2) -> str:
    """根据现有文件生成下一个不冲突的顺序输出路径，例如 result_01.xlsx、result_02.xlsx。

    规则：
    - 如果 base_path 是目录，则在该目录下生成 result_XX.xlsx
    - 如果 base_path 是文件路径，则在该文件所在目录下生成 result_XX.xlsx
    - 如果 base_path 不存在且看起来像目录（无扩展名），则在该目录下生成并创建目录
    - 序号至少 2 位，不覆盖已有文件，递增到下一个可用编号
    """
    # 判定目标目录
    if os.path.isdir(base_path):
        target_dir = base_path
    else:
        root, extension = os.path.splitext(base_path)
        # 无扩展名，视为目录
        if extension == '':
            target_dir = base_path if base_path else '.'
        else:
            target_dir = os.path.dirname(base_path) or '.'

    os.makedirs(target_dir or '.', exist_ok=True)

    pattern = re.compile(rf'^{re.escape(prefix)}(\d+){re.escape(ext)}$')
    max_index = 0
    try:
        for filename in os.listdir(target_dir or '.'):
            match = pattern.match(filename)
            if match:
                try:
                    idx = int(match.group(1))
                    if idx > max_index:
                        max_index = idx
                except ValueError:
                    continue
    except FileNotFoundError:
        # 目录不存在时已在上方创建，这里忽略
        pass

    next_index = max_index + 1
    pad_width = max(min_pad, len(str(next_index)))
    filename = f"{prefix}{str(next_index).zfill(pad_width)}{ext}"
    return os.path.join(target_dir or '.', filename)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='自动化邮件发送脚本')
    parser.add_argument('--data', '-d', required=True, help='教师数据文件路径 (Excel/CSV)')
    parser.add_argument('--config', '-c', default='config_en.json', help='配置文件路径')
    parser.add_argument('--output', '-o', default='results.xlsx', help='结果输出文件路径（可为文件或目录）')
    parser.add_argument('--workers', '-w', type=int, default=25, help='API处理并发数量')
    parser.add_argument('--smtp-workers', type=int, default=10, help='SMTP发送并发数量')
    parser.add_argument(
        '--delivery-mode',
        choices=['smtp', 'invitation_api', 'both'],
        default='smtp',
        help='发送模式: smtp(默认) / invitation_api(仅调新接口) / both(两者都发)'
    )
    parser.add_argument(
        '--campaign-mode',
        choices=['classic', 'scholar'],
        default='scholar',
        help='邮件活动模式: classic(旧的领域推荐流程) / scholar(学者主页流程)'
    )
    parser.add_argument(
        '--template-variant',
        choices=['A', 'B', 'a', 'b'],
        default='A',
        help='scholar 模板版本: A(主页通知) / B(研究亮点)'
    )
    parser.add_argument(
        '--paper-sort',
        choices=['latest', 'highly_cited'],
        default='latest',
        help='scholar 论文排序策略'
    )
    parser.add_argument('--top-papers', type=int, default=3, help='邮件中展示的论文数量')
    parser.add_argument('--template', '-t', help='邮件模板文件路径')
    parser.add_argument('--timestamp', action='store_true', help='在输出文件名中追加时间戳（如 results_20250819_153012.xlsx）')
    
    args = parser.parse_args()
    
    try:
        config = load_config(args.config)
        
        if args.template:
            if args.campaign_mode == 'scholar':
                config.setdefault('templates', {}).setdefault('scholar_cn', {})[args.template_variant.upper()] = args.template
            else:
                config['template']['path'] = args.template
        
        sender = AutomatedEmailSender(
            config,
            max_smtp_workers=args.smtp_workers,
            delivery_mode=args.delivery_mode,
            campaign_mode=args.campaign_mode,
            template_variant=args.template_variant,
            paper_sort=args.paper_sort,
            top_papers=args.top_papers
        )
        
        df = sender.read_data(args.data)
        
        results = sender.process_batch(df, max_workers=args.workers)
        
        # 计算最终输出路径（顺序命名：result_01.xlsx、result_02.xlsx ...）
        output_base = args.output or '.'
        output_path = get_next_sequential_output_path(output_base, prefix='result_', ext='.xlsx', min_pad=2)
        # 确保目录存在
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        
        sender.save_results(results, output_path)
        
        sender.print_summary()
        
    except Exception as e:
        logger.error(f"程序执行失败: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())
