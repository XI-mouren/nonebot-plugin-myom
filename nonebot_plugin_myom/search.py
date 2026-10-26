# Copyright (C) 2026 Funny1Potato
# Modified by XI-mouren, 2026
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

from abc import ABC, abstractmethod

import httpx
from nonebot import logger

from .config import plugin_config


class SearchClient(ABC):
    """搜索客户端基类"""

    @abstractmethod
    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        """执行搜索
        
        Args:
            query: 搜索查询词
            max_results: 最大结果数
            
        Returns:
            搜索结果列表，每项包含 title, content, url
        """
        pass

    def format_results(self, results: list[dict]) -> str:
        """格式化搜索结果为 LLM 可读文本"""
        if not results:
            return "未找到相关搜索结果"
        
        formatted = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            formatted.append(f"{i}. {title}\n   {content}\n   来源: {url}")
        
        return "\n\n".join(formatted)


class TavilySearchClient(SearchClient):
    """Tavily 搜索客户端（需要安装 tavily-python）"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = None

    async def _get_client(self):
        if self._client is None:
            try:
                from tavily import AsyncTavilyClient
                self._client = AsyncTavilyClient(api_key=self.api_key)
            except ImportError:
                logger.error("tavily-python 未安装，请运行: pip install tavily-python")
                raise
        return self._client

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            client = await self._get_client()
            response = await client.search(
                query=query,
                max_results=max_results,
                search_depth="basic",
                include_answer=False,
            )
            
            results = []
            for item in response.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    "content": item.get("content", ""),
                    "url": item.get("url", ""),
                })
            
            logger.info(f"[搜索] Tavily 搜索 '{query}' 返回 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"[搜索] Tavily 搜索失败: {e}")
            return []


class BochaSearchClient(SearchClient):
    """Bocha AI 搜索客户端（国产 AI 搜索 API，使用 httpx 异步请求）"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.bochaai.com/v1/web-search"

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "query": query,
                "count": max_results,
                "summary": False,
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

            results = []
            for item in data.get("data", {}).get("webPages", {}).get("value", []):
                results.append({
                    "title": item.get("name", ""),
                    "content": item.get("snippet", ""),
                    "url": item.get("url", ""),
                })

            logger.info(f"[搜索] Bocha 搜索 '{query}' 返回 {len(results)} 条结果")
            return results

        except Exception as e:
            logger.error(f"[搜索] Bocha 搜索失败: {e}")
            return []


class BingSearchClient(SearchClient):
    """Bing Web Search API 客户端（使用 httpx 异步请求，需要 Bing API Key）"""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.bing.microsoft.com/v7.0/search"

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {"Ocp-Apim-Subscription-Key": self.api_key}
            params = {
                "q": query,
                "count": max_results,
                "textDecorations": True,
                "textFormat": "HTML",
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.endpoint, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
            
            results = []
            for item in data.get("webPages", {}).get("value", []):
                results.append({
                    "title": item.get("name", ""),
                    "content": item.get("snippet", ""),
                    "url": item.get("url", ""),
                })
            
            logger.info(f"[搜索] Bing 搜索 '{query}' 返回 {len(results)} 条结果")
            return results
            
        except Exception as e:
            logger.error(f"[搜索] Bing 搜索失败: {e}")
            return []


class DeepSeekSearchClient(SearchClient):
    """DeepSeek 网页搜索客户端
    文档：https://api-docs.deepseek.com/zh-cn/guides/search
    端点：POST https://api.deepseek.com/v1/tools/search（Bearer 认证）
    响应核心字段：results[{title, url, snippet, summary, ...}]
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.endpoint = "https://api.deepseek.com/v1/tools/search"

    async def search(self, query: str, max_results: int = 3) -> list[dict]:
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "query": query[:128],  # DeepSeek 限制 query 最长 128 字符，防 LLM 生成超长查询
                "count": max_results,
                "need_content": False,  # 不取网页正文；summary 长摘要已足够注入 prompt
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(self.endpoint, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()

            results = []
            for item in data.get("results", []):
                results.append({
                    "title": item.get("title", ""),
                    # summary（模型向长摘要，600-800字）优先，snippet（短摘要）兜底
                    "content": item.get("summary") or item.get("snippet", ""),
                    "url": item.get("url", ""),
                })
            logger.info(f"[搜索] DeepSeek 搜索 '{query}' 返回 {len(results)} 条结果")
            return results
        except Exception as e:
            logger.error(f"[搜索] DeepSeek 搜索失败: {e}")
            return []


def create_search_client() -> SearchClient | None:
    """根据配置创建搜索客户端
    
    Returns:
        SearchClient 实例，如果搜索未启用则返回 None
    """
    if not plugin_config.aigf_search_enabled:
        return None

    api = plugin_config.aigf_search_api.lower()
    api_key = plugin_config.aigf_search_api_key
    
    if api == "tavily":
        if not api_key:
            logger.warning("[搜索] Tavily API Key 未配置（AIGF_SEARCH_API_KEY），搜索功能禁用")
            return None
        return TavilySearchClient(api_key)
    
    elif api == "bocha":
        if not api_key:
            logger.warning("[搜索] Bocha API Key 未配置（AIGF_SEARCH_API_KEY），搜索功能禁用")
            return None
        return BochaSearchClient(api_key)
    
    elif api == "bing":
        if not api_key:
            logger.warning("[搜索] Bing API Key 未配置（AIGF_SEARCH_API_KEY），搜索功能禁用")
            return None
        return BingSearchClient(api_key)

    elif api == "deepseek":
        if not api_key:
            logger.warning("[搜索] DeepSeek API Key 未配置（AIGF_SEARCH_API_KEY），搜索功能禁用")
            return None
        return DeepSeekSearchClient(api_key)

    else:
        logger.warning(f"[搜索] 不支持的搜索 API: {api}")
        return None


# 全局搜索客户端实例
_search_client: SearchClient | None = None


def get_search_client() -> SearchClient | None:
    """获取全局搜索客户端实例"""
    global _search_client
    if _search_client is None and plugin_config.aigf_search_enabled:
        _search_client = create_search_client()
    return _search_client
