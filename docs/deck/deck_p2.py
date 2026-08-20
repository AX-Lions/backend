# -*- coding: utf-8 -*-
"""해결 방식 (9–15)."""
from deck_base import *          # noqa: F401,F403
from deck_base import (W, H, ML, MR, CW, INK, INK2, MUTE, FAINT, LINE, WHITE,
                       SURF, SURF2, BLUE, BLUE_L, BLUE_XL, MINT, MINT_L,
                       MINT_XL, LAV, LAV_L, LAV_XL, ROSE, ROSE_L, ROSE_XL,
                       SLATE, SLATE_L, SLATE_XL, C, T, TR, fit, card, box,
                       oval, pill, shape, hline, vline, arrow, frame, blank,
                       source, placeholder, ic_clock, ic_person, ic_doc,
                       ic_bubble, ic_badge, toggle, PAGE, A_NS)
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.util import Inches, Pt
from pptx.oxml import parse_xml


def conn(sl, x1, y1, x2, y2, color=SLATE_L, lw=1.6, dash=False, head=True):
    """화살촉 달린 직선. python-pptx 에 API 가 없어 XML 로 붙입니다."""
    ln = sl.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                                 Inches(x2), Inches(y2))
    ln.line.color.rgb = color
    ln.line.width = Pt(lw)
    el = ln.line._get_or_add_ln()
    if dash:
        el.append(parse_xml("<a:prstDash " + A_NS + ' val="sysDash"/>'))
    if head:
        el.append(parse_xml("<a:tailEnd " + A_NS
                            + ' type="triangle" w="med" len="med"/>'))
    return ln


def chev(sl, cx, cy, color=C("CBD5E3"), s=0.19):
    """단계 사이에 놓는 작은 꺾쇠."""
    return shape(sl, MSO_SHAPE.RIGHT_ARROW, cx - s / 2, cy - s * 0.52, s,
                 s * 1.04, fill=color)


def chip(sl, x, y, text, fg, bg, size=9.5, pad=0.30, h=0.34, line=None):
    w = pad + len(text) * (size / 72.0) * 1.02
    p = pill(sl, x, y, w, h, fill=bg, line=line, lw=1.0)
    fit(sl, p, text, size=size, color=fg, bold=True)
    return w


# ─────────────────────────────── 09 솔루션 개요
def s09(prs):
    sl, y0 = frame(prs, "SOLUTION",
                   "모이는 시간을 만드는 대신, 빈자리를 채웁니다",
                   accent=BLUE)
    card(sl, ML, y0 + 0.04, CW, 0.84, fill=BLUE_XL, line=BLUE_L, shadow=False)
    TR(sl, ML + 0.36, y0 + 0.10, CW - 0.72, 0.72,
       [("Bordo", 14, BLUE, True), ("  는 회의에 참석하지 못한 사람의 ", 13, INK, False),
        ("업무 맥락과 의견", 13, INK, True),
        ("을 바탕으로, 부재 중에도 필요한 질문에 답하고 의견을 전달하는 ", 13, INK, False),
        ("개인 AI 대리인", 13, INK, True), ("입니다.", 13, INK, False)],
       anchor="m", lh=1.4)

    stages = [
        ("회의 전", "의사와 판단 범위를 설정", BLUE,
         "쟁점별 내 입장을 쓰고, 어디까지\n답해도 되는지 스위치로 정합니다.", "lock"),
        ("회의 중", "기다리지 않게 합니다", MINT,
         "준비된 입장과 작업 기록을 근거로\n답하고, 없으면 유보합니다.", "bubble"),
        ("회의 직후", "회의 전체를 복기하지 않게", LAV,
         "무슨 일 · 무엇이 정해졌나\n확인할 것 · 요청받은 것 네 갈래로.", "doc"),
        ("전달 이후", "상대를 기다리지 않게", ROSE,
         "상대가 퇴근했으면 그 사람의\n대리인이 먼저 받아 정리합니다.", "peer"),
    ]
    w = (CW - 3 * 0.30) / 4
    ytop = y0 + 1.18
    for i, (stage, title, col, desc, icon) in enumerate(stages):
        x = ML + i * (w + 0.30)
        card(sl, x, ytop, w, 2.86)
        box(sl, x, ytop, w, 0.055, fill=col)
        p = pill(sl, x + 0.30, ytop + 0.32, 1.06, 0.32, fill=col)
        fit(sl, p, stage, size=9.5, color=WHITE, bold=True)
        cx, cy = x + w - 0.62, ytop + 0.48
        if icon == "lock":
            ic_clock(sl, cx, cy, 0.46, col, lw=1.6)
        elif icon == "bubble":
            ic_bubble(sl, cx, cy, 0.52, col)
        elif icon == "doc":
            ic_doc(sl, cx, cy, 0.50, col)
        else:
            ic_person(sl, cx - 0.13, cy, 0.40, col)
            ic_person(sl, cx + 0.15, cy, 0.40, C("D5DDE9"))
        T(sl, x + 0.30, ytop + 0.94, w - 0.60, 0.64, title, 13.5, INK, True,
          "l", "t", lh=1.35)
        hline(sl, x + 0.30, ytop + 1.72, w - 0.60, LINE)
        T(sl, x + 0.30, ytop + 1.90, w - 0.60, 0.84, desc, 9.5, MUTE, False,
          "l", "t", lh=1.55)
        if i < 3:
            chev(sl, x + w + 0.15, ytop + 1.43)

    y = ytop + 3.08
    T(sl, ML, y, CW, 0.4,
      "대신 판단하는 AI가 아닙니다. 허용한 범위 안에서만 말하고, 사람이 판단해야 할 순간에는 다시 사람에게 넘깁니다.",
      12, INK, True, "l", "t")
    return sl


# ─────────────────────────────── 10 회의 전
def s10(prs):
    sl, y0 = frame(prs, "FEATURE 01 · 회의 전",
                   "무엇을 말해도 되는지 사람이 먼저 정합니다",
                   "AI 의 자의적 판단을 막는 자리입니다. 켜지 않은 것은 대리인이 아예 답하지 않습니다.",
                   accent=BLUE)
    # ── 권한 스위치
    lw_ = 5.42
    card(sl, ML, y0 + 0.04, lw_, 4.12)
    T(sl, ML + 0.40, y0 + 0.34, lw_ - 0.8, 0.3, "대리인 권한 설정", 13.5, INK,
      True, "l", "t")
    T(sl, ML + 0.40, y0 + 0.68, lw_ - 0.8, 0.26,
      "사용자 단위 설정. 팀마다 다른 대리인을 갖지 않습니다.", 9.5, MUTE, False, "l", "t")
    rows = [("구현 가능성 판단", "“이거 다음 주까지 됩니까”에 답할지", False),
            ("일정 수정 동의", "일정을 당기자는 제안에 답할지", True),
            ("회의 중 되묻기", "애매하면 대리인이 되물어도 되는지", True)]
    ry = y0 + 1.02
    for nm, desc, on in rows:
        T(sl, ML + 0.40, ry + 0.02, 3.3, 0.28, nm, 11.5, INK, True, "l", "t")
        T(sl, ML + 0.40, ry + 0.30, 3.5, 0.24, desc, 9.5, MUTE, False, "l", "t")
        toggle(sl, ML + lw_ - 1.36, ry + 0.06, 0.72, on)
        T(sl, ML + lw_ - 0.56, ry + 0.10, 0.5, 0.24, "ON" if on else "OFF",
          9.5, (MINT if on else FAINT), True, "l", "t")
        ry += 0.70
    hline(sl, ML + 0.40, ry - 0.04, lw_ - 0.8, LINE)
    T(sl, ML + 0.40, ry + 0.08, 3.4, 0.28, "내 기록 공개 범위", 11.5, INK, True,
      "l", "t")
    T(sl, ML + 0.40, ry + 0.36, 4.5, 0.24,
      "끈 항목은 서버로 전송되지 않고, 존재 여부도 말하지 않습니다.",
      9.5, MUTE, False, "l", "t")
    for i, (nm, on) in enumerate((("작업", True), ("계획", True), ("생각", False))):
        x = ML + 0.40 + i * 1.62
        toggle(sl, x, ry + 0.70, 0.62, on)
        T(sl, x + 0.70, ry + 0.74, 0.9, 0.24, nm, 10.5, INK, True, "l", "t")

    # ── 쟁점과 입장
    rx, rw = ML + lw_ + 0.30, CW - lw_ - 0.30
    card(sl, rx, y0 + 0.04, rw, 1.86)
    T(sl, rx + 0.36, y0 + 0.30, rw - 0.72, 0.28, "예상 쟁점 · 내 입장", 13.5,
      INK, True, "l", "t")
    T(sl, rx + 0.36, y0 + 0.64, rw - 0.72, 0.24,
      "쟁점은 회의에 답니다. 사람마다 만들면 같은 예측을 두 번 돌리게 됩니다.",
      9.5, MUTE, False, "l", "t")
    b = card(sl, rx + 0.36, y0 + 0.98, rw - 0.72, 0.74, fill=SURF, line=SURF,
             shadow=False)
    T(sl, rx + 0.56, y0 + 1.10, rw - 1.1, 0.24,
      "쟁점  결제 모듈을 이번 스프린트에 넣을 것인가", 10.5, INK, True, "l", "t")
    T(sl, rx + 0.56, y0 + 1.38, rw - 1.1, 0.24,
      "내 입장  검증 로직이 남아 다음 스프린트를 제안합니다", 10.5, BLUE, False,
      "l", "t")

    placeholder(sl, rx, y0 + 2.12, rw, 2.04,
                "화면 캡처 자리 — 대리인 설정",
                "권한 스위치 4종과 시스템 프롬프트가 보이는 설정 화면\n(계정 › AI 대리인)",
                accent=BLUE)
    source(sl, "회의 하나 때문에 평소 설정을 바꿔 두면 되돌리는 것이 사람 몫이 되므로, 회의별 덮어쓰기(settings_override · prompt_override)를 따로 둡니다.")
    return sl


# ─────────────────────────────── 11 판단 파이프라인
def s11(prs):
    sl, y0 = frame(prs, "FEATURE 02 · 회의 중",
                   "말할 수 있을 때만 말하고, 아니면 유보합니다",
                   "문장은 모델이 쓰지만 답해도 되는지는 코드가 정합니다.",
                   accent=BLUE)
    steps = [("01", "질문 감지", "불참자에게 답을\n요구하는 발언인가", "코드", SLATE),
             ("02", "의도 분류", "구현 가능성 · 일정 ·\n현황 · 되묻기", "LLM", LAV),
             ("03", "POLICY 게이트", "사용자가 켜 둔\n권한인지 확인", "코드", BLUE),
             ("04", "근거 검색", "준비된 입장 → 회의 →\n작업 기록 순차 검색", "LLM", LAV),
             ("05", "유보 판정", "근거가 답으로\n이어지는지 검사", "코드", BLUE),
             ("06", "발화 / 유보", "근거와 적용 권한을\n함께 기록", "코드", MINT)]
    gap = 0.26
    w = (CW - 5 * gap) / 6
    ytop = y0 + 0.06
    for i, (num, title, desc, who, col) in enumerate(steps):
        x = ML + i * (w + gap)
        card(sl, x, ytop, w, 1.72)
        box(sl, x, ytop, w, 0.05, fill=col)
        T(sl, x + 0.22, ytop + 0.24, 0.6, 0.24, num, 10, col, True, "l", "t",
          spc=10)
        p = pill(sl, x + w - 0.76, ytop + 0.20, 0.54, 0.28,
                 fill=(LAV_XL if who == "LLM" else SURF2))
        fit(sl, p, who, size=8.5, color=(LAV if who == "LLM" else SLATE),
            bold=True)
        T(sl, x + 0.22, ytop + 0.62, w - 0.44, 0.34, title, 12, INK, True,
          "l", "t")
        T(sl, x + 0.22, ytop + 1.00, w - 0.44, 0.62, desc, 9.5, MUTE, False,
          "l", "t", lh=1.45)
        if i < 5:
            chev(sl, x + w + gap / 2, ytop + 0.86)

    y = ytop + 1.94
    guards = [("도구 호출 상한", "6회"), ("대리인 간 조회 깊이", "3홉"),
              ("확신 임계값", "0.70"), ("기록 신선도", "14일")]
    x = ML
    for nm, val in guards:
        wch = 2.72
        b = card(sl, x, y, wch, 0.60, fill=SURF, line=SURF, shadow=False)
        T(sl, x + 0.24, y + 0.16, 1.7, 0.28, nm, 10, MUTE, False, "l", "t")
        T(sl, x + wch - 1.0, y + 0.13, 0.76, 0.32, val, 12.5, INK, True, "r", "t")
        x += wch + 0.25

    y2 = y + 0.86
    card(sl, ML, y2, CW, 1.10, fill=BLUE_XL, line=BLUE_L, shadow=False)
    T(sl, ML + 0.40, y2 + 0.22, CW - 0.80, 0.34,
      "유보 여부는 모델이 아니라 코드가 판정합니다.", 13, INK, True, "l", "t")
    T(sl, ML + 0.40, y2 + 0.60, CW - 0.80, 0.4,
      "“모르면 유보해”라고 시키면 모델은 답할 수 있다고 스스로를 설득합니다. 코드가 판정하면 같은 입력에 같은 결과가 나오고, 왜 유보했는지 규칙으로 설명할 수 있습니다.",
      10.5, INK2, False, "l", "t", lh=1.5)
    return sl


# ─────────────────────────────── 12 유보
def s12(prs):
    sl, y0 = frame(prs, "FEATURE 02 · 회의 중",
                   "유보는 실패가 아니라 이 서비스의 가치입니다",
                   "대부분의 도구는 모르는 것도 그럴듯하게 채웁니다. Bordo 는 “본인 확인 필요”로 남기고 그 이유를 보여줍니다.",
                   accent=BLUE)
    lw_ = 6.62
    card(sl, ML, y0 + 0.04, lw_, 3.58)
    T(sl, ML + 0.38, y0 + 0.30, lw_ - 0.76, 0.28, "유보 사유 일곱 가지", 13.5,
      INK, True, "l", "t")
    T(sl, ML + 0.38, y0 + 0.64, lw_ - 0.76, 0.24,
      "순서가 있고 먼저 걸리는 것이 이깁니다 — 사용자에게 사유는 하나만 보여 줍니다.",
      9.5, MUTE, False, "l", "t")
    reasons = [("근거 없음", "인용할 기록이 하나도 없음"),
               ("내 기록이 아님", "남의 작업으로만 답이 됨"),
               ("추론뿐", "기록에 없는 것을 이어 붙여야 함"),
               ("논의 필요", "혼자 정할 수 없는 사안"),
               ("확신 부족", "관련도가 임계값 아래"),
               ("오래된 기록", "14일 넘은 근거"),
               ("서로 어긋남", "기록끼리 반대로 말함")]
    ry = y0 + 1.02
    for i, (nm, desc) in enumerate(reasons):
        y = ry + i * 0.335
        oval(sl, ML + 0.40, y + 0.09, 0.10, 0.10, fill=BLUE_L)
        T(sl, ML + 0.66, y, 1.72, 0.26, nm, 10.5, INK, True, "l", "t")
        T(sl, ML + 2.42, y + 0.01, lw_ - 2.9, 0.26, desc, 10, MUTE, False,
          "l", "t")

    rx, rw = ML + lw_ + 0.30, CW - lw_ - 0.30
    card(sl, rx, y0 + 0.04, rw, 3.58)
    T(sl, rx + 0.34, y0 + 0.28, rw - 0.68, 0.28, "회의에서는 이렇게 보입니다",
      13.5, INK, True, "l", "t")
    seq = [("회의 중 질문", "“이 기능 다음 주까지 됩니까?”", SURF, INK, SLATE),
           ("내 설정", "구현 가능성 판단 · OFF", SURF2, INK, SLATE),
           ("대리인", "“본인 확인이 필요합니다.”\n사유 · 권한 밖의 질문", BLUE_XL, INK, BLUE)]
    yy = y0 + 0.68
    for i, (who, txt, bg, fg, col) in enumerate(seq):
        h = 0.82 if i == 2 else 0.62
        card(sl, rx + 0.34, yy, rw - 0.68, h, fill=bg, line=bg, shadow=False)
        T(sl, rx + 0.54, yy + 0.10, 1.4, 0.24, who, 9, col, True, "l", "t")
        T(sl, rx + 0.54, yy + 0.32, rw - 1.1, 0.5, txt, 10.5, fg, False, "l",
          "t", lh=1.45)
        if i < 2:
            chev(sl, rx + rw / 2, yy + h + 0.12, C("CBD5E3"), 0.17)
        yy += h + 0.24
    T(sl, rx + 0.34, yy - 0.14, rw - 0.68, 0.3,
      "→ 돌아온 사람이 유보 목록에서 직접 답합니다", 10, MINT, True, "l", "t")

    y = y0 + 3.80
    card(sl, ML, y, CW, 0.70, fill=SURF, line=SURF, shadow=False)
    T(sl, ML + 0.36, y + 0.19, CW - 0.72, 0.34,
      "업무 환경에서 AI 의 오답과 정보 유출은 프로젝트에 직접 영향을 줍니다. 그래서 통제권을 사람 쪽에 남겨 둡니다.",
      12, INK, True, "l", "t")
    return sl


# ─────────────────────────────── 13 브리핑
def s13(prs):
    sl, y0 = frame(prs, "FEATURE 03 · 회의 직후",
                   "같은 회의라도 필요한 정보는 사람마다 다릅니다",
                   "고정 길이 요약 대신 네 갈래로 나누고, 그 사람의 작업·계획·생각에 붙여 다시 씁니다.",
                   accent=LAV)
    secs = [("무슨 일이 있었나", "부재 중 오간 논의의 흐름", LAV),
            ("무엇이 정해졌나", "결정 사항과 그 근거", BLUE),
            ("내가 확인할 것", "대리인이 유보한 질문 목록", ROSE),
            ("나에게 온 요청", "나에게 넘어온 작업 후보", MINT)]
    cw_ = 3.34
    for i, (nm, desc, col) in enumerate(secs):
        x = ML + (i % 2) * (cw_ + 0.22)
        y = y0 + 0.06 + (i // 2) * 1.72
        card(sl, x, y, cw_, 1.52)
        box(sl, x, y, 0.05, 1.52, fill=col)
        T(sl, x + 0.34, y + 0.28, cw_ - 0.68, 0.3, nm, 13, INK, True, "l", "t")
        T(sl, x + 0.34, y + 0.66, cw_ - 0.68, 0.28, desc, 10, MUTE, False,
          "l", "t")
        hline(sl, x + 0.34, y + 1.04, cw_ - 0.68, LINE)
        T(sl, x + 0.34, y + 1.16, cw_ - 0.68, 0.26,
          ["회의 흐름", "결정 · 근거", "유보 질문", "작업 후보"][i], 9.5, col,
          True, "l", "t")

    px_ = ML + 2 * cw_ + 0.22 + 0.30
    pw = CW - (px_ - ML)
    placeholder(sl, px_, y0 + 0.06, pw, 3.18,
                "화면 캡처 자리 — 회의 브리핑",
                "네 섹션이 모두 보이는 브리핑 패널\n(회의 › 브리핑, susu@bordo.dev 계정)",
                accent=LAV)

    y = y0 + 3.50
    card(sl, ML, y, CW, 0.80, fill=LAV_XL, line=LAV_L, shadow=False)
    TR(sl, ML + 0.36, y + 0.14, CW - 0.72, 0.56,
       [("직무와 언어에 맞춰 다시 씁니다.  ", 12, INK, True),
        ("백엔드가 쓴 기술 표현은 디자이너가 이해할 배경과 함께 풀고, 쓰는 언어가 다르면 그 사람의 언어로 옮깁니다. "
         "언어라는 경계가 한 번 더 무너지는 지점입니다.", 10.5, INK2, False)],
       anchor="m", lh=1.5)
    return sl


# ─────────────────────────────── 14 대리인 간 연결
def s14(prs):
    sl, y0 = frame(prs, "FEATURE 04 · 전달 이후",
                   "상대가 퇴근했으면 그 사람의 대리인이 먼저 받습니다",
                   "AI 끼리 직접 통신하지 않습니다. 서버가 상대의 실행을 새로 돌리고 결과를 양쪽에 기록합니다.",
                   accent=BLUE)
    nodes = [("사람 A", "업무 시작", SURF2, INK, "person"),
             ("A 의 대리인", "질의 생성", BLUE_XL, BLUE, "bubble"),
             ("서버", "실행 · 기록", BLUE, WHITE, "server"),
             ("B 의 대리인", "허용 범위 안에서 응답", BLUE_XL, BLUE, "bubble"),
             ("사람 B", "퇴근 · 다음 날 확인", SURF2, INK, "person")]
    nw, gap = 1.94, 0.53
    ytop = y0 + 0.16
    for i, (nm, desc, bg, fg, kind) in enumerate(nodes):
        x = ML + i * (nw + gap)
        hot = kind == "server"
        card(sl, x, ytop, nw, 1.46, fill=bg, line=(bg if hot else C("E3E9F1")),
             shadow=hot)
        cy = ytop + 0.44
        if kind == "person":
            ic_person(sl, x + nw / 2, cy, 0.44, SLATE)
        elif kind == "bubble":
            ic_bubble(sl, x + nw / 2, cy, 0.50, BLUE)
        else:
            box(sl, x + nw / 2 - 0.26, cy - 0.24, 0.52, 0.16, fill=WHITE)
            box(sl, x + nw / 2 - 0.26, cy - 0.04, 0.52, 0.16, fill=WHITE)
            box(sl, x + nw / 2 - 0.26, cy + 0.16, 0.52, 0.16, fill=WHITE)
        T(sl, x + 0.16, ytop + 0.80, nw - 0.32, 0.28, nm, 11.5, fg, True,
          "c", "t")
        T(sl, x + 0.10, ytop + 1.08, nw - 0.20, 0.28, desc, 9.5,
          (C("D6E4F8") if hot else MUTE), False, "c", "t")
        if i < 4:
            conn(sl, x + nw + 0.09, ytop + 0.73, x + nw + gap - 0.09,
                 ytop + 0.73, SLATE_L, 1.8)
    T(sl, ML + 2 * (nw + gap), ytop + 1.60, nw, 0.28,
      "trace_id · hop_count ≤ 3", 9.5, BLUE, True, "c", "t")

    y = y0 + 2.32
    T(sl, ML, y, CW, 0.3, "받은 쪽 화면에는 네 단으로 남습니다", 12.5, INK,
      True, "l", "t")
    four = [("조회 이유", "왜 물어봤는지"), ("질문", "무엇을 물었는지"),
            ("확인된 내용", "무엇을 받았는지"), ("출처 · 시각", "어느 기록에서 언제")]
    fw = (CW - 3 * 0.24) / 4
    for i, (nm, desc) in enumerate(four):
        x = ML + i * (fw + 0.24)
        card(sl, x, y + 0.40, fw, 0.94, fill=SURF, line=SURF, shadow=False)
        T(sl, x + 0.26, y + 0.56, fw - 0.5, 0.28, nm, 11, INK, True, "l", "t")
        T(sl, x + 0.26, y + 0.86, fw - 0.5, 0.26, desc, 9.5, MUTE, False,
          "l", "t")
        if i < 3:
            chev(sl, x + fw + 0.12, y + 0.87, C("CBD5E3"), 0.16)

    y2 = y + 1.58
    card(sl, ML, y2, CW, 0.74, fill=SURF, line=SURF, shadow=False)
    T(sl, ML + 0.36, y2 + 0.20, CW - 0.72, 0.34,
      "답을 못 얻어도 기록은 남깁니다. “물어봤는데 답을 못 받았다”가 화면에 없으면 알아보지도 않은 줄 압니다.",
      12, INK, True, "l", "t")
    return sl


# ─────────────────────────────── 15 Flow
def s15(prs):
    sl, y0 = frame(prs, "FEATURE 05 · 기록",
                   "비동기로 오간 말도 발생 맥락과 함께 남습니다",
                   "누가 누구에게 무엇을 전달했고 어떤 작업이 생겼는지. 대리인이 옮긴 선은 색으로 구분합니다.",
                   accent=BLUE)
    kinds = [("의견", BLUE), ("요청사항", MINT), ("변동사항", LAV),
             ("일정", ROSE), ("결론", SLATE), ("기타", C("9AA6B8"))]
    x = ML
    for nm, col in kinds:
        w = chip(sl, x, y0 + 0.02, nm, WHITE, col, size=9.5)
        x += w + 0.14
    T(sl, x + 0.14, y0 + 0.06, 4.0, 0.28, "콘텐츠 종류 여섯 가지로 걸러 봅니다",
      9.5, MUTE, False, "l", "t")

    # ── 노드-엣지 도식
    gx, gy, gw, gh = ML, y0 + 0.54, 6.42, 3.30
    card(sl, gx, gy, gw, gh, fill=SURF, line=SURF, shadow=False)
    #: 이름표는 위·아래 바깥으로 빼고 뱃지는 선 위에 얹습니다.
    #: 셋을 같은 높이에 두면 서로 겹칩니다.
    ax, bx = gx + 1.55, gx + 4.90
    ty, by = gy + 1.00, gy + 2.16
    conn(sl, ax + 0.44, ty, bx - 0.44, ty, BLUE, 2.0)
    conn(sl, bx, ty + 0.44, bx, by - 0.44, MINT, 2.0)
    conn(sl, ax, by - 0.44, ax, ty + 0.44, LAV, 2.0)
    conn(sl, ax + 0.42, ty + 0.34, bx - 0.40, by - 0.34, ROSE, 2.0, dash=True)
    people = [("유수인", ax, ty, BLUE, -1), ("서재민", bx, ty, MINT, -1),
              ("임수연", ax, by, LAV, 1), ("최비성", bx, by, ROSE, 1)]
    for nm, cx, cy, col, side in people:
        oval(sl, cx - 0.40, cy - 0.40, 0.80, 0.80, fill=WHITE, line=col, lw=2.0)
        ic_person(sl, cx, cy - 0.02, 0.42, col)
        T(sl, cx - 0.9, cy + (0.46 if side > 0 else -0.74), 1.8, 0.26, nm, 10,
          INK, True, "c", "t")
    for lab, cx, cy, col in (("의견 3", (ax + bx) / 2, ty, BLUE),
                             ("요청 2", bx, (ty + by) / 2, MINT),
                             ("변동 1", ax, (ty + by) / 2, LAV),
                             ("일정 1", ax + (bx - ax) * 0.61, gy + 1.66, ROSE)):
        p = pill(sl, cx - 0.44, cy - 0.16, 0.88, 0.32, fill=WHITE, line=col, lw=1.0)
        fit(sl, p, lab, size=8.5, color=col, bold=True)
    T(sl, gx + 0.28, gy + gh - 0.36, gw - 0.56, 0.26,
      "점선 = 대리인이 옮긴 선. 누르면 근거와 적용된 권한이 열립니다.",
      9.5, MUTE, False, "l", "t")

    placeholder(sl, gx + gw + 0.30, gy, CW - gw - 0.30, gh,
                "화면 캡처 자리 — 플로우",
                "사람 노드와 화살표 뱃지가 보이는 회의 플로우 화면\n(회의 › 플로우)",
                accent=BLUE)

    y = gy + gh + 0.28
    T(sl, ML, y, CW, 0.34,
      "전달 1건마다 선을 하나씩 그리면 두 사람 사이에 선이 열 개 겹칩니다. 사람 쌍마다 하나로 묶고 종류별 개수를 뱃지로 붙였습니다.",
      11, INK2, False, "l", "t")
    return sl
