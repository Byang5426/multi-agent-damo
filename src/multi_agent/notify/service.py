"""通知服务：抽象接口与实现。

设计原则：
- 抽象接口 NotificationService 支持扩展不同通知渠道
- LogNotificationService 作为默认实现，输出到结构化日志
- 通知失败不影响主工作流运行
- 后续可扩展：IM（钉钉/飞书）、邮件、Webhook 等渠道
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NotificationPayload:
    """通知内容载体，包含任务上下文信息。"""

    task_id: str
    project_title: str = ""
    failure_reason: str = ""
    output_summary: str = ""
    recovery_guidance: str = (
        "请通过 PATCH /api/v1/tasks/{task_id} 接口恢复任务，"
        "body: {\"status\": \"TODO\", \"resolution\": \"human_fix\", \"comment\": \"...\"}"
    )
    tenant_id: str = "default"
    assigned_worker: str = ""
    retry_count: int = 0
    max_retries: int = 3

    def format_message(self) -> str:
        """格式化为可读通知消息。"""
        lines = [
            f"[HUMAN_PENDING] 任务需要人工介入",
            f"  Task ID:    {self.task_id}",
            f"  项目:       {self.project_title or '(未知)'}",
            f"  Worker:     {self.assigned_worker or '(未指定)'}",
            f"  重试次数:   {self.retry_count}/{self.max_retries}",
            f"  失败原因:   {self.failure_reason}",
        ]
        if self.output_summary:
            lines.append(f"  当前产物:   {self.output_summary[:200]}")
        lines.append(f"  恢复操作:   {self.recovery_guidance}")
        return "\n".join(lines)


class NotificationService(ABC):
    """通知服务抽象接口。

    所有通知渠道实现必须继承此类并实现 notify 方法。
    notify 方法的异常不应影响调用方主流程。
    """

    @abstractmethod
    async def notify(self, payload: NotificationPayload) -> None:
        """发送通知。

        Args:
            payload: 通知内容。
        """
        ...


class LogNotificationService(NotificationService):
    """基于日志的通知实现。

    将通知内容输出到结构化日志，作为默认通知渠道。
    适用于开发调试和初期部署阶段。
    """

    async def notify(self, payload: NotificationPayload) -> None:
        """将通知内容写入结构化日志。"""
        try:
            message = payload.format_message()
            logger.warning(
                "Human intervention required:\n%s",
                message,
            )
        except Exception as e:
            # 通知本身失败也不应影响主流程
            logger.error("LogNotificationService failed: %s", e)


class CompositeNotificationService(NotificationService):
    """组合通知服务，将通知分发到多个渠道。"""

    def __init__(self, services: list[NotificationService]) -> None:
        self._services = services

    async def notify(self, payload: NotificationPayload) -> None:
        """向所有注册的渠道发送通知。"""
        for svc in self._services:
            try:
                await svc.notify(payload)
            except Exception as e:
                logger.error(
                    "Notification channel %s failed: %s",
                    type(svc).__name__,
                    e,
                )


# ── 全局单例 ──

_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """获取全局通知服务单例。

    默认返回 LogNotificationService。
    后续可通过配置切换为 CompositeNotificationService 以启用多渠道通知。
    """
    global _service
    if _service is None:
        _service = LogNotificationService()
    return _service
