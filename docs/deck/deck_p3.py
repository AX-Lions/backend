# -*- coding: utf-8 -*-
"""동작 구조 · 시장 · 실행 (16–24)."""
from deck_base import *          # noqa: F401,F403
from deck_base import (W, H, ML, MR, CW, INK, INK2, MUTE, FAINT, LINE, WHITE,
                       SURF, SURF2, BLUE, BLUE_L, BLUE_XL, MINT, MINT_L,
                       MINT_XL, LAV, LAV_L, LAV_XL, ROSE, ROSE_L, ROSE_XL,
                       SLATE, SLATE_L, SLATE_XL, C, T, TR, fit, card, box,
                       oval, pill, shape, hline, vline, arrow, frame, blank,
                       source, placeholder, ic_clock, ic_person, ic_doc,
                       ic_bubble, ic_badge, toggle, PAGE, FONT_MONO)
from deck_p2 import conn, chev, chip
from pptx.enum.shapes import MSO_SHAPE


# ─────────────────────────────── 16 3대 원칙
def s16(prs):
    sl, y0 = frame(prs, "ARCHITECTURE 01",
                   "세 가지 원칙은 문서가 아니라 코드로 지킵니다",
                   "리뷰에서 이 셋을 깨는 변경은 막습니다.",
                   accent=LAV)
    w = (CW - 2 * 0.30) / 3
    ytop = y0 + 0.06
    hgt = 3.82

    # 1
    x = ML
    card(sl, x, ytop, w, hgt)
    box(sl, x, ytop, w, 0.055, fill=LAV)
    T(sl, x + 0.34, ytop + 0.38, w - 0.68, 0.26, "원칙 01", 10, LAV, True,
      "l", "t", spc=14)
    T(sl, x + 0.34, ytop + 0.70, w - 0.68, 0.34, "사람 최종 승인", 15, INK,
      True, "l", "t")
    T(sl, x + 0.34, ytop + 1.12, w - 0.68, 0.7,
      "AI 가 만든 태스크·일정·결정은 예외 없이 승인 대기 상태로 시작합니다. "
      "AI 는 후보만 만들고 확정은 사람이 합니다.", 10.5, MUTE, False, "l", "t", lh=1.55)
    yb = ytop + 2.00
    p1 = pill(sl, x + 0.34, yb, 1.42, 0.36, fill=SURF2)
    fit(sl, p1, "승인 대기", size=9.5, color=SLATE, bold=True)
    conn(sl, x + 1.84, yb + 0.18, x + 2.24, yb + 0.18, MINT, 1.6)
    p2 = pill(sl, x + 2.32, yb, 1.00, 0.36, fill=MINT)
    fit(sl, p2, "할 일", size=9.5, color=WHITE, bold=True)
    T(sl, x + 0.34, yb + 0.44, w - 0.68, 0.24,
      "전용 엔드포인트 approve 로만 이동", 9, MINT, True, "l", "t")
    b = card(sl, x + 0.34, yb + 0.80, w - 0.68, 0.72, fill=C("FBF2F6"),
             line=ROSE_L, shadow=False)
    T(sl, x + 0.52, yb + 0.92, w - 1.04, 0.26,
      "PATCH { \"status\": \"TODO\" }", 9.5, ROSE, True, "l", "t", font=FONT_MONO)
    T(sl, x + 0.52, yb + 1.16, w - 1.04, 0.26,
      "→ 400. 승인 단계를 건너뛸 수 없습니다", 9.5, INK2, False, "l", "t")

    # 2
    x = ML + w + 0.30
    card(sl, x, ytop, w, hgt)
    box(sl, x, ytop, w, 0.055, fill=BLUE)
    T(sl, x + 0.34, ytop + 0.38, w - 0.68, 0.26, "원칙 02", 10, BLUE, True,
      "l", "t", spc=14)
    T(sl, x + 0.34, ytop + 0.70, w - 0.68, 0.34, "서버 중심", 15, INK, True,
      "l", "t")
    T(sl, x + 0.34, ytop + 1.12, w - 0.68, 0.7,
      "AI 끼리 직접 통신하지 않습니다. 모든 AI↔AI 메시지는 서버를 거치며 "
      "추적 아이디와 홉 수로 무한 대화를 끊습니다.", 10.5, MUTE, False, "l", "t", lh=1.55)
    yb = ytop + 2.00
    for i, (lab, col) in enumerate((("대리인 A", BLUE_XL), ("서버", BLUE),
                                    ("대리인 B", BLUE_XL))):
        bx = x + 0.34 + i * 1.02
        p = pill(sl, bx, yb, 0.92, 0.36, fill=col)
        fit(sl, p, lab, size=8.5, color=(WHITE if i == 1 else BLUE), bold=True)
        if i < 2:
            conn(sl, bx + 0.94, yb + 0.18, bx + 1.00, yb + 0.18, SLATE_L, 1.4)
    T(sl, x + 0.34, yb + 0.44, w - 0.68, 0.24, "hop_count ≤ 3", 9, BLUE, True,
      "l", "t")
    b = card(sl, x + 0.34, yb + 0.80, w - 0.68, 0.72, fill=SURF, line=SURF,
             shadow=False)
    T(sl, x + 0.52, yb + 0.92, w - 1.04, 0.5,
      "외부 호출은 트랜잭션 밖에서 합니다. 롤백돼도 메시지는 이미 나가 있으니까요.",
      9.5, INK2, False, "l", "t", lh=1.5)

    # 3
    x = ML + 2 * (w + 0.30)
    card(sl, x, ytop, w, hgt)
    box(sl, x, ytop, w, 0.055, fill=MINT)
    T(sl, x + 0.34, ytop + 0.38, w - 0.68, 0.26, "원칙 03", 10, MINT, True,
      "l", "t", spc=14)
    T(sl, x + 0.34, ytop + 0.70, w - 0.68, 0.34, "자동 동기화 없음", 15, INK,
      True, "l", "t")
    T(sl, x + 0.34, ytop + 1.12, w - 0.68, 0.7,
      "파일 감시자를 두지 않습니다. 자동 병합은 금지이고 사람이 고르는 "
      "네 가지만 허용합니다.", 10.5, MUTE, False, "l", "t", lh=1.55)
    yb = ytop + 2.00
    opts = ["내 것 유지", "서버 것 유지", "둘 다 보관", "직접 병합"]
    cx = x + 0.34
    cy = yb
    for i, o in enumerate(opts):
        if i == 2:
            cx = x + 0.34
            cy = yb + 0.46
        wch = chip(sl, cx, cy, o, MINT, MINT_XL, size=9.5, h=0.36)
        cx += wch + 0.12
    b = card(sl, x + 0.34, yb + 1.02, w - 0.68, 0.70, fill=SURF, line=SURF,
             shadow=False)
    T(sl, x + 0.52, yb + 1.13, w - 1.04, 0.5,
      "말없이 합치면 무엇이 바뀌었는지 모른 채 다음 작업이 그 위에 쌓입니다.",
      9.5, INK2, False, "l", "t", lh=1.5)
    return sl


# ─────────────────────────────── 17 진입점 네 개
def s17(prs):
    sl, y0 = frame(prs, "ARCHITECTURE 02",
                   "부르는 쪽이 넷이라 문도 넷으로 나눴습니다",
                   "인증 방식이 다른 요청을 한 구역에 섞으면 하나가 뚫릴 때 전부 뚫립니다.",
                   accent=LAV)
    ytop = y0 + 0.14
    step = 0.86
    lanes = [("웹 프론트엔드", "화면에서 오는 요청", "/api/v1", "JWT", BLUE),
             ("Discord 봇", "회의 스레드 · 발송함", "/internal/v1", "서비스 토큰", MINT),
             ("개인 AI 도구", "Claude Code · Codex · Cursor", "/mcp", "brd_ 토큰", LAV),
             ("브라우저 실시간", "대리인 응답 스트리밍", "/ws", "JWT", ROSE)]
    cw_, pw = 2.42, 3.10
    px_ = ML + cw_ + 0.40
    for i, (nm, desc, path, auth, col) in enumerate(lanes):
        y = ytop + i * step
        card(sl, ML, y, cw_, 0.70, fill=WHITE)
        T(sl, ML + 0.22, y + 0.12, cw_ - 0.44, 0.26, nm, 10.5, INK, True, "l", "t")
        T(sl, ML + 0.22, y + 0.38, cw_ - 0.44, 0.24, desc, 8.5, MUTE, False,
          "l", "t")
        conn(sl, ML + cw_ + 0.06, y + 0.35, px_ - 0.06, y + 0.35, SLATE_L, 1.6)
        p = card(sl, px_, y, pw, 0.70, fill=WHITE, line=col, lw=1.4)
        T(sl, px_ + 0.24, y + 0.11, pw - 0.48, 0.28, path, 12, col, True,
          "l", "t", font=FONT_MONO)
        T(sl, px_ + 0.24, y + 0.40, pw - 0.48, 0.24, auth, 9, MUTE, False,
          "l", "t")
        conn(sl, px_ + pw + 0.06, y + 0.35, px_ + pw + 0.34, y + 0.35,
             SLATE_L, 1.6)

    cx = px_ + pw + 0.42
    cwd = W - MR - cx
    chgt = step * 3 + 0.70
    card(sl, cx, ytop, cwd, chgt, fill=SURF, line=C("DEE5EF"))
    T(sl, cx + 0.34, ytop + 0.26, cwd - 0.68, 0.3, "Django 5 · DRF", 14, INK,
      True, "l", "t")
    T(sl, cx + 0.34, ytop + 0.60, cwd - 0.68, 0.26,
      "뷰는 함수형. 권한은 공용 모듈 세 함수로만.", 9.5, MUTE, False, "l", "t")
    apps = ["accounts", "orgs", "meetings", "agent", "chat", "tasks",
            "calendars", "documents", "states", "discord", "mcp", "home",
            "common"]
    ax, ay = cx + 0.34, ytop + 0.98
    for a in apps:
        wch = chip(sl, ax, ay, a, SLATE, WHITE, size=8.5, h=0.30,
                   line=C("DEE5EF"))
        ax += wch + 0.10
        if ax > cx + cwd - 1.15:
            ax = cx + 0.34
            ay += 0.38
    hline(sl, cx + 0.34, ay + 0.58, cwd - 0.68, C("DEE5EF"))
    infra = [("PostgreSQL", "pgvector"), ("Celery", "대리인 실행"),
             ("Channels", "WebSocket")]
    for i, (nm, desc) in enumerate(infra):
        bx = cx + 0.34 + i * ((cwd - 0.68) / 3)
        T(sl, bx, ay + 0.74, (cwd - 0.68) / 3, 0.26, nm, 10.5, INK, True,
          "l", "t")
        T(sl, bx, ay + 1.00, (cwd - 0.68) / 3, 0.24, desc, 8.5, MUTE, False,
          "l", "t")

    y = ytop + chgt + 0.28
    facts = [("라우트", "131개"), ("API 오퍼레이션", "185개"),
             ("Django 앱", "13개"), ("API 계약", "OpenAPI 0.0.7")]
    x = ML
    fw = (CW - 3 * 0.24) / 4
    for nm, val in facts:
        b = card(sl, x, y, fw, 0.62, fill=WHITE, line=LINE, shadow=False)
        T(sl, x + 0.24, y + 0.17, fw * 0.55, 0.28, nm, 9.5, MUTE, False,
          "l", "t")
        T(sl, x + fw - 0.24 - fw * 0.55, y + 0.14, fw * 0.55, 0.32, val, 11.5,
          INK, True, "r", "t")
        x += fw + 0.24
    return sl


# ─────────────────────────────── 18 발송함
def s18(prs):
    sl, y0 = frame(prs, "ARCHITECTURE 03",
                   "회의 발언 한 줄이 대리인의 답으로 돌아오기까지",
                   "Discord 호출을 요청 트랜잭션 안에서 하지 않습니다. 롤백돼도 메시지는 이미 나가 있기 때문입니다.",
                   accent=LAV)
    ytop = y0 + 0.34
    inner = [("봇이 발언 전달", "/internal/v1"), ("발언 저장", "Utterance"),
             ("대리인 실행 등록", "Celery 태스크"), ("근거 수집 · 판정", "ReAct · POLICY"),
             ("발송함에 한 줄", "OutboxEvent · 대기")]
    gap = 0.22
    iw = (CW - 0.56 - 4 * gap) / 5
    tx = ML
    twd = CW
    b = shape(sl, MSO_SHAPE.ROUNDED_RECTANGLE, tx, ytop - 0.34, twd, 1.62,
              fill=LAV_XL, line=LAV_L, lw=1.0, radius=0.14)
    T(sl, tx + 0.28, ytop - 0.24, twd - 0.56, 0.24,
      "요청 트랜잭션 — 이 안에서는 Discord 를 부르지 않습니다", 9.5, LAV, True,
      "l", "t")
    for i, (nm, sub) in enumerate(inner):
        x = tx + 0.28 + i * (iw + gap)
        card(sl, x, ytop + 0.10, iw, 0.98, fill=WHITE, line=WHITE)
        T(sl, x + 0.16, ytop + 0.24, iw - 0.32, 0.28, nm, 10.5, INK, True,
          "c", "t")
        T(sl, x + 0.12, ytop + 0.54, iw - 0.24, 0.36, sub, 8.5, MUTE, False,
          "c", "t")
        if i < 4:
            chev(sl, x + iw + gap / 2, ytop + 0.59, C("CFC7EA"), 0.16)

    y = ytop + 1.62
    conn(sl, ML + CW - 1.30, y, ML + CW - 1.30, y + 0.40, SLATE_L, 1.6)
    T(sl, ML + CW - 4.6, y + 0.06, 3.2, 0.26, "커밋된 뒤에야", 9.5, MUTE,
      False, "r", "t")

    y2 = y + 0.52
    outer = [("봇이 가져감", "대기 중인 것만 폴링", MINT),
             ("Discord 게시", "채널에 대리인 발언", MINT),
             ("결과 회신", "성공 → 발송 완료", MINT),
             ("실패하면", "지수 백오프 · 상한 5회", ROSE),
             ("계속 실패", "DEAD · 사람이 확인", ROSE)]
    ow = (CW - 4 * gap) / 5
    for i, (nm, sub, col) in enumerate(outer):
        x = ML + i * (ow + gap)
        card(sl, x, y2, ow, 1.00, fill=WHITE, line=(MINT_L if col == MINT else ROSE_L),
             lw=1.2)
        T(sl, x + 0.16, y2 + 0.18, ow - 0.32, 0.28, nm, 10.5, INK, True, "c", "t")
        T(sl, x + 0.12, y2 + 0.50, ow - 0.24, 0.36, sub, 8.5, col, False,
          "c", "t")
        if i < 4:
            chev(sl, x + ow + gap / 2, y2 + 0.50, C("CBD5E3"), 0.16)

    y3 = y2 + 1.20
    card(sl, ML, y3, CW, 0.80, fill=SURF, line=SURF, shadow=False)
    TR(sl, ML + 0.36, y3 + 0.13, CW - 0.72, 0.56,
       [("같은 일이 두 번 나가지 않게 팀과 멱등 키를 함께 유니크로 묶습니다.  ", 11.5, INK, True),
        ("무한 재시도는 같은 오류를 반복하고, 그냥 버리면 사용자는 대리인이 말한 줄 알고 기다립니다. "
         "그래서 DEAD 는 사람이 봐야 하는 상태로 남깁니다.", 10.5, INK2, False)],
       anchor="m", lh=1.5)
    return sl


# ─────────────────────────────── 19 MCP
def s19(prs):
    sl, y0 = frame(prs, "ARCHITECTURE 04",
                   "따로 쓰게 하지 않습니다. 이미 남아 있는 기록을 씁니다",
                   "개인 AI 도구에서 오간 작업 맥락이 그대로 회의 발언의 근거가 됩니다.",
                   accent=LAV)
    chain = [("개인 AI 도구", "Claude Code · Codex · Cursor\n에서 오간 작업 대화", SURF2, SLATE),
             ("MCP 서버", "JSON-RPC · brd_ 토큰\n쓰기 도구 세 가지만", LAV_XL, LAV),
             ("사용자 승인", "작업 · 계획 · 생각\n각각 따로 켜고 끕니다", MINT_XL, MINT),
             ("서버 기록", "work · plan · thought\n문서 · 비밀키는 저장 전 마스킹", BLUE_XL, BLUE),
             ("회의 발언의 근거", "무엇을 보고 답했는지\nEvidence 로 함께 남습니다", BLUE, WHITE)]
    gap = 0.28
    cw_ = (CW - 4 * gap) / 5
    ytop = y0 + 0.10
    for i, (nm, desc, bg, fg) in enumerate(chain):
        x = ML + i * (cw_ + gap)
        hot = i == 4
        card(sl, x, ytop, cw_, 1.72, fill=bg, line=(bg if bg != SURF2 else SURF2),
             shadow=hot)
        T(sl, x + 0.22, ytop + 0.30, cw_ - 0.44, 0.3, nm, 12,
          (WHITE if hot else INK), True, "c", "t")
        T(sl, x + 0.16, ytop + 0.72, cw_ - 0.32, 0.8, desc, 9.5,
          (C("D6E4F8") if hot else MUTE), False, "c", "t", lh=1.5)
        if i < 4:
            chev(sl, x + cw_ + gap / 2, ytop + 0.86, C("CBD5E3"), 0.17)

    y = ytop + 2.02
    tools = [("bordo_record_work", "지금 무엇을 하고 있는지 남깁니다"),
             ("bordo_upload_document", "설계·결정 문서를 올립니다"),
             ("bordo_complete_work", "끝난 작업을 닫습니다")]
    T(sl, ML, y, 4.0, 0.28, "쓰기 도구 세 가지", 12, INK, True, "l", "t")
    for i, (nm, desc) in enumerate(tools):
        yy = y + 0.36 + i * 0.52
        p = pill(sl, ML, yy, 2.42, 0.38, fill=LAV_XL)
        fit(sl, p, nm, size=9, color=LAV, bold=True, font=FONT_MONO)
        T(sl, ML + 2.60, yy + 0.07, 3.4, 0.26, desc, 10, MUTE, False, "l", "t")

    rx = ML + 6.30
    rw = CW - 6.30
    cards = [("읽기 도구를 두지 않았습니다",
              "장기 토큰이 새도 가져갈 것이 없어야 합니다. 읽기는 토큰 만료와 함께 붙입니다.", ROSE),
             ("기록을 새로 쓰게 하지 않습니다",
              "커밋과 세션 기록에서 뽑아 두고 승인만 요청합니다.", MINT)]
    for i, (nm, desc, col) in enumerate(cards):
        yy = y + i * 0.94
        card(sl, rx, yy, rw, 0.82, fill=WHITE, line=LINE)
        box(sl, rx, yy, 0.05, 0.82, fill=col)
        T(sl, rx + 0.28, yy + 0.12, rw - 0.56, 0.26, nm, 11, INK, True, "l", "t")
        T(sl, rx + 0.28, yy + 0.40, rw - 0.56, 0.36, desc, 9.5, MUTE, False,
          "l", "t", lh=1.45)
    return sl


# ─────────────────────────────── 20 차별점
def s20(prs):
    sl, y0 = frame(prs, "DIFFERENTIATION",
                   "회의를 기록하는 AI는 많지만 대신 말하는 AI는 없습니다",
                   accent=MINT)
    cols = [("회의 기록 · 요약형", "Otter · Fireflies · Sembly"),
            ("아바타 참석형", "Zoom AI Companion"),
            ("업무 대행형", "DelegateWorker"),
            ("Bordo", "개인 AI 대리인")]
    rows = [("개입 시점", ["회의 종료 후", "회의 중 (참석 자체)", "회의 밖 단순 업무",
                        "회의 전 · 중 · 후 · 이후"]),
            ("불참자 의견", ["미반영", "음성·외형만 재현", "해당 없음",
                         "대리인이 대신 발화"]),
            ("결과물", ["모두에게 같은 요약", "참석 흔적", "처리 결과 보고",
                     "직무·언어별 브리핑"]),
            ("모를 때", ["그럴듯하게 채움", "해당 없음", "실패 처리",
                      "유보 — 코드 규칙이 판정"]),
            ("근거 데이터", ["회의 녹취", "사용자 음성·외형", "지시받은 업무",
                        "MCP 로 쌓인 개인 작업 기록"])]
    lw_ = 1.96
    cwd = (CW - lw_) / 4
    ytop = y0 + 0.10
    hh, rh = 0.76, 0.60
    bx = ML + lw_ + 3 * cwd
    card(sl, bx - 0.06, ytop - 0.10, cwd + 0.12, hh + 5 * rh + 0.20,
         fill=MINT_XL, line=MINT_L, radius=0.14)
    for i, (nm, sub) in enumerate(cols):
        x = ML + lw_ + i * cwd
        last = i == 3
        T(sl, x + 0.12, ytop + 0.10, cwd - 0.24, 0.28, nm, 11.5,
          (MINT if last else INK), True, "l", "t")
        T(sl, x + 0.12, ytop + 0.40, cwd - 0.24, 0.26, sub, 8.5,
          (MINT if last else FAINT), False, "l", "t")
    hline(sl, ML, ytop + hh, CW, C("D8E0EA"), 1.2)
    for r, (label, vals) in enumerate(rows):
        y = ytop + hh + r * rh
        T(sl, ML, y + 0.18, lw_ - 0.2, 0.28, label, 10.5, SLATE, True, "l", "t")
        for i, v in enumerate(vals):
            x = ML + lw_ + i * cwd
            last = i == 3
            T(sl, x + 0.12, y + 0.17, cwd - 0.24, 0.34, v, 10,
              (INK if last else MUTE), last, "l", "t", lh=1.3)
        if r < 4:
            hline(sl, ML, y + rh, CW, LINE)

    y = ytop + hh + 5 * rh + 0.34
    card(sl, ML, y, CW, 0.78, fill=SURF, line=SURF, shadow=False)
    T(sl, ML + 0.36, y + 0.22, CW - 0.72, 0.34,
      "근거가 개인 작업 도구에 쌓입니다. 오래 쓸수록 정확해지고, 그만큼 옮겨 가는 비용도 올라갑니다.",
      12, INK, True, "l", "t")
    return sl


# ─────────────────────────────── 21 시장
def s21(prs):
    sl, y0 = frame(prs, "MARKET",
                   "시차를 안고 일하는 IT 프로젝트 팀부터 시작합니다",
                   accent=MINT)
    lw_ = 5.30
    card(sl, ML, y0 + 0.06, lw_, 4.04)
    T(sl, ML + 0.36, y0 + 0.34, lw_ - 0.72, 0.3, "시장 규모", 13.5, INK, True,
      "l", "t")
    T(sl, ML + lw_ - 3.0, y0 + 0.42, 2.64, 0.24,
      "막대 길이는 비율이 아니라 단계 도식", 8.5, FAINT, False, "r", "t")
    tiers = [("TAM", "182억 달러", "글로벌 협업 소프트웨어 시장 2024 · 연 7.7% 성장", 1.00, SLATE_XL, SLATE),
             ("SAM", "52.8억 달러", "IT · 통신 산업 비중 29%", 0.72, MINT_XL, MINT),
             ("SOM", "1.06억 달러", "보수적으로 잡은 2%", 0.44, MINT, WHITE)]
    yy = y0 + 0.86
    for nm, val, desc, frac, bg, fg in tiers:
        bw = (lw_ - 0.72) * frac
        b = shape(sl, MSO_SHAPE.ROUNDED_RECTANGLE, ML + 0.36, yy, bw, 0.68,
                  fill=bg, line=None, radius=0.10)
        T(sl, ML + 0.54, yy + 0.06, 1.0, 0.3, nm, 11, fg, True, "l", "t", spc=10)
        T(sl, ML + 0.54, yy + 0.34, bw - 0.36, 0.28, val, 13, fg, True, "l", "t")
        T(sl, ML + 0.36, yy + 0.74, lw_ - 0.72, 0.26, desc, 9, MUTE, False,
          "l", "t")
        yy += 1.02

    rx = ML + lw_ + 0.30
    rw = CW - lw_ - 0.30
    targets = [("1차", "글로벌 IT 프로젝트 팀", MINT,
                "서로 다른 시간대에서 비정형 업무를 하는 개발자 · 디자이너 · PM",
                ["비정형 업무일수록 협업 비중이 높습니다",
                 "IT · 통신이 2024년 협업 SW 시장의 29%",
                 "전원 참석을 위해 근무 외 시간을 쓰고, 다음 날 성과까지 떨어집니다"]),
               ("2차", "팀 프로젝트를 운영하는 기관", BLUE,
                "부트캠프 · 교육 프로그램 · 해커톤 · 대학 캡스톤",
                ["학생이 겪은 어려움 중 팀 협업 73.4%, 원격·하이브리드 협업 67.7%",
                 "계약 단위가 작고 도입 결정이 빨라 초기 사례를 얻기 좋습니다"])]
    yy = y0 + 0.06
    for tag, nm, col, sub, bullets in targets:
        hgt = 2.06 if tag == "1차" else 1.74
        card(sl, rx, yy, rw, hgt)
        box(sl, rx, yy, 0.05, hgt, fill=col)
        p = pill(sl, rx + 0.32, yy + 0.26, 0.62, 0.30, fill=col)
        fit(sl, p, tag, size=9, color=WHITE, bold=True)
        T(sl, rx + 1.06, yy + 0.26, rw - 1.4, 0.3, nm, 13.5, INK, True, "l", "t")
        T(sl, rx + 0.32, yy + 0.66, rw - 0.64, 0.26, sub, 10, MUTE, False,
          "l", "t")
        for i, bl in enumerate(bullets):
            by = yy + 0.98 + i * 0.32
            oval(sl, rx + 0.34, by + 0.09, 0.08, 0.08, fill=col)
            T(sl, rx + 0.56, by, rw - 0.9, 0.28, bl, 9.5, INK2, False, "l", "t")
        yy += hgt + 0.24
    source(sl, "Global Market Insights 2024 · OECD · Quan et al. 2025 · Microsoft Work Trend Index 2023")
    return sl


# ─────────────────────────────── 22 수익 모델
def s22(prs):
    sl, y0 = frame(prs, "BUSINESS MODEL",
                   "팀 단위로 작동하니 팀 단위로 팝니다",
                   accent=MINT)
    plans = [("기업", "Seat based SaaS", MINT, True,
              ["기본료 + 활성 사용자당 구독료", "사용량 기반 AI 크레딧 결합",
               "핵심 수익원"]),
             ("교육 프로그램", "Cohort License", BLUE, False,
              ["기수 인원 × 기간", "기수 종료 시 정산 완료",
               "초기 사용 사례 확보"]),
             ("해커톤 · 단기 프로젝트", "Event License", LAV, False,
              ["참가자 수 × 행사 기간", "행사 단위 계약",
               "도입 결정이 빠릅니다"])]
    w = (CW - 2 * 0.30) / 3
    ytop = y0 + 0.06
    for i, (who, nm, col, hot, items) in enumerate(plans):
        x = ML + i * (w + 0.30)
        card(sl, x, ytop, w, 2.86, fill=(MINT_XL if hot else WHITE),
             line=(MINT_L if hot else LINE))
        box(sl, x, ytop, w, 0.055, fill=col)
        T(sl, x + 0.36, ytop + 0.36, w - 0.72, 0.26, who, 10, col, True,
          "l", "t", spc=12)
        T(sl, x + 0.36, ytop + 0.66, w - 0.72, 0.36, nm, 16, INK, True, "l", "t")
        hline(sl, x + 0.36, ytop + 1.16, w - 0.72, (MINT_L if hot else LINE))
        for j, it in enumerate(items):
            yy = ytop + 1.34 + j * 0.44
            oval(sl, x + 0.38, yy + 0.10, 0.09, 0.09, fill=col)
            T(sl, x + 0.62, yy, w - 1.0, 0.34, it, 10.5,
              (INK if j == 2 else INK2), (j == 2), "l", "t")

    y = ytop + 3.12
    T(sl, ML, y, CW, 0.3, "개인 판매를 하지 않는 이유", 12.5, INK, True, "l", "t")
    reasons = [("팀 전원이 연결돼야 가치가 생깁니다",
                "한 명이 빠지면 그 자리는 여전히 공백입니다"),
               ("회의마다 검색과 추론을 반복해 원가가 큽니다",
                "팀 단위 도구에는 팀 단위 구매 주체가 필요합니다")]
    rw = (CW - 0.30) / 2
    for i, (nm, desc) in enumerate(reasons):
        x = ML + i * (rw + 0.30)
        card(sl, x, y + 0.38, rw, 0.78, fill=SURF, line=SURF, shadow=False)
        T(sl, x + 0.28, y + 0.50, rw - 0.56, 0.28, nm, 11, INK, True, "l", "t")
        T(sl, x + 0.28, y + 0.78, rw - 0.56, 0.26, desc, 9.5, MUTE, False,
          "l", "t")
    return sl


# ─────────────────────────────── 23 로드맵
def s23(prs):
    sl, y0 = frame(prs, "EXECUTION",
                   "1단계는 이미 동작합니다",
                   "지금 시연하는 화면은 전부 붙어 있는 코드에서 나옵니다.",
                   accent=MINT)
    phases = [("1단계 · MVP", "동작합니다", MINT, True,
               ["회의 시작 · 종료와 대리 참석 등록", "대리 발언과 유보 (POLICY · ReAct)",
                "개인화 브리핑 네 섹션", "플로우 그래프 조회 · 생성",
                "MCP 쓰기 3종 · Discord · WebSocket"]),
              ("2단계", "다음", BLUE, False,
               ["MCP 읽기 도구와 토큰 만료", "로컬 문서 동기화 네 가지",
                "대리인 간 질의 확장", "문서 임베딩 검색", "일일 채팅 요약 생성기"]),
              ("3단계", "이후", LAV, False,
               ["음성 회의 전사", "대리인 답변 평가", "팀 단위 협업 분석",
                "말하는 방식의 차이 — 문화 경계"])]
    w = (CW - 2 * 0.30) / 3
    ytop = y0 + 0.06
    for i, (nm, tag, col, hot, items) in enumerate(phases):
        x = ML + i * (w + 0.30)
        card(sl, x, ytop, w, 3.00, fill=(MINT_XL if hot else WHITE),
             line=(MINT_L if hot else LINE))
        T(sl, x + 0.34, ytop + 0.32, w - 0.68, 0.3, nm, 13.5, INK, True, "l", "t")
        p = pill(sl, x + w - 1.24, ytop + 0.30, 0.90, 0.30, fill=col)
        fit(sl, p, tag, size=9, color=WHITE, bold=True)
        hline(sl, x + 0.34, ytop + 0.78, w - 0.68, (MINT_L if hot else LINE))
        for j, it in enumerate(items):
            yy = ytop + 0.94 + j * 0.40
            if hot:
                T(sl, x + 0.34, yy, 0.2, 0.28, "✓", 10, col, True, "l", "t")
            else:
                oval(sl, x + 0.36, yy + 0.10, 0.08, 0.08, fill=col)
            T(sl, x + 0.60, yy, w - 0.94, 0.36, it, 10, INK2, False, "l", "t",
              lh=1.3)

    y = ytop + 3.20
    T(sl, ML, y, CW, 0.3, "역할 경계와 시스템 경계를 맞췄습니다", 12.5, INK,
      True, "l", "t")
    team = [("유수인", "디자인"), ("임수연", "프론트엔드 · 실시간"),
            ("서재민", "기획 · 대리인 엔진"), ("최비성", "MCP · 인프라"),
            ("강다은", "Discord 봇")]
    x = ML
    for nm, role in team:
        wch = 2.24
        card(sl, x, y + 0.38, wch, 0.62, fill=SURF, line=SURF, shadow=False)
        T(sl, x + 0.22, y + 0.46, wch - 0.44, 0.26, nm, 10.5, INK, True, "l", "t")
        T(sl, x + 0.22, y + 0.70, wch - 0.44, 0.24, role, 9, MUTE, False,
          "l", "t")
        x += wch + 0.20
    return sl


# ─────────────────────────────── 24 클로징
def s24(prs):
    sl = blank(prs)
    PAGE["n"] += 1
    box(sl, 0, 0, W, H, fill=SURF)
    box(sl, 0, 0, 0.085, H, fill=BLUE)
    oval(sl, 9.3, 0.9, 4.9, 4.9, fill=BLUE_XL)
    oval(sl, -1.2, 4.6, 3.4, 3.4, fill=MINT_XL)

    T(sl, ML, 2.16, 9.6, 1.5,
      "시차는 없앨 수 없습니다.\n시차가 만든 공백은 없앨 수 있습니다.",
      30, INK, True, "l", "t", lh=1.42)
    hline(sl, ML, 4.06, 1.3, BLUE, 2.2)
    T(sl, ML, 4.36, 8.6, 0.8,
      "허용한 범위 안에서만 말하고, 판단할 수 없는 것은 사람에게 돌려줍니다.\n"
      "대신 판단하는 AI 가 아니라, 부재한 시간의 맥락을 잇는 AI 대리인입니다.",
      12.5, INK2, False, "l", "t", lh=1.6)

    T(sl, ML, 5.72, 6.0, 0.7, "Bordo", 40, INK, True, "l", "t", spc=-14)
    T(sl, ML + 2.3, 6.02, 5.0, 0.3, "BORDER TO ZERO", 11, BLUE, True, "l",
      "t", spc=26)
    T(sl, W - MR - 4.0, 6.10, 4.0, 0.26,
      "국민대학교 · 유수인 임수연 서재민 최비성 강다은", 10, MUTE, False, "r", "t")
    T(sl, W - MR - 1.2, H - 0.50, 1.2, 0.22, "%02d" % PAGE["n"], 9, FAINT,
      True, "r", "t", spc=20)
    return sl
