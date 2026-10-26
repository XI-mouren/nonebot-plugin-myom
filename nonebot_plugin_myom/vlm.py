# Copyright (C) 2025 shadow3aaa <shadow3aaaa@gmail.com>
# Modified by Funny1Potato, 2026
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

from openai import AsyncOpenAI

from .client import _emit_usage


class VLM:
    def __init__(self, api_key: str, model: str = "Pro/Qwen/Qwen2.5-VL-7B-Instruct",
                 base_url: str = "https://api.siliconflow.cn/v1", timeout: int = 60,
                 http_client=None, max_tokens: int = 0):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        self.model = model
        self.timeout = timeout
        self.max_tokens = int(max_tokens or 0)  # 0 = 不限制

    async def request(self, prompt: str, image_base64: str, image_format: str,
                      group_id: str = "") -> str | None:
        """请求 VLM；group_id 提供时该次调用计入对应群的用量统计"""
        kwargs: dict = {}
        if self.max_tokens > 0:
            kwargs["max_tokens"] = self.max_tokens
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/{image_format};base64,{image_base64}"}},
                {"type": "text", "text": prompt},
            ]}],
            timeout=self.timeout,
            **kwargs,
        )
        _emit_usage(response, group_id, source="vlm")
        return response.choices[0].message.content
