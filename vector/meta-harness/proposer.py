"""제안자: 로컬 Claude Code(claude.ai 구독 로그인)를 claude-agent-sdk로 헤드리스 실행함.

- 작업 폴더(cwd)는 아카이브(archive/<run_id>/). 여기에 이전 후보의 코드·점수·실행 기록이 있음
- 도구는 Read·Grep·Glob·Write·Edit만. PreToolUse 훅이 경로를 코드로 검사함
  - 읽기: 아카이브 안만 허용(holdout 질문·평가 파일은 아카이브 밖이라 볼 수 없음)
  - 쓰기: 이번 후보 폴더(cand_<i>/) 안만 허용
- 사용자·프로젝트 설정(CLAUDE.md 등)을 읽지 않게 격리(setting_sources=[])
- API 과금 방지: ANTHROPIC_API_KEY·ANTHROPIC_AUTH_TOKEN 환경 변수를 지운 뒤 실행(구독으로만 실행)
- 예산: 최대 턴 수 + 호출당 토큰 상한(스트림에서 누적해 넘으면 중단). 사용량·환산 비용(참고)을 기록
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE / "skill" / "PROPOSER.md"
READ_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Write", "Edit"}


@dataclass
class ProposalResult:
    ok: bool
    reason: str = ""
    turns: int = 0
    tokens: int = 0                  # 입력+출력+캐시 읽기·생성 토큰 합
    usage: dict = field(default_factory=dict)
    cost_usd_estimate: float = 0.0   # API 요금 환산 추정치(구독에서는 실제 청구 아님, 참고용)
    seconds: float = 0.0
    final_text: str = ""
    denied: list = field(default_factory=list)


def _inside(path: str | None, root: Path, cwd: Path) -> bool:
    if not path:
        return True
    p = Path(path)
    if not p.is_absolute():
        p = cwd / p
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def path_guard(tool: str, tool_input: dict, run_dir: Path, cand_dir: Path) -> str | None:
    """허용되면 None, 막으면 이유. (단위 테스트로 검사함)"""
    if tool in READ_TOOLS:
        for key in ("file_path", "path"):
            if not _inside(tool_input.get(key), run_dir, run_dir):
                return f"{tool}: 아카이브 밖 경로는 읽을 수 없음({tool_input.get(key)})"
        pattern = str(tool_input.get("pattern", ""))
        if tool == "Glob" and (".." in pattern or pattern.startswith(("/", "\\")) or ":" in pattern):
            return "Glob: 아카이브 밖을 가리키는 패턴은 쓸 수 없음"
        return None
    if tool in WRITE_TOOLS:
        if not _inside(tool_input.get("file_path"), cand_dir, run_dir):
            return f"{tool}: 이번 후보 폴더({cand_dir.name}/) 밖에는 쓸 수 없음"
        return None
    return f"{tool}: 허용되지 않은 도구"


def _usage_tokens(u: dict | None) -> int:
    if not u:
        return 0
    return sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                            "cache_read_input_tokens", "cache_creation_input_tokens"))


async def _propose(run_dir: Path, cand_dir: Path, task: str, model: str, max_turns: int,
                   token_cap: int, log_path: Path) -> ProposalResult:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, HookMatcher, ResultMessage,
                                  TextBlock, ToolUseBlock, query)

    denied: list[str] = []

    async def guard(input_data, tool_use_id, context):
        reason = path_guard(input_data.get("tool_name", ""), input_data.get("tool_input", {}) or {}, run_dir, cand_dir)
        if reason:
            denied.append(reason)
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        return {}

    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):   # 구독으로만 실행(API 과금 방지)
        os.environ.pop(key, None)
    options = ClaudeAgentOptions(
        cwd=str(run_dir),
        model=model,
        tools=["Read", "Grep", "Glob", "Write", "Edit"],
        permission_mode="acceptEdits",
        setting_sources=[],
        max_turns=max_turns,
        system_prompt={"type": "preset", "preset": "claude_code", "append": SKILL.read_text(encoding="utf-8")},
        hooks={"PreToolUse": [HookMatcher(matcher="Read|Grep|Glob|Write|Edit", hooks=[guard])]},
    )
    res = ProposalResult(ok=False)
    started = time.time()
    per_message: dict[str, int] = {}   # 한 API 응답이 블록마다 같은 사용량을 반복해 보내므로 message_id로 한 번만 셈
    tokens = 0
    with open(log_path, "w", encoding="utf-8") as log:
        async for msg in query(prompt=task, options=options):
            if isinstance(msg, AssistantMessage):
                key = msg.message_id or msg.uuid or str(len(per_message))
                per_message[key] = _usage_tokens(getattr(msg, "usage", None))
                tokens = sum(per_message.values())
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        log.write(json.dumps({"type": "text", "text": block.text[:2000]}, ensure_ascii=False) + "\n")
                    elif isinstance(block, ToolUseBlock):
                        log.write(json.dumps({"type": "tool", "name": block.name,
                                              "input": {k: str(v)[:300] for k, v in block.input.items()}},
                                             ensure_ascii=False) + "\n")
                if tokens > token_cap:
                    res.reason = f"token_cap: 호출당 토큰 상한 {token_cap:,} 초과"
                    break
            elif isinstance(msg, ResultMessage):
                res.turns = msg.num_turns
                res.usage = msg.usage or {}
                res.cost_usd_estimate = float(msg.total_cost_usd or 0)
                res.final_text = (msg.result or "")[:2000]
                if msg.is_error:
                    status = getattr(msg, "api_error_status", None)
                    res.reason = f"error: {msg.subtype} status={status} {(msg.errors or [''])[0] if msg.errors else ''}"[:300]
                    if status == 429:
                        res.reason = "rate_limited: 구독 사용 한도 도달"
                log.write(json.dumps({"type": "result", "subtype": msg.subtype, "turns": msg.num_turns,
                                      "usage": msg.usage, "cost_usd_estimate": msg.total_cost_usd}, ensure_ascii=False) + "\n")
    res.tokens = max(tokens, _usage_tokens(res.usage))
    res.seconds = round(time.time() - started, 1)
    res.denied = denied
    res.ok = (cand_dir / "harness.py").exists() and not res.reason.startswith(("rate_limited", "error"))
    if not (cand_dir / "harness.py").exists() and not res.reason:
        res.reason = "harness.py를 쓰지 않음"
    return res


def propose(run_dir: Path, cand_dir: Path, task: str, model: str = "sonnet", max_turns: int = 40,
            token_cap: int = 3_000_000) -> ProposalResult:
    cand_dir.mkdir(parents=True, exist_ok=True)
    try:
        res = asyncio.run(_propose(run_dir, cand_dir, task, model, max_turns, token_cap,
                                   cand_dir / "proposer_log.jsonl"))
    except Exception as e:  # noqa: BLE001
        res = ProposalResult(ok=False, reason=f"exception: {type(e).__name__}: {str(e)[:300]}")
    (cand_dir / "proposer.json").write_text(json.dumps(asdict(res), ensure_ascii=False, indent=2), encoding="utf-8")
    return res
