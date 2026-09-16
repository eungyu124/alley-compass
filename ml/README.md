# 골목 컴퍼스 — LightGBM 생존 안정성 모델

PRD §7, §14, §15 구현. `alley_compass_etl/`이 만드는 `district_features`
다분기 패널을 입력으로, 상권×업종의 향후 생존 안정성을 예측한다.

## ⚠️ 지금 상태: 배관은 완성, 실제 학습은 대기

`alley_compass_etl.py`로 아직 **1개 분기(20251)**만 받아둔 상태다.
Temporal Split(과거 학습 → 미래 검증, PRD §15)과 Label(§7.1, "향후 N분기
실적")은 둘 다 여러 시점의 데이터가 있어야 의미가 생긴다. 그래서:

- **지금 할 수 있는 것**: `--synthetic`으로 배관 점검. 합성 데이터라 실제
  예측 성능이 아니지만, 코드가 안 죽고 Temporal Split/라벨링/평가지표
  계산까지 정상 작동하는지 확인할 수 있다. `model_versions`에는 저장되지
  않도록 막아 뒀다(`is_synthetic` 체크).
- **실제 학습을 하려면**: 먼저 `alley_compass_etl.py`로 2021Q1 이후
  여러 분기를 모아야 한다.
  ```bash
  cd ../alley_compass_etl
  python alley_compass_etl.py --start-quarter 20211 --end-quarter 20252 \
    --business-name "커피-음료"   # 필요한 업종만 먼저
  ```

## 사용법

```bash
# 1) 배관 점검 (API 키·데이터 불필요)
python train.py --synthetic

# 2) 실제 학습 (다분기 데이터 확보 후)
python train.py --supabase \
  --train-end-quarter 20224 --val-end-quarter 20234   # 2021~22 학습, 2023 검증, 2024 테스트
```

`--train-end-quarter`/`--val-end-quarter`는 `YYYYQ` 형식(예: `20224` =
2022년 4분기)이며, 그 이후 분기는 자동으로 테스트 구간이 된다.

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

`backend/scoring.py`는 아직 이 모델을 안 쓴다 — `MODEL_VERSION =
"heuristic-v0"`로 명시된 휴리스틱 Score를 대신 쓰고 있다. 실제 모델이
나오면 `scoring.rank_districts()`의 `stability_score` 계산 부분을
`ml/models/<version>.joblib`을 불러와 `model.predict_proba()` 결과로
바꾸면 된다 — API 응답 스키마(`RankResponse`)는 그대로 유지된다.

## 저장물

`models/*.joblib`은 git에 커밋하지 않는다(재현 가능한 산출물이자 용량
문제). `--save-to-supabase`를 주면 `model_versions` 테이블에도 메타데이터
(지표·기간·feature importance)를 남긴다 — 합성 데이터 실행은 여기 저장을
거부한다.
