from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from jianwei.analysis.segments import BEIJING


logger = logging.getLogger("jianwei.notify")

# 微信云托管内网开放接口，容器内调用免 access_token。
SUBSCRIBE_SEND_URL = "http://api.weixin.qq.com/cgi-bin/message/subscribe/send"


def send_alert_notifications(
    openids: list[str],
    alert: dict[str, Any],
    post: Any | None = None,
) -> int:
    """给设备绑定的所有用户发订阅消息，返回成功条数。

    未配置 WX_SUBSCRIBE_TEMPLATE_ID 时只记日志不发送；任何失败都不影响入库主流程。
    模板字段名（thing1/time2 等）与具体模板有关，可用 WX_SUBSCRIBE_MESSAGE_KEY /
    WX_SUBSCRIBE_TIME_KEY 环境变量调整。
    """
    template_id = os.environ.get("WX_SUBSCRIBE_TEMPLATE_ID", "")
    if not template_id or not openids:
        logger.info("skip subscribe message (template=%s, recipients=%d): %s",
                    bool(template_id), len(openids), alert.get("message"))
        return 0

    message_key = os.environ.get("WX_SUBSCRIBE_MESSAGE_KEY", "thing1")
    time_key = os.environ.get("WX_SUBSCRIBE_TIME_KEY", "time2")
    page = os.environ.get("WX_SUBSCRIBE_PAGE", "pages/dashboard/dashboard")
    post = post or _post_json

    created_at = alert.get("created_at")
    time_text = created_at.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M") if created_at else ""
    payload_data = {
        # thing 类型字段最长 20 个字符
        message_key: {"value": str(alert.get("message", ""))[:20]},
        time_key: {"value": time_text},
    }

    sent = 0
    for openid in openids:
        try:
            response = post(
                SUBSCRIBE_SEND_URL,
                {
                    "touser": openid,
                    "template_id": template_id,
                    "page": page,
                    "data": payload_data,
                },
            )
            if response.get("errcode", 0) == 0:
                sent += 1
            else:
                logger.warning("subscribe send failed for %s: %s", openid, response)
        except Exception:
            logger.exception("subscribe send error for %s", openid)
    return sent


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))
