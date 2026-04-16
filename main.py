import asyncio
import json
import os
import time
from typing import Optional

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register


@register(
    "newcomer_verify",
    "AI Assistant",
    "QQ群新人入群验证插件：向新人发送私聊验证，超时未回应则在群内公布并通知管理员",
    "1.0.0",
    "https://github.com/yourname/astrbot_plugin_newcomer_verify",
)
class NewcomerVerifyPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # 获取 AstrBot 标准插件数据目录
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

            base_data_dir = get_astrbot_plugin_data_path()
        except Exception:
            # 兜底：使用相对路径
            base_data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data",
                "plugins",
            )

        self.data_dir = os.path.join(
            base_data_dir, "astrbot_plugin_newcomer_verify"
        )
        os.makedirs(self.data_dir, exist_ok=True)

        self.pending_file = os.path.join(self.data_dir, "pending_users.json")
        # pending_users 仅保存可序列化的元数据
        self.pending_users: dict[str, dict] = {}
        # pending_tasks 保存内存中的超时任务对象
        self.pending_tasks: dict[str, asyncio.Task] = {}

        self._load_pending()

    def _load_pending(self) -> None:
        """从 JSON 恢复等待列表，并清理已超时的记录。"""
        if not os.path.exists(self.pending_file):
            return
        try:
            with open(self.pending_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            cleaned: dict[str, dict] = {}
            for key, info in data.items():
                if info.get("expire_time", 0) > now:
                    cleaned[key] = info
                else:
                    logger.info(
                        f"[NewcomerVerify] 启动时清理已超时记录: {key}"
                    )
            self.pending_users = cleaned
            self._save_pending()
        except Exception as e:
            logger.error(f"[NewcomerVerify] 加载 pending 数据失败: {e}")

    def _save_pending(self) -> None:
        """持久化等待列表（不包含 asyncio.Task）。"""
        try:
            with open(self.pending_file, "w", encoding="utf-8") as f:
                json.dump(self.pending_users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[NewcomerVerify] 保存 pending 数据失败: {e}")

    def _is_enabled_for_group(self, group_id: str) -> bool:
        """检查目标群是否在启用列表中；列表为空则对所有群启用。"""
        enabled_groups = self.config.get("enabled_groups", [])
        if not enabled_groups:
            return True
        return str(group_id) in [str(g) for g in enabled_groups]

    @filter.event_message_type(EventMessageType.ALL)
    async def on_all_events(self, event: AstrMessageEvent):
        """监听所有事件，从中过滤出 group_increase 通知事件。"""
        raw = getattr(event.message_obj, "raw_message", None)
        if not isinstance(raw, dict):
            return

        if raw.get("post_type") != "notice":
            return
        if raw.get("notice_type") != "group_increase":
            return

        group_id = str(raw.get("group_id", ""))
        user_id = str(raw.get("user_id", ""))
        if not group_id or not user_id:
            return

        if not self._is_enabled_for_group(group_id):
            return

        key = f"{group_id}:{user_id}"
        if key in self.pending_users:
            logger.debug(f"[NewcomerVerify] 已在等待列表，跳过: {key}")
            return

        logger.info(
            f"[NewcomerVerify] 检测到新人入群: group={group_id}, user={user_id}"
        )
        await self._start_verify(event, group_id, user_id)

    async def _start_verify(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> None:
        """向新人发送私聊验证消息并注册超时任务。"""
        timeout_minutes = int(self.config.get("timeout_minutes", 10))
        welcome_msg = str(self.config.get("welcome_message", ""))
        welcome_msg = welcome_msg.replace("{timeout}", str(timeout_minutes))

        # 发送私聊验证消息（携带 group_id 以触发 NapCat 临时会话）
        send_ok = await self._send_private_msg(
            event, user_id, welcome_msg, group_id=group_id
        )
        if not send_ok:
            logger.warning(
                f"[NewcomerVerify] 临时会话发送失败，改为在群内 @ 提醒: {user_id}"
            )
            # 兜底：在群内 @ 新人提醒验证
            await self._fallback_group_remind(event, group_id, user_id, welcome_msg)
            return

        key = f"{group_id}:{user_id}"
        expire_time = time.time() + timeout_minutes * 60
        self.pending_users[key] = {
            "group_id": group_id,
            "user_id": user_id,
            "group_umo": event.unified_msg_origin,
            "expire_time": expire_time,
            "start_time": time.time(),
        }
        self._save_pending()

        task = asyncio.create_task(
            self._timeout_handler(key, group_id, user_id)
        )
        self.pending_tasks[key] = task

    async def _send_private_msg(
        self,
        event: AstrMessageEvent,
        user_id: str,
        message: str,
        group_id: str = "",
    ) -> bool:
        """尝试向指定 QQ 发送临时会话/私聊消息。"""
        # 方案 1：通过 context.send_message 发送（标准 AstrBot 方式）
        try:
            platform_name = "aiocqhttp"
            if event.unified_msg_origin:
                platform_name = event.unified_msg_origin.split(":")[0]
            # 私聊 UMO 格式示例: aiocqhttp:FriendMessage:123456
            private_umo = f"{platform_name}:FriendMessage:{user_id}"
            chain = MessageChain().message(message)
            await self.context.send_message(private_umo, chain)
            logger.info(f"[NewcomerVerify] 标准 API 私聊成功: {user_id}")
            return True
        except Exception as e:
            logger.warning(
                f"[NewcomerVerify] 标准 API 私聊失败，尝试回退: {e}"
            )

        # 方案 2：回退到 NapCat / aiocqhttp 原生 API（携带 group_id 触发临时会话）
        try:
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_platform_adapter import (
                AiocqhttpAdapter,
            )

            platform = self.context.get_platform(
                filter.PlatformAdapterType.AIOCQHTTP
            )
            if platform and isinstance(platform, AiocqhttpAdapter):
                bot = platform.get_client()
                payloads: dict = {
                    "user_id": int(user_id),
                    "message": [
                        {"type": "text", "data": {"text": message}}
                    ],
                }
                # 携带 group_id 后 NapCat 会走群内临时会话通道，无需加好友
                if group_id:
                    payloads["group_id"] = int(group_id)
                await bot.api.call_action("send_private_msg", **payloads)
                logger.info(
                    f"[NewcomerVerify] 回退 API {'临时会话' if group_id else '私聊'}成功: {user_id}"
                )
                return True
        except Exception as e:
            logger.error(f"[NewcomerVerify] 回退 API 私聊也失败: {e}")

        return False

    async def _fallback_group_remind(
        self,
        event: AstrMessageEvent,
        group_id: str,
        user_id: str,
        welcome_msg: str,
    ) -> None:
        """临时会话发送失败时，在群内 @ 新人并说明验证规则。"""
        key = f"{group_id}:{user_id}"
        timeout_minutes = int(self.config.get("timeout_minutes", 10))
        expire_time = time.time() + timeout_minutes * 60
        self.pending_users[key] = {
            "group_id": group_id,
            "user_id": user_id,
            "group_umo": event.unified_msg_origin,
            "expire_time": expire_time,
            "mode": "group_at",  # 标记为群内验证模式
        }
        self._save_pending()

        task = asyncio.create_task(
            self._timeout_handler(key, group_id, user_id)
        )
        self.pending_tasks[key] = task

        try:
            chain = MessageChain()
            chain.chain.append(Comp.At(qq=int(user_id)))
            chain.chain.append(
                Comp.Plain(
                    f" 欢迎入群！由于私聊受限，请直接在群里回复本条消息或发送任意内容完成验证。"
                    f"（超时时间：{timeout_minutes} 分钟）"
                )
            )
            await self.context.send_message(event.unified_msg_origin, chain)
        except Exception as e:
            logger.error(f"[NewcomerVerify] 群内兜底提醒也失败: {e}")

    @filter.event_message_type(EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """监听私聊回复，若发送者在等待列表中，则视为通过验证。"""
        await self._check_verify_pass(event)

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，用于兜底模式（临时会话发不了时）或新人直接在群内回复完成验证。"""
        await self._check_verify_pass(event)

    async def _check_verify_pass(self, event: AstrMessageEvent) -> None:
        """检查当前消息是否来自等待列表中的新人，如果是则视为通过验证。"""
        sender_id = str(event.get_sender_id())
        self_id = str(event.get_self_id())

        # 防御 1：排除机器人自己发送的消息（某些协议可能会回显 self-message）
        if sender_id == self_id:
            return

        # 防御 2：排除空消息（系统消息/回显消息可能内容为空）
        message_str = (getattr(event, "message_str", "") or "").strip()
        if not message_str:
            return

        matched_key: Optional[str] = None
        for key, info in self.pending_users.items():
            if str(info.get("user_id")) == sender_id:
                matched_key = key
                break

        if not matched_key:
            return

        info = self.pending_users[matched_key]
        group_id = str(info.get("group_id", ""))
        user_id = str(info.get("user_id", ""))
        group_umo = info.get("group_umo", "")
        verify_mode = info.get("mode", "private")
        start_time = info.get("start_time", 0)

        # 防御 3：加入最小时间间隔，防止发送验证消息后立即被系统回显/自动回复误触发
        if time.time() - start_time < 2:
            logger.debug(
                f"[NewcomerVerify] 收到来自 {user_id} 的消息但时间过短，"
                "忽略以防误判"
            )
            return

        # 若是群消息，需要确认是在同一个群
        if verify_mode == "group_at":
            msg_group_id = str(getattr(event.message_obj, "group_id", ""))
            if msg_group_id != group_id:
                return

        # 取消超时任务
        task = self.pending_tasks.pop(matched_key, None)
        if task:
            task.cancel()

        # 从等待列表移除
        self.pending_users.pop(matched_key, None)
        self._save_pending()

        logger.info(
            f"[NewcomerVerify] 用户 {user_id} 完成入群验证"
            f"（通过方式: {verify_mode}）"
        )

        # 在群聊发送通过公告
        if group_umo:
            announcement = str(self.config.get("pass_announcement", ""))
            announcement = announcement.replace("{user_id}", user_id)
            nickname = event.get_sender_name() or "新人"
            announcement = announcement.replace("{nickname}", nickname)

            try:
                chain = MessageChain()
                chain.chain.append(Comp.At(qq=int(user_id)))
                chain.chain.append(Comp.Plain(f" {announcement}"))
                await self.context.send_message(group_umo, chain)
            except Exception as e:
                logger.error(f"[NewcomerVerify] 发送通过公告失败: {e}")

    async def _timeout_handler(
        self, key: str, group_id: str, user_id: str
    ) -> None:
        """超时后：在群内公布结果并 @ 管理员。"""
        timeout_minutes = int(self.config.get("timeout_minutes", 10))
        await asyncio.sleep(timeout_minutes * 60)

        info = self.pending_users.pop(key, None)
        self.pending_tasks.pop(key, None)
        self._save_pending()

        if not info:
            return  # 已被处理

        group_umo = info.get("group_umo", "")
        if not group_umo:
            return

        announcement = str(self.config.get("timeout_announcement", ""))
        announcement = announcement.replace("{user_id}", user_id)
        announcement = announcement.replace("{nickname}", "新人")

        # 构建消息链
        chain = MessageChain()
        chain.chain.append(Comp.At(qq=int(user_id)))
        chain.chain.append(Comp.Plain(f" {announcement}"))

        admin_list = self.config.get("admin_qq_list", [])
        if admin_list:
            at_admins: list = []
            for admin in admin_list:
                try:
                    at_admins.append(Comp.At(qq=int(admin)))
                except (ValueError, TypeError):
                    continue
            if at_admins:
                chain.chain.append(Comp.Plain("\n请管理员尽快处理: "))
                for at in at_admins:
                    chain.chain.append(at)

        try:
            await self.context.send_message(group_umo, chain)
            logger.info(f"[NewcomerVerify] 超时公告已发送: {key}")
        except Exception as e:
            logger.error(f"[NewcomerVerify] 发送超时公告失败: {e}")

    async def terminate(self) -> None:
        """插件卸载/停用时清理所有待处理任务。"""
        for task in self.pending_tasks.values():
            task.cancel()
        self.pending_tasks.clear()
        logger.info("[NewcomerVerify] 插件已终止，所有超时任务已取消")
