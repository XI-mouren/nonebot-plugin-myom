# Copyright (C) 2025 shadow3aaa <shadow3aaaa@gmail.com>
# Modified by Funny1Potato, 2026
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

from dataclasses import asdict, dataclass, field
import json
import anyio
from nonebot import logger
import nonebot_plugin_localstore as store


@dataclass
class RolePreset:
    name: str
    role: str
    knowledges: list[str] = field(default_factory=list)
    hidden: bool = False


_DEFAULT_PRESET = RolePreset(
    name="小助手",
    role="一个友好的群聊助手，会用轻松的语气和大家聊天",
)

PRESETS: dict[str, RolePreset] = {}
PRESETS_DIR = store.get_plugin_config_dir() / "presets"


async def load_presets():
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    default_file = PRESETS_DIR / "default.json"
    if not default_file.exists():
        async with await anyio.open_file(default_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps(asdict(_DEFAULT_PRESET), ensure_ascii=False, indent=2))
    try:
        for filename in PRESETS_DIR.iterdir():
            if filename.suffix == ".json":
                try:
                    async with await anyio.open_file(filename, encoding="utf-8-sig") as f:
                        data = json.loads(await f.read())
                    PRESETS[filename.stem] = RolePreset(**data)
                    logger.debug(f"[预设] 已加载: {filename.stem}")
                except Exception as e:
                    logger.warning(f"无法加载预设 {filename.name}: {e}")
    except Exception as e:
        logger.error(f"扫描预设目录失败: {e}")
    logger.success(f"[启动] 预设加载完成: {len(PRESETS)} 个")


async def save_preset(name: str, preset: RolePreset):
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path = PRESETS_DIR / f"{name}.json"
    async with await anyio.open_file(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(asdict(preset), ensure_ascii=False, indent=2))
    PRESETS[name] = preset
    logger.info(f"[预设] 已保存: {name}")
