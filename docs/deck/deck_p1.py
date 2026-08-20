# -*- coding: utf-8 -*-
"""표지 · 목차 · 문제 정의 (1–8)."""
from deck_base import *          # noqa: F401,F403
from deck_base import (W, H, ML, MR, CW, INK, INK2, MUTE, FAINT, LINE, WHITE,
                       SURF, SURF2, BLUE, BLUE_L, BLUE_XL, MINT, MINT_L,
                       MINT_XL, LAV, LAV_L, LAV_XL, ROSE, ROSE_L, ROSE_XL,
                       SLATE, SLATE_L, SLATE_XL, C, T, TR, fit, card, box,
                       oval, pill, shape, hline, vline, arrow, frame, blank,
                       source, placeholder, ic_clock, ic_person, ic_doc,
                       ic_bubble, ic_badge, toggle, PAGE)
from pptx.enum.shapes import MSO_SHAPE


#: python-pptx 는 각도 조절값을 100000 배로 씁니다. PowerPoint 는 60000 배로 읽으므로
#: 0.6 을 곱해 두지 않으면 27% 게이지가 꽉 찬 원으로 그려집니다.
_ANG = 0.6


def ring(sl, cx, cy, d, pct, color, track):
    """도넛 게이지 — 12시에서 시작해 시계 방향으로 pct 만큼."""
    x, y = cx - d / 2, cy - d / 2
    oval(sl, x, y, d, d, fill=track)
    arc = shape(sl, MSO_SHAPE.PIE, x, y, d, d, fill=color)
    arc.adjustments[0] = 270.0 * _ANG
    arc.adjustments[1] = ((270.0 + 360.0 * pct) % 360.0) * _ANG
    oval(sl, x + d * 0.155, y + d * 0.155, d * 0.69, d * 0.69, fill=WHITE)
    return arc


# ─────────────────────────────────────────────────────────────── 01 표지
def s01(prs):
    sl = blank(prs)
    PAGE["n"] += 1

    # 배경 — 옅은 파스텔 면 두 장으로 깊이만 만들고 장식은 여기서 끝냅니다
    oval(sl, 8.05, 1.28, 4.55, 4.55, fill=BLUE_XL)
    oval(sl, -1.0, 5.4, 3.2, 3.2, fill=MINT_XL)
    box(sl, 0, 0, 0.085, H, fill=BLUE)

    T(sl, ML, 0.72, 6.0, 0.26, "AX LIONS 2026   ·   BORDERLESS TRACK",
      10, BLUE, True, "l", "t", spc=22)

    T(sl, ML, 1.86, 5.0, 1.1, "Bordo", 62, INK, True, "l", "t", spc=-20)
    hline(sl, ML, 3.02, 1.5, INK, 2.2)
    T(sl, ML, 3.28, 6.4, 1.1,
      "회의에 못 들어간 팀원의 자리를\n대신 채우는 AI 대리인",
      21, INK, True, "l", "t", lh=1.42)
    T(sl, ML, 4.62, 6.5, 0.8,
      "시차로 빠진 사람의 의견을 회의 안으로.\n오간 말은 그 사람의 직무와 언어로.",
      12, INK2, False, "l", "t", lh=1.6)

    hline(sl, ML, 5.92, 5.9, LINE)
    TR(sl, ML, 6.12, 6.4, 0.3,
       [("국민대학교", 10.5, INK2, True), ("      ", 10.5, MUTE, False),
        ("유수인 · 임수연 · 서재민 · 최비성 · 강다은", 10.5, MUTE, False)])

    # 세 시간대가 서로 비껴 있다는 것만 보여 주는 표지 도식
    cx, cy = 10.33, 3.55
    bars = [("한국", 8.62, 2.30, BLUE), ("베트남", 9.02, 2.30, MINT),
            ("미국", 10.28, 1.72, LAV)]
    for i, (nm, x, w, col) in enumerate(bars):
        y = cy - 0.86 + i * 0.72
        p = pill(sl, x, y, w, 0.40, fill=col)
        fit(sl, p, nm, size=10.5, color=WHITE, bold=True)
    vline(sl, 10.30, 1.95, 3.25, color=INK, lw=1.2, dash=True)
    T(sl, 8.05, 5.30, 4.55, 0.5,
      "세 사람이 동시에 일하는 시간\n하루 0시간", 11, INK, True, "c", "t", lh=1.45)

    T(sl, W - MR - 3.0, H - 0.52, 3.0, 0.24, "BORDER TO ZERO", 9, FAINT,
      True, "r", "t", spc=30)
    return sl


# ─────────────────────────────────────────────────────────────── 02 목차
def s02(prs):
    sl, y0 = frame(prs, "CONTENTS", "네 개의 이야기로 나눠 말씀드리겠습니다",
                   accent=SLATE)
    items = [
        ("01", "문제", "시차는 같이 일할\n시간 자체를 없앱니다",
         "겹치는 시간 · 누적되는 대기 · 기존 도구가 닿지 않는 구간", SLATE),
        ("02", "해결", "부재한 자리를\n대리인이 채웁니다",
         "회의 전 준비 · 회의 중 대리와 유보 · 개인화 브리핑 · 대리인 간 연결", BLUE),
        ("03", "구조", "권한과 유보를\n코드가 지킵니다",
         "3대 설계 원칙 · 진입점 네 개 · Outbox · MCP 로 쌓이는 근거", LAV),
        ("04", "시장", "팀 단위로 작동하고\n팀 단위로 팝니다",
         "타깃 · TAM/SAM/SOM · 수익 모델 · 로드맵과 팀", MINT),
    ]
    w = (CW - 3 * 0.30) / 4
    for i, (num, kicker, title, desc, col) in enumerate(items):
        x = ML + i * (w + 0.30)
        card(sl, x, y0 + 0.10, w, 3.62)
        box(sl, x, y0 + 0.10, w, 0.055, fill=col)
        T(sl, x + 0.34, y0 + 0.56, w - 0.68, 0.5, num, 30, col, True, "l", "t",
          spc=-10)
        T(sl, x + 0.34, y0 + 1.16, w - 0.68, 0.26, kicker, 10, col, True, "l",
          "t", spc=16)
        T(sl, x + 0.34, y0 + 1.50, w - 0.68, 1.0, title, 15, INK, True, "l",
          "t", lh=1.42)
        hline(sl, x + 0.34, y0 + 2.62, w - 0.68, LINE)
        T(sl, x + 0.34, y0 + 2.82, w - 0.68, 1.0, desc, 10, MUTE, False, "l",
          "t", lh=1.55)
    return sl


# ─────────────────────────────────────────── 03 네 개의 경계
def s03(prs):
    sl, y0 = frame(prs, "PROBLEM 01",
                   "글로벌 협업의 네 경계 중 시차만 성격이 다릅니다",
                   "언어·문화·조직은 같이 일하는 과정의 어려움. 시차는 같이 일할 시간 자체를 없앱니다.",
                   accent=ROSE)
    cw = (CW - 0.30) / 2
    ch = 1.42
    data = [
        ("지리", "시차", "다른 나라, 다른 시간대.\n같이 있을 시간 자체가 없습니다.", True),
        ("언어", "", "같은 말도 서로 다르게 읽힙니다.", False),
        ("문화", "", "일하는 방식과 말하는 방식이 나라마다 다릅니다.", False),
        ("조직", "", "소속이 다르면 정보가 그대로 넘어가지 않습니다.", False),
    ]
    for i, (name, tag, desc, hot) in enumerate(data):
        x = ML + (i % 2) * (cw + 0.30)
        y = y0 + 0.06 + (i // 2) * (ch + 0.26)
        if hot:
            card(sl, x, y, cw, ch, fill=ROSE, line=ROSE, shadow=True)
            ic_clock(sl, x + 0.66, y + ch / 2, 0.62, WHITE, lw=2.0)
            TR(sl, x + 1.28, y + 0.30, cw - 1.6, 0.34,
               [(name, 15, WHITE, True), ("  ·  ", 15, C("EFC3D5"), False),
                (tag, 15, WHITE, True)])
            T(sl, x + 1.28, y + 0.70, cw - 1.6, 0.6, desc, 10.5, C("FBE7EF"),
              False, "l", "t", lh=1.5)
            p = pill(sl, x + cw - 1.42, y + 0.22, 1.12, 0.30, fill=WHITE)
            fit(sl, p, "저희가 푸는 것", size=9, color=ROSE, bold=True)
        else:
            card(sl, x, y, cw, ch, fill=WHITE)
            n = ic_badge(sl, x + 0.66, y + ch / 2, 0.62, "", SLATE, SURF2)
            T(sl, x + 0.35, y + ch / 2 - 0.14, 0.62, 0.3, name, 12, SLATE,
              True, "c", "t")
            T(sl, x + 1.28, y + 0.36, cw - 1.6, 0.34, name, 14, INK, True,
              "l", "t")
            T(sl, x + 1.28, y + 0.74, cw - 1.6, 0.5, desc, 10.5, MUTE, False,
              "l", "t", lh=1.5)
    y = y0 + 0.06 + 2 * (ch + 0.26) + 0.10
    b = card(sl, ML, y, CW, 0.72, fill=SURF, line=SURF, shadow=False)
    T(sl, ML + 0.34, y + 0.20, CW - 0.68, 0.36,
      "시간이 겹치지 않으면 나머지 셋을 아무리 잘 다뤄도 협업이 시작되지 않습니다. 그래서 시차부터 풀었습니다.",
      12, INK, True, "l", "t")
    source(sl, "Chauvin et al., 2024, Organization Science 35(5) — 시간적 거리가 커질수록 근무시간 중첩이 줄어 실시간 소통 기회가 제한된다", y=H - 0.88)
    return sl


# ─────────────────────────────── 04 동시 근무 시간 = 0
def s04(prs):
    sl, y0 = frame(prs, "PROBLEM 02",
                   "한국 · 베트남 · 미국. 셋이 함께 있는 시간은 하루 0시간입니다",
                   "각 팀원이 09시–18시 근무한다고 두고 UTC 기준으로 겹쳐 보면 이렇게 됩니다.",
                   accent=ROSE)
    x0, xw = 2.72, 9.55
    def px(h):
        return x0 + xw * (h / 24.0)

    # 축
    for h in range(0, 25, 3):
        vline(sl, px(h), y0 + 0.42, 2.62, color=C("EFF3F8"), lw=1.0)
        T(sl, px(h) - 0.30, y0 + 0.12, 0.60, 0.24, "%02d" % h, 9,
          FAINT, False, "c", "t")
    T(sl, ML, y0 + 0.12, 1.4, 0.24, "UTC 기준", 9, FAINT, True, "l", "t")

    # 겹치는 구간을 막대 뒤에 먼저 깔아 둡니다
    box(sl, px(0), y0 + 0.42, px(2) - px(0), 2.62, fill=LAV_XL)
    box(sl, px(2), y0 + 0.42, px(9) - px(2), 2.62, fill=MINT_XL)

    rows = [("한국 · KST", "UTC+9", BLUE, [(0, 9)]),
            ("베트남 · ICT", "UTC+7", MINT, [(2, 11)]),
            ("미국 서부 · PST", "UTC−8", LAV, [(0, 2), (17, 24)])]
    for i, (nm, tz, col, spans) in enumerate(rows):
        y = y0 + 0.60 + i * 0.80
        T(sl, ML, y + 0.02, 1.78, 0.26, nm, 11, INK, True, "l", "t")
        T(sl, ML, y + 0.26, 1.78, 0.24, tz, 9.5, MUTE, False, "l", "t")
        for a, b in spans:
            bar = pill(sl, px(a), y, px(b) - px(a), 0.46, fill=col)
            if b - a >= 5:
                fit(sl, bar, "현지 09:00 – 18:00", size=9.5, color=WHITE, bold=True)

    T(sl, px(0) + 0.06, y0 + 2.68, 2.4, 0.24, "↑ 전날 근무의 끝부분", 8.5,
      MUTE, False, "l", "t")
    ytop = y0 + 0.42
    for a, b, col, lab in ((0, 2, LAV, "2시간"), (2, 9, MINT, "7시간")):
        p = pill(sl, (px(a) + px(b)) / 2 - 0.52, ytop - 0.02, 1.04, 0.30, fill=col)
        fit(sl, p, lab, size=9.5, color=WHITE, bold=True)

    y = y0 + 3.26
    card(sl, ML, y, CW, 1.02, fill=ROSE_XL, line=ROSE_L, shadow=False)
    TR(sl, ML + 0.36, y + 0.20, 5.4, 0.62,
       [("세 사람이 동시에 일하는 시간  ", 13, INK, True),
        ("0", 30, ROSE, True), ("시간", 13, ROSE, True)], anchor="m")
    vline(sl, ML + 6.0, y + 0.22, 0.58, color=ROSE_L)
    T(sl, ML + 6.36, y + 0.22, CW - 6.7, 0.62,
      "둘씩 겹치는 시간도 한국·베트남 7시간, 한국·미국 2시간뿐입니다.\n"
      "세 사람이 필요한 결정은 어느 회의에서도 그 자리에서 끝나지 않습니다.",
      10.5, INK2, False, "l", "m", lh=1.5)
    return sl


# ─────────────────────────────── 05 결정 하나가 23시간
def s05(prs):
    sl, y0 = frame(prs, "PROBLEM 03",
                   "그래서 결정 하나가 23시간을 건너갑니다",
                   "한국·베트남이 회의에서 다음 일정과 구현 방식을 정하려는데, 미국 백엔드 담당이 자고 있는 상황.",
                   accent=ROSE)
    x0, xw = 1.72, 10.30
    #: 시간에 비례해 찍으면 +16h 와 +17h 가 붙어 라벨이 겹칩니다.
    #: 자리는 등간격으로 두고 경과 시간은 숫자로 읽게 합니다.
    def px(f):
        return x0 + xw * f

    # ── 지금
    T(sl, ML, y0 + 0.06, 1.6, 0.26, "지금", 12, INK, True, "l", "t")
    ylane = y0 + 0.98
    hline(sl, x0, ylane, xw, SLATE_L, 2.0)
    steps = [(0.00, 0, "회의 · 결정 보류", "미국 담당 의견을 확인한 뒤\n정하기로 하고 멈춤", ROSE),
             (0.42, 16, "미국 담당 출근", "회의록과 이전 작업부터\n다시 확인", SLATE),
             (0.68, 17, "의견 전달", "정리해서 회신", SLATE),
             (1.00, 23, "한국·베트남 확인", "다음 날 업무 시작.\n그제서야 결정", ROSE)]
    for f, t, title, desc, col in steps:
        x = px(f)
        oval(sl, x - 0.105, ylane - 0.105, 0.21, 0.21, fill=col)
        oval(sl, x - 0.045, ylane - 0.045, 0.09, 0.09, fill=WHITE)
        lx = max(ML, min(x - 1.25, W - MR - 2.5))
        T(sl, lx, ylane - 0.72, 2.5, 0.28, title, 11, INK, True, "c", "t")
        T(sl, lx, ylane + 0.20, 2.5, 0.66, desc, 9.5, MUTE, False, "c",
          "t", lh=1.45)
        T(sl, lx, ylane - 1.02, 2.5, 0.24, "+%dh" % t if t else "0h",
          9.5, col, True, "c", "t", spc=8)
    for a, b, lab in ((0.00, 0.42, "대기 16시간"), (0.68, 1.00, "대기 6시간")):
        mid = (px(a) + px(b)) / 2
        p = pill(sl, mid - 0.68, ylane + 1.02, 1.36, 0.30, fill=ROSE_XL)
        fit(sl, p, lab, size=9.5, color=ROSE, bold=True)

    hline(sl, ML, y0 + 2.62, CW, LINE, dash=True)

    # ── Bordo
    T(sl, ML, y0 + 2.76, 1.6, 0.26, "Bordo", 12, BLUE, True, "l", "t")
    ylane2 = y0 + 3.50
    hline(sl, x0, ylane2, 3.30, BLUE_L, 2.0)
    oval(sl, px(0) - 0.105, ylane2 - 0.105, 0.21, 0.21, fill=BLUE)
    oval(sl, px(0) - 0.045, ylane2 - 0.045, 0.09, 0.09, fill=WHITE)
    T(sl, px(0) - 1.25, ylane2 - 0.48, 2.5, 0.28, "회의 중 대리인이 응답", 11,
      INK, True, "c", "t")
    T(sl, px(0) - 1.25, ylane2 + 0.22, 2.6, 0.5,
      "준비된 입장과 작업 기록을 근거로\n그 자리에서 답하거나 유보", 9.5, MUTE,
      False, "c", "t", lh=1.45)
    p = pill(sl, x0 + 2.34, ylane2 - 0.17, 1.34, 0.34, fill=BLUE)
    fit(sl, p, "대기 0시간", size=9.5, color=WHITE, bold=True)
    T(sl, x0 + 4.10, ylane2 - 0.34, 6.2, 0.7,
      "대리인이 답할 수 있는 범위일 때. 범위 밖이면 유보하고 본인에게 넘깁니다 —\n"
      "그때도 팀은 “왜 지금 못 정하는지”를 회의 중에 압니다.",
      10, INK2, False, "l", "m", lh=1.5)
    return sl


# ─────────────────────────────── 06 숫자
def s06(prs):
    sl, y0 = frame(prs, "PROBLEM 04",
                   "그 하루를 가장 크게 잃는 자리가 회의입니다",
                   accent=ROSE)
    stats = [(0.57, "57%", "직장인이 업무 시간 중\n커뮤니케이션에 쓰는 비중", BLUE, BLUE_L,
              "Microsoft Work Trend Index 2023"),
             (0.23, "23%", "그 커뮤니케이션 중\n팀 회의가 차지하는 비중 · 최다", LAV, LAV_L,
              "Microsoft Work Trend Index 2023"),
             (0.569, "56.9%", "회의 불참 시 가장 번거로운 순간\n“의견을 몰라 결정을 미뤄야 할 때”",
              ROSE, ROSE_L, "자체 설문")]
    w = (CW - 2 * 0.30) / 3
    for i, (pct, big, desc, col, light, src) in enumerate(stats):
        x = ML + i * (w + 0.30)
        card(sl, x, y0 + 0.16, w, 3.30)
        ring(sl, x + w / 2, y0 + 1.30, 1.56, pct, col, light)
        T(sl, x + w / 2 - 1.0, y0 + 1.10, 2.0, 0.5, big, 23, col, True, "c", "t")
        hline(sl, x + 0.9, y0 + 2.32, w - 1.8, LINE)
        T(sl, x + 0.42, y0 + 2.52, w - 0.84, 0.7, desc, 11.5, INK, True, "c",
          "t", lh=1.5)
        T(sl, x + 0.42, y0 + 3.14, w - 0.84, 0.24, src, 8.5, FAINT, False,
          "c", "t")
    y = y0 + 3.66
    T(sl, ML, y, CW, 0.4,
      "한 사람의 불참은 그 사람만의 손해로 끝나지 않습니다. 빠진 사람은 혼자 복구하고, 남은 사람은 따로 정리해 전달하고, 팀은 결정을 미룹니다.",
      12, INK, True, "l", "t")
    return sl


# ─────────────────────────────── 07 기존 도구가 닿는 구간
def s07(prs):
    sl, y0 = frame(prs, "GAP",
                   "기존 회의 요약 AI는 회의가 끝난 뒤에 도착합니다",
                   "성능 문제가 아니라 개입 시점의 문제입니다.",
                   accent=ROSE)
    segs = [("회의 전", "쟁점을 예상하고\n내 입장을 준비"),
            ("회의 중", "질문에 답하고\n의견을 전달"),
            ("회의 직후", "내가 확인할 것부터\n먼저 파악"),
            ("전달 이후", "상대가 없을 때도\n맥락을 이어감")]
    x0 = 2.62
    sw = (CW - (x0 - ML) - 3 * 0.16) / 4
    for i, (nm, desc) in enumerate(segs):
        x = x0 + i * (sw + 0.16)
        T(sl, x, y0 + 0.02, sw, 0.26, nm, 11, INK, True, "c", "t")
        T(sl, x, y0 + 0.30, sw, 0.56, desc, 9.5, MUTE, False, "c", "t", lh=1.45)

    rows = [("기존 회의 요약 AI", "Otter · Fireflies · Sembly", SLATE,
             [0, 0, 1, 0], "끝난 대화를 축약해 모두에게 같은 요약"),
            ("Bordo", "회의 전 · 중 · 후 · 이후", BLUE,
             [1, 1, 1, 1], "허용 범위 안에서 대신 말하고, 사람마다 다르게 재작성")]
    for r, (nm, sub, col, cov, tail) in enumerate(rows):
        y = y0 + 1.16 + r * 1.34
        T(sl, ML, y + 0.10, 1.66, 0.28, nm, 11.5, INK, True, "l", "t")
        T(sl, ML, y + 0.38, 1.72, 0.24, sub, 9, MUTE, False, "l", "t")
        for i in range(4):
            x = x0 + i * (sw + 0.16)
            if cov[i]:
                s = pill(sl, x, y, sw, 0.62, fill=col)
                fit(sl, s, "닿음", size=10, color=WHITE, bold=True)
            else:
                s = pill(sl, x, y, sw, 0.62, fill=WHITE, line=C("DFE5EE"), lw=1.0)
                fit(sl, s, "닿지 않음", size=10, color=FAINT, bold=False)
        T(sl, ML, y + 0.74, CW, 0.26, tail, 9.5, MUTE, False, "l", "t")

    y = y0 + 3.64
    card(sl, ML, y, CW, 0.68, fill=SURF, line=SURF, shadow=False)
    T(sl, ML + 0.34, y + 0.19, CW - 0.68, 0.34,
      "요약이 아무리 정확해도, 회의 그 자리에 내 의견이 없었다는 사실은 그대로 남습니다.",
      12, INK, True, "l", "t")
    source(sl, "Asthana et al., 2025, Proc. ACM HCI — 고정 길이 요약보다 구조화·개인화가 필요하다", y=y + 0.82)
    return sl


# ─────────────────────────────── 08 문제 정의
def s08(prs):
    sl = blank(prs)
    PAGE["n"] += 1
    box(sl, 0, 0, W, H, fill=SURF)
    box(sl, 0, 0, 0.085, H, fill=ROSE)
    oval(sl, 10.4, -1.5, 4.4, 4.4, fill=ROSE_XL)
    oval(sl, -1.4, 4.9, 3.6, 3.6, fill=BLUE_XL)

    T(sl, ML, 1.72, CW, 0.28, "PROBLEM STATEMENT", 10, ROSE, True, "l", "t",
      spc=22)
    hline(sl, ML, 2.16, 1.3, ROSE, 2.2)
    T(sl, ML, 2.52, 10.2, 1.9,
      "시차로 필요한 사람의 의견과 확인을\n제때 얻지 못해\n의사결정이 지속적으로 지연되는 문제",
      27, INK, True, "l", "t", lh=1.42)
    T(sl, ML, 4.90, 9.4, 0.6,
      "질문 · 답변 · 확인 · 의사결정 사이에 대기 시간이 반복되고, 하나의 결정이 여러 업무시간대를 거치며 밀립니다.",
      12, INK2, False, "l", "t", lh=1.55)
    chips = ["회의가 멈춥니다", "연결된 작업이 멈춥니다", "복귀한 사람은 복구부터 합니다"]
    x = ML
    for ch in chips:
        w = 0.34 + len(ch) * 0.125
        p = pill(sl, x, 5.62, w, 0.42, fill=WHITE, line=C("E7DDE4"), lw=1.0)
        fit(sl, p, ch, size=10.5, color=ROSE, bold=True)
        x += w + 0.18
    T(sl, ML, H - 0.50, 2.0, 0.22, "Bordo", 9, FAINT, True, "l", "t", spc=30)
    T(sl, W - MR - 1.2, H - 0.50, 1.2, 0.22, "%02d" % PAGE["n"], 9, FAINT,
      True, "r", "t", spc=20)
    return sl
