"""기준 후보(cand_000): 현재 5단계 하네스(vector/harness, 프롬프트 v3)를 그대로 씀.

Meta-Harness 후보는 모두 이 형태를 따름: `class Candidate(Step5)` 하나를 정의하고,
바꾸고 싶은 메서드(system_prompt, tools, middleware, finish 등)만 덮어씀.
"""

from step5_trace import Step5


class Candidate(Step5):
    pass
