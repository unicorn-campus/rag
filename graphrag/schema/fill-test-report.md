# 채우기 시험(fill test) 보고서

- 채우기 시험: 스키마 초안대로 시험용 청크에서 실제로 노드·관계를 뽑아 보고, 사람이 확정한 기대 결과와 비교해 스키마가 쓸 만한지 확인하는 시험
- 추출: `LLMEntityRelationExtractor`(openai/gpt-oss-120b) → `GraphPruning.run(graph, schema)`(`additional_node_types`·`additional_relationship_types`·`additional_patterns` 모두 False)
- 기준: 재현율(recall, 기대 항목 중 뽑힌 비율) ≥ 0.8, 지어냄 비율(hallucination rate, 뽑힌 항목 중 기대에 없는 비율) ≤ 0.1, 역량질문 모두 답 가능
- 항목: 노드(타입, 이름) + 관계(시작, 관계 타입, 끝). 이름은 공백·문장부호·괄호 속 한자를 지우고 비교하며, 기대 결과의 aliases(사람이 인정한 다른 이름)도 같은 이름으로 봄
- `NOT_IN_SCHEMA`: 스키마에 없어 정리(pruning) 단계에서 지워진 항목. 스키마에 빠진 후보의 신호

## 1. 회차별 지표

| 회차 | 측정 | 재현율 | 지어냄 비율 | 역량질문 답 가능 | 기대 / 뽑힘 / 일치 | 판정 |
|---|---|---|---|---|---|---|
| 1 | 1회 | 0.878 | 0.368 | 8/9 | 82 / 114 / 72 | 미달 |
| 2 | 1회 | 0.927 | 0.321 | 9/9 | 82 / 112 / 76 | 미달 |
| 3 | 1회 | 0.341 | 0.125 | 6/9 | 82 / 32 / 28 | 미달 |
| 4 | 1회 | 0.512 | 0.400 | 9/9 | 82 / 70 / 42 | 미달 |
| 5 | 3회 평균 | 0.870 (0.817 ~ 0.927) | 0.294 (0.269 ~ 0.309) | 8/9 | 82 / 101 / 71.3 | 미달 |
| 6 | 3회 평균 | 0.679 (0.512 ~ 0.854) | 0.369 (0.231 ~ 0.538) | 9/9 | 82 / 88.3 / 55.7 | 미달 |
| 7 | 3회 평균 | 0.833 (0.786 ~ 0.905) | 0.343 (0.276 ~ 0.382) | 8/9 | 84 / 106.7 / 70 | 미달 |

## 2. 최종 결정

- 2026-10-26 사용자 결정: 가장 나은 2회차 스키마(5회차 3회 반복 측정: 재현율 0.870, 지어냄 비율 0.294, 역량질문 8/9)를 최종 스키마로 확정. 기준을 넘은 회차는 없음
- 한계: 지어냄 비율 기준(0.1 이하) 미달
- 한계: CQ04는 3회 중 1회만 이어짐
- 한계: 권고: 인덱싱 단계에서 고립 노드 제거 후처리(저장 결과 계산상 지어냄 0.274 → 0.206)

## 1회차 상세

- 스키마 초안 출처: work/schema-draft-r1.reviewed.json
- 추출 프롬프트: 라이브러리 기본값(ERExtractionTemplate)
- 고친 내용: 최초 초안

### 원인 추정

- CQ01 미달은 평가 코드 결함(서로 다른 발명 노드에서 시작하는 두 사슬을 같은 노드로 묶어 검사)이었음. 코드 수정 후 저장된 추출 결과로 재채점(--rescore)해 8/9
- 지어냄 원인: 범위 밖 절차(chunk 15 전자문서 이용신고, chunk 116·180 소관 업무 나열 문장의 재심·취소신청), 이름에 권리가 들어 있는 주체의 HOLDS 자동 생성(chunk 100·186), 관계 없는 고립 노드, 괄호 속 부연 설명이 들어간 이름(chunk 82)
- CQ04 끊김: '특허거절결정 또는 … 연장등록거절결정을 받은 자'를 둘로 나눠 기대 결과의 주체와 맞지 않음

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 5 | 0 | 5 |
| 17 | 역량질문 관련 | 11 | 9 | 9 | 0 |
| 21 | 역량질문 관련 | 8 | 13 | 5 | 8 |
| 51 | 역량질문 관련 | 11 | 12 | 11 | 1 |
| 73 | 역량질문 관련 | 3 | 9 | 3 | 6 |
| 82 | 역량질문 관련 | 9 | 9 | 7 | 2 |
| 100 | 역량질문 관련 | 24 | 28 | 24 | 4 |
| 116 | 역량질문 관련 | 11 | 19 | 8 | 11 |
| 180 | 역량질문 무관 | 0 | 3 | 0 | 3 |
| 186 | 역량질문 관련 | 5 | 7 | 5 | 2 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O | 산업상이용할수있는발명 → 특허 / 공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O | 공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O | 누구든지 → 출원심사의청구 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | X |  |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O | 심판 → 그결정등본을송달받은날부터3개월이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O | 특허권 → 특허권을설정등록한날부터특허출원일후20년이되는날까지 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O | 특허권또는전용실시권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O | 특허권 → 연구또는시험약사법에따른의약품의품목허가품목신고및농약관리법에따른농약의등록을위한연구또는시험을포함한다을하기위한특허발명의실시 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O | 전용실시권자 → 침해의금지또는예방 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:지식재산처장
  - 지어냄: Actor:특허심판원장
  - 지어냄: Procedure:전자문서이용신고
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:지식재산처장
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:특허심판원장
- chunk 17
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
- chunk 21
  - 놓침: Right:특허를받을수있는권리
  - 놓침: Actor:그승계인 -HOLDS-> Right:특허를받을수있는권리
  - 놓침: Actor:발명을한사람 -HOLDS-> Right:특허를받을수있는권리
  - 지어냄: Actor:무권리자
  - 지어냄: Actor:정당한권리자
  - 지어냄: Period:무권리자가특허를받지못하게된날부터30일이지난후
  - 지어냄: Procedure:특허출원
  - 지어냄: Actor:그승계인 -HOLDS-> Right:특허
  - 지어냄: Actor:무권리자 -CAN_FILE-> Procedure:특허출원
  - 지어냄: Actor:발명을한사람 -HOLDS-> Right:특허
  - 지어냄: Actor:정당한권리자 -CAN_FILE-> Procedure:특허출원
- chunk 51
  - 지어냄: Actor:특허출원인
- chunk 73
  - 지어냄: Actor:무권리자
  - 지어냄: Actor:정당한권리자
  - 지어냄: Invention:특허발명
  - 지어냄: Period:무권리자의특허출원일의다음날부터
  - 지어냄: Actor:정당한권리자 -HOLDS-> Right:특허권
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:무권리자의특허출원일의다음날부터
- chunk 82
  - 놓침: Exclusion:연구또는시험을하기위한특허발명의실시
  - 놓침: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:연구또는시험을하기위한특허발명의실시
  - 지어냄: Exclusion:연구또는시험약사법에따른의약품의품목허가품목신고및농약관리법에따른농약의등록을위한연구또는시험을포함한다을하기위한특허발명의실시
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:연구또는시험약사법에따른의약품의품목허가품목신고및농약관리법에따른농약의등록을위한연구또는시험을포함한다을하기위한특허발명의실시
- chunk 100
  - 지어냄: Right:전용실시권
  - 지어냄: Right:특허권
  - 지어냄: Actor:전용실시권자 -HOLDS-> Right:전용실시권
  - 지어냄: Actor:특허권자 -HOLDS-> Right:특허권
- chunk 116
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판
  - 놓침: Procedure:특허거절결정등에대한심판 -HANDLED_BY-> Actor:특허심판원
  - 지어냄: Actor:지식재산처장
  - 지어냄: Actor:특허거절결정받은자
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정받은자
  - 지어냄: Procedure:재심
  - 지어냄: Procedure:특허심판
  - 지어냄: Procedure:특허취소신청
  - 지어냄: Actor:특허거절결정받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판
  - 지어냄: Procedure:재심 -HANDLED_BY-> Actor:특허심판원
  - 지어냄: Procedure:특허심판 -HANDLED_BY-> Actor:특허심판원
  - 지어냄: Procedure:특허취소신청 -HANDLED_BY-> Actor:특허심판원
- chunk 180
  - 지어냄: Procedure:심판
  - 지어냄: Procedure:재심
  - 지어냄: Procedure:특허취소신청
- chunk 186
  - 지어냄: Right:전용실시권
  - 지어냄: Right:특허권

## 2회차 상세

- 스키마 초안 출처: work/schema-draft-r2.reviewed.json
- 추출 프롬프트: 라이브러리 기본값(ERExtractionTemplate)
- 고친 내용: 1회차 시험 분석 반영(데이터 엔지니어 검토): ① 모든 노드 설명에 '관계로 이어질 때만 뽑음' 공통 규칙 추가(고립 노드 지어냄 대응) ② 이름 공통 규칙 추가: 괄호 속 부연 설명 제거, '또는'으로 묶인 표현은 나누지 않음(chunk 82·116 불일치, CQ04 끊김 대응) ③ Procedure 범위를 심사 청구와 불복·분쟁 절차로 좁히고 소관 업무 나열·언급 문장 제외(chunk 15·116·180 지어냄 대응) ④ HOLDS는 권리 귀속을 직접 정한 문장에서만(chunk 100·186 지어냄 대응) ⑤ HANDLED_BY에 기관 소관 절차의 상속 규칙 추가(CQ04) ⑥ Period·HAS_DURATION은 원칙적 존속기간만(chunk 73 지어냄 대응) ⑦ Right 이름을 줄이지 않음(chunk 21 불일치 대응)

### 원인 추정

- 재현율·역량질문 기준은 통과. 지어냄 36건 = 범위 밖 관계 약 15건 + 고립 노드 약 13건 + 이름 변형 약 8건
- 설명 끝에 붙인 공통 규칙(관계로 이어질 때만 뽑음, 범위 밖 제외)을 추출 LLM이 따르지 않음
- 표본 20건 반복 측정: 같은 스키마에서도 고립 노드 수가 실행마다 13 ~ 18건(2회차안), 9 ~ 21건(3회차 후보안)으로 흔들려 단일 실행 지표의 변동이 큼(temperature 0이어도 결정적이지 않음)
- 추론 강도 high는 Groq JSON 검증 오류(json_validate_failed)로 쓸 수 없었음

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 7 | 0 | 7 |
| 17 | 역량질문 관련 | 11 | 11 | 9 | 2 |
| 21 | 역량질문 관련 | 8 | 11 | 6 | 5 |
| 51 | 역량질문 관련 | 11 | 11 | 11 | 0 |
| 73 | 역량질문 관련 | 3 | 14 | 1 | 13 |
| 82 | 역량질문 관련 | 9 | 9 | 9 | 0 |
| 100 | 역량질문 관련 | 24 | 28 | 24 | 4 |
| 116 | 역량질문 관련 | 11 | 16 | 11 | 5 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 5 | 5 | 0 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O | 산업상이용할수있는발명 → 특허 / 특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O | 특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O | 특허에관한절차를밟는자 → 전자문서이용신고 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | O | 특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 → 심판 → 특허거절결정 / 심판 → 특허심판원 |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O | 심판 → 결정등본을송달받은날부터3개월이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O | 특허권 → 특허출원일후20년이되는날까지 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O | 특허권또는전용실시권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O | 특허권 → 특허출원을한때부터국내에있는물건 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O | 전용실시권자 → 침해행위에제공된설비의제거 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:지식재산처장
  - 지어냄: Actor:특허심판원장
  - 지어냄: Actor:특허에관한절차를밟는자
  - 지어냄: Procedure:전자문서이용신고
  - 지어냄: Actor:특허에관한절차를밟는자 -CAN_FILE-> Procedure:전자문서이용신고
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:지식재산처장
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:특허심판원장
- chunk 17
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 -NOT_ELIGIBLE_FOR-> Right:특허
- chunk 21
  - 놓침: Right:특허
  - 놓침: Invention:공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 지어냄: Actor:무권리자
  - 지어냄: Actor:정당한권리자
  - 지어냄: Actor:지식재산처직원
  - 지어냄: Actor:특허심판원직원
  - 지어냄: Invention:공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 -NOT_ELIGIBLE_FOR-> Right:특허를받을수있는권리
- chunk 73
  - 놓침: Period:특허권을설정등록한날부터특허출원일후20년이되는날까지
  - 놓침: Right:특허권 -HAS_DURATION-> Period:특허권을설정등록한날부터특허출원일후20년이되는날까지
  - 지어냄: Actor:무권리자
  - 지어냄: Actor:정당한권리자
  - 지어냄: Actor:허가등을받은자
  - 지어냄: Invention:특허발명
  - 지어냄: Period:5년의기간까지
  - 지어냄: Period:무권리자의특허출원일의다음날부터
  - 지어냄: Period:특허출원일후20년이되는날까지
  - 지어냄: Period:허가등을받은날부터14년
  - 지어냄: Procedure:특허권의존속기간의연장
  - 지어냄: Procedure:특허권의존속기간의연장 -HAS_DEADLINE-> Period:5년의기간까지
  - 지어냄: Procedure:특허권의존속기간의연장 -HAS_DEADLINE-> Period:허가등을받은날부터14년
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:무권리자의특허출원일의다음날부터
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:특허출원일후20년이되는날까지
- chunk 100
  - 지어냄: Actor:지식재산처소속공무원
  - 지어냄: Actor:지식재산처장
  - 지어냄: Actor:통상실시권자
  - 지어냄: Decision:확정된결정
- chunk 116
  - 지어냄: Actor:지식재산처장
  - 지어냄: Procedure:재심
  - 지어냄: Procedure:취소신청
  - 지어냄: Procedure:재심 -HANDLED_BY-> Actor:특허심판원
  - 지어냄: Procedure:취소신청 -HANDLED_BY-> Actor:특허심판원

## 3회차 상세

- 스키마 초안 출처: work/schema-draft-r3.reviewed.json
- 추출 프롬프트: work/extraction-prompt-r3.txt
- 고친 내용: 2회차 시험 분석 반영(데이터 엔지니어 검토): ① 노드 설명 맨 앞에 【뽑는 조건】을 두고 그 노드를 만들 수 있는 관계를 명시(설명 끝의 공통 규칙을 추출 LLM이 무시해 고립 노드 13건 발생) ② Procedure·CAN_FILE·HANDLED_BY에 범위 밖 절차를 구체적으로 나열(전자문서 이용신고, 존속기간 연장, 소관 업무 나열 문장의 재심·취소신청) ③ ELIGIBLE_FOR·NOT_ELIGIBLE_FOR의 끝 노드를 Right '특허'로 고정 ④ Period 이름을 기점·끝을 모두 포함한 원문 구절로, 예외 기산점·연장 제외 ⑤ Invention 이름의 조건문('~한 경우')을 '~한 발명'으로 줄이는 규칙. 설명 예시에서 시험용 청크 문구는 줄임 ⑥ 추출 프롬프트 보완(extraction-prompt.txt, 사용자 허용): 관계 없는 노드 출력 금지, 【뽑는 조건】·【범위 밖】 준수, 해당 없으면 빈 결과

### 원인 추정

- 추출 프롬프트 보완(사용자 허용)으로 고립 노드와 무관 청크 추출은 0건이 됐지만, 청크 17·51·100에서 추출 LLM이 빈 결과를 냄 → 재현율 0.341
- 진단 재실행: 청크 17은 노드 5개로 정상, 청크 51·100은 다시 빈 결과. '해당 없으면 빈 결과가 정답' 규칙과 【뽑는 조건】이 범위 안 조문(심사청구·금지청구)까지 억제한 것으로 추정
- 'all nodes were pruned' 경고는 빈 그래프에서 나는 메시지이며 정리 단계가 지운 것이 아님(NOT_IN_SCHEMA 0건)
- 설계 결함: 2회차부터 넣은 이름 규칙('또는'으로 묶인 표현은 나누지 않음)이 확정 기대 결과(chunk 21의 '발명을 한 사람'·'그 승계인' 분리)와 충돌함
- Period 이름 앞부분을 잘라 내는 변형('특허출원일 후 20년이 되는 날까지')은 규칙을 보강해도 계속됨

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 17 | 역량질문 관련 | 11 | 0 | 0 | 0 |
| 21 | 역량질문 관련 | 8 | 6 | 4 | 2 |
| 51 | 역량질문 관련 | 11 | 0 | 0 | 0 |
| 73 | 역량질문 관련 | 3 | 3 | 1 | 2 |
| 82 | 역량질문 관련 | 9 | 9 | 9 | 0 |
| 100 | 역량질문 관련 | 24 | 0 | 0 | 0 |
| 116 | 역량질문 관련 | 11 | 9 | 9 | 0 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 5 | 5 | 0 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | X |  |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O | 공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O | 특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 → 심판 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | X |  |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O | 심판 → 그결정등본을송달받은날부터3개월이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O | 특허권 → 특허출원일후20년이되는날까지 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O | 특허권또는전용실시권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O | 특허권 → 국내를통과하는데불과한선박항공기차량또는이에사용되는기계기구장치그밖의물건 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | X |  |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 17
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명
  - 놓침: Invention:산업상이용할수있는발명
  - 놓침: Invention:특허출원전에국내또는국외에서공지되었거나공연히실시된발명
  - 놓침: Invention:특허출원전에국내또는국외에서반포된간행물에게재되었거나전기통신회선을통하여공중이이용할수있는발명
  - 놓침: Right:특허
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 놓침: Invention:산업상이용할수있는발명 -ELIGIBLE_FOR-> Right:특허
  - 놓침: Invention:특허출원전에국내또는국외에서공지되었거나공연히실시된발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 놓침: Invention:특허출원전에국내또는국외에서반포된간행물에게재되었거나전기통신회선을통하여공중이이용할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
- chunk 21
  - 놓침: Actor:그승계인
  - 놓침: Actor:발명을한사람
  - 놓침: Actor:그승계인 -HOLDS-> Right:특허를받을수있는권리
  - 놓침: Actor:발명을한사람 -HOLDS-> Right:특허를받을수있는권리
  - 지어냄: Actor:발명을한사람또는그승계인
  - 지어냄: Actor:발명을한사람또는그승계인 -HOLDS-> Right:특허를받을수있는권리
- chunk 51
  - 놓침: Actor:누구든지
  - 놓침: Actor:정당한권리자
  - 놓침: Actor:지식재산처장
  - 놓침: Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내
  - 놓침: Period:특허출원일부터3년이내
  - 놓침: Procedure:출원심사의청구
  - 놓침: Actor:누구든지 -CAN_FILE-> Procedure:출원심사의청구
  - 놓침: Actor:정당한권리자 -CAN_FILE-> Procedure:출원심사의청구
  - 놓침: Procedure:출원심사의청구 -HANDLED_BY-> Actor:지식재산처장
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:특허출원일부터3년이내
- chunk 73
  - 놓침: Period:특허권을설정등록한날부터특허출원일후20년이되는날까지
  - 놓침: Right:특허권 -HAS_DURATION-> Period:특허권을설정등록한날부터특허출원일후20년이되는날까지
  - 지어냄: Period:특허출원일후20년이되는날까지
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:특허출원일후20년이되는날까지
- chunk 100
  - 놓침: Actor:전용실시권자
  - 놓침: Actor:침해한자
  - 놓침: Actor:침해할우려가있는자
  - 놓침: Actor:특허권자
  - 놓침: Remedy:그밖에침해의예방에필요한행위
  - 놓침: Remedy:침해의금지또는예방
  - 놓침: Remedy:침해행위를조성한물건의폐기
  - 놓침: Remedy:침해행위에제공된설비의제거
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해의금지또는예방
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해의금지또는예방
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해의금지또는예방 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:침해의금지또는예방 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
- chunk 116
  - 놓침: Actor:특허심판원
  - 놓침: Procedure:특허거절결정등에대한심판 -HANDLED_BY-> Actor:특허심판원

## 4회차 상세

- 스키마 초안 출처: work/schema-draft-r4.reviewed.json
- 추출 프롬프트: work/extraction-prompt-r4.txt
- 고친 내용: 4회차(사용자 지시): 2회차 설명을 바탕으로 ① 이름 규칙의 '또는' 처리를 확정 기대 결과와 맞춤 — 따로 설 수 있는 이름(발명을 한 사람 또는 그 승계인, 특허권자 또는 전용실시권자)은 나누고, 수식어를 함께 받는 표현(특허권 또는 전용실시권을 침해한 자, 침해의 금지 또는 예방)은 나누지 않음(2회차 규칙은 모두 나누지 않아 chunk 21 기대 결과와 충돌) ② 추출 프롬프트 완화(extraction-prompt.txt): 3회차의 '해당 없으면 빈 결과가 정답' 규칙을 빼고, 고립 노드 금지·설명 규칙 준수·원문 이름 복사만 남김

### 원인 추정

- 청크 51이 빈 결과(놓침 11건). 진단: 같은 프롬프트로 3번 호출하면 보완 프롬프트는 3번 중 1번 빈 결과, 기본 프롬프트는 3번 모두 정상 → 빈 결과는 확률적으로 생기며 이번 회차에서 그 경우에 걸림
- 청크 100은 금지청구만 뽑고 폐기·제거·예방 청구(②항)를 놓침(놓침 17건)
- 청크 15(전자문서)는 3회차 0건 → 4회차 12건. '빈 결과가 정답' 규칙을 빼자 범위 밖 절차 추출이 되살아남
- 이름 규칙 변형: 청크 186에서 수식어를 함께 받는 '특허권 또는 전용실시권을 침해한 자'를 둘로 나눔(규칙과 반대), 청크 116은 '특허거절결정 등을 받은 자'로 줄임, 청크 17은 '~한 경우/~할 수 있으면' 조건문을 그대로 씀
- 종합: 1 ~ 4회차 지표 차이의 상당 부분이 실행마다의 변동임. 같은 설정에서도 청크 하나가 11건 전부 맞음과 0건 사이를 오가므로, 단일 실행 판정으로는 스키마 개선 효과를 가려내기 어려움

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 12 | 0 | 12 |
| 17 | 역량질문 관련 | 11 | 11 | 7 | 4 |
| 21 | 역량질문 관련 | 8 | 7 | 5 | 2 |
| 51 | 역량질문 관련 | 11 | 0 | 0 | 0 |
| 73 | 역량질문 관련 | 3 | 5 | 3 | 2 |
| 82 | 역량질문 관련 | 9 | 9 | 9 | 0 |
| 100 | 역량질문 관련 | 24 | 7 | 7 | 0 |
| 116 | 역량질문 관련 | 11 | 11 | 9 | 2 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 8 | 2 | 6 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O | 산업상이용할수있는발명 → 특허 / 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O | 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O | 전자문서특허에관한절차밟으려는자 → 전자문서이용신고절차 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | O | 특허거절결정등을받은자 → 특허거절결정등에대한심판 → 특허권의존속기간의연장등록거절결정 / 특허거절결정등에대한심판 → 특허심판원 |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O | 특허거절결정등에대한심판 → 그결정등본을송달받은날부터3개월이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O | 특허권 → 특허권을설정등록한날부터특허출원일후20년이되는날까지 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O | 특허권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O | 특허권 → 조제행위와그조제에의한의약 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O | 특허권자 → 침해의금지또는예방 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:전자문서특허에관한절차밟으려는자
  - 지어냄: Actor:지식재산처장
  - 지어냄: Actor:특허심판원장
  - 지어냄: Actor:특허에관한절차밟는자
  - 지어냄: Procedure:전자문서이용신고절차
  - 지어냄: Procedure:특허에관한절차
  - 지어냄: Actor:전자문서특허에관한절차밟으려는자 -CAN_FILE-> Procedure:전자문서이용신고절차
  - 지어냄: Actor:특허에관한절차밟는자 -CAN_FILE-> Procedure:특허에관한절차
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:지식재산처장
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:특허심판원장
  - 지어냄: Procedure:특허에관한절차 -HANDLED_BY-> Actor:지식재산처장
  - 지어냄: Procedure:특허에관한절차 -HANDLED_BY-> Actor:특허심판원장
- chunk 17
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있으면
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있으면 -NOT_ELIGIBLE_FOR-> Right:특허
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 -NOT_ELIGIBLE_FOR-> Right:특허
- chunk 21
  - 놓침: Invention:공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명
  - 놓침: Right:특허
  - 놓침: Invention:공공의질서또는선량한풍속에어긋나거나공중의위생을해칠우려가있는발명 -NOT_ELIGIBLE_FOR-> Right:특허
  - 지어냄: Invention:특허를받을수없는발명
  - 지어냄: Invention:특허를받을수없는발명 -NOT_ELIGIBLE_FOR-> Right:특허를받을수있는권리
- chunk 51
  - 놓침: Actor:누구든지
  - 놓침: Actor:정당한권리자
  - 놓침: Actor:지식재산처장
  - 놓침: Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내
  - 놓침: Period:특허출원일부터3년이내
  - 놓침: Procedure:출원심사의청구
  - 놓침: Actor:누구든지 -CAN_FILE-> Procedure:출원심사의청구
  - 놓침: Actor:정당한권리자 -CAN_FILE-> Procedure:출원심사의청구
  - 놓침: Procedure:출원심사의청구 -HANDLED_BY-> Actor:지식재산처장
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:특허출원일부터3년이내
- chunk 73
  - 지어냄: Period:무권리자의특허출원일의다음날부터
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:무권리자의특허출원일의다음날부터
- chunk 100
  - 놓침: Actor:침해할우려가있는자
  - 놓침: Remedy:그밖에침해의예방에필요한행위
  - 놓침: Remedy:침해행위를조성한물건의폐기
  - 놓침: Remedy:침해행위에제공된설비의제거
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해의금지또는예방 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해한자
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자
- chunk 116
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판
  - 지어냄: Actor:특허거절결정등을받은자
  - 지어냄: Actor:특허거절결정등을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판
- chunk 186
  - 놓침: Actor:특허권또는전용실시권을침해한자
  - 놓침: Actor:특허권또는전용실시권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금
  - 놓침: Actor:특허권또는전용실시권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역
  - 지어냄: Actor:전용실시권을침해한자
  - 지어냄: Actor:특허권을침해한자
  - 지어냄: Actor:전용실시권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금
  - 지어냄: Actor:전용실시권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역
  - 지어냄: Actor:특허권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금
  - 지어냄: Actor:특허권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역

## 5회차 상세

- 구분: 비교 A: 2회차 스키마 + 기본 추출 프롬프트, 3회 반복
- 측정: 같은 설정으로 3회 추출. 재현율·지어냄 비율은 평균, 역량질문은 과반 실행에서 이어지면 답 가능. 실행별: 재현율 0.866 / 지어냄 0.304 / 역량질문 9/9, 재현율 0.817 / 지어냄 0.309 / 역량질문 8/9, 재현율 0.927 / 지어냄 0.269 / 역량질문 8/9
- 스키마 초안 출처: work/schema-draft-r2.reviewed.json
- 추출 프롬프트: 라이브러리 기본값(ERExtractionTemplate)
- 고친 내용: 1회차 시험 분석 반영(데이터 엔지니어 검토): ① 모든 노드 설명에 '관계로 이어질 때만 뽑음' 공통 규칙 추가(고립 노드 지어냄 대응) ② 이름 공통 규칙 추가: 괄호 속 부연 설명 제거, '또는'으로 묶인 표현은 나누지 않음(chunk 82·116 불일치, CQ04 끊김 대응) ③ Procedure 범위를 심사 청구와 불복·분쟁 절차로 좁히고 소관 업무 나열·언급 문장 제외(chunk 15·116·180 지어냄 대응) ④ HOLDS는 권리 귀속을 직접 정한 문장에서만(chunk 100·186 지어냄 대응) ⑤ HANDLED_BY에 기관 소관 절차의 상속 규칙 추가(CQ04) ⑥ Period·HAS_DURATION은 원칙적 존속기간만(chunk 73 지어냄 대응) ⑦ Right 이름을 줄이지 않음(chunk 21 불일치 대응)

### 원인 추정

- 비교 결과 A(2회차 스키마 + 기본 프롬프트)가 B보다 재현율이 높고(0.870 대 0.679) 지어냄 비율이 낮으며(0.294 대 0.369), 실행 간 폭도 좁음(재현율 0.817 ~ 0.927 대 0.512 ~ 0.854)
- A의 지어냄은 실행마다 반복되는 항목이 대부분(3회 중 3회): chunk 73의 제88조②·제89조 기간(무권리자의 특허출원일의 다음 날부터, 5년의 기간까지)과 그 주체, chunk 15의 전자문서 이용신고와 처리 기관 → 변동이 아니라 스키마 설명 또는 기대 결과 범위의 문제
- chunk 73의 반복 지어냄은 조문에 실제로 있는 존속기간 관련 내용(예외적 기산점·연장)이라, 기대 결과(제1항만)의 범위를 넓힐지 스키마 설명으로 막을지 결정이 필요함
- CQ04는 3회 중 1회만 이어짐: 거절결정 불복 주체 이름이 '특허거절결정을 받은 자' 등으로 줄거나 HANDLED_BY가 빠지는 변동

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 7.3 | 0 | 7.3 |
| 17 | 역량질문 관련 | 11 | 12.3 | 7.7 | 4.7 |
| 21 | 역량질문 관련 | 8 | 8.3 | 5 | 3.3 |
| 51 | 역량질문 관련 | 11 | 11 | 11 | 0 |
| 73 | 역량질문 관련 | 3 | 11.7 | 1 | 10.7 |
| 82 | 역량질문 관련 | 9 | 9 | 9 | 0 |
| 100 | 역량질문 관련 | 24 | 26 | 23 | 3 |
| 116 | 역량질문 관련 | 11 | 9.7 | 9.7 | 0 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 5.7 | 5 | 0.7 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O (3/3) | 산업상이용할수있는발명 → 특허 / 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O (3/3) | 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O (3/3) | 정당한권리자 → 출원심사의청구 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | X (1/3) | 특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 → 특허거절결정등에대한심판 → 특허거절결정 / 특허거절결정등에대한심판 → 특허심판원 |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O (3/3) | 출원심사의청구 → 특허출원일부터3년이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O (3/3) | 특허권 → 무권리자의특허출원일의다음날부터 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O (3/3) | 특허권또는전용실시권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O (3/3) | 특허권 → 특허출원을한때부터국내에있는물건 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O (3/3) | 특허권자 → 침해의금지또는예방 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:지식재산처장 (3/3)
  - 지어냄: Actor:특허심판원장 (3/3)
  - 지어냄: Procedure:전자문서이용신고 (3/3)
  - 지어냄: Actor:특허에관한절차를밟는자 (2/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 (2/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:지식재산처장 (3/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:특허심판원장 (3/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 -CAN_FILE-> Procedure:전자문서이용신고 (2/3)
  - 지어냄: Actor:특허에관한절차를밟는자 -CAN_FILE-> Procedure:전자문서이용신고 (1/3)
- chunk 17
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 (3/3)
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 (2/3)
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (2/3)
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 (2/3)
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 (1/3)
  - 지어냄: Invention:그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 (1/3)
  - 지어냄: Invention:그특허출원일전에출원된특허출원일것 (1/3)
  - 지어냄: Invention:그특허출원후제64조에따라출원공개되거나제87조제3항에따라등록공고된특허출원일것 (1/3)
  - 지어냄: Invention:특허출원한발명 (1/3)
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 -NOT_ELIGIBLE_FOR-> Right:특허 (2/3)
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:그특허출원일전에출원된특허출원일것 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:그특허출원후제64조에따라출원공개되거나제87조제3항에따라등록공고된특허출원일것 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:특허출원한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
- chunk 21
  - 놓침: Actor:그승계인 (2/3)
  - 놓침: Actor:발명을한사람 (2/3)
  - 놓침: Right:특허를받을수있는권리 (1/3)
  - 놓침: Actor:그승계인 -HOLDS-> Right:특허를받을수있는권리 (2/3)
  - 놓침: Actor:발명을한사람 -HOLDS-> Right:특허를받을수있는권리 (2/3)
  - 지어냄: Actor:무권리자 (2/3)
  - 지어냄: Actor:발명을한사람또는그승계인 (2/3)
  - 지어냄: Actor:정당한권리자 (2/3)
  - 지어냄: Actor:2명이상이공동으로발명한경우 (1/3)
  - 지어냄: Actor:2명이상이공동으로발명한경우 -HOLDS-> Right:특허 (1/3)
  - 지어냄: Actor:발명을한사람또는그승계인 -HOLDS-> Right:특허 (1/3)
  - 지어냄: Actor:발명을한사람또는그승계인 -HOLDS-> Right:특허를받을수있는권리 (1/3)
- chunk 73
  - 놓침: Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (3/3)
  - 놓침: Right:특허권 -HAS_DURATION-> Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (3/3)
  - 지어냄: Actor:무권리자 (3/3)
  - 지어냄: Actor:정당한권리자 (3/3)
  - 지어냄: Actor:허가등을받은자 (3/3)
  - 지어냄: Period:5년의기간까지 (3/3)
  - 지어냄: Period:무권리자의특허출원일의다음날부터 (3/3)
  - 지어냄: Period:특허출원일후20년이되는날까지 (3/3)
  - 지어냄: Period:허가등을받은날부터14년 (2/3)
  - 지어냄: Invention:특허발명 (1/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:5년의기간까지 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:무권리자의특허출원일의다음날부터 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:특허출원일후20년이되는날까지 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:허가등을받은날부터14년 (2/3)
- chunk 100
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
  - 지어냄: Actor:지식재산처장 (3/3)
  - 지어냄: Actor:통상실시권자 (3/3)
  - 지어냄: Actor:지식재산처소속공무원 (2/3)
  - 지어냄: Decision:확정된결정 (1/3)
- chunk 116
  - 놓침: Actor:특허심판원 (2/3)
  - 놓침: Procedure:특허거절결정등에대한심판 -HANDLED_BY-> Actor:특허심판원 (2/3)
- chunk 186
  - 지어냄: Right:전용실시권 (1/3)
  - 지어냄: Right:특허권 (1/3)

## 6회차 상세

- 구분: 비교 B: 4회차 스키마 + 보완 추출 프롬프트, 3회 반복
- 측정: 같은 설정으로 3회 추출. 재현율·지어냄 비율은 평균, 역량질문은 과반 실행에서 이어지면 답 가능. 실행별: 재현율 0.854 / 지어냄 0.231 / 역량질문 8/9, 재현율 0.512 / 지어냄 0.538 / 역량질문 8/9, 재현율 0.671 / 지어냄 0.337 / 역량질문 9/9
- 스키마 초안 출처: work/schema-draft-r4.reviewed.json
- 추출 프롬프트: work/extraction-prompt-r4.txt
- 고친 내용: 4회차(사용자 지시): 2회차 설명을 바탕으로 ① 이름 규칙의 '또는' 처리를 확정 기대 결과와 맞춤 — 따로 설 수 있는 이름(발명을 한 사람 또는 그 승계인, 특허권자 또는 전용실시권자)은 나누고, 수식어를 함께 받는 표현(특허권 또는 전용실시권을 침해한 자, 침해의 금지 또는 예방)은 나누지 않음(2회차 규칙은 모두 나누지 않아 chunk 21 기대 결과와 충돌) ② 추출 프롬프트 완화(extraction-prompt.txt): 3회차의 '해당 없으면 빈 결과가 정답' 규칙을 빼고, 고립 노드 금지·설명 규칙 준수·원문 이름 복사만 남김

### 원인 추정

- B(4회차 스키마 + 보완 프롬프트)는 실행 간 변동이 가장 큼: 재현율 0.512 ~ 0.854, 지어냄 비율 0.231 ~ 0.538
- 보완 프롬프트에서 chunk 51·100이 실행에 따라 비거나 일부만 뽑혀 재현율이 떨어지고, chunk 82에서 이름 변형(괄호 속 부연 포함 등)이 늘어 지어냄이 커짐
- 결론: 보완 프롬프트는 고립 노드는 줄이지만 범위 안 추출을 불안정하게 만들어, 기본 프롬프트보다 나쁨

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 8.3 | 0 | 8.3 |
| 17 | 역량질문 관련 | 11 | 11 | 6.3 | 4.7 |
| 21 | 역량질문 관련 | 8 | 7.7 | 7 | 0.7 |
| 51 | 역량질문 관련 | 11 | 7.3 | 7.3 | 0 |
| 73 | 역량질문 관련 | 3 | 7.7 | 1.7 | 6 |
| 82 | 역량질문 관련 | 9 | 15.7 | 7 | 8.7 |
| 100 | 역량질문 관련 | 24 | 12 | 12 | 0 |
| 116 | 역량질문 관련 | 11 | 12.7 | 10.3 | 2.3 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 6 | 4 | 2 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O (2/3) | 산업상이용할수있는발명 → 특허 / 특허출원전에국내또는국외에서반포된간행물에게재되었거나전기통신회선을통하여공중이이용할수있는발명 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O (3/3) | 특허출원전에국내또는국외에서반포된간행물에게재되었거나전기통신회선을통하여공중이이용할수있는발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O (3/3) | 특허거절결정을받은자 → 특허거절결정등에대한심판 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | O (2/3) | 특허거절결정을받은자 → 특허거절결정등에대한심판 → 특허권의존속기간의연장등록거절결정 / 특허거절결정등에대한심판 → 특허심판원 |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O (3/3) | 특허거절결정등에대한심판 → 그결정등본을송달받은날부터3개월이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O (3/3) | 특허권 → 특허권을설정등록한날부터특허출원일후20년이되는날까지 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O (3/3) | 특허권또는전용실시권을침해한자 → 1억원이하의벌금 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O (3/3) | 특허권 → 특허출원을한때부터국내에있는물건 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O (3/3) | 특허권자 → 그밖에침해의예방에필요한행위 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:지식재산처장 (3/3)
  - 지어냄: Actor:특허심판원장 (3/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 (2/3)
  - 지어냄: Procedure:전자문서이용신고절차 (2/3)
  - 지어냄: Actor:특허에관한절차를밟는자 (1/3)
  - 지어냄: Procedure:전자문서이용신고 (1/3)
  - 지어냄: Actor:지식재산처 (1/3)
  - 지어냄: Actor:특허심판원 (1/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 -CAN_FILE-> Procedure:전자문서이용신고절차 (2/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:지식재산처장 (2/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:특허심판원장 (2/3)
  - 지어냄: Actor:특허에관한절차를밟는자 -CAN_FILE-> Procedure:전자문서이용신고 (1/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:지식재산처장 (1/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:특허심판원장 (1/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:지식재산처 (1/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:특허심판원 (1/3)
- chunk 17
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 (3/3)
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 (3/3)
  - 놓침: Invention:산업상이용할수있는발명 (1/3)
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 놓침: Invention:산업상이용할수있는발명 -ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 (2/3)
  - 지어냄: Invention:특허출원한발명 (2/3)
  - 지어냄: Invention:다른특허출원의발명 (1/3)
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 (1/3)
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 (1/3)
  - 지어냄: Invention:그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (2/3)
  - 지어냄: Invention:특허출원한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (2/3)
  - 지어냄: Invention:다른특허출원의발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:특허출원전에그발명이속하는기술분야에서통상의지식을가진사람이제1항각호의어느하나에해당하는발명에의하여쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
  - 지어냄: Invention:특허출원한발명이다음각호의요건을모두갖춘다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한경우 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
- chunk 21
  - 놓침: Right:특허를받을수있는권리 (1/3)
  - 놓침: Actor:그승계인 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 놓침: Actor:발명을한사람 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 지어냄: Actor:그승계인 -HOLDS-> Right:특허 (1/3)
  - 지어냄: Actor:발명을한사람 -HOLDS-> Right:특허 (1/3)
- chunk 51
  - 놓침: Actor:누구든지 (1/3)
  - 놓침: Actor:정당한권리자 (1/3)
  - 놓침: Actor:지식재산처장 (1/3)
  - 놓침: Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내 (1/3)
  - 놓침: Period:특허출원일부터3년이내 (1/3)
  - 놓침: Procedure:출원심사의청구 (1/3)
  - 놓침: Actor:누구든지 -CAN_FILE-> Procedure:출원심사의청구 (1/3)
  - 놓침: Actor:정당한권리자 -CAN_FILE-> Procedure:출원심사의청구 (1/3)
  - 놓침: Procedure:출원심사의청구 -HANDLED_BY-> Actor:지식재산처장 (1/3)
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:정당한권리자가특허출원을한날분할출원을한날분리출원을한날또는변경출원을한날부터각각30일이내 (1/3)
  - 놓침: Procedure:출원심사의청구 -HAS_DEADLINE-> Period:특허출원일부터3년이내 (1/3)
- chunk 73
  - 놓침: Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (2/3)
  - 놓침: Right:특허권 -HAS_DURATION-> Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Period:5년의기간까지 (3/3)
  - 지어냄: Period:무권리자의특허출원일의다음날부터 (3/3)
  - 지어냄: Period:특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Period:허가등을받은날부터14년 (1/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:5년의기간까지 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:무권리자의특허출원일의다음날부터 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:허가등을받은날부터14년 (1/3)
- chunk 82
  - 놓침: Exclusion:국내를통과하는데불과한선박항공기차량또는이에사용되는기계기구장치그밖의물건 (1/3)
  - 놓침: Exclusion:약사법에따른조제행위와그조제에의한의약 (1/3)
  - 놓침: Exclusion:연구또는시험을하기위한특허발명의실시 (1/3)
  - 놓침: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:국내를통과하는데불과한선박항공기차량또는이에사용되는기계기구장치그밖의물건 (1/3)
  - 놓침: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:약사법에따른조제행위와그조제에의한의약 (1/3)
  - 놓침: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:연구또는시험을하기위한특허발명의실시 (1/3)
  - 지어냄: Exclusion:국내를통과하는데불과한선박 (1/3)
  - 지어냄: Exclusion:국내를통과하는데불과한차량 (1/3)
  - 지어냄: Exclusion:국내를통과하는데불과한항공기 (1/3)
  - 지어냄: Exclusion:그밖의물건 (1/3)
  - 지어냄: Exclusion:그조제에의한의약 (1/3)
  - 지어냄: Exclusion:둘이상의의약을혼합하여의약을제조하는방법의발명 (1/3)
  - 지어냄: Exclusion:둘이상의의약이혼합되어제조되는의약의발명 (1/3)
  - 지어냄: Exclusion:시험을하기위한특허발명의실시 (1/3)
  - 지어냄: Exclusion:연구를하기위한특허발명의실시 (1/3)
  - 지어냄: Exclusion:이에사용되는기계 (1/3)
  - 지어냄: Exclusion:이에사용되는기구 (1/3)
  - 지어냄: Exclusion:이에사용되는장치 (1/3)
  - 지어냄: Exclusion:조제행위 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:국내를통과하는데불과한선박 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:국내를통과하는데불과한차량 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:국내를통과하는데불과한항공기 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:그밖의물건 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:그조제에의한의약 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:둘이상의의약을혼합하여의약을제조하는방법의발명 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:둘이상의의약이혼합되어제조되는의약의발명 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:시험을하기위한특허발명의실시 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:연구를하기위한특허발명의실시 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:이에사용되는기계 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:이에사용되는기구 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:이에사용되는장치 (1/3)
  - 지어냄: Right:특허권 -NOT_EFFECTIVE_FOR-> Exclusion:조제행위 (1/3)
- chunk 100
  - 놓침: Remedy:그밖에침해의예방에필요한행위 (2/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 (2/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 (2/3)
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해한자 (3/3)
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (3/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해한자 (3/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (3/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해한자 (3/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (3/3)
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위 (2/3)
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기 (2/3)
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거 (2/3)
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:그밖에침해의예방에필요한행위 (2/3)
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기 (2/3)
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거 (2/3)
- chunk 116
  - 놓침: Actor:특허심판원 (1/3)
  - 놓침: Procedure:특허거절결정등에대한심판 -HANDLED_BY-> Actor:특허심판원 (1/3)
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정을받은자 (3/3)
  - 지어냄: Actor:지식재산처장 (1/3)
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판 (3/3)
- chunk 186
  - 놓침: Actor:특허권또는전용실시권을침해한자 (1/3)
  - 놓침: Actor:특허권또는전용실시권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금 (1/3)
  - 놓침: Actor:특허권또는전용실시권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역 (1/3)
  - 지어냄: Actor:전용실시권을침해한자 (1/3)
  - 지어냄: Actor:특허권을침해한자 (1/3)
  - 지어냄: Actor:전용실시권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금 (1/3)
  - 지어냄: Actor:전용실시권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역 (1/3)
  - 지어냄: Actor:특허권을침해한자 -PUNISHED_BY-> Penalty:1억원이하의벌금 (1/3)
  - 지어냄: Actor:특허권을침해한자 -PUNISHED_BY-> Penalty:7년이하의징역 (1/3)

## 7회차 상세

- 구분: 2회차 기반 + 제89조 연장 제외·제88조② 기산점 포함(기대 결과 chunk 73 수정), 기본 추출 프롬프트, 3회 반복
- 측정: 같은 설정으로 3회 추출. 재현율·지어냄 비율은 평균, 역량질문은 과반 실행에서 이어지면 답 가능. 실행별: 재현율 0.905 / 지어냄 0.276 / 역량질문 8/9, 재현율 0.810 / 지어냄 0.382 / 역량질문 8/9, 재현율 0.786 / 지어냄 0.371 / 역량질문 8/9
- 스키마 초안 출처: work/schema-draft-r7.reviewed.json
- 추출 프롬프트: 라이브러리 기본값(ERExtractionTemplate)
- 고친 내용: 7회차(사용자 지시): 2회차 설명을 바탕으로 ① Period·HAS_DURATION에 존속기간 기산점 특례(제88조② 유형)를 넣고, 존속기간 연장 기간·연장 한도(제89조·제92조의2 유형)는 뽑지 않도록 명시 ② 4회차에서 고친 '또는' 이름 규칙(따로 설 수 있는 이름은 나누고, 수식어를 함께 받는 표현은 나누지 않음)을 유지해 확정 기대 결과와 맞춤. 추출 프롬프트는 라이브러리 기본값

### 원인 추정

- 기대 결과 chunk 73에 제88조② 기산점을 넣은 효과: '무권리자의 특허출원일의 다음 날부터'는 3회 모두 일치(CQ06 예시로도 나옴)
- 제89조 연장 기간을 설명으로 막았지만 '5년의 기간까지'는 3회 모두, '허가등을 받은 날부터 14년'은 2회 뽑힘 → 기본 프롬프트에서는 설명의 [뽑지 않음]이 거의 지켜지지 않음
- 5회차(2회차 스키마) 대비 지어냄 증가(0.294 → 0.343): chunk 15 전자문서(7.3 → 10), chunk 21 고립 주체(무권리자·직원 등) 증가. CQ04는 0/3(특허심판원 HANDLED_BY 누락 2/3)
- 저장된 추출 결과로 계산(LLM 호출 없음, 현재 기대 결과 기준): 고립 노드를 지우는 후처리를 하면 5회차 지어냄 0.274 → 0.206, 7회차 0.343 → 0.287. 재현율은 거의 그대로(0.873, 0.829)
- 종합: 남은 지어냄의 대부분은 관계로 이어진 범위 밖 항목(전자문서 절차, 존속기간 연장, 조건문 이름 변형)이라 스키마 설명만으로는 0.1 이하에 닿기 어려움. 청크 단위 범위 판별 같은 별도 단계가 필요함

### 청크별 결과

| 청크 | 구분 | 기대 | 뽑힘 | 일치 | 지어냄 |
|---|---|---|---|---|---|
| 15 | 역량질문 무관 | 0 | 10 | 0 | 10 |
| 17 | 역량질문 관련 | 11 | 11 | 7 | 4 |
| 21 | 역량질문 관련 | 8 | 13.3 | 7 | 6.3 |
| 51 | 역량질문 관련 | 11 | 11 | 11 | 0 |
| 73 | 역량질문 관련 | 5 | 12 | 3.7 | 8.3 |
| 82 | 역량질문 관련 | 9 | 9 | 9 | 0 |
| 100 | 역량질문 관련 | 24 | 24 | 18.7 | 5.3 |
| 116 | 역량질문 관련 | 11 | 10.7 | 8.7 | 2 |
| 180 | 역량질문 무관 | 0 | 0 | 0 | 0 |
| 186 | 역량질문 관련 | 5 | 5.7 | 5 | 0.7 |

### 역량질문 답 가능

| ID | 패턴 경로 | 답 가능 | 이어진 예 |
|---|---|---|---|
| CQ01 | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | O (3/3) | 산업상이용할수있는발명 → 특허 / 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ02 | `Invention -NOT_ELIGIBLE_FOR-> Right` | O (3/3) | 특허출원전에국내또는국외에서공지되었거나공연히실시된발명 → 특허 |
| CQ03 | `Actor -CAN_FILE-> Procedure` | O (3/3) | 누구든지 → 출원심사의청구 |
| CQ04 | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | X (0/3) |  |
| CQ05 | `Procedure -HAS_DEADLINE-> Period` | O (3/3) | 출원심사의청구 → 각각30일이내 |
| CQ06 | `Right -HAS_DURATION-> Period` | O (3/3) | 특허권 → 무권리자의특허출원일의다음날부터 |
| CQ07 | `Actor -PUNISHED_BY-> Penalty` | O (3/3) | 특허권또는전용실시권을침해한자 → 7년이하의징역 |
| CQ08 | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | O (3/3) | 특허권 → 특허출원을한때부터국내에있는물건 |
| CQ09 | `Actor -CAN_CLAIM-> Remedy` | O (3/3) | 특허권자 → 침해의금지또는예방 |

### NOT_IN_SCHEMA로 지워진 항목(타입별)

- 없음

### 놓친 항목과 지어낸 항목

- chunk 15
  - 지어냄: Actor:지식재산처장 (3/3)
  - 지어냄: Actor:특허심판원장 (3/3)
  - 지어냄: Actor:특허에관한절차를밟는자 (3/3)
  - 지어냄: Procedure:특허에관한절차 (2/3)
  - 지어냄: Procedure:전자문서이용신고 (2/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 (1/3)
  - 지어냄: Procedure:전자문서이용신고절차 (1/3)
  - 지어냄: Actor:특허에관한절차를밟는자 -CAN_FILE-> Procedure:특허에관한절차 (2/3)
  - 지어냄: Procedure:특허에관한절차 -HANDLED_BY-> Actor:지식재산처장 (2/3)
  - 지어냄: Procedure:특허에관한절차 -HANDLED_BY-> Actor:특허심판원장 (2/3)
  - 지어냄: Actor:특허에관한절차를밟는자 -CAN_FILE-> Procedure:전자문서이용신고 (2/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:지식재산처장 (2/3)
  - 지어냄: Procedure:전자문서이용신고 -HANDLED_BY-> Actor:특허심판원장 (2/3)
  - 지어냄: Actor:전자문서로특허에관한절차를밟으려는자 -CAN_FILE-> Procedure:전자문서이용신고절차 (1/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:지식재산처장 (1/3)
  - 지어냄: Procedure:전자문서이용신고절차 -HANDLED_BY-> Actor:특허심판원장 (1/3)
- chunk 17
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 (3/3)
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 (3/3)
  - 놓침: Invention:그발명이속하는기술분야에서통상의지식을가진사람이쉽게발명할수있는발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 놓침: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명과동일한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 지어냄: Invention:특허출원한발명 (3/3)
  - 지어냄: Invention:그발명 (2/3)
  - 지어냄: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명 (1/3)
  - 지어냄: Invention:특허출원한발명 -NOT_ELIGIBLE_FOR-> Right:특허 (3/3)
  - 지어냄: Invention:그발명 -NOT_ELIGIBLE_FOR-> Right:특허 (2/3)
  - 지어냄: Invention:다른특허출원의출원서에최초로첨부된명세서또는도면에기재된발명 -NOT_ELIGIBLE_FOR-> Right:특허 (1/3)
- chunk 21
  - 놓침: Right:특허를받을수있는권리 (1/3)
  - 놓침: Actor:그승계인 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 놓침: Actor:발명을한사람 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 지어냄: Actor:무권리자 (3/3)
  - 지어냄: Actor:정당한권리자 (3/3)
  - 지어냄: Actor:2명이상이공동으로발명한경우 (2/3)
  - 지어냄: Actor:지식재산처직원 (2/3)
  - 지어냄: Actor:특허심판원직원 (2/3)
  - 지어냄: Period:30일 (1/3)
  - 지어냄: Procedure:특허출원 (1/3)
  - 지어냄: Actor:2명이상이공동으로발명한경우 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 지어냄: Actor:정당한권리자 -HOLDS-> Right:특허를받을수있는권리 (1/3)
  - 지어냄: Actor:2명이상이공동으로발명한경우 -HOLDS-> Right:특허 (1/3)
  - 지어냄: Actor:그승계인 -HOLDS-> Right:특허 (1/3)
  - 지어냄: Actor:발명을한사람 -HOLDS-> Right:특허 (1/3)
- chunk 73
  - 놓침: Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (2/3)
  - 놓침: Right:특허권 -HAS_DURATION-> Period:특허권을설정등록한날부터특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Period:5년의기간까지 (3/3)
  - 지어냄: Invention:특허발명 (2/3)
  - 지어냄: Period:특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Period:허가등을받은날부터14년 (2/3)
  - 지어냄: Actor:정당한권리자 (2/3)
  - 지어냄: Actor:무권리자 (1/3)
  - 지어냄: Actor:허가등을받은자 (1/3)
  - 지어냄: Period:허가등을받은날부터14년을초과하여 (1/3)
  - 지어냄: Period:실시할수없었던기간 (1/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:5년의기간까지 (3/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:특허출원일후20년이되는날까지 (2/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:허가등을받은날부터14년 (2/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:허가등을받은날부터14년을초과하여 (1/3)
  - 지어냄: Actor:정당한권리자 -HOLDS-> Right:특허권 (1/3)
  - 지어냄: Right:특허권 -HAS_DURATION-> Period:실시할수없었던기간 (1/3)
- chunk 100
  - 놓침: Remedy:침해행위를조성한물건의폐기 (1/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 (1/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해한자 (2/3)
  - 놓침: Remedy:침해행위를조성한물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (2/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해한자 (2/3)
  - 놓침: Remedy:침해행위에제공된설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (2/3)
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기 (1/3)
  - 놓침: Actor:전용실시권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거 (1/3)
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위를조성한물건의폐기 (1/3)
  - 놓침: Actor:특허권자 -CAN_CLAIM-> Remedy:침해행위에제공된설비의제거 (1/3)
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해한자 (1/3)
  - 놓침: Remedy:그밖에침해의예방에필요한행위 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
  - 지어냄: Actor:지식재산처소속공무원 (2/3)
  - 지어냄: Actor:지식재산처장 (2/3)
  - 지어냄: Remedy:물건의폐기 (1/3)
  - 지어냄: Remedy:설비의제거 (1/3)
  - 지어냄: Actor:통상실시권자 (1/3)
  - 지어냄: Decision:확정된결정 (1/3)
  - 지어냄: Actor:전용실시권자 -CAN_CLAIM-> Remedy:물건의폐기 (1/3)
  - 지어냄: Actor:전용실시권자 -CAN_CLAIM-> Remedy:설비의제거 (1/3)
  - 지어냄: Actor:특허권자 -CAN_CLAIM-> Remedy:물건의폐기 (1/3)
  - 지어냄: Actor:특허권자 -CAN_CLAIM-> Remedy:설비의제거 (1/3)
  - 지어냄: Remedy:물건의폐기 -CLAIMED_AGAINST-> Actor:침해한자 (1/3)
  - 지어냄: Remedy:물건의폐기 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
  - 지어냄: Remedy:설비의제거 -CLAIMED_AGAINST-> Actor:침해한자 (1/3)
  - 지어냄: Remedy:설비의제거 -CLAIMED_AGAINST-> Actor:침해할우려가있는자 (1/3)
- chunk 116
  - 놓침: Actor:특허심판원 (2/3)
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 (1/3)
  - 놓침: Procedure:특허거절결정등에대한심판 -HANDLED_BY-> Actor:특허심판원 (3/3)
  - 놓침: Actor:특허거절결정또는특허권의존속기간의연장등록거절결정을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판 (1/3)
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정을받은자 (1/3)
  - 지어냄: Actor:지식재산처장 (1/3)
  - 지어냄: Actor:특허거절결정등을받은자 (1/3)
  - 지어냄: Procedure:특허취소신청 (1/3)
  - 지어냄: Actor:특허권의존속기간의연장등록거절결정을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판 (1/3)
  - 지어냄: Actor:특허거절결정등을받은자 -CAN_FILE-> Procedure:특허거절결정등에대한심판 (1/3)
- chunk 186
  - 지어냄: Right:전용실시권 (1/3)
  - 지어냄: Right:특허권 (1/3)
