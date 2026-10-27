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

import hashlib
import json
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import anyio
from nonebot import logger
import nonebot_plugin_localstore as store
from PIL import Image
from .config import plugin_config


@dataclass
class TextMessage:
    content: str

@dataclass
class MemeMessage:
    meme_id: str

@dataclass
class AtMessage:
    user_name: str

@dataclass
class Meme:
    id: str
    path: str          # 相对文件名，如 "afdba23d055a.gif"
    keywords: list[str]
    description: str
    usage_count: int = 0    # 被发送的次数（使用频率，清理时判定依据）
    last_used: float = 0.0  # 最近一次被发送的时间戳（0 表示从未被发送过）


def _meme_dir() -> Path:
    """表情包目录（数据目录）"""
    d = store.get_plugin_data_dir() / "memes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_dir() -> Path:
    """表情包缓存目录（与记忆数据同在数据目录下，便于统一查看和备份）"""
    d = store.get_plugin_data_dir() / "sticker_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


class MemeManager:
    def __init__(self):
        self.admin_memes: dict[str, Meme] = {}
        self.collected_memes: dict[str, Meme] = {}
        self._recent: list[str] = []
        self._recent_max = 5
        self._cache_index: dict[str, dict] = {}

    @property
    def memes(self) -> dict[str, Meme]:
        return {**self.collected_memes, **self.admin_memes}

    def _full_path(self, filename: str) -> Path:
        """将相对文件名转为完整路径"""
        return _meme_dir() / filename

    async def load_all(self):
        self.admin_memes = await self._load_file(_meme_dir() / "memes.json")
        self.collected_memes = await self._load_file(_meme_dir() / "collected.json")
        logger.success(f"[启动] 表情包加载完成: 管理员 {len(self.admin_memes)} 个, 自动收集 {len(self.collected_memes)} 个")

    async def _load_file(self, path: Path) -> dict[str, Meme]:
        if not path.exists():
            return {}
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                data = json.loads(await f.read())
            result = {}
            for item in data:
                full_path = _meme_dir() / item["path"]
                if full_path.exists():
                    result[item["id"]] = Meme(
                        id=item["id"], path=item["path"],
                        keywords=item.get("keywords", []),
                        description=item.get("description", ""),
                        usage_count=item.get("usage_count", 0),
                        last_used=item.get("last_used", 0.0),
                    )
            return result
        except Exception as e:
            logger.error(f"加载 {path.name} 失败: {e}")
            return {}

    async def _save_index(self, memes: dict[str, Meme], path: Path):
        data = [
            {"id": m.id, "path": m.path, "keywords": m.keywords, "description": m.description,
             "usage_count": m.usage_count, "last_used": m.last_used}
            for m in memes.values()
        ]
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    async def _save_collected(self):
        await self._save_index(self.collected_memes, _meme_dir() / "collected.json")

    def prompt_list(self) -> str:
        lines = []
        for meme in self.memes.values():
            kw = "、".join(meme.keywords)
            lines.append(f"- [{meme.id}] {meme.description}（适用场景：{kw}）")
        return "\n".join(lines)

    def select_candidate_items(self, match_text: str, limit: int = 20) -> list:
        """按当前对话文本预筛选表情包候选，返回 Meme 对象列表。

        匹配规则：keywords 命中（+2 分）、description 分词命中（+1 分）；
        命中的按分数排序，不足 limit 时用使用频率最高的未命中条目补足
        （保证模型总有可选，同时全量 500 条不再整体注入 prompt）。
        """
        text_lower = (match_text or "").lower()
        hit, miss = [], []
        for meme in self.memes.values():
            score = 0
            for kw in meme.keywords:
                if kw and kw.lower() in text_lower:
                    score += 2
            for word in re.split(r"[，。！？、,.!?\s（）()【】「」]+", meme.description):
                word = word.strip()
                if len(word) >= 2 and word.lower() in text_lower:
                    score += 1
            (hit if score > 0 else miss).append((score, meme))
        hit.sort(key=lambda x: -x[0])
        miss.sort(key=lambda x: -x[1].usage_count)
        picked = [m for _, m in hit[:limit]]
        if len(picked) < limit:
            # 补足时同样要拆包：miss 里存的是 (score, meme) 元组，
            # 直接 += 会让调用方拿到 tuple，取 m.id 时抛 AttributeError
            picked += [m for _, m in miss[: limit - len(picked)]]
        return picked

    def select_candidates(self, match_text: str, limit: int = 20) -> str:
        """预筛选候选并格式化为 prompt 文本（保留原接口，供旧调用点使用）"""
        return "\n".join(
            f"- [{m.id}] {m.description}（适用场景：{'、'.join(m.keywords)}）"
            for m in self.select_candidate_items(match_text, limit))

    async def resolve(self, meme_id: str) -> str | None:
        meme = self.memes.get(meme_id)
        if not meme or meme_id in self._recent:
            return None
        self._recent.append(meme_id)
        if len(self._recent) > self._recent_max:
            self._recent.pop(0)
        # 记录使用频率（清理时按此判定"使用频率最低"）
        meme.usage_count += 1
        meme.last_used = time.time()
        # 使用统计只回写自动收集库；管理员库可能被手动维护，不回写（内存中累计即可）
        if meme_id in self.collected_memes:
            try:
                await self._save_collected()
            except Exception as e:
                logger.error(f"[resolve] 保存使用统计失败: {e}")
        path = str(self._full_path(meme.path))
        logger.debug(f"[resolve] {meme_id} -> {path}")
        return path

    async def auto_collect(self, image_bytes: bytes, description: str) -> bool:
        file_hash = hashlib.md5(image_bytes).hexdigest()[:12]
        for meme in self.memes.values():
            if file_hash in meme.path:
                return False
        filename = await self._save_image(image_bytes, file_hash)
        meme_id = self._generate_id(description)
        keywords = self._extract_keywords(description)
        self.collected_memes[meme_id] = Meme(
            id=meme_id, path=filename, keywords=keywords, description=description[:50],
        )
        await self._save_collected()
        logger.debug(f"[自动收藏] 生成条目: {meme_id}")
        await self.cleanup()
        return True

    async def _save_image(self, image_bytes: bytes, file_hash: str) -> str:
        """保存图片到 memes 目录，返回相对文件名"""
        try:
            img_format = Image.open(BytesIO(image_bytes)).format
        except Exception:
            img_format = None
        ext = "gif" if img_format == "GIF" else "jpg" if img_format in ("JPEG", "JPG") else "png"
        filename = f"{file_hash}.{ext}"
        full_path = _meme_dir() / filename
        if not full_path.exists():
            async with await anyio.open_file(full_path, "wb") as f:
                await f.write(image_bytes)
        return filename

    def _generate_id(self, description: str) -> str:
        cleaned = re.sub(r"[，。！？、\s]+", "_", description[:20])
        cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", cleaned) or "meme"
        return f"{cleaned}_{hashlib.md5(description.encode()).hexdigest()[:4]}"

    def _extract_keywords(self, description: str) -> list[str]:
        parts = re.split(r"[，、,/\s]+", description.strip())
        keywords = [p.strip() for p in parts if p.strip() and len(p.strip()) <= 4]
        return keywords[:3] or ["表情包"]

    # ---- 缓存管理 ----

    async def save_to_cache(self, image_bytes: bytes, description: str, emotion: str) -> str:
        """保存表情包到缓存，返回 cache_id"""
        cache_id = hashlib.md5(image_bytes).hexdigest()[:12]
        try:
            img_format = Image.open(BytesIO(image_bytes)).format
        except Exception:
            img_format = None
        ext = "gif" if img_format == "GIF" else "jpg" if img_format in ("JPEG", "JPG") else "png"
        cache_path = _cache_dir() / f"{cache_id}.{ext}"
        if not cache_path.exists():
            async with await anyio.open_file(cache_path, "wb") as f:
                await f.write(image_bytes)
        self._cache_index[cache_id] = {
            "path": str(cache_path),
            "description": description,
            "emotion": emotion,
        }
        logger.info(f"[缓存] 表情包已缓存: {cache_id}")
        return cache_id

    def get_cached_stickers(self) -> list[dict]:
        """获取当前缓存中的所有表情包信息"""
        return [
            {"id": cid, "description": info["description"], "emotion": info["emotion"]}
            for cid, info in self._cache_index.items()
        ]

    async def save_from_cache(self, cache_id: str, description: str, keywords: list[str]) -> bool:
        """从缓存中保存表情包到正式目录，使用 LLM 提供的描述和关键词"""
        cache_info = self._cache_index.get(cache_id)
        if not cache_info:
            logger.warning(f"[缓存] 未找到 cache_id={cache_id}")
            return False
        cache_path = Path(cache_info["path"])
        if not cache_path.exists():
            logger.warning(f"[缓存] 缓存文件不存在: {cache_path}")
            return False
        async with await anyio.open_file(cache_path, "rb") as f:
            image_bytes = await f.read()
        file_hash = hashlib.md5(image_bytes).hexdigest()[:12]
        for meme in self.memes.values():
            if file_hash in meme.path:
                return False
        ext = cache_path.suffix
        filename = f"{file_hash}{ext}"
        dest = _meme_dir() / filename
        async with await anyio.open_file(dest, "wb") as f:
            await f.write(image_bytes)
        # cache_id 本身就是 hash，直接用作 meme id
        self.collected_memes[cache_id] = Meme(
            id=cache_id, path=filename, keywords=keywords, description=description,
        )
        await self._save_collected()
        logger.success(f"[表情包收藏] 已保存到表情包库: {cache_id} - {description[:30]}")
        # 收藏后检查是否触及上限，需要时清理使用频率最低的条目以腾出空位
        await self.cleanup()
        return True

    def clear_cache(self):
        """清空内存中的缓存索引（不删磁盘文件，供下次重建）"""
        self._cache_index.clear()

    async def cleanup_cache(self, max_age_hours: float = 168) -> tuple[int, int]:
        """清理 sticker_cache 中超过 max_age_hours 的文件，返回 (删除数, 释放字节)。

        候选描述只存在于内存索引 _cache_index，进程重启即失效；磁盘上的图片一旦
        不在索引里，就永远不会再被模型看到，因此按时间回收是安全的。顺带把索引中
        指向已消失文件的条目一并移除，避免收藏时踩到「缓存文件不存在」。

        max_age_hours <= 0 视为不清理（配置填 0 不应把整个候选池删光）；需要立即
        清空请用 clear_cache_dir()。
        """
        if max_age_hours <= 0:
            return 0, 0
        now = time.time()
        deleted = 0
        freed = 0
        cache = _cache_dir()
        for f in cache.iterdir():
            if not f.is_file():
                continue
            try:
                if (now - f.stat().st_mtime) / 3600 <= max_age_hours:
                    continue
                size = f.stat().st_size
                f.unlink()
                deleted += 1
                freed += size
            except OSError as e:
                logger.warning(f"[清理] 删除缓存文件失败 {f.name}: {e}")
        for cid in [c for c, info in self._cache_index.items()
                    if not Path(info["path"]).exists()]:
            del self._cache_index[cid]
        if deleted:
            logger.info(f"[清理] sticker_cache: 删除 {deleted} 个过期文件，"
                        f"释放 {freed / 1048576:.1f} MB")
        return deleted, freed

    async def clear_cache_dir(self) -> tuple[int, int]:
        """清空 sticker_cache 全部文件并清空内存索引，返回 (删除数, 释放字节)。"""
        deleted = 0
        freed = 0
        for f in _cache_dir().iterdir():
            if not f.is_file():
                continue
            try:
                size = f.stat().st_size
                f.unlink()
                deleted += 1
                freed += size
            except OSError as e:
                logger.warning(f"[清理] 删除缓存文件失败 {f.name}: {e}")
        self._cache_index.clear()
        if deleted:
            logger.info(f"[清理] sticker_cache: 已清空 {deleted} 个文件，"
                        f"释放 {freed / 1048576:.1f} MB")
        return deleted, freed

    def cache_stats(self) -> tuple[int, int]:
        """sticker_cache 当前文件数、占用字节数。"""
        count = 0
        total = 0
        try:
            for f in _cache_dir().iterdir():
                if f.is_file():
                    count += 1
                    total += f.stat().st_size
        except OSError:
            pass
        return count, total

    async def cleanup(self, max_count: int = 0):
        """表情包数量达到上限时触发清理：删除使用频率最低的一批条目（图片文件 + 描述索引），
        使数量回落到 max_count - reserve，从而永远空出 reserve 个位置接收新表情包。
        判定依据：usage_count（发送次数）最低者优先；次数相同时，最久未使用的优先
        （从未被发送过的，用图片文件的修改时间兜底，越早收藏的越先被清理）。
        仅清理自动收集库（collected_memes），管理员手动添加的表情包不受影响。"""
        if max_count <= 0:
            max_count = plugin_config.aigf_meme_max_count
        if max_count <= 0:
            # 上限配成 0 或负数视为「不限制」——绝不能因为配置写错就把整个库清空
            return
        reserve = max(0, min(plugin_config.aigf_meme_reserve_count, max_count))
        target = max_count - reserve  # 清理后应保留的目标数量
        if len(self.collected_memes) < max_count:
            return
        remove_count = len(self.collected_memes) - target
        if remove_count <= 0:
            return

        def _last_active(meme: Meme) -> float:
            if meme.last_used > 0:
                return meme.last_used
            try:
                return self._full_path(meme.path).stat().st_mtime
            except OSError:
                return 0.0

        ordered = sorted(self.collected_memes.values(),
                         key=lambda m: (m.usage_count, _last_active(m)))
        to_remove = ordered[:remove_count]
        for meme in to_remove:
            meme_path = self._full_path(meme.path)
            if meme_path.exists():
                meme_path.unlink()
            self.collected_memes.pop(meme.id, None)
        await self._save_collected()
        logger.info(f"[清理] 表情包清理: 删除使用频率最低的 {len(to_remove)} 个, "
                    f"剩余 {len(self.collected_memes)} 个（保留 {reserve} 个空位）")


meme_manager = MemeManager()
