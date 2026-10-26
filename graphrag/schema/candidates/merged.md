# 스키마 후보 합치기 결과

- 표본 수: 20건(`SchemaFromTextExtractor` 호출별 원본 응답 기준)
- 등장 수: 그 후보가 나온 표본 청크 수. 속성 옆 괄호도 같은 뜻
- 노드 타입(node type): 그래프의 점(개체) 종류. 관계 타입(relationship type): 점을 잇는 선의 종류
- 패턴(pattern): `시작 노드 타입 -관계 타입-> 끝 노드 타입` 3개 묶음. 어떤 관계가 어떤 노드 사이에 오는지 정함
- 이 단계에서는 등장 수가 적은 후보도 지우지 않고, 뜻이 같아 보이는 후보도 합치지 않고 표시만 함

## 1. 노드 타입 후보 (68개)

| 노드 타입 | 등장 수 | 속성(등장 수) | 등장한 표본 번호 |
|---|---|---|---|
| Patent | 9 | patent_number:STRING(4), status:STRING(1), title:STRING(1) | 7, 62, 64, 67, 79, 102, 110, 147, 150 |
| Decision | 7 | decision_type:STRING(3), type:STRING(1), reason:STRING(1), decision_date:DATE(1), date:DATE(1) | 10, 20, 48, 140, 145, 147, 150 |
| Party | 6 | name:STRING(3), role:STRING(1) | 10, 62, 145, 147, 150, 184 |
| PatentApplication | 6 | application_number:STRING(5), filing_date:DATE(2), submission_date:DATE(1), fee_paid:BOOLEAN(1), fee_amount:FLOAT(1), title:STRING(1), priority_withdraw_deadline:DATE(1) | 7, 20, 28, 48, 64, 67 |
| Person | 6 | name:STRING(5), role:STRING(1), address:STRING(1), identifier:STRING(1) | 7, 28, 64, 67, 140, 187 |
| Invention | 4 | title:STRING(3) | 20, 28, 102, 172 |
| Applicant | 3 | name:STRING(3), address:STRING(1) | 48, 79, 172 |
| Organization | 3 | name:STRING(3), location:STRING(1) | 28, 67, 187 |
| Procedure | 3 | name:STRING(3) | 6, 7, 110 |
| Article | 2 | code:STRING(1), number:STRING(1) | 173, 187 |
| Document | 2 | title:STRING(1), name:STRING(1) | 67, 140 |
| Fee | 2 | amount:FLOAT(2), fee_type:STRING(1) | 7, 67 |
| InternationalPatentApplication | 2 | application_number:STRING(2), filing_date:DATE(1) | 172, 173 |
| Judge | 2 | name:STRING(1) | 10, 147 |
| LegalProvision | 2 | code:STRING(1), reference:STRING(1), article_number:STRING(1) | 20, 110 |
| Official | 2 | name:STRING(1), title:STRING(1) | 6, 10 |
| PatentRight | 2 | registration_number:STRING(1), registration_date:DATE(1) | 102, 172 |
| Period | 2 | start_date:DATE(1), end_date:DATE(1), name:STRING(1), max_days:INTEGER(1) | 6, 10 |
| Agent | 1 | name:STRING(1), address:STRING(1) | 79 |
| Amendment | 1 | description:STRING(1) | 173 |
| Appeal | 1 | type:STRING(1) | 184 |
| Application | 1 | - | 184 |
| Authority | 1 | name:STRING(1) | 7 |
| BusinessEntity | 1 | - | 102 |
| BusinessOperator | 1 | name:STRING(1) | 172 |
| CivilProcedureCostLaw | 1 | name:STRING(1) | 145 |
| CivilProcedureLaw | 1 | name:STRING(1) | 145 |
| Claim | 1 | number:INTEGER(1), text:STRING(1) | 28 |
| ConfidentialityOrder | 1 | - | 184 |
| CorrectionRequest | 1 | type:STRING(1) | 110 |
| Cost | 1 | - | 145 |
| Court | 1 | name:STRING(1) | 184 |
| DecisionDocument | 1 | date:DATE(1) | 184 |
| Decree | 1 | name:STRING(1) | 67 |
| Director | 1 | name:STRING(1) | 48 |
| Evidence | 1 | type:STRING(1) | 140 |
| Examiner | 1 | name:STRING(1), qualification:STRING(1) | 48 |
| ExclusiveLicense | 1 | - | 102 |
| ExtensionApplication | 1 | extension_period:DURATION(1) | 79 |
| GovernmentAgency | 1 | name:STRING(1) | 20 |
| InfringingAct | 1 | action:STRING(1) | 102 |
| Judgment | 1 | - | 145 |
| Law | 1 | name:STRING(1) | 67 |
| LegalArticle | 1 | article_id:STRING(1), title:STRING(1) | 64 |
| License | 1 | license_type:STRING(1) | 150 |
| Method | 1 | - | 102 |
| Offense | 1 | name:STRING(1) | 187 |
| PatentAttorney | 1 | name:STRING(1) | 145 |
| PatentOffice | 1 | name:STRING(1) | 79 |
| PatentRightHolder | 1 | name:STRING(1) | 110 |
| PatentTrialBoard | 1 | name:STRING(1) | 145 |
| Penalty | 1 | max_imprisonment_years:INTEGER(1), max_fine_amount:FLOAT(1) | 187 |
| PriorApplication | 1 | application_number:STRING(1), filing_date:DATE(1), status:STRING(1) | 48 |
| Product | 1 | - | 102 |
| Reason | 1 | description:STRING(1) | 79 |
| Reexamination | 1 | - | 150 |
| ReexaminationRequest | 1 | - | 147 |
| Region | 1 | name:STRING(1) | 6 |
| ReinstatementRequest | 1 | date:DATE(1), status:STRING(1) | 10 |
| Requirement | 1 | type:STRING(1), description:STRING(1) | 173 |
| RightHolder | 1 | name:STRING(1) | 20 |
| Role | 1 | name:STRING(1) | 187 |
| Specification | 1 | description:STRING(1) | 7 |
| TechnicalField | 1 | name:STRING(1) | 28 |
| TimePeriod | 1 | description:STRING(1) | 110 |
| TradeSecret | 1 | description:STRING(1) | 184 |
| Trial | 1 | - | 147 |
| Tribunal | 1 | name:STRING(1) | 140 |

## 2. 관계 타입 후보 (92개)

| 관계 타입 | 등장 수 | 속성(등장 수) | 등장한 표본 번호 |
|---|---|---|---|
| APPLIES_TO | 4 | - | 6, 10, 79, 147 |
| DECIDES | 4 | - | 10, 140, 150, 184 |
| FILED_BY | 4 | - | 48, 64, 67, 147 |
| SUBMITS | 4 | - | 10, 67, 140, 184 |
| GOVERNED_BY | 3 | - | 6, 20, 64 |
| RELATES_TO | 3 | - | 20, 64, 150 |
| SUBMITTED_TO | 3 | - | 20, 28, 79 |
| TARGETS | 3 | - | 7, 147, 184 |
| FILED | 2 | - | 79, 172 |
| GRANTED_TO | 2 | - | 67, 150 |
| HAS_INVENTION | 2 | - | 102, 172 |
| HAS_RIGHT | 2 | - | 20, 102 |
| INCLUDES | 2 | - | 7, 110 |
| MADE_BY | 2 | - | 110, 145 |
| NOTIFIES | 2 | - | 10, 140 |
| OVERSEES | 2 | - | 48, 147 |
| RECEIVES | 2 | - | 10, 184 |
| REPRESENTS | 2 | - | 28, 79 |
| RESULTS_IN | 2 | - | 147, 150 |
| ABANDON_APPLICATION | 1 | - | 62 |
| ACT_TARGETS_LICENSE | 1 | - | 102 |
| ACT_TARGETS_RIGHT | 1 | - | 102 |
| ACT_USES_PRODUCT | 1 | - | 102 |
| ALLOWED_FOR | 1 | - | 173 |
| ALLOWS | 1 | - | 110 |
| APPLIES_FOR | 1 | - | 28 |
| APPLIES_LAW | 1 | - | 145 |
| ASSIGNS | 1 | - | 140 |
| ASSOCIATED_WITH | 1 | - | 67 |
| BELONGS_TO | 1 | - | 28 |
| CHANGES_DEADLINE | 1 | - | 6 |
| CLAIM_COMPENSATION | 1 | - | 172 |
| CLAIM_REIMBURSEMENT | 1 | - | 62 |
| COMMITTED_BY | 1 | - | 187 |
| CONTAINS_INVENTION | 1 | - | 28 |
| CREATES | 1 | - | 184 |
| DECIDED_BY | 1 | - | 20 |
| DEFINED_BY | 1 | - | 67 |
| DEFINED_IN | 1 | - | 173 |
| DELIVERS_TO | 1 | - | 184 |
| DETERMINED_BY_DECISION | 1 | - | 145 |
| DETERMINED_BY_JUDGMENT | 1 | - | 145 |
| EXAMINED_BY | 1 | - | 48 |
| EXEMPTED_BY | 1 | - | 67 |
| EXEMPTED_FOR | 1 | - | 173 |
| EXTENDS | 1 | - | 6 |
| FEE_TREATED_AS_COST | 1 | - | 145 |
| GRANTED_FROM | 1 | - | 172 |
| HAS | 1 | - | 110 |
| HAS_APPEAL | 1 | - | 184 |
| HAS_CLAIM | 1 | - | 28 |
| HAS_EXCLUSIVE_LICENSE | 1 | - | 102 |
| HAS_FEE | 1 | - | 7 |
| HAS_REASON | 1 | - | 79 |
| HAS_REQUIREMENT | 1 | - | 173 |
| HAS_SUCCESSOR | 1 | - | 7 |
| HOLDER_OF | 1 | - | 64 |
| INITIATED_BY | 1 | - | 150 |
| INVENTS | 1 | - | 28 |
| INVESTIGATES | 1 | - | 140 |
| ISSUES | 1 | - | 7 |
| ISSUES_WARNING | 1 | - | 172 |
| IS_METHOD_INVENTION | 1 | - | 102 |
| IS_PRODUCT_INVENTION | 1 | - | 102 |
| LEGAL_BASIS | 1 | - | 67 |
| MAKES_DECISION | 1 | - | 48 |
| OFFENSE_DEFINED_IN | 1 | - | 187 |
| OFFENSE_HAS_PENALTY | 1 | - | 187 |
| OWNS | 1 | - | 110 |
| PAID_BY | 1 | - | 145 |
| PAID_TO | 1 | - | 145 |
| PARTICIPATES_IN | 1 | - | 147 |
| PAY_ADDITIONAL_FEES | 1 | - | 62 |
| PAY_COMPENSATION | 1 | - | 150 |
| PAY_FEE | 1 | - | 64 |
| PAY_FEES | 1 | - | 62 |
| PERFORMS_ACT | 1 | - | 102 |
| PERSON_AFFILIATED_WITH | 1 | - | 187 |
| PERSON_HAS_ROLE | 1 | - | 187 |
| PRACTICES | 1 | - | 172 |
| PRIORITY_BASED_ON | 1 | - | 48 |
| RECEIVED_BY | 1 | - | 7 |
| REFERS_TO | 1 | - | 110 |
| REQUESTS | 1 | - | 140 |
| REQUIRED_FOR | 1 | - | 67 |
| RESULTS_FROM | 1 | - | 20 |
| SENDS | 1 | - | 184 |
| SETS | 1 | - | 10 |
| SHORTENS | 1 | - | 6 |
| SUBMITTED_BY | 1 | - | 20 |
| UNDERGOES | 1 | - | 110 |
| WITHDRAWS_PRIORITY | 1 | - | 48 |

## 3. 패턴 후보 (131개)

| 시작 | 관계 | 끝 | 등장 수 | 등장한 표본 번호 |
|---|---|---|---|---|
| PatentApplication | FILED_BY | Person | 2 | 64, 67 |
| Person | SUBMITS | Document | 2 | 67, 140 |
| Agent | REPRESENTS | Applicant | 1 | 79 |
| Amendment | ALLOWED_FOR | InternationalPatentApplication | 1 | 173 |
| Amendment | DEFINED_IN | Article | 1 | 173 |
| Applicant | CLAIM_COMPENSATION | BusinessOperator | 1 | 172 |
| Applicant | FILED | InternationalPatentApplication | 1 | 172 |
| Applicant | FILED | ExtensionApplication | 1 | 79 |
| Applicant | ISSUES_WARNING | BusinessOperator | 1 | 172 |
| Applicant | WITHDRAWS_PRIORITY | PatentApplication | 1 | 48 |
| Application | CREATES | ConfidentialityOrder | 1 | 184 |
| Authority | ISSUES | Procedure | 1 | 7 |
| BusinessEntity | PERFORMS_ACT | InfringingAct | 1 | 102 |
| BusinessOperator | PRACTICES | Invention | 1 | 172 |
| ConfidentialityOrder | HAS_APPEAL | Appeal | 1 | 184 |
| ConfidentialityOrder | TARGETS | TradeSecret | 1 | 184 |
| CorrectionRequest | HAS | TimePeriod | 1 | 110 |
| CorrectionRequest | MADE_BY | PatentRightHolder | 1 | 110 |
| CorrectionRequest | REFERS_TO | LegalProvision | 1 | 110 |
| Cost | DETERMINED_BY_DECISION | Decision | 1 | 145 |
| Cost | DETERMINED_BY_JUDGMENT | Judgment | 1 | 145 |
| Cost | PAID_BY | Party | 1 | 145 |
| Cost | PAID_TO | PatentTrialBoard | 1 | 145 |
| Court | DECIDES | ConfidentialityOrder | 1 | 184 |
| Court | SENDS | DecisionDocument | 1 | 184 |
| Decision | APPLIES_TO | ReinstatementRequest | 1 | 10 |
| Decision | APPLIES_TO | Patent | 1 | 147 |
| Decision | DECIDED_BY | GovernmentAgency | 1 | 20 |
| Decision | DECIDES | Patent | 1 | 150 |
| Decision | MADE_BY | PatentTrialBoard | 1 | 145 |
| Decision | RESULTS_FROM | PatentApplication | 1 | 20 |
| DecisionDocument | DELIVERS_TO | Party | 1 | 184 |
| Decree | DEFINED_BY | Law | 1 | 67 |
| Director | OVERSEES | Examiner | 1 | 48 |
| Document | REQUIRED_FOR | Fee | 1 | 67 |
| Examiner | MAKES_DECISION | Decision | 1 | 48 |
| ExtensionApplication | APPLIES_TO | Patent | 1 | 79 |
| ExtensionApplication | HAS_REASON | Reason | 1 | 79 |
| ExtensionApplication | SUBMITTED_TO | PatentOffice | 1 | 79 |
| Fee | ASSOCIATED_WITH | PatentApplication | 1 | 67 |
| Fee | ASSOCIATED_WITH | Patent | 1 | 67 |
| Fee | EXEMPTED_BY | Organization | 1 | 67 |
| InfringingAct | ACT_TARGETS_LICENSE | ExclusiveLicense | 1 | 102 |
| InfringingAct | ACT_TARGETS_RIGHT | PatentRight | 1 | 102 |
| InfringingAct | ACT_USES_PRODUCT | Product | 1 | 102 |
| InternationalPatentApplication | HAS_INVENTION | Invention | 1 | 172 |
| InternationalPatentApplication | HAS_REQUIREMENT | Requirement | 1 | 173 |
| Invention | BELONGS_TO | TechnicalField | 1 | 28 |
| Invention | IS_METHOD_INVENTION | Method | 1 | 102 |
| Invention | IS_PRODUCT_INVENTION | Product | 1 | 102 |
| Invention | RELATES_TO | Decision | 1 | 20 |
| Judge | DECIDES | Decision | 1 | 10 |
| Judge | NOTIFIES | Party | 1 | 10 |
| Judge | OVERSEES | Trial | 1 | 147 |
| Judge | RECEIVES | ReinstatementRequest | 1 | 10 |
| Judge | SETS | Period | 1 | 10 |
| Judgment | MADE_BY | PatentTrialBoard | 1 | 145 |
| License | GRANTED_TO | Party | 1 | 150 |
| License | RELATES_TO | Patent | 1 | 150 |
| Offense | COMMITTED_BY | Person | 1 | 187 |
| Offense | OFFENSE_DEFINED_IN | Article | 1 | 187 |
| Offense | OFFENSE_HAS_PENALTY | Penalty | 1 | 187 |
| Official | APPLIES_TO | Region | 1 | 6 |
| Official | CHANGES_DEADLINE | Period | 1 | 6 |
| Official | DECIDES | Decision | 1 | 10 |
| Official | EXTENDS | Period | 1 | 6 |
| Official | NOTIFIES | Party | 1 | 10 |
| Official | RECEIVES | ReinstatementRequest | 1 | 10 |
| Official | SETS | Period | 1 | 10 |
| Official | SHORTENS | Period | 1 | 6 |
| Organization | LEGAL_BASIS | Decree | 1 | 67 |
| Party | ABANDON_APPLICATION | Patent | 1 | 62 |
| Party | CLAIM_REIMBURSEMENT | Party | 1 | 62 |
| Party | PARTICIPATES_IN | Trial | 1 | 147 |
| Party | PAY_ADDITIONAL_FEES | Patent | 1 | 62 |
| Party | PAY_COMPENSATION | Patent | 1 | 150 |
| Party | PAY_FEES | Patent | 1 | 62 |
| Party | RECEIVES | ConfidentialityOrder | 1 | 184 |
| Party | SUBMITS | ReinstatementRequest | 1 | 10 |
| Party | SUBMITS | Application | 1 | 184 |
| Patent | GOVERNED_BY | LegalArticle | 1 | 64 |
| Patent | GRANTED_TO | Person | 1 | 67 |
| Patent | HAS_EXCLUSIVE_LICENSE | ExclusiveLicense | 1 | 102 |
| Patent | HAS_INVENTION | Invention | 1 | 102 |
| Patent | HAS_RIGHT | PatentRight | 1 | 102 |
| Patent | HAS_SUCCESSOR | Person | 1 | 7 |
| Patent | UNDERGOES | Procedure | 1 | 110 |
| PatentApplication | CONTAINS_INVENTION | Invention | 1 | 28 |
| PatentApplication | EXAMINED_BY | Examiner | 1 | 48 |
| PatentApplication | FILED_BY | Applicant | 1 | 48 |
| PatentApplication | GOVERNED_BY | LegalProvision | 1 | 20 |
| PatentApplication | HAS_CLAIM | Claim | 1 | 28 |
| PatentApplication | HAS_FEE | Fee | 1 | 7 |
| PatentApplication | INCLUDES | Specification | 1 | 7 |
| PatentApplication | PRIORITY_BASED_ON | PriorApplication | 1 | 48 |
| PatentApplication | RELATES_TO | Patent | 1 | 64 |
| PatentApplication | SUBMITTED_BY | RightHolder | 1 | 20 |
| PatentApplication | SUBMITTED_TO | GovernmentAgency | 1 | 20 |
| PatentApplication | SUBMITTED_TO | Organization | 1 | 28 |
| PatentAttorney | FEE_TREATED_AS_COST | Cost | 1 | 145 |
| PatentRight | GRANTED_FROM | InternationalPatentApplication | 1 | 172 |
| PatentRightHolder | OWNS | Patent | 1 | 110 |
| PatentTrialBoard | APPLIES_LAW | CivilProcedureLaw | 1 | 145 |
| PatentTrialBoard | APPLIES_LAW | CivilProcedureCostLaw | 1 | 145 |
| Period | APPLIES_TO | ReinstatementRequest | 1 | 10 |
| Person | APPLIES_FOR | PatentApplication | 1 | 28 |
| Person | HOLDER_OF | Patent | 1 | 64 |
| Person | INVENTS | Invention | 1 | 28 |
| Person | PAY_FEE | Patent | 1 | 64 |
| Person | PERSON_AFFILIATED_WITH | Organization | 1 | 187 |
| Person | PERSON_HAS_ROLE | Role | 1 | 187 |
| Person | REPRESENTS | PatentApplication | 1 | 28 |
| Person | REQUESTS | Evidence | 1 | 140 |
| Procedure | ALLOWS | CorrectionRequest | 1 | 110 |
| Procedure | GOVERNED_BY | Period | 1 | 6 |
| Procedure | INCLUDES | LegalProvision | 1 | 110 |
| Procedure | RECEIVED_BY | Person | 1 | 7 |
| Procedure | TARGETS | PatentApplication | 1 | 7 |
| Procedure | TARGETS | Patent | 1 | 7 |
| Reexamination | INITIATED_BY | Party | 1 | 150 |
| Reexamination | RESULTS_IN | Decision | 1 | 150 |
| ReexaminationRequest | FILED_BY | Party | 1 | 147 |
| ReexaminationRequest | TARGETS | Decision | 1 | 147 |
| Requirement | DEFINED_IN | Article | 1 | 173 |
| Requirement | EXEMPTED_FOR | InternationalPatentApplication | 1 | 173 |
| RightHolder | HAS_RIGHT | Invention | 1 | 20 |
| Trial | RESULTS_IN | Decision | 1 | 147 |
| Tribunal | ASSIGNS | Person | 1 | 140 |
| Tribunal | DECIDES | Decision | 1 | 140 |
| Tribunal | INVESTIGATES | Evidence | 1 | 140 |
| Tribunal | NOTIFIES | Person | 1 | 140 |

## 4. 합칠 후보(표시만 함)

### 4.1 노드 타입

| 묶음 | 이름 | 이유 |
|---|---|---|
| N1 | PatentApplication, InternationalPatentApplication | 두 타입 모두 특허 출원 절차를 나타내며 의미가 동일합니다. |
| N2 | Application, ExtensionApplication | Application은 일반적인 출원을 의미하고 ExtensionApplication은 출원의 연장을 의미하지만 둘 다 출원 행위와 관련됩니다. |
| N3 | Decision, Judgment | 두 노드 모두 심판·판결 결과를 나타내는 의미가 같습니다. |
| N4 | Article, LegalArticle | 둘 다 문서 형태의 조문·조항을 의미합니다. |
| N5 | Fee, Cost | 두 용어는 모두 금전적 비용을 나타냅니다. |
| N6 | PatentRight, ExclusiveLicense, License | 특허권·라이선스와 같이 권리 부여와 관련된 개념이므로 묶을 수 있습니다. |
| N7 | RightHolder, PatentRightHolder | 권리를 보유한 주체를 나타내는 노드이므로 동일합니다. |
| N8 | Period, TimePeriod | 시간 구간을 나타내는 의미가 같습니다. |
| N9 | Authority, GovernmentAgency, PatentOffice | 공공기관·기관을 의미하는 노드이므로 같은 범주에 속합니다. |
| N10 | Invention, Method | 발명과 그 구현 방법을 나타내는 개념으로 서로 밀접합니다. |
| N11 | Offense, InfringingAct | 불법 행위라는 의미가 동일합니다. |
| N12 | BusinessEntity, BusinessOperator | 사업 주체를 나타내는 노드이므로 같은 의미입니다. |
| N13 | Tribunal, Court | 법원을 의미하는 노드로 동일합니다. |
| N14 | Amendment, CorrectionRequest | 문서·출원의 수정·정정을 의미합니다. |
| N15 | Reexamination, ReexaminationRequest | 재심사와 그 요청을 나타내는 개념이 동일합니다. |
| N16 | Procedure, Trial | 법적 절차를 나타내는 노드이므로 같은 범주에 포함됩니다. |
| N17 | LegalProvision, Law | 법령·조항을 의미하는 노드로 의미가 같습니다. |

### 4.2 관계 타입

| 묶음 | 이름 | 이유 |
|---|---|---|
| R1 | FILED, FILED_BY, SUBMITTED_BY, SUBMITS | 출원·제출 행위와 그 주체를 나타내는 관계이므로 묶었습니다. |
| R2 | APPLIES_TO, APPLIES_FOR, APPLIES_LAW | ‘적용’ 의미를 가진 관계들입니다. |
| R3 | GRANTED_TO, GRANTED_FROM | 특허·권리 부여의 방향을 나타내는 관계이므로 동일합니다. |
| R4 | HAS, HAS_INVENTION, HAS_RIGHT, HAS_CLAIM, HAS_EXCLUSIVE_LICENSE, HAS_FEE, HAS_REASON, HAS_REQUIREMENT, HAS_SUCCESSOR | ‘가지다’ 의미의 관계들로 모두 같은 의미 범주에 속합니다. |
| R5 | DECIDES, MAKES_DECISION, DECIDED_BY | 판단·결정 행위를 나타내는 관계입니다. |
| R6 | RELATES_TO, ASSOCIATED_WITH, BELONGS_TO | 대상 간의 연관성을 나타내는 관계이므로 묶었습니다. |
| R7 | RESULTS_IN, RESULTS_FROM | ‘결과’를 나타내는 관계입니다. |
| R8 | PAY_FEE, PAY_FEES, PAY_COMPENSATION, PAY_ADDITIONAL_FEES | ‘지불’ 의미의 관계들입니다. |
| R9 | PAID_BY, PAID_TO | ‘지불 주체·대상’ 관계를 나타냅니다. |
| R10 | PERSON_AFFILIATED_WITH, PERSON_HAS_ROLE | 인물과 역할·소속 관계를 나타냅니다. |
| R11 | OFFENSE_DEFINED_IN, OFFENSE_HAS_PENALTY | 범죄와 그 법적 근거·벌칙을 연결하는 관계입니다. |
| R12 | ACT_TARGETS_LICENSE, ACT_TARGETS_RIGHT, ACT_USES_PRODUCT | ‘행위가 대상·목적을 지정’하는 관계들입니다. |
| R13 | ALLOWED_FOR, ALLOWS | ‘허용’ 의미를 가진 관계입니다. |
| R14 | CLAIM_COMPENSATION, CLAIM_REIMBURSEMENT | 보상 청구와 관련된 관계이므로 묶었습니다. |
| R15 | CONTAINS_INVENTION, IS_METHOD_INVENTION, IS_PRODUCT_INVENTION | ‘발명·방법·제품과의 포함 관계’를 나타냅니다. |
| R16 | CHANGES_DEADLINE, SHORTENS, SETS | 기한·조건을 변경·설정하는 관계입니다. |
| R17 | RECEIVES, RECEIVED_BY | ‘수령’ 의미의 관계입니다. |
| R18 | NOTIFIES, SENDS | ‘통보·전송’ 의미의 관계입니다. |
