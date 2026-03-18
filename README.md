# Grattol Cosmetics Data Project

이 레포지토리는 Grattol 네일 시장 프로젝트의 최종 노트북과 전처리 코드를 GitHub용으로 정리한 구조입니다.

## Included Components

- `src/preprocessing/preprocess.py`: 월별 CSV 병합, 정제, 분석용 데이터 생성
- `notebooks/eda/14조_eda.ipynb`: 시장 구조, 경쟁사 비교, 퍼널 분석
- `notebooks/eda/14조_eda2.ipynb`: 시계열, 요일/시간대, 고객 행동 인사이트
- `notebooks/ml/14조_ML.ipynb`: RFM + KMeans 기반 세그먼트 분석
- `docs/reports/`: 통합 보고서

## Repository Layout

```text
src/
  preprocessing/
notebooks/
  eda/
  ml/
docs/
  reports/
  project/
data/
```

## Data Policy

원천 데이터와 대용량 산출물은 GitHub에 포함하지 않습니다.
실행 코드와 최종 노트북 중심으로만 공개합니다.
