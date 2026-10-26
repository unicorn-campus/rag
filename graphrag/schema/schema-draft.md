# 스키마 초안 (1회차)

- 상태: 확정(2026-10-26 사용자 확정) (확정하면 schema-draft.json의 status를 "확정"으로 바꿈)
- 출처: work/schema-draft-r1.reviewed.json

## 1. 후보 처리(추가·제거·병합)

| 처리 | 후보 | 스키마 이름 | 근거 |
|---|---|---|---|
| 제거 | PatentApplication, InternationalPatentApplication, Application, ExtensionApplication |  | 조문에는 개별 출원 사례가 없음. 출원과 관련된 청구·신청은 Procedure로 담음 |
| 병합 | Party, Person, Applicant, Organization, RightHolder, PatentRightHolder, Agent, Authority, GovernmentAgency, PatentOffice, Examiner, Judge, Official, Director, Tribunal, Court | Actor | 주체와 기관을 나누면 '특허심판원' 같은 이름이 양쪽에 걸쳐 흔들림. 조문이 권한·의무를 정하는 주체 유형을 하나로 묶고 역할은 관계로 구분함 |
| 병합 | PatentRight, ExclusiveLicense, License | Right | 특허권·전용실시권·통상실시권은 모두 권리. 이름으로 구분함 |
| 병합 | Procedure, Trial, Appeal, ReexaminationRequest, CorrectionRequest, Amendment | Procedure | 당사자가 하는 청구·신청·심판을 하나로 묶음. CQ03·CQ04·CQ05의 중심 노드 |
| 병합 | Decision, Judgment, DecisionDocument | Decision | 특허거절결정 같은 처분·결정. CQ04의 불복 대상 |
| 병합 | Period, TimePeriod | Period | 기간·기한. 실제 날짜가 아니라 조문 표현 문자열로 담음(CQ05·CQ06) |
| 유지 | Invention | Invention | 특허를 받을 수 있는지가 갈리는 발명의 유형(CQ01·CQ02). 개별 발명 이름은 담지 않음 |
| 제거 | Requirement |  | 조문은 '신규성·진보성' 같은 요건 명칭을 쓰지 않고 '~한 발명은 특허를 받을 수 없다'로 씀. 요건은 Invention 유형과 ELIGIBLE_FOR·NOT_ELIGIBLE_FOR 관계로 담아야 원문 표현과 맞음 |
| 병합 | Penalty, Offense, InfringingAct | Penalty | 형벌은 Penalty, 침해한 자는 Actor로 담고 PUNISHED_BY로 이음(CQ07) |
| 추가 |  | Exclusion | 특허권의 효력이 미치지 아니하는 경우(CQ08). 후보에는 없지만 질문 답에 필요함 |
| 추가 |  | Remedy | 침해 시 청구할 수 있는 구제 수단(CQ09). 후보의 CLAIM_COMPENSATION 등 관계를 노드로 올림 |
| 제거 | Article, LegalArticle, LegalProvision, Law, Decree, CivilProcedureLaw, CivilProcedureCostLaw |  | 조문 노드를 두면 역량질문과 무관한 청크에서도 노드가 생김. 근거 조문은 청크(lexical graph)로 추적함 |
| 제거 | Fee, Cost, Document, Evidence, Specification, Claim, ConfidentialityOrder, BusinessEntity, BusinessOperator, Method |  | 역량질문 답에 쓰이지 않는 행정·서류 개념은 범위 밖 |

## 2. 노드 타입

| 노드 타입 | 설명(추출 기준) | 속성 |
|---|---|---|
| Actor | 특허법 조문이 권리·의무·권한을 정한 주체 유형(사람·법인·행정기관). 예: 특허권자, 전용실시권자, 특허출원인, 누구든지, 특허거절결정을 받은 자, 특허권 또는 전용실시권을 침해한 자, 특허심판원, 심사관. 아래 패턴의 관계로 다른 노드와 이어질 때만 뽑고, 혼자 등장하는 주체는 뽑지 않음. 서류 제출·전자문서·송달·고유번호·열람 업무에만 나오는 주체(예: 특허에 관한 절차를 밟는 자, 지식재산처장이 서류를 받는 문장)는 뽑지 않음 | `name`(STRING): 조문 원문의 주체 표현 그대로. 조사(은·는·이·가·에게)와 괄호 속 한자는 뺌. 예: '특허권자', '특허거절결정을 받은 자' |
| Right | 특허법이 정한 권리 또는 특허 그 자체. 예: 특허, 특허권, 전용실시권, 통상실시권, 특허를 받을 수 있는 권리. 특허를 받을 수 있는지, 존속기간, 효력 제한을 말하는 문장에서 뽑음 | `name`(STRING): 조문 원문의 권리 명칭 그대로. '특허를 받을 수 있다/없다'의 대상은 '특허'로 씀 |
| Invention | 특허를 받을 수 있는지가 갈리는 발명의 유형. 특허요건(제29조)과 특허를 받을 수 없는 발명(제32조) 문장에서만 뽑음. 예: 산업상 이용할 수 있는 발명, 특허출원 전에 국내 또는 국외에서 공지되었거나 공연히 실시된 발명, 공공의 질서 또는 선량한 풍속에 어긋나거나 공중의 위생을 해칠 우려가 있는 발명. 개별 발명 이름이나 '특허발명의 실시' 같은 표현은 뽑지 않음 | `name`(STRING): 조문 원문의 발명 유형 명사구 그대로(‘~한 발명’으로 끝남). 괄호 속 한자는 뺌 |
| Procedure | 당사자가 청구·신청·제기하는 특허 절차. 예: 출원심사의 청구, 특허거절결정에 대한 심판, 특허무효심판, 정정심판. 서류 제출 방식, 전자문서 이용신고, 송달·공시송달, 고유번호 신청, 서류 열람 같은 행정 사무는 뽑지 않음 | `name`(STRING): 조문 제목이나 원문에 나온 절차 명칭 그대로. 예: '출원심사의 청구', '특허거절결정에 대한 심판' |
| Decision | 지식재산처장·심사관·특허심판원이 내리는 처분·결정으로 불복 절차의 대상이 되는 것. 예: 특허거절결정, 특허권의 존속기간의 연장등록거절결정, 특허취소결정. 결정을 '받은 자'는 Actor로 뽑음 | `name`(STRING): 조문 원문의 결정 명칭 그대로 |
| Period | 절차의 기한 또는 권리의 존속기간을 나타내는 조문 표현. 예: 특허출원일부터 3년 이내, 그 결정등본을 송달받은 날부터 3개월 이내, 특허권의 설정등록을 한 날부터 특허출원일 후 20년이 되는 날까지. 송달의 효력 발생 시점이나 서류 제출 기간 같은 행정 사무 기간은 뽑지 않음 | `name`(STRING): 조문 원문의 기간 표현 그대로(기점과 끝을 모두 포함). 실제 날짜로 바꾸지 않음 |
| Penalty | 범죄에 대한 형벌. 예: 7년 이하의 징역, 1억원 이하의 벌금. 징역과 벌금이 '또는'으로 함께 나오면 각각 따로 뽑음 | `name`(STRING): 조문 원문의 형벌 표현 그대로. 예: '7년 이하의 징역', '1억원 이하의 벌금' |
| Exclusion | 특허권의 효력이 미치지 아니하는 경우(제96조 등). 예: 연구 또는 시험을 하기 위한 특허발명의 실시, 국내를 통과하는데 불과한 선박ㆍ항공기ㆍ차량 또는 이에 사용되는 기계ㆍ기구ㆍ장치, 그 밖의 물건, 특허출원을 한 때부터 국내에 있는 물건 | `name`(STRING): 조문 각 호의 표현 그대로(호 번호는 뺌) |
| Remedy | 권리를 침해당한 자가 침해한 자에게 청구할 수 있는 민사 구제. 예: 침해의 금지 또는 예방, 손해의 배상, 침해행위를 조성한 물건의 폐기. 형벌(Penalty)은 넣지 않음 | `name`(STRING): 조문 원문의 청구 내용 표현 그대로. 예: '침해의 금지 또는 예방' |

## 3. 관계 타입

| 관계 타입 | 설명(추출 기준) |
|---|---|
| ELIGIBLE_FOR | Invention → Right('특허'). '~한 발명은 특허를 받을 수 있다'일 때 |
| NOT_ELIGIBLE_FOR | Invention → Right('특허'). '~한 발명은 특허를 받을 수 없다', '~를 제외하고는 특허를 받을 수 있다'의 제외 대상일 때 |
| CAN_FILE | Actor → Procedure. 주체가 그 절차를 청구·신청할 수 있다고 정한 문장. 예: 누구든지 → 출원심사의 청구 |
| HANDLED_BY | Procedure → Actor. 절차를 심리·처리하는 기관. 예: 특허거절결정에 대한 심판 → 특허심판원, 출원심사의 청구 → 지식재산처장(‘~에게 청구’) |
| TARGETS | Procedure → Decision. 불복 절차의 대상 처분. 예: 특허거절결정에 대한 심판 → 특허거절결정 |
| HAS_DEADLINE | Procedure → Period. 절차를 할 수 있는 기한. '~ 이내에 ~를 청구할 수 있다'일 때 |
| HAS_DURATION | Right → Period. 권리의 존속기간. '~의 존속기간은 ~까지로 한다'일 때 |
| NOT_EFFECTIVE_FOR | Right → Exclusion. '특허권의 효력은 다음 각 호의 어느 하나에 해당하는 사항에는 미치지 아니한다'의 각 호 |
| HOLDS | Actor → Right. 주체가 그 권리를 가짐. 예: 특허권자 → 특허권 |
| CAN_CLAIM | Actor → Remedy. 권리자가 청구할 수 있는 구제. 예: 특허권자 → 침해의 금지 또는 예방 |
| CLAIMED_AGAINST | Remedy → Actor. 구제 청구의 상대방. 예: 침해의 금지 또는 예방 → 권리를 침해한 자 |
| PUNISHED_BY | Actor → Penalty. 그 행위를 한 자가 받는 형벌. 예: 특허권 또는 전용실시권을 침해한 자 → 7년 이하의 징역 |

## 4. 패턴

- `(Invention)-[:ELIGIBLE_FOR]->(Right)`
- `(Invention)-[:NOT_ELIGIBLE_FOR]->(Right)`
- `(Actor)-[:CAN_FILE]->(Procedure)`
- `(Procedure)-[:HANDLED_BY]->(Actor)`
- `(Procedure)-[:TARGETS]->(Decision)`
- `(Procedure)-[:HAS_DEADLINE]->(Period)`
- `(Right)-[:HAS_DURATION]->(Period)`
- `(Right)-[:NOT_EFFECTIVE_FOR]->(Exclusion)`
- `(Actor)-[:HOLDS]->(Right)`
- `(Actor)-[:CAN_CLAIM]->(Remedy)`
- `(Remedy)-[:CLAIMED_AGAINST]->(Actor)`
- `(Actor)-[:PUNISHED_BY]->(Penalty)`

## 5. 역량질문별 패턴 경로

| ID | 역량질문 | 패턴 경로 | 설명 |
|---|---|---|---|
| CQ01 | 특허를 받을 수 있는 발명의 요건은 무엇인가? | `Invention -ELIGIBLE_FOR-> Right ; Invention -NOT_ELIGIBLE_FOR-> Right` | 특허를 받을 수 있는 발명 유형과 제외되는 발명 유형을 함께 보면 요건(산업상 이용가능성, 공지·간행물 게재가 아닐 것, 쉽게 발명할 수 없을 것)이 나옴 |
| CQ02 | 특허를 받을 수 없는 발명은 어떤 것인가? | `Invention -NOT_ELIGIBLE_FOR-> Right` | '특허'에 NOT_ELIGIBLE_FOR로 이어진 발명 유형이 답 |
| CQ03 | 특허출원심사의 청구는 누가 할 수 있는가? | `Actor -CAN_FILE-> Procedure` | '출원심사의 청구'에 CAN_FILE로 이어진 주체(누구든지)가 답 |
| CQ04 | 특허거절결정에 불복하려면 누구에게 어떤 심판을 청구하는가? | `Actor -CAN_FILE-> Procedure -TARGETS-> Decision ; Procedure -HANDLED_BY-> Actor` | '특허거절결정'을 TARGETS로 가진 절차가 '어떤 심판', 그 절차의 HANDLED_BY 기관이 '누구에게' |
| CQ05 | 특허출원심사의 청구는 언제까지 할 수 있는가? | `Procedure -HAS_DEADLINE-> Period` | '출원심사의 청구'의 기한이 답 |
| CQ06 | 특허권의 존속기간은 언제부터 언제까지인가? | `Right -HAS_DURATION-> Period` | '특허권'의 존속기간 표현이 답 |
| CQ07 | 특허권을 침해한 자는 어떤 형벌을 받는가? | `Actor -PUNISHED_BY-> Penalty` | '특허권 또는 전용실시권을 침해한 자'의 형벌이 답 |
| CQ08 | 특허권의 효력이 미치지 아니하는 경우는 무엇인가? | `Right -NOT_EFFECTIVE_FOR-> Exclusion` | '특허권'에 이어진 Exclusion이 답 |
| CQ09 | 특허권을 침해당한 특허권자는 어떤 청구를 할 수 있는가? | `Actor -CAN_CLAIM-> Remedy` | '특허권자'가 CAN_CLAIM으로 이어진 구제 수단이 답(상대방은 CLAIMED_AGAINST) |
