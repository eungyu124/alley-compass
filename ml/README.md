# 골목 컴퍼스 — LightGBM 생존 안정성 모델

PRD §7, §14, §15 구현. `alley_compass_etl/`이 만드는 `district_features`
다분기 패널을 입력으로, 상권×업종의 향후 생존 안정성을 예측한다.

## 지금 상태: 실 데이터로 학습·배포 완료

2021Q1~2025Q2(18개 분기), 10개 업종, 22.5만 행으로 실제 학습했고
`backend/models/`에 승격돼 `/rank`가 쓰고 있다.

학습 과정에서 실측으로 발견한 편향 두 가지를 반영한다:

- **코로나 시기 라벨 오염**: 2021~2022년은 폐업률 중앙값 자체가 0에 가까운
  분기가 많아, Label 정의(`future_closure_rate > median * 1.15`)가 "폐업률이
  조금만 늘어도 불안정"으로 쉽게 뒤집힌다 — 강남역의 `label_unstable`이
  2021~2022엔 불안정, 2023엔 안정으로 바뀌는데 실제 `closure_rate`는
  4~6%로 거의 그대로였던 사례로 확인했다. `--train-start-quarter`로 급성
  코로나 구간을 학습에서 제외한다.
- **소표본(점포 1~2개) 편향**: `ml/train.py` 자체보다는, 그 결과를 쓰는
  `backend/scoring.py`가 후처리로 보정한다 — 점포수가 적어 폐업할 기회
  자체가 없는 상권을 업종별 점포수 중앙값에 비례한 베이지안 축소로 끌어
  내린다. 자세한 내용은 `backend/README.md`의 "모델 버전" 참고.

## 사용법

```bash
# 1) 배관 점검 (API 키·데이터 불필요, 합성 데이터라 model_versions에는 저장 안 함)
python train.py --synthetic

# 2) 실제 학습
python train.py --supabase \
  --train-start-quarter 20223 \
  --train-end-quarter 20233 --val-end-quarter 20234 \
  --save-to-supabase
```

`--train-start-quarter`/`--train-end-quarter`/`--val-end-quarter`는 모두
`YYYYQ` 형식(예: `20224` = 2022년 4분기)이다. `--train-start-quarter`를 주면
그 이전 분기는 학습에서 완전히 제외되고(급성 코로나 구간 배제용), 생략하면
수집된 가장 이른 분기부터 쓴다. `--val-end-quarter` 이후 분기는 자동으로
테스트 구간이 된다.

학습이 끝나면 `models/<version>.joblib`(실험용, gitignore)에 저장된다.
실제로 배포에 반영하려면 **같은 파일을 `backend/models/`에 복사**해야
한다 — "승격" 절차이며, `ml/`을 다시 import하지 않도록 feature 목록을
joblib 아티팩트 안에 함께 저장해 둔다.

## Label 정의 (PRD §7.1, `labels.py`)

PRD가 제시한 4개 후보 중 ④(폐업률·매출 안정성 결합)에 ①(향후 폐업률 증가
여부)의 관점을 더해 채택했다:

```
label_unstable = 1  if  향후 H분기 평균 폐업률 > 같은 시점·업종 상권 중앙값 * 1.15
                     or  향후 H분기 평균 매출성장률 < -5%
               = 0  otherwise
```

라벨은 미래 시점 값만, feature는 그 이전 값만 쓴다(look-ahead 방지). 미래
구간이 없는 마지막 H개 분기는 라벨을 만들지 않고 학습에서 제외한다 —
추정으로 채우지 않는다(루트 README 설계원칙 3).

다른 정의로 바꾸고 싶으면 `labels.py`의 `DEFAULT_CLOSURE_MARGIN` /
`DEFAULT_SALES_DECLINE` / `DEFAULT_HORIZON_QUARTERS`를 조정하거나
`build_labels()`를 교체하면 된다 — `features.py`/`train.py`는 그대로 쓸 수
있다.

## 평가지표 (PRD §15)

`train.py`가 Train/Validation/Test 세 구간에 대해 각각 계산한다.

| 지표 | 의미 |
|---|---|
| ROC-AUC | 안정 상권과 위험 상권을 얼마나 잘 구분하는지 |
| PR-AUC | 위험(양성) 클래스가 적을 때(Class Imbalance) 성능 |
| Brier Score | 예측 확률과 실제 결과의 오차 |
| Calibration Error | "70% 위험"이라 한 집단의 실제 위험 발생률이 70%에 가까운지 |
| Top-K Lift | 모델이 추천한 상위 K(기본 5%)의 실제 안정 비율이 전체 평균보다 높은지 |

## 백엔드 연결

`backend/scoring.py`가 이 모델을 쓴다. `_load_lightgbm()`이 `backend/models/`
아래 가장 최신 `*.joblib`을 불러와 캐싱하고, `_lightgbm_stability()`가
`model.predict_proba()` 결과를 `stability_score`로 바꾼다. 모델 파일이 없거나
로드/예측에 실패하면 휴리스틱 Score로 조용히 대체하고 `model_version`에
어느 쪽을 썼는지 그대로 남긴다 — API 응답 스키마(`RankResponse`)는 그대로
유지된다.

## 저장물

`models/*.joblib`은 git에 커밋하지 않는다(재현 가능한 산출물이자 용량
문제) — 이건 실험용 산출 경로다. `--save-to-supabase`를 주면 `model_versions`
테이블에도 메타데이터(지표·기간·feature importance)를 남긴다(합성 데이터
실행은 여기 저장을 거부한다). 실제로 배포에 태우려면 저장된 파일을
**`backend/models/`에 같은 버전 문자열로 복사**해야 한다 — 이 디렉터리는
커밋 대상이며 Docker 이미지 빌드에 포함된다.
