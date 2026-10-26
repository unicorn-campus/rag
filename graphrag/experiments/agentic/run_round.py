"""방안 실행기. 여러 방안을 실행 회차마다 번갈아 돌려(짝지은 비교) results/{방안}-{tag}-{split}.json에 저장함.

실행(저장소 루트에서):
    python graphrag/experiments/agentic/run_round.py --pipelines base vote critic scope_mask scope_cut --split dev --repeat 5 --tag r1
    python graphrag/experiments/agentic/run_round.py --pipelines base --split dev --repeat 1 --tag smoke   # 빠른 동작 확인
"""

from __future__ import annotations

import argparse
import asyncio

from harness import run_interleaved
from common import setup_console
import pipelines


async def main() -> None:
    setup_console()
    p = argparse.ArgumentParser()
    p.add_argument("--pipelines", nargs="+", required=True, choices=list(pipelines.PIPELINES))
    p.add_argument("--split", choices=["dev", "holdout"], default="dev")
    p.add_argument("--repeat", type=int, default=5)
    p.add_argument("--tag", required=True, help="결과 파일 이름에 붙일 라운드 표시(예: r1)")
    args = p.parse_args()
    await run_interleaved({f"{n}-{args.tag}": pipelines.PIPELINES[n] for n in args.pipelines}, args.split, args.repeat)


if __name__ == "__main__":
    asyncio.run(main())
