"""claude-agent-sdk 集成层。

内置系统提示词用 preset 原样保留，只 append 见微的约束（prompts.py）。
未配置 LLM_API_KEY 或 SDK 不可用时 runner 处于禁用态，上层接口返回 503，
不影响监测/报告/告警等核心功能。
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from jianwei.agent import tools as agent_tools
from jianwei.agent.prompts import APPEND_SYSTEM_PROMPT


logger = logging.getLogger("jianwei.agent")

DEFAULT_BASE_URL = "https://api.minimaxi.com/anthropic"
DEFAULT_MODEL = "MiniMax-M3"
DEFAULT_MAX_TURNS = 8

# 生产容器里禁用全部内置工具，agent 只能用我们的只读数据工具
BUILTIN_TOOLS_DISABLED = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "NotebookEdit",
    "WebFetch",
    "WebSearch",
    "Task",
    "TodoWrite",
]


class AgentUnavailable(Exception):
    """SDK 未安装或 LLM_API_KEY 未配置。"""


class ClaudeAgentRunner:
    def __init__(self) -> None:
        self.api_key = os.environ.get("LLM_API_KEY", "")
        self.base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        self.model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.max_turns = int(os.environ.get("JIANWEI_AGENT_MAX_TURNS", DEFAULT_MAX_TURNS))
        self._sdk = _import_sdk()
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self._sdk is not None

    def cli_found(self) -> bool:
        """SDK 平台 wheel 自带 CLI 二进制；装到 sdist 时没有，运行时才会炸。"""
        if self._sdk is None:
            return False
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
        return bundled.is_file() or shutil.which("claude") is not None

    def diagnostics(self) -> dict[str, Any]:
        """暴露到 /health 的自检信息，线上排障用（不含密钥）。"""
        info: dict[str, Any] = {
            "sdk_installed": self._sdk is not None,
            "api_key_configured": bool(self.api_key),
            "cli_found": self.cli_found(),
            "model": self.model,
        }
        if self.last_error:
            # 默认只暴露异常类型；设 JIANWEI_DEBUG=1 才带详细信息
            if os.environ.get("JIANWEI_DEBUG") == "1":
                info["last_error"] = self.last_error[:500]
            else:
                info["last_error"] = self.last_error.split(":", 1)[0]
        return info

    async def run(self, prompt: str, context: agent_tools.AgentContext) -> str:
        if not self.available:
            raise AgentUnavailable("agent is not configured")

        try:
            return await self._run(prompt, context)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise

    async def _run(self, prompt: str, context: agent_tools.AgentContext) -> str:
        sdk = self._sdk
        agent_tools.set_context(context)
        options = self._build_options(sdk)

        final_text: list[str] = []
        result_text: str | None = None
        async for message in sdk["query"](prompt=prompt, options=options):
            if isinstance(message, sdk["AssistantMessage"]):
                for block in message.content:
                    if isinstance(block, sdk["TextBlock"]):
                        final_text.append(block.text)
            elif isinstance(message, sdk["ResultMessage"]):
                result_text = getattr(message, "result", None)

        reply = (result_text or "".join(final_text)).strip()
        if not reply:
            raise RuntimeError("agent returned empty reply")
        self.last_error = None
        return reply

    def _build_options(self, sdk: dict[str, Any]) -> Any:
        server = sdk["create_sdk_mcp_server"](
            name="jianwei",
            version="1.0.0",
            tools=[_wrap_tool(sdk, spec) for spec in agent_tools.TOOL_SPECS],
        )
        return sdk["ClaudeAgentOptions"](
            system_prompt=_system_prompt_config(sdk),
            model=self.model,
            max_turns=self.max_turns,
            mcp_servers={"jianwei": server},
            allowed_tools=[f"mcp__jianwei__{name}" for name, *_ in agent_tools.TOOL_SPECS],
            disallowed_tools=BUILTIN_TOOLS_DISABLED,
            setting_sources=[],
            env={
                "ANTHROPIC_BASE_URL": self.base_url,
                "ANTHROPIC_AUTH_TOKEN": self.api_key,
                "ANTHROPIC_MODEL": self.model,
            },
        )


def _system_prompt_config(sdk: dict[str, Any]) -> Any:
    # 官方 preset+append：内置提示词不动，只追加见微约束
    if sdk.get("SystemPromptConfig") is not None:
        return sdk["SystemPromptConfig"](preset="claude_code", append=APPEND_SYSTEM_PROMPT)
    return {"type": "preset", "preset": "claude_code", "append": APPEND_SYSTEM_PROMPT}


def _wrap_tool(sdk: dict[str, Any], spec: tuple) -> Any:
    name, description, schema, implementation = spec

    @sdk["tool"](name, description, schema)
    async def _tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            result = implementation(**args)
        except TypeError:
            # 模型漏传/多传参数时给出可恢复的错误而不是崩掉循环
            result = '{"error": "参数不正确，请检查工具入参。"}'
        except Exception:
            logger.exception("agent tool %s failed", name)
            result = '{"error": "数据查询暂时失败，请稍后再试。"}'
        return {"content": [{"type": "text", "text": result}]}

    return _tool


def _import_sdk() -> dict[str, Any] | None:
    try:
        import claude_agent_sdk
    except ImportError:
        logger.info("claude-agent-sdk not installed; agent disabled")
        return None

    return {
        "query": claude_agent_sdk.query,
        "tool": claude_agent_sdk.tool,
        "create_sdk_mcp_server": claude_agent_sdk.create_sdk_mcp_server,
        "ClaudeAgentOptions": claude_agent_sdk.ClaudeAgentOptions,
        "AssistantMessage": claude_agent_sdk.AssistantMessage,
        "TextBlock": claude_agent_sdk.TextBlock,
        "ResultMessage": claude_agent_sdk.ResultMessage,
        "SystemPromptConfig": getattr(claude_agent_sdk, "SystemPromptConfig", None),
    }
