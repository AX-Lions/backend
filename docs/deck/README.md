# 발표덱 생성기

`../../Bordo_발표덱.pptx` 를 만드는 스크립트입니다. PowerPoint 네이티브 도형만
쓰므로 만들어진 파일은 그대로 손으로 고칠 수 있습니다 — **급하면 스크립트를 건너뛰고
pptx 를 직접 고치십시오.** 여기 있는 코드는 전체를 다시 뽑아야 할 때만 씁니다.

```bash
python docs/deck/build_deck.py "Bordo_발표덱.pptx"     # python-pptx 필요
```

| 파일 | 내용 |
|---|---|
| `deck_base.py` | 팔레트 · 도형/텍스트 헬퍼 · 픽토그램 · 슬라이드 뼈대 |
| `deck_p1.py` | 표지 · 목차 · 문제 정의 (1–8) |
| `deck_p2.py` | 해결 방식 (9–15) |
| `deck_p3.py` | 동작 구조 · 시장 · 실행 (16–24) |
| `build_deck.py` | 슬라이드 순서 · 발표자 노트 |

## 발표 PC 에 글꼴이 없을 때

`deck_base.py` 의 `FONT` 한 줄만 바꾸고 다시 뽑으면 됩니다.
현재 값은 `Noto Sans KR` 이고, 어느 Windows 에나 있는 값은 `맑은 고딕` 입니다.
이미 만든 pptx 라면 PowerPoint 의 **홈 › 바꾸기 › 글꼴 바꾸기** 로도 됩니다.

## 도형 각도를 다룰 때 주의

python-pptx 는 `adjustments` 를 100000 배로 쓰는데 PowerPoint 는 60000 배로 읽습니다.
파이 게이지(`deck_p1.ring`)에서 `0.6` 을 곱하는 이유가 이것이고, 빼면 27% 게이지가
꽉 찬 원으로 그려집니다.
