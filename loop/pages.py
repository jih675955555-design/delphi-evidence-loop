"""Pages — overview, collection, notes, claims, hypotheses, one hypothesis, checklist. Same renderers serve the
web console (with controls) and the static report (without)."""
from __future__ import annotations

import json

from . import store
from .ui import SIGNAL_KO, button, chip, esc, highlight, label_chip, shell, signal, status_chip, tag

STANCE_ORDER = ("SUPPORTS", "CONTRADICTS", "NEUTRAL")


def load_notes() -> list[dict]:
    return json.loads(store.FIELD_NOTES.read_text())


def demo_card(state: dict, contract: dict, notes_n: int, web: bool = True) -> str:
    """What this demo is, what gets extracted, and where to click — for a reader who arrives cold."""
    segs = " · ".join(esc(s) for s in contract["segments"])
    sigs = "".join(f'<li><b>{esc(code)}</b> — {esc(ko)}</li>' for code, ko in SIGNAL_KO.items())
    notes_link = '<a href="/notes">면담 기록 보기</a>' if web else '<a href="#notes">면담 기록 보기</a>'
    hcps_n = len({n["hcp_ref"] for n in load_notes()})
    collect_link = '<a href="/collect">현장 수집</a>' if web else "현장 수집(웹 콘솔)"
    steps = ("<ol class=\"steps\">"
             f"<li>{notes_link} — 무엇이 입력인지 먼저 읽는다 (합성 {notes_n}건, 한국어). 새 면담은 {collect_link}에서 대본을 듣고 받아 적어 넣는다</li>"
             f"<li><b>① 추출 실행</b> — 발언 카드가 생기고(지금 {len(state['claims'])}장), 인용마다 원문 위치가 붙는다</li>"
             "<li><b>가설 하나</b>를 연다 — 예: 유방암 환자가 보조요법으로 요청하는 신호(허가 밖 수요)</li>"
             "<li><b>② 근거 교차검증</b> — PubMed·CT.gov·라벨을 읽고 지지/반대/중립을 인용과 함께 표시</li>"
             "<li><b>③ 서명</b> — 근거를 읽었다고 이름으로 서명해야 심의로 간다 (사람 관문)</li>"
             "<li><b>④ 심의 → ⑤ 결정</b> — 임원 에이전트 7인이 토론하고 간사가 회의록을 쓴다. 결정하면 후속 질문이 체크리스트에 들어간다</li></ol>")
    return (f'<div class="card"><b>데모 안내</b> <span class="sub">— 의료진 면담 기록을 환자군 × 신호 유형으로 세고, 문턱을 넘은 조합을 공개 근거로 검증한 뒤, 사람이 서명·결정한 후속 질문을 다음 면담으로 보내는 과정을 실행해 볼 수 있다.</span>'
            f'<div class="grid" style="grid-template-columns:1fr 1fr;margin-top:10px">'
            f'<div><div class="eyebrow">입력</div>{esc(contract["drug_ko"])}에 관한 <b>합성 면담 기록 {notes_n}건</b> (가상 의료진 {hcps_n}인 · 한국어 · 실제 인물·기관 없음). {notes_link}.'
            f'<div class="eyebrow" style="margin-top:10px">무엇을 뽑나 — 사람이 정한 고정 헤더</div><b>환자군 {len(contract["segments"])}</b>: {segs}<br><b>신호 유형 {len(SIGNAL_KO)}</b>:<ul style="margin:4px 0 0">{sigs}</ul>'
            f'<div class="faint" style="margin-top:6px">「써봤다」와 「막혔다」는 규제상 다른 신호이므로 구분한다. 유해사례로 읽히는 발언은 별도 경로로 보낸다.</div></div>'
            f'<div><div class="eyebrow">보는 순서 (5분)</div>{steps}'
            f'<div class="faint" style="margin-top:8px">같은 입력은 캐시에서 즉시 재생된다. 새 이름으로 서명해 심의하면 그때만 Nemotron이 실제로 돈다(임원 7인 병렬, 약 5분).</div>'
            f'<div class="faint" style="margin-top:4px"><b>확인할 것</b>: 유방암 × 허가 밖 수요 가설에서 대규모 3상(MA.32, n=3,649) 무효 결과가 <span class="chip oppose">반대</span>로 표시되고, 심의 기록에서 임원들이 이를 인용한다.</div></div></div></div>')


# ── shared pieces ──────────────────────────────────────────────────────────────

def next_step(state: dict, h: dict) -> tuple[str, str]:
    """(who, what) — whose turn it is for this hypothesis."""
    st = h["status"]
    if st == "DRAFT":
        return "agent", "근거 교차검증"
    if st == "SCREENED":
        return "human", "근거 검토 서명"
    if st == "REVIEWED":
        return "agent", "심의"
    if st == "DELIBERATED":
        return "human", "결정"
    return "done", "체크리스트 반영됨"


def journey(state: dict, h: dict, web: bool = True) -> str:
    """신호의 여정 — one hypothesis from field statements to the checklist, with who and when at each step."""
    hid = h["id"]
    s, rev, memo = state["screens"].get(hid), state["reviews"].get(hid), state["board"].get(hid)
    dec = (memo or {}).get("decision")
    acts = [a for a in state["actions"] if a["hypothesis_id"] == hid]
    link = (lambda href, t: f'<a href="{href}">{t}</a>') if web else (lambda href, t: t)
    steps = [
        ("현장 발언", f'{h["field"]["mentions"]}회 · {h["field"]["hcps"]}인', "코드 집계", "done", None),
        ("가설", "DRAFT", h["created_at"][:16].replace("T", " "), "done", None),
        ("외부 근거", (f'지지 {s["totals"]["SUPPORTS"]} · 반대 {s["totals"]["CONTRADICTS"]} · 중립 {s["totals"]["NEUTRAL"]}' if s else "대기"),
         (s["ran_at"][:16].replace("T", " ") if s else "에이전트 차례"), "done" if s else "next", None),
        ("서명 · 관문 ①", (rev["by"] if rev else "대기"), (rev["at"][:16].replace("T", " ") if rev else "사람 차례"), "done" if rev else ("human" if s else "later"), None),
        ("심의 · AI Board", (f'{len(memo["transcript"])}턴 → {memo["recommendation"]}' if memo and "transcript" in memo else (memo["recommendation"] if memo else "대기")),
         (memo["deliberated_at"][:16].replace("T", " ") if memo else ("에이전트 차례" if rev else "")), "done" if memo else ("next" if rev else "later"),
         f"/hypotheses/{hid}/board" if memo and web else None),
        ("결정 · 관문 ②", (dec["by"] if dec else "대기"), (dec["at"][:16].replace("T", " ") if dec else ("사람 차례" if memo else "")), "done" if dec else ("human" if memo else "later"), None),
        ("체크리스트", f"{len(acts)}건" if acts else "—", "다음 면담이 참조" if acts else "", "done" if acts else "later", "/checklist" if acts and web else None),
    ]
    cells = []
    for name, value, when, cls, href in steps:
        v = link(href, esc(value)) if href else esc(value)
        cells.append(f'<div class="{cls}"><div class="k">{esc(name)}</div><b>{v}</b><div class="s">{esc(when)}</div></div>')
    return f'<div class="eyebrow" style="margin-top:14px">신호의 여정</div><div class="strip j">{"".join(cells)}</div>'


def controls_for(state: dict, h: dict) -> str:
    hid, st = h["id"], h["status"]
    if st == "DRAFT":
        return button("② 근거 교차검증 실행", "/run/screen", {"hyp": hid})
    if st == "SCREENED":
        return button("③ 외부 근거를 직접 검토했습니다 — 서명", "/run/review", {"hyp": hid},
                      [("by", "검토자 이름"), ("note", "메모 (선택)")], turn=True)
    if st == "REVIEWED":
        return button("④ 심의 실행 (AI Board · 임원 7인 + 간사)", "/run/board", {"hyp": hid})
    if st == "DELIBERATED":
        return button("⑤ 권고를 받아들입니다 — 결정", "/run/approve", {"hyp": hid}, [("by", "결정자 이름")], turn=True)
    return '<span class="faint">완료 — 질문이 다음 면담 체크리스트에 내려갔다</span>'


def tally_chips(s: dict | None) -> str:
    if not s:
        return '<span class="faint">아직 근거 없음</span>'
    t = s["totals"]
    return (f'<span class="chip support">지지 {t["SUPPORTS"]}</span><span class="chip oppose">반대 {t["CONTRADICTS"]}</span>'
            f'<span class="chip hold">중립 {t["NEUTRAL"]}</span><span class="faint">버림 {len(s["dropped"])}</span>')


def band(state: dict) -> str:
    """The processing line, compact — on top of every console page."""
    return pipeline_strip(state, len(load_notes()), mini=True)


def pipeline_strip(state: dict, notes_n: int, mini: bool = False, contract: dict | None = None) -> str:
    contract = contract or store.contract()
    claims = state["claims"]
    verified = sum(c["verified"] for c in claims)
    hyps = state["hypotheses"]
    human_turn = [h for h in hyps if next_step(state, h)[0] == "human"]
    cells = [
        ("면담 기록", notes_n, "합성 · 한국어", False),
        ("발언 카드", len(claims), f"원문 검증 {verified}/{len(claims)} · 유해사례 {len(state['safety_queue'])} 분리", False),
        ("가설", len(hyps), f"문턱 {contract['threshold']['min_mentions']}회·{contract['threshold']['min_hcps']}인 (코드)", False),
        ("외부 근거", len(state["screens"]), "PubMed · CT.gov · 라벨 · FAERS · Part D", False),
        ("서명 · 심의", f"{len(state['reviews'])} · {len(state['board'])}", f"사람 차례 {len(human_turn)}건" if human_turn else "관문 대기 없음", bool(human_turn)),
        ("체크리스트", len(state["actions"]), "다음 면담이 참조", False),
    ]
    if mini:
        return '<div class="strip mini">' + "".join(
            f'<div class="{"turn" if turn else ""}"><span class="k">{esc(k)}</span><b>{esc(v)}</b></div>'
            for k, v, s, turn in cells) + "</div>"
    return '<div class="strip">' + "".join(
        f'<div class="{"turn" if turn else ""}"><div class="k">{esc(k)}</div><b>{esc(v)}</b><div class="s">{esc(s)}</div></div>'
        for k, v, s, turn in cells) + "</div>"


def signal_map(state: dict, contract: dict, web: bool = True) -> str:
    """Segment × signal tiles — width is mentions, tint is distinct HCPs, dashed is below threshold.
    Big-but-pale tiles are one or two people repeating themselves: the map exists to show that difference."""
    from .sense import tally
    rows = tally(state)
    if not rows:
        return '<p class="sub">아직 신호가 없다 — 추출을 실행하면 채워진다.</p>'
    thr = contract["threshold"]
    max_h = max(r["hcps"] for r in rows)
    hyp_by = {(h["segment"], h["signal_type"]): h["id"] for h in state["hypotheses"]}
    by_seg: dict[str, list] = {}
    for r in rows:
        by_seg.setdefault(r["segment"], []).append(r)
    out = ['<div class="smap">']
    for seg in contract["segments"]:
        cells = by_seg.get(seg)
        if not cells:
            continue
        tiles = []
        for c in sorted(cells, key=lambda x: -x["mentions"]):
            passed = c["mentions"] >= thr["min_mentions"] and c["hcps"] >= thr["min_hcps"]
            op = 0.45 + 0.55 * (c["hcps"] / max_h)
            label = f'<b>{c["mentions"]}</b>회 · {c["hcps"]}인 · {esc(SIGNAL_KO.get(c["signal_type"], c["signal_type"]))}'
            title = f'{seg} × {c["signal_type"]} — {c["mentions"]}회 · 독립 의료진 {c["hcps"]}인 · ' + ("문턱 충족" if passed else f'문턱 미달 (기준 {thr["min_mentions"]}회·{thr["min_hcps"]}인)')
            hid = hyp_by.get((seg, c["signal_type"]))
            style = f'flex:{c["mentions"]} 1 0;' + (f"opacity:{op:.2f}" if passed else "")
            if hid and web:
                tiles.append(f'<a class="tile" href="/hypotheses/{hid}" style="{style}" title="{esc(title)}">{label}</a>')
            else:
                tiles.append(f'<span class="tile{"" if passed else " below"}" style="{style}" title="{esc(title)}">{label}</span>')
        out.append(f'<div class="srow"><div class="seg">{esc(seg)}</div>{"".join(tiles)}</div>')
    out.append("</div>")
    near = [r for r in rows if not (r["mentions"] >= thr["min_mentions"] and r["hcps"] >= thr["min_hcps"])
            and (r["mentions"] >= thr["min_mentions"] - 1 or r["hcps"] >= thr["min_hcps"] - 1)]
    if near:
        out.append('<div class="faint" style="margin-top:8px">' + tag("pattern") + '<b>임계 근접</b> — ' +
                   " · ".join(f'{esc(r["segment"])} × {esc(SIGNAL_KO.get(r["signal_type"], r["signal_type"]))} {r["mentions"]}회/{r["hcps"]}인' for r in near) +
                   f'. 한 사람만 더 말하면 가설이 된다 — 다음 면담이 물어볼 자리.</div>')
    return "".join(out)


def hyp_table(state: dict, link: bool = True) -> str:
    rows = []
    for h in state["hypotheses"]:
        s = state["screens"].get(h["id"])
        who, what = next_step(state, h)
        turn = f'<span class="chip turn">사람 차례 · {what}</span>' if who == "human" else (f'<span class="faint">{what}</span>' if who == "agent" else f'<span class="faint">{what}</span>')
        name = f'<a href="/hypotheses/{h["id"]}"><b>{esc(h["id"])}</b></a>' if link else f'<a href="#{h["id"]}"><b>{esc(h["id"])}</b></a>'
        rows.append(f'<tr><td class="n">{name}<br>{status_chip(h["status"])}</td>'
                    f'<td>{esc(h["segment"])} × {esc(h["signal_type"])}<br><span class="faint">{esc(h["statement_ko"][:80])}{"…" if len(h["statement_ko"]) > 80 else ""}</span></td>'
                    f'<td class="n">{h["field"]["mentions"]}회 / {h["field"]["hcps"]}인</td><td>{tally_chips(s)}</td><td>{label_chip(h["label_status"])}</td><td>{turn}</td></tr>')
    if not rows:
        return '<p class="sub">아직 가설이 없다 — 추출을 실행하면 문턱을 넘은 묶음이 가설이 된다.</p>'
    return ('<table><tr><th>가설</th><th>환자군 × 신호 유형</th><th>현장</th><th>외부 근거 (코드 집계)</th><th>라벨</th><th>다음</th></tr>'
            + "".join(rows) + "</table>")


# ── pages ─────────────────────────────────────────────────────────────────────

def overview(state: dict, contract: dict, banner: str = "", web: bool = True) -> str:
    notes_n = len(load_notes())
    body = [f'<div class="eyebrow">{esc(contract["drug_ko"])} · 계약 {esc(contract["version"])} · 환자군 {len(contract["segments"])} · 신호 유형 {len(contract["signal_types"])}</div>',
            '<h1>처리 현황</h1>',
            '<p class="sub">면담 기록에서 다음 면담 체크리스트까지의 처리 단계와 건수. 모든 면담 기록은 합성이다.</p>',
            pipeline_strip(state, notes_n), demo_card(state, contract, notes_n, web)]
    if web:
        body.append('<div class="card"><div class="row"><div class="grow"><b>① 추출</b> <span class="sub">면담 기록을 Nemotron이 읽고 환자군 × 신호 유형에 해당하는 발언을 원문 그대로 인용한다. 코드가 인용을 원문에서 찾아 검증하고 세어, 문턱을 넘은 조합을 가설로 만든다.</span></div>'
                    + button("① 추출 실행", "/run/sense") + '</div>'
                    '<div class="row"><div class="grow faint">결과만 지우고 처음부터 (캐시는 유지)</div>' + button("초기화", "/run/reset", ghost=True) + '</div></div>')
    body.append('<h2>신호 지도</h2><p class="sub">' + tag("pattern") + '칸 크기는 <b>반복 횟수</b>, 진하기는 <b>독립 의료진 수</b>다. 가설이 되려면 둘 다 문턱을 넘어야 한다. '
                '크지만 옅은 칸은 한두 사람이 반복한 것이다. 점선은 문턱 미달. 칸을 누르면 가설로 이동한다.</p>')
    body.append(signal_map(state, contract, web))
    body.append("<h2>가설</h2>")
    body.append(hyp_table(state))
    sq = state["safety_queue"]
    if sq:
        body.append(f'<div class="card" style="border-left:4px solid var(--rust)">{tag("fact")}<b>유해사례 후보 {len(sq)}건</b>이 safety 큐에 있다 — 분석 집계에 섞이지 않고 별도 경로로만 간다. <a href="/claims#safety">보기</a></div>')
    return shell("개요", "\n".join(body), "/console", banner)


def origin(n: dict) -> str:
    """Where a note's text came from — typed synthetic text, a script read by TTS and heard by STT, or a recording."""
    t = n.get("stt")
    if t and t.get("script_id"):
        tts_name = "Chatterbox" if "chatterbox" in ((t.get("tts") or {}).get("model") or "") else "TTS"
        cer = f' · CER {round(t["cer"] * 100, 1):.1f}%' if isinstance(t.get("cer"), (int, float)) else ""
        return (f'<span class="chip st">대본 수집 · {esc(t["script_id"])} · {tts_name} → {esc((t.get("engine") or "").capitalize())} · '
                f'{t["duration_s"]}초{cer}</span>')
    return f'<span class="chip st">음성 전사 · {esc(t["model"])} · {t["duration_s"]}초</span>' if t else "합성"


UPLOAD_FORM = (
    '<form method="post" action="/run/transcribe" enctype="multipart/form-data" class="card">'
    '<b>면담 음성 올리기</b> — NVIDIA 호스팅 ASR 이 한국어로 전사해 면담 기록 한 건으로 넣는다. 음성 파일은 저장하지 않고 해시만 남긴다.<br>'
    '<input type="file" name="audio" accept="audio/*,.wav,.m4a,.mp3,.ogg,.opus,.flac" required> '
    '<input type="text" name="hcp" placeholder="의료진 (예: HCP-13)" required> '
    '<input type="text" name="specialty" placeholder="전문과 · 기관" required> '
    '<input type="date" name="date" required> '
    '<input type="text" name="consent_by" placeholder="녹음 동의 확인자 이름" required> '
    '<select name="engine"><option value="parakeet">Parakeet 1.1B 다국어 (NVIDIA · 기본)</option>'
    '<option value="whisper">Whisper Large v3 (OpenAI)</option></select> '
    '<button class="btn">전사해서 넣기</button></form>')


def note_cards(state: dict, notes: list[dict], web: bool = True) -> str:
    """Each field note with its verified claims highlighted in place — the evidence pointer made visible."""
    by_doc: dict[str, list] = {}
    for c in state["claims"]:
        by_doc.setdefault(c["doc_id"], []).append((c, "other" if c["segment"] == "OTHER" else "claim"))
    for c in state["safety_queue"]:
        by_doc.setdefault(c["doc_id"], []).append((c, "ae"))
    out = []
    for n in notes:
        spans = [(c["char_start"], c["char_end"], cls,
                  f'{c["id"]} · {c["segment"]} × {c["signal_type"]}' + (" · 유해사례 후보 (safety 큐)" if cls == "ae" else ""))
                 for c, cls in by_doc.get(n["doc_id"], []) if c["verified"]]
        k = len([1 for c, cls in by_doc.get(n["doc_id"], []) if cls != "ae"])
        ae = len([1 for c, cls in by_doc.get(n["doc_id"], []) if cls == "ae"])
        tally = (f'<span class="chip st">발언 카드 {k}</span>' if k else "") + (f'<span class="chip oppose">유해사례 후보 {ae}</span>' if ae else "")
        out.append(f'<div class="note" id="{esc(n["doc_id"])}"><div class="meta"><span class="mono">{esc(n["doc_id"])}</span> · {esc(n["hcp_ref"])} · {esc(n["specialty"])} · {esc(n["date"])} · {origin(n)} {tally}</div>'
                   f'{highlight(n["text"], spans)}</div>')
    return "".join(out)


COLLECT_CARD = ('<div class="card"><div class="row"><div class="grow"><b>현장 수집</b> <span class="sub">— 저장소의 합성 면담 대본을 '
                'Chatterbox(TTS)가 읽고, 그 음성을 Parakeet(STT)가 듣고 받아 적어 면담 기록 한 건으로 넣는다. 듣는 동안 받아 적는 글이 화면에 올라온다.</span></div>'
                '<a class="btn" href="/collect">현장 수집으로 →</a></div></div>')


def notes_page(state: dict, contract: dict, banner: str = "", web: bool = True) -> str:
    notes = load_notes()
    syn = [n for n in notes if not n.get("stt")]
    scripted = [n for n in notes if (n.get("stt") or {}).get("script_id")]
    recorded = len(notes) - len(syn) - len(scripted)
    extra = (f" · 대본 수집 {len(scripted)}건" if scripted else "") + (f" · 음성 전사 {recorded}건" if recorded else "")
    body = ['<div class="eyebrow">Input</div><h1>면담 기록</h1>',
            f'<p class="sub">{tag("fact")}이 루프의 입력. 의학부 담당자가 의료진을 만나고 남기는 기록을 본떠 <b>합성</b>한 {len(syn)}건이다(가상 의료진 {len({n["hcp_ref"] for n in syn})}인, 실제 인물·기관·발언 없음){extra}. '
            f'약은 {esc(contract["drug_ko"])}. 추출을 실행하면 모델이 고른 발언이 <mark>원문 위에 표시</mark>되고, 유해사례로 읽힌 발언은 <mark class="ae">따로 표시</mark>된다 — 표시된 자리가 곧 코드가 검증한 원문 위치다.</p>',
            COLLECT_CARD if web else "", UPLOAD_FORM if web else "", note_cards(state, notes, web)]
    return shell("면담 기록", "\n".join(body), "/notes", banner, band=band(state))


def claims_page(state: dict, contract: dict, banner: str = "", web: bool = True) -> str:
    claims, sq = state["claims"], state["safety_queue"]
    body = ['<div class="eyebrow">Sense</div><h1>발언 카드</h1>',
            f'<p class="sub">{tag("fact")}모델이 면담 기록에서 고른 발언. 인용문은 코드가 원문에서 위치를 찾은 것만 검증 통과 — 못 찾으면 «검증 실패»로 남고 세지 않는다.</p>']
    body.append('<div class="grid g4">' + "".join(f'<div class="kpi"><b>{v}</b><span>{k}</span></div>' for k, v in [
        ("발언 카드", len(claims)), ("원문 검증 통과", f"{sum(c['verified'] for c in claims)}/{len(claims)}"),
        ("환자군 × 신호 묶음", len({(c['segment'], c['signal_type']) for c in claims if c['verified']})), ("유해사례 후보", len(sq))]) + "</div>")
    if claims:
        body.append("<h2>카드</h2><table><tr><th>카드</th><th>환자군 × 신호 유형</th><th>인용 (원문 위치)</th><th>의료진</th></tr>")
        for c in claims:
            pos = f'<a href="/notes#{esc(c["doc_id"])}" class="mono faint">{esc(c["doc_id"])} @{c["char_start"]}–{c["char_end"]}</a>' if c["verified"] else '<span class="chip oppose">검증 실패</span>'
            body.append(f'<tr><td class="mono">{esc(c["id"])}</td><td>{esc(c["segment"])}<br><span class="faint">{signal(c["signal_type"])}</span></td>'
                        f'<td><span class="q">“{esc(c["quote"])}”</span><br>{pos} <span class="faint">{esc(c["note_ko"])}</span></td><td class="mono">{esc(c["hcp_ref"])}</td></tr>')
        body.append("</table>")
    else:
        body.append('<p class="sub">아직 없음 — 개요에서 추출을 실행한다.</p>')
    if sq:
        body.append('<h2 id="safety">safety 큐 — 분석에 섞이지 않는다</h2><table><tr><th>카드</th><th>인용</th><th>의료진</th></tr>')
        for c in sq:
            body.append(f'<tr><td class="mono">{esc(c["id"])}</td><td class="q">“{esc(c["quote"])}”<br><span class="faint">{esc(c["note_ko"])}</span></td><td class="mono">{esc(c["hcp_ref"])}</td></tr>')
        body.append("</table>")
    return shell("발언 카드", "\n".join(body), "/claims", banner, band=band(state))


def hypotheses_page(state: dict, contract: dict, banner: str = "", web: bool = True) -> str:
    body = ['<div class="eyebrow">Hypotheses</div><h1>가설</h1>',
            f'<p class="sub">{tag("pattern")}언급 {contract["threshold"]["min_mentions"]}회 · 의료진 {contract["threshold"]["min_hcps"]}인을 넘은 (환자군 × 신호 유형) 묶음만 가설이 된다. 문턱은 코드다. 문장과 검색식은 모델이 쓴다.</p>',
            hyp_table(state)]
    return shell("가설", "\n".join(body), "/hypotheses", banner, band=band(state))


def hypothesis_section(state: dict, contract: dict, h: dict, web: bool = True) -> str:
    hid = h["id"]
    s = state["screens"].get(hid)
    who, what = next_step(state, h)
    p = [f'<div class="eyebrow">{esc(hid)}</div>',
         f'<h1>{esc(h["segment"])} × {esc(h["signal_type"])} <span class="sub" style="font-weight:400">({esc(SIGNAL_KO.get(h["signal_type"], ""))})</span></h1>',
         f'<div>{status_chip(h["status"])}{label_chip(h["label_status"])}' + (f'<span class="chip turn">사람 차례 · {esc(what)}</span>' if who == "human" else "") + "</div>",
         f'<div class="card">{tag("interp")}<b>{esc(h["statement_ko"])}</b><br><span class="sub">{esc(h["statement_en"])}</span>'
         f'<div class="faint" style="margin-top:8px">{tag("pattern")}현장 {h["field"]["mentions"]}회 / {h["field"]["hcps"]}인 · 검색식 <code>{esc(h["search"]["pubmed_query"])}</code> · CT.gov 조건 <code>{esc(h["search"]["ctgov_condition"])}</code></div></div>',
         journey(state, h, web)]
    explain = {
        "DRAFT": "누르면 에이전트가 검색식으로 PubMed · ClinicalTrials.gov · FDA 라벨을 읽고, 기록마다 지지/반대/중립을 원문 인용과 함께 표시한다. 건수는 코드가 센다.",
        "SCREENED": "아래 근거 표를 직접 읽었다는 서명이다. 이름이 기록에 남고, 서명이 있어야 심의로 갈 수 있다. 반대 근거가 많아도 상정할 수 있으며 판단은 검토자가 한다.",
        "REVIEWED": "누르면 AI Board가 열린다. 간사가 개회하고 임원 7인(CMO · RA · PV · R&D · CFO · CCO · CEO)이 모두발언 → 토론 → 최종 입장을 내며, 코드가 입장을 집계해 권고를 정하고 간사가 회의록을 쓴다. 약 5분.",
        "DELIBERATED": "권고를 받아들이면 회의록의 후속 질문이 다음 면담 체크리스트에 들어간다. 결정자 이름이 남는다.",
    }.get(h["status"], "질문이 체크리스트에 있다. 다음 면담이 이 질문을 참조해 수집한다.")
    if web:
        p.append(f'<div class="card"><div class="row"><div class="grow"><b>다음</b> <span class="sub">{"사람 차례" if who == "human" else ("에이전트 차례" if who == "agent" else "완료")} — {esc(what)}</span>'
                 f'<div class="faint" style="margin-top:4px">{esc(explain)}</div></div>{controls_for(state, h)}</div></div>')
    # The field statements behind this hypothesis — with a link back to the highlighted note.
    claims = [c for c in state["claims"] if c["id"] in h["field"]["claim_ids"]]
    if claims:
        rows = "".join(f'<tr><td class="mono"><a href="{"/notes" if web else ""}#{esc(c["doc_id"])}">{esc(c["doc_id"])}</a><br><span class="faint">{esc(c["hcp_ref"])}</span></td>'
                       f'<td><span class="q">“{esc(c["quote"])}”</span> <span class="faint mono">@{c["char_start"]}–{c["char_end"]}</span></td><td class="faint">{esc(c["note_ko"])}</td></tr>' for c in claims)
        p.append(f'<h2>현장 발언 — 이 가설의 출발점</h2><div class="faint">{tag("fact")}{len(claims)}회 / {h["field"]["hcps"]}인. 인용은 원문에서 코드가 찾은 것만 (위치 표기).</div>'
                 f'<table><tr><th>기록</th><th>인용 (원문 위치)</th><th>왜 이 칸인가</th></tr>{rows}</table>')
    if s:
        n, t = s["numbers"], s["totals"]
        p.append("<h2>외부 근거</h2>")
        p.append('<div class="grid g6">' + "".join(f'<div class="kpi"><b>{v}</b><span>{k}</span></div>' for k, v in [
            ("PubMed 검색", f'{n["pubmed_hits"]:,}'), ("RCT·3상·메타", n.get("pubmed_heavy_hits", "-")), ("CT.gov 시험", f'{n["ctgov_total"]:,}'),
            ("그중 3상 · 모집 중", f'{n.get("ctgov_phase3_total", "-")} · {n["ctgov_recruiting"]}'), ("FAERS 보고", f'{n["faers_total"]:,}'),
            ("Part D 2024 청구", f'{n["partd_per_year"]["2024"]["claims"]:,}')]) + "</div>")
        flags = "".join(f' <span class="chip oppose">{esc(f)}</span>' for f in s["flags"])
        p.append(f'<div class="card">{tag("pattern")}읽은 기록 PubMed {n["pubmed_read"]} · CT.gov {n["ctgov_read"]} · 라벨 섹션 {len([i for i in s["items"] + s["dropped"] if i["source"] == "label"])} → '
                 f'<span class="chip support">지지 {t["SUPPORTS"]}</span><span class="chip oppose">반대 {t["CONTRADICTS"]}</span><span class="chip hold">중립 {t["NEUTRAL"]}</span>'
                 f'<span class="faint">인용 검증 실패로 버림 {len(s["dropped"])}</span>{flags} · 라벨 판정 <b>{esc(s["label_status"])}</b>'
                 f'<div class="faint" style="margin-top:6px">{tag("fact")}라벨 {esc(n["label"]["brand"])} ({esc(n["label"]["effective_time"])}) · 조회일 {esc(n["as_of"]["pubmed"])} · 집계는 출처·판정별 고유 건수</div></div>')
        p.append("<table><tr><th>판정</th><th>출처</th><th>인용 (원문 위치)</th><th>해석</th></tr>")
        for it in sorted(s["items"], key=lambda x: STANCE_ORDER.index(x["stance"])):
            sid = it["source_id"]
            base, sec = (sid.split("#", 1) + [""])[:2]
            p.append(f'<tr><td>{chip(it["stance"])}</td><td><a href="{esc(it["url"])}" target="_blank" rel="noopener" class="mono">{esc(base)}</a>'
                     + (f'<br><span class="faint mono">{esc(sec)}</span>' if sec else "") +
                     f'</td><td><span class="q">“{esc(it["quote"])}”</span> <span class="faint mono">@{it["char_start"]}–{it["char_end"]}</span></td><td>{tag("interp")}{esc(it["note_ko"])}</td></tr>')
        for it in s["dropped"]:
            p.append(f'<tr class="drop"><td><span class="chip st">버림</span></td><td class="mono">{esc(it["source_id"].split("#")[0])}</td><td class="q">“{esc(it["quote"][:160])}”</td><td>{esc(it["drop_reason"])} — 세지 않음</td></tr>')
        p.append("</table>")
        faers = " · ".join(f'{esc(r["term"].title())} {r["count"]:,}' for r in n["faers_top"][:6])
        pd = n["partd_per_year"]
        p.append(f'<div class="faint" style="margin-top:8px">{tag("fact")}FAERS 상위 반응: {faers}. Part D 청구 2020→2024: ' +
                 " · ".join(f'{y} {v["claims"]:,}' for y, v in pd.items()) + " (숫자는 API 값 그대로)</div>")
    rev = state["reviews"].get(hid)
    if rev:
        p.append(f'<div class="card gate">{tag("fact")}<b>관문 ① 근거 검토 서명</b> — <b>{esc(rev["by"])}</b> 이(가) {esc(rev["at"])} 에 외부 근거 {rev["items_read"]}건(버림 {rev["dropped_seen"]}건 포함)을 직접 검토했다.'
                 + (f' <span class="sub">{esc(rev["note"])}</span>' if rev["note"] else "") + "</div>")
    memo = state["board"].get(hid)
    if memo:
        p.append(board_section(memo, web))
    return "\n".join(p)


STANCE_KO = {"SUPPORT": ("지지", "support"), "HOLD": ("보류", "hold"), "OPPOSE": ("반대", "oppose")}


def _stance_chip(s: str) -> str:
    ko, cls = STANCE_KO.get(s, (s, "hold"))
    return f'<span class="chip {cls}">{ko}</span>'


def board_section(memo: dict, web: bool = True) -> str:
    """The meeting as a record: header, transcript, stance evolution, code tally, minutes, decision."""
    p = ["<h2>심의 — AI Board</h2>"]
    if "transcript" not in memo:   # memo from the single-call board (older state)
        p.append(f'<div class="card">{tag("proposal")}권고 <b>{esc(memo["recommendation"])}</b> — {esc(memo["rationale_ko"])}</div>')
        return "\n".join(p)
    t = memo["tally"]
    p.append(f'<div class="card">{tag("fact")}가설 유형 <b>{esc(memo["hypothesis_type"])}</b> · 주무 임원 <b>{esc(memo["lead_ko"])}</b> · 참석 {len(memo["attendees"])}인 · 발언 {len(memo["transcript"])}턴 · '
             f'라벨 판정 {esc(memo["label_status"])} → 경로 {esc(memo["route"])}</div>')
    # stance evolution
    rows = "".join(f'<tr><td>{esc(e["speaker_ko"])}{" <span class=faint>(주무)</span>" if e["speaker"] == memo["lead"] else ""}</td><td>{_stance_chip(e["opening"])}</td>'
                   f'<td>{_stance_chip(e["final"])}{" <span class=chip st>변경</span>" if e["changed"] else ""}</td><td class="n">{e["confidence"]}</td></tr>'
                   for e in memo["stance_evolution"])
    p.append('<div class="grid" style="grid-template-columns:1fr 1fr">'
             f'<div class="card"><b>{tag("pattern")}입장 변화 (모두발언 → 최종)</b><table><tr><th>참석자</th><th>모두발언</th><th>최종</th><th>확신 1–5</th></tr>{rows}</table></div>'
             f'<div class="card"><b>{tag("pattern")}집계 (코드 · 확신 가중, 주무 ×1.5)</b><table><tr><th>입장</th><th>인원</th><th>가중치</th></tr>'
             + "".join(f'<tr><td>{_stance_chip(s)}</td><td class="n">{t["counts"][s]}</td><td class="n">{t["weights"][s]:.1f}</td></tr>' for s in ("SUPPORT", "HOLD", "OPPOSE"))
             + f'</table><div style="margin-top:8px">{tag("proposal")}권고 <b>{esc(memo["recommendation"])}</b> <span class="faint">— 가중치가 가장 큰 입장을 코드가 권고로 옮긴다. 간사는 바꾸지 못한다.</span></div></div></div>')
    # transcript — same layout the streaming page draws, rendered server-side
    turns, last_phase = [], None
    for tr in memo["transcript"]:
        group = tr["phase"].split(" · ")[0]
        if group != last_phase:
            turns.append(f'<div class="divider">{esc(group)}</div>')
            last_phase = group
        m = PERSONA_SHORT.get(tr["speaker"], "간사")
        st = _stance_chip(tr["stance"]) if tr.get("stance") else ""
        chg = ' <span class="chip st">입장 변경</span>' if tr.get("stance_changed") else ""
        conf = f' <span class="faint">확신 {tr["confidence"]}</span>' if tr.get("confidence") else ""
        cited = "".join(f'<span class="cite">{esc(c.split("#")[0])}</span>' for c in tr.get("cited", []))
        dropped = f' <span class="faint">근거 목록에 없는 인용 {len(tr["cited_dropped"])}건 버림</span>' if tr.get("cited_dropped") else ""
        sentences = tr["utterance_ko"].strip()
        head, _, rest = sentences.partition(". ")
        body = f'<span class="lead">{esc(head)}.</span> {esc(rest)}' if rest else f'<span class="lead">{esc(sentences)}</span>'
        turns.append(f'<div class="turn"><div class="av {esc(tr["speaker"].lower())}">{esc(m)}</div><div><span class="who">{esc(tr["speaker_ko"])}</span><span class="ph">{esc(tr["phase"])}</span> {st}{chg}{conf}'
                     f'<div class="ut">{body}</div><div class="cites">{cited}{dropped}</div></div></div>')
    p.append(f'<h3>{tag("interp")}회의 기록 <span class="faint">— 첫 문장이 결론. 인용은 근거 표에 있는 ID만 인정.</span></h3><div class="room">{"".join(turns)}</div>')
    if memo.get("blocked_actions"):
        b = "".join(f'<li><b>{esc(x["speaker_ko"])}</b>: “{esc(x["action_ko"])}” — {esc(x["reason_ko"])}</li>' for x in memo["blocked_actions"])
        p.append(f'<div class="card" style="border-left:4px solid var(--rust)">{tag("fact")}<b>코드가 차단한 제안 {len(memo["blocked_actions"])}건</b><ul>{b}</ul></div>')
    # minutes
    qs = "".join(f'<li>{esc(q["question_ko"])} <span class="faint">— {esc(q["why_ko"])}</span></li>' for q in memo["follow_up_questions"])
    risks = "".join(f"<li>{esc(r)}</li>" for r in memo["risks_ko"])
    kills = "".join(f"<li>{esc(r)}</li>" for r in memo.get("kill_criteria_ko", []))
    p.append(f'<div class="card navy"><b>{tag("proposal")}회의록</b><div style="margin-top:6px">{esc(memo["summary_ko"])}</div>'
             f'<div style="margin-top:8px;color:var(--on-navy-2)">{tag("interp")}{esc(memo["evidence_summary_ko"])}</div>'
             f'<div style="margin-top:8px;color:var(--on-navy-2)">{tag("proposal")}권고 사유 — {esc(memo["rationale_ko"])}</div></div>')
    p.append('<div class="grid" style="grid-template-columns:1fr 1fr 1fr">'
             f'<div class="card"><b>{tag("proposal")}중단 기준</b><ul>{kills}</ul></div>'
             f'<div class="card"><b>{tag("proposal")}위험</b><ul>{risks}</ul></div>'
             f'<div class="card"><b>{tag("proposal")}다음 면담에서 물을 것</b><ul>{qs}</ul></div></div>')
    dec = memo.get("decision")
    if dec:
        p.append(f'<div class="card gate">{tag("action")}<b>관문 ② 결정</b> — <b>{esc(dec["by"])}</b> 이(가) {esc(dec["at"])} 에 권고 {esc(dec["accepted"])} 을 받아들였다. 후속 질문이 체크리스트로 내려갔다.</div>')
    return "\n".join(p)


def hypothesis_page(state: dict, contract: dict, hid: str, banner: str = "", web: bool = True) -> str:
    h = store.hypothesis(state, hid)
    return shell(hid, hypothesis_section(state, contract, h, web), "/hypotheses", banner, band=band(state))


PERSONA_SHORT = {"ORCHESTRATOR": "간사", "CMO": "CMO", "RA_HEAD": "RA", "PV_HEAD": "PV", "RND_HEAD": "R&D", "CFO": "CFO", "CCO": "CCO", "CEO": "CEO"}

ROOM_JS = """<script>
(function(){
const META=%(meta)s, EVID=%(evid)s, MODE=%(mode)s, TURNS=%(turns)s, HID=%(hid)s;
const room=document.getElementById('room'), liveEl=document.getElementById('live'), bar=document.getElementById('bar');
let speed=1, seen=0, lastPhase=null, playing=false, cancel=false;
const ST={SUPPORT:['지지','support'],HOLD:['보류','hold'],OPPOSE:['반대','oppose']};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function cite(id){const base=id.split('#')[0];const q=EVID[id];const tip=q?'<div class=th>'+esc(base)+'</div>'+esc(q.stance)+' · “'+esc(q.quote.slice(0,220))+'”':'';return '<span class=cite'+(tip?' data-tip="'+tip.replace(/"/g,'&quot;')+'"':'')+'>'+esc(base)+'</span>';}
function el(t){const m=META[t.speaker]||{cls:'orchestrator',short:'간사'};const d=document.createElement('div');d.className='turn';
const st=t.stance?'<span class="chip '+ST[t.stance][1]+'">'+ST[t.stance][0]+'</span>':'';const chg=t.stance_changed?'<span class="chip st">입장 변경</span>':'';const conf=t.confidence?'<span class=faint> 확신 '+t.confidence+'</span>':'';
const cites=(t.cited||[]).map(cite).join('')+(t.cited_dropped&&t.cited_dropped.length?'<span class=faint> 근거 목록에 없는 인용 '+t.cited_dropped.length+'건 버림</span>':'');
d.innerHTML='<div class="av '+m.cls+'">'+esc(m.short)+'</div><div><span class=who>'+esc(t.speaker_ko)+'</span><span class=ph>'+esc(t.phase)+'</span> '+st+chg+conf+'<div class=ut><span class=txt></span></div><div class=cites>'+cites+'</div></div>';
return d;}
function divider(phase){const d=document.createElement('div');d.className='divider';d.textContent=phase.replace(/ · .*/,'');return d;}
function typeInto(span,text){return new Promise(res=>{const lead=text.indexOf('. ');let i=0;const step=Math.max(2,Math.round(3*speed));
span.classList.add('caret');const tick=()=>{if(cancel){span.innerHTML=fmt(text,lead);span.classList.remove('caret');return res();}
i=Math.min(text.length,i+step);span.innerHTML=fmt(text.slice(0,i),lead);if(i<text.length){setTimeout(tick,20/speed);}else{span.classList.remove('caret');res();}};tick();});}
function fmt(s,lead){if(lead>0&&s.length>lead)return '<span class=lead>'+esc(s.slice(0,lead+1))+'</span>'+esc(s.slice(lead+1));return '<span class=lead>'+esc(s)+'</span>';}
async function add(t,typed){const group=t.phase.replace(/ · .*/,'');if(group!==lastPhase){room.appendChild(divider(t.phase));lastPhase=group;}
const d=el(t);room.appendChild(d);d.scrollIntoView({block:'nearest',behavior:'smooth'});
if(typed){await typeInto(d.querySelector('.txt'),t.utterance_ko);await new Promise(r=>setTimeout(r,(700+Math.random()*400)/speed));}else{d.querySelector('.txt').innerHTML=fmt(t.utterance_ko,t.utterance_ko.indexOf('. '));}}
function setLive(msg){if(liveEl){liveEl.innerHTML=msg?'<span class=dot></span>'+esc(msg):'';}}
async function poll(){try{const r=await fetch('/hypotheses/'+HID+'/board.json?after='+seen);const j=await r.json();
for(const t of j.turns){setLive(t.speaker_ko+' 발언 중');await add(t,true);seen=t.no;}
if(j.status==='DONE'){setLive('회의록을 정리하는 중');setTimeout(()=>location.reload(),1200);return;}
if(j.status==='ERROR'){setLive('심의 중단 — '+(j.error||''));return;}
setLive(seen?'다음 발언을 기다리는 중':'간사가 개회를 준비하는 중');}catch(e){setLive('연결 재시도 중');}
setTimeout(poll,1200);}
async function replay(){if(playing)return;playing=true;cancel=false;room.innerHTML='';lastPhase=null;const rec=document.getElementById('record');if(rec)rec.style.display='none';
for(const t of TURNS){if(cancel)break;setLive(t.speaker_ko+' 발언 중');await add(t,true);}setLive('');playing=false;if(rec)rec.style.display='';}
if(bar){bar.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.speed){speed=+b.dataset.speed;bar.querySelectorAll('[data-speed]').forEach(x=>x.classList.toggle('on',x===b));}
if(b.dataset.act==='replay'){cancel=true;setTimeout(replay,50);}if(b.dataset.act==='skip'){cancel=true;}});}
if(MODE==='live'){poll();}
})();
</script>"""


def _room_js(memo: dict | None, hid: str, mode: str, screen: dict | None) -> str:
    meta = {k: {"cls": k.lower(), "short": v} for k, v in PERSONA_SHORT.items()}
    evid = {it["source_id"]: {"stance": {"SUPPORTS": "지지", "CONTRADICTS": "반대", "NEUTRAL": "중립"}[it["stance"]], "quote": it["quote"]}
            for it in (screen or {}).get("items", [])}
    turns = memo["transcript"] if memo and "transcript" in memo else []
    return ROOM_JS % {"meta": json.dumps(meta, ensure_ascii=False), "evid": json.dumps(evid, ensure_ascii=False),
                      "mode": json.dumps(mode), "turns": json.dumps(turns, ensure_ascii=False), "hid": json.dumps(hid)}


def board_page(state: dict, contract: dict, hid: str, banner: str = "", web: bool = True, live_rec: dict | None = None) -> str:
    """The meeting on its own page — live while it runs, replayable when it is done."""
    h = store.hypothesis(state, hid)
    memo = state["board"].get(hid)
    screen = state["screens"].get(hid)
    head = (f'<div class="eyebrow">{esc(hid)} · <a href="/hypotheses/{esc(hid)}">가설 상세로</a></div>'
            f'<h1>AI Board 회의 — {esc(h["segment"])} × {esc(h["signal_type"])}</h1>'
            f'<p class="sub">{tag("interp")}{esc(h["statement_ko"])}</p>')
    running = bool(live_rec and live_rec["status"] == "RUNNING")
    if running:
        n = len(live_rec["turns"])
        body = (head + journey(state, h, web) +
                f'<div class="card"><b>심의 진행 중</b> <span class="sub">— 간사 1 + 임원 7인. 발언이 생기는 대로 올라온다. 지금까지 {n}턴.</span>'
                '<div class="bar" id="bar"><span class="faint">속도</span><button class="btn sm on" data-speed="1">×1</button><button class="btn sm" data-speed="2">×2</button><button class="btn sm" data-speed="4">×4</button></div>'
                '<div class="room" id="room"></div><div class="live" id="live"><span class="dot"></span>간사가 개회를 준비하는 중</div></div>')
        return shell(f"{hid} 회의", body, "/hypotheses", banner, band=band(state), script=_room_js(None, hid, "live", screen))
    if memo and "transcript" in memo:
        ctrl = ('<div class="bar" id="bar"><button class="btn sm" data-act="replay">▶ 처음부터 재생</button><button class="btn sm ghost" data-act="skip">건너뛰기</button>'
                '<span class="faint">속도</span><button class="btn sm on" data-speed="1">×1</button><button class="btn sm" data-speed="2">×2</button><button class="btn sm" data-speed="4">×4</button>'
                f'<span class="sp">{len(memo["transcript"])}턴 · {esc(memo["deliberated_at"][:16].replace("T", " "))} · 실제 회의 기록을 재생한다 (모델 재호출 없음)</span></div>')
        body = (head + journey(state, h, web) + ctrl + '<div class="room" id="room"></div><div class="live" id="live"></div>'
                + f'<div id="record">{board_section(memo, web)}</div>')
        return shell(f"{hid} 회의 기록", body, "/hypotheses", banner, band=band(state), script=_room_js(memo, hid, "record", screen))
    err = f'<div class="banner">이전 심의가 중단됐다 — {esc(live_rec["error"])}</div>' if live_rec and live_rec.get("status") == "ERROR" else ""
    body = head + journey(state, h, web) + err + '<p class="sub">아직 심의 전이다. 가설 상세에서 서명 후 심의를 실행한다.</p>'
    return shell(f"{hid} 회의", body, "/hypotheses", banner, band=band(state))


def checklist_page(state: dict, contract: dict, banner: str = "", web: bool = True) -> str:
    acts = state["actions"]
    body = ['<div class="eyebrow">Field checklist</div><h1>다음 면담 체크리스트</h1>',
            f'<p class="sub">{tag("action")}사람이 받아들인 권고의 후속 질문. 다음 면담이 이 항목을 참조해 수집한다.</p>']
    if acts:
        body.append("<table><tr><th>항목</th><th>질문</th><th>이유</th><th>가설</th><th>승인</th></tr>")
        for a in acts:
            body.append(f'<tr><td class="mono">{esc(a["id"])}</td><td><b>{esc(a["question_ko"])}</b></td><td class="faint">{esc(a["why_ko"])}</td>'
                        f'<td><a href="/hypotheses/{esc(a["hypothesis_id"])}" class="mono">{esc(a["hypothesis_id"])}</a></td><td class="faint">{esc(a["approved_by"])}<br>{esc(a["approved_at"])}</td></tr>')
        body.append("</table>")
    else:
        body.append('<p class="sub">아직 없음 — 심의를 거쳐 사람이 결정하면 여기에 생긴다.</p>')
    return shell("체크리스트", "\n".join(body), "/checklist", banner, band=band(state))


def static_report(state: dict, contract: dict) -> str:
    """One page for readers who will not run anything: overview + every hypothesis + checklist."""
    notes = load_notes()
    notes_n = len(notes)
    parts = [f'<div class="eyebrow">{esc(contract["drug_ko"])} · 계약 {esc(contract["version"])} · 정적 리포트</div>',
             '<h1>현장 신호가 근거를 지나 실행이 되기까지</h1>',
             '<p class="sub">모든 면담 기록은 합성. 숫자는 전부 코드가 계산. 인용은 원문 위치가 확인된 것만.</p>',
             pipeline_strip(state, notes_n), demo_card(state, contract, notes_n, web=False),
             f'<h2 id="notes">면담 기록 (입력) — 추출된 발언을 원문 위에 표시 · 처음 40건 / 전체 {notes_n}건</h2>', note_cards(state, notes[:40], web=False),
             "<h2>신호 지도</h2>", signal_map(state, contract, web=False),
             "<h2>가설</h2>", hyp_table(state, link=False)]
    for h in state["hypotheses"]:
        parts.append(f'<div id="{esc(h["id"])}" style="margin-top:40px;border-top:1px solid var(--line-2);padding-top:20px"></div>')
        parts.append(hypothesis_section(state, contract, h, web=False))
    if state["actions"]:
        parts.append("<h2>다음 면담 체크리스트</h2><table><tr><th>항목</th><th>질문</th><th>이유</th><th>승인</th></tr>" + "".join(
            f'<tr><td class="mono">{esc(a["id"])}</td><td><b>{esc(a["question_ko"])}</b></td><td class="faint">{esc(a["why_ko"])}</td><td class="faint">{esc(a["approved_by"])} · {esc(a["approved_at"])}</td></tr>'
            for a in state["actions"]) + "</table>")
    return shell("리포트", "\n".join(parts), "/", static=True)


# ── ⓪ 현장 수집 — script → TTS → listen while STT listens → field note ─────────

LISTEN_LABEL = {"replay": "저장된 전사를 음성 시각에 맞춰 보여준다 — 모델 호출 없음"}


def _listen_labels() -> dict:
    from . import stt
    try:
        boost = stt.settings("parakeet")["boost"]
    except stt.SttUnavailable:   # the page shows the settings error and no live button
        boost = 0.0
    return {**LISTEN_LABEL, "live": "STT가 듣는 중 — 실시간 전사 (Parakeet 1.1B 다국어 · 스트리밍 · "
                                     + ("단어 가중 없음)" if not boost else f"단어 가중 {boost:g})")}


def collect_rows(state: dict, contract: dict) -> list[dict]:
    """One row per script, for the list page and /collect/scripts.json. Current counts are sense.tally's."""
    from . import collect
    from .sense import tally
    done = collect.collected(load_notes())
    by = {(r["segment"], r["signal_type"]): r for r in tally(state)}
    thr = contract["threshold"]
    stt_err = collect.stt_problem()   # a bad STT setting hides saved transcripts (their cache key needs it), never a 500
    rows = []
    for s in collect.list_scripts(contract):
        sid = s["script_id"]
        if "_error" in s:
            rows.append({"script_id": sid, "title_ko": s.get("title_ko", ""), "_error": s["_error"], "github_url": collect.blob_url(sid)})
            continue
        a = collect.audio(s)
        it = s["intent"]
        cur = by.get((it["segment"], it["signal_type"]), {"mentions": 0, "hcps": 0})
        live = collect.live_read(sid)
        heard = bool(a and live and live.get("status") == "DONE" and live.get("audio_sha256") == a["audio_sha256"])
        row = {"script_id": sid, "title_ko": s["title_ko"], "hcp_ref": s["hcp_ref"], "specialty": s["specialty"], "date": s["date"],
               "chars": s["chars"], "intent": it,
               "current": {"mentions": cur["mentions"], "hcps": cur["hcps"],
                           "passed": cur["mentions"] >= thr["min_mentions"] and cur["hcps"] >= thr["min_hcps"]},
               "audio": {"duration_s": a["duration_s"], "audio_sha256": a["audio_sha256"], "source": a["source"]} if a else None,
               "transcript_ready": heard or bool(a and not stt_err and collect.saved_transcript(s)),
               "collected_doc_id": done[sid]["doc_id"] if sid in done else None, "github_url": collect.blob_url(sid)}
        if s.get("_warn"):
            row["_warn"] = s["_warn"]
        rows.append(row)
    return rows


def _source_line() -> str:
    from . import collect
    src = collect.source_info()
    sha = f' (커밋 <span class="mono">{esc(src["commit"][:7])}</span>)' if src["commit"] else ""
    if src["repo"]:
        ref = src["ref"] or src["commit"]
        where = (f'GitHub <a href="https://github.com/{esc(src["repo"])}/tree/{esc(ref)}/{esc(src["path"])}" target="_blank" rel="noopener">'
                 f'{esc(src["repo"])}</a> · {esc(src["path"])} @ {esc(ref)}')
    else:
        where = f'{esc(src["path"])}'
    return f'<div class="faint" style="margin:6px 0 10px">{tag("fact")}출처 {where} · 저장소 사본{sha}</div>'


def _stt_problem_card(err: str | None) -> str:
    if not err:
        return ""
    return (f'<div class="card gate">{tag("fact")}<b>STT 설정을 쓸 수 없다</b> — {esc(err)}'
            '<div class="faint" style="margin-top:4px">고칠 때까지 저장된 전사 재생과 실시간 듣기를 멈춘다. 대본과 음성은 그대로 보인다.</div></div>')


def _state_cell(r: dict) -> str:
    if r.get("collected_doc_id"):
        d = esc(r["collected_doc_id"])
        return f'<span class="chip support">수집됨</span><br><a href="/notes#{d}" class="mono">→ {d}</a>'
    if r["transcript_ready"]:
        return '<span class="chip st">전사 있음</span>'
    if r["audio"]:
        return '<span class="chip st">음성 있음</span>'
    return '<span class="faint">음성 없음</span>'


def _effect_panel(state: dict, contract: dict) -> str:
    from . import collect
    eff = collect.effect(state, contract, load_notes())
    mm, mh = eff["threshold"]
    p = ['<h2 id="effect">이번 수집이 바꾼 것</h2>',
         f'<p class="sub">{tag("pattern")}수집한 면담 기록의 발언 카드를 빼고 센 집계(전)와 넣고 센 집계(후). 둘 다 코드가 센다 — 문턱 {mm}회·{mh}인.</p>']
    if not eff["docs"]:
        p.append('<p class="sub">아직 대본에서 수집한 면담 기록이 없다 — 대본을 열어 듣고, 동의를 확인해 저장하면 여기에 생긴다.</p>')
        return "\n".join(p)
    if eff["pending_docs"]:
        p.append(f'<div class="card gate">{tag("fact")}추출 전 {len(eff["pending_docs"])}건 — '
                 + ", ".join(f'<a href="/notes#{esc(d)}" class="mono">{esc(d)}</a>' for d in eff["pending_docs"])
                 + ' · 위의 <b>다음 단계 — ① 추출 실행</b>을 누르면 집계에 들어간다.</div>')
    if eff["rows"]:
        rows = []
        for r in eff["rows"]:
            (bm, bh), (am, ah) = r["before"], r["after"]
            if r["passed_after"]:
                gate = '<span class="chip support">문턱 통과</span>'
            else:
                miss = " · ".join(x for x in ((f"언급 {r['need_mentions']}회" if r["need_mentions"] else ""),
                                              (f"의료진 {r['need_hcps']}인" if r["need_hcps"] else "")) if x)
                gate = f'<span class="chip hold">미달</span> <span class="faint">{miss} 모자람</span>'
            hid = r["hypothesis_id"]
            hyp = (f'<a href="/hypotheses/{esc(hid)}"><b>{esc(hid)}</b></a><br>'
                   + ('<span class="chip st" style="white-space:nowrap">이번 수집으로 생긴 초안</span>' if r["new_hypothesis"] else '<span class="faint">이미 있던 가설</span>')
                   if hid else '<span class="faint">—</span>')
            rows.append(f'<tr><td>{esc(r["segment"])} × {signal(r["signal_type"])}</td><td class="n">{bm}회/{bh}인</td>'
                        f'<td class="n">→ <b>{am}회/{ah}인</b></td><td>{gate}</td><td>{hyp}</td></tr>')
        p.append('<table><tr><th>환자군 × 신호 유형</th><th>전</th><th>후</th><th>문턱 (코드)</th><th>가설</th></tr>' + "".join(rows) + "</table>")
    extra = []
    if eff["safety_added"]:
        extra.append(f'{tag("fact")}유해사례 후보 {eff["safety_added"]}건 → <a href="/claims#safety">safety 큐</a> (집계에 넣지 않는다)')
    if eff["dropped"]:
        extra.append(f'{tag("fact")}원문에서 인용을 찾지 못해 버린 발언 {eff["dropped"]}건 — 세지 않는다')
    if eff["new_hypotheses"]:
        links = ", ".join(f'<a href="/hypotheses/{esc(h)}"><b>{esc(h)}</b></a>' for h in eff["new_hypotheses"])
        extra.append(f'{tag("interp")}새 가설 {links} — 여기서부터는 이미 있는 흐름이다: ② 근거 교차검증 → ③ 서명 → ④ 심의 → ⑤ 결정 → 체크리스트')
    if extra:
        p.append('<div class="card">' + "<br>".join(extra) + "</div>")
    return "\n".join(p)


def collect_page(state: dict, contract: dict, banner: str = "") -> str:
    from . import collect
    rows = collect_rows(state, contract)
    thr = contract["threshold"]
    notes = load_notes()
    original = sum(1 for n in notes if not (n.get("stt") or {}).get("script_id"))
    body = ['<div class="eyebrow">Collect · 현장 수집</div><h1>현장 수집 — 대본을 듣고 받아 적는다</h1>',
            f'<p class="sub">{tag("fact")}의학부 담당자가 면담을 녹음해 오는 자리를 데모로 옮겼다. 저장소에 있는 <b>합성 면담 대본</b>을 '
            'Chatterbox(TTS)가 읽고, 평가자가 그 음성을 듣는 동안 Parakeet(STT)가 <b>같은 음성 파일</b>을 실제 속도로 들으며 받아 적는다. '
            '받아 적은 글이 녹음 동의 확인을 거쳐 면담 기록 한 건이 되면, 그다음은 이미 있는 흐름(① 추출 → 가설 → 근거 → 서명 → 심의 → 결정)이 그대로 이어진다.</p>',
            _source_line(), _stt_problem_card(collect.stt_problem())]
    trs = []
    for r in rows:
        sid = esc(r["script_id"])
        gh = f'<a href="{esc(r["github_url"])}" target="_blank" rel="noopener">GitHub에서 보기</a>' if r.get("github_url") else '<span class="faint">—</span>'
        if "_error" in r:
            trs.append(f'<tr><td class="mono">{sid}</td><td colspan="5"><span class="chip oppose">형식 오류</span> {esc(r["_error"])}</td><td>{gh}</td></tr>')
            continue
        it, cur = r["intent"], r["current"]
        secs = f' · {r["audio"]["duration_s"]:.1f}초' if r["audio"] else ""
        warn = f'<br><span class="chip hold">{esc(r["_warn"])}</span>' if r.get("_warn") else ""
        trs.append(f'<tr><td><a href="/collect/{sid}"><b class="mono">{sid}</b></a><br><a href="/collect/{sid}">{esc(r["title_ko"])}</a>{warn}</td>'
                   f'<td><span class="mono">{esc(r["hcp_ref"])}</span><br><span class="faint">{esc(r["specialty"])}<br>{esc(r["date"])}</span></td>'
                   f'<td class="n">{r["chars"]}자{secs}</td>'
                   f'<td title="{esc(it["why_ko"])}">{esc(it["segment"])} × {signal(it["signal_type"])}<br>'
                   f'<span class="faint">{tag("pattern")}지금 {cur["mentions"]}회/{cur["hcps"]}인 · 문턱 {thr["min_mentions"]}회·{thr["min_hcps"]}인</span></td>'
                   f'<td style="white-space:nowrap">{_state_cell(r)}</td><td><a class="btn sm" href="/collect/{sid}">열기</a></td><td>{gh}</td></tr>')
    body.append('<table><tr><th>대본</th><th>의료진 · 전문과 · 날짜</th><th>길이</th><th>작성 의도 (지금 집계)</th><th>상태</th><th></th><th>원본</th></tr>'
                + "".join(trs) + "</table>" if trs else '<p class="sub">data/field_scripts/ 에 대본이 없다.</p>')
    try:
        checklist = json.loads(store.FIELD_CHECKLIST.read_text()) if store.FIELD_CHECKLIST.exists() else []
    except ValueError:
        checklist = []
    open_q = [a for a in checklist if a.get("status", "OPEN") == "OPEN"]
    if open_q:
        qs = "".join(f'<li>{esc(a["question_ko"])} <span class="faint mono">{esc(a["hypothesis_id"])}</span></li>' for a in open_q[:5])
        body.append(f'<div class="card"><b>{tag("action")}이번 면담에서 물을 질문</b> <span class="faint">— 사람이 결정한 체크리스트 (<a href="/checklist">전체</a>). '
                    f'대본은 이 질문과 따로 쓴 합성 면담이다.</span><ul>{qs}</ul></div>')
    body.append(f'<div class="card" id="next"><div class="row"><div class="grow"><b>다음 단계 — ① 추출</b> <span class="sub">수집한 면담 기록을 기존 추출에 넣는다. '
                'Nemotron이 발언을 고르고 인용하면, 코드가 인용을 원문에서 찾아 검증하고 세어 문턱을 넘은 조합을 가설로 만든다. 개요의 ① 추출과 같은 단계다.</span></div>'
                + button("다음 단계 — ① 추출 실행", "/run/collect/sense") + '</div>'
                f'<div class="row"><div class="grow faint">다음 평가자를 위해 — 대본에서 수집한 면담 기록과 그로부터 생긴 발언 카드·가설 초안만 지운다. '
                f'원래 면담 {original}건과 사람이 서명한 기록은 그대로 둔다.</div>' + button("수집 되돌리기", "/run/collect/reset", ghost=True) + '</div>'
                '<div class="row"><div class="grow faint">저장소 사본이 GitHub의 같은 파일과 같은지 해시로 대조한다 (읽기만 한다).</div>'
                + button("GitHub 원본과 대조", "/run/collect/verify", ghost=True) + '</div></div>')
    body.append(_effect_panel(state, contract))
    return shell("현장 수집", "\n".join(body), "/collect", banner, band=band(state))


def miss_marks(hyp: str, ref: str) -> str:
    """Escape the transcript and mark what differs from the script — same normalisation as stt.cer
    (case, spaces and punctuation ignored), aligned by difflib. A dropped stretch marks the character after it."""
    import difflib
    import re
    keep = [i for i, ch in enumerate(hyp) if re.match(r"[^\W_]", ch)]
    h = "".join(hyp[i].lower() for i in keep)
    r = re.sub(r"[\s\W_]+", "", (ref or "").lower())
    if len(h) != len(keep):   # lower() changed a length — skip the marks rather than misplace them
        return esc(hyp)
    bad: set[int] = set()
    for op, i1, i2, _, _ in difflib.SequenceMatcher(None, h, r, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            bad.update(keep[i] for i in range(i1, i2))
        elif op == "insert" and keep:
            bad.add(keep[min(i1, len(keep) - 1)])
    out, run = [], []
    for i, ch in enumerate(hyp):
        if i in bad:
            run.append(ch)
            continue
        if run:
            out.append(f'<mark class="miss">{esc("".join(run))}</mark>')
            run = []
        out.append(esc(ch))
    if run:
        out.append(f'<mark class="miss">{esc("".join(run))}</mark>')
    return "".join(out)


COLLECT_JS = """<script>
(function(){
const SID=%(sid)s, LABEL=%(labels)s, RUNNING=%(running)s, BACKSTOP_MS=%(backstop_ms)s;
const audio=document.getElementById('player'), heard=document.getElementById('heard'), st=document.getElementById('lstatus');
const btns=[...document.querySelectorAll('[data-listen]')];
let reloading=false, gen=0, endedAt=0, lastSig='', lastChange=Date.now(), gaveUp=false;
if(audio)audio.addEventListener('ended',()=>{endedAt=Date.now();});
function say(msg,dot){if(!st)return;st.textContent='';if(dot){const d=document.createElement('span');d.className='dot';st.appendChild(d);}st.appendChild(document.createTextNode(msg||''));}
function enable(on){btns.forEach(b=>{b.disabled=!on;});}
function render(j){heard.textContent='';const f=document.createElement('span');f.textContent=(j.finals||[]).join(' ');heard.appendChild(f);
const i=document.createElement('span');i.className='interim'+(j.status==='RUNNING'?' caret':'');i.textContent=j.interim?((f.textContent?' ':'')+j.interim):'';heard.appendChild(i);}
function reload(){if(reloading)return;reloading=true;setTimeout(()=>location.reload(),1200);}
// the server cuts a stalled stream at its deadline and gives up a stuck one at its watchdog time, then says ERROR;
// this is only the backstop for when that answer never comes (both limits are past the server's own)
function stalled(j){const sig=(j.updated_at||'')+'|'+(j.finals||[]).join(' ').length+'|'+(j.interim||'').length,now=Date.now();
if(sig!==lastSig){lastSig=sig;lastChange=now;}return (endedAt&&now-endedAt>BACKSTOP_MS)||now-lastChange>(j.duration_s||0)*1000+BACKSTOP_MS;}
async function poll(g){if(g!==gen)return;let j;try{const r=await fetch('/collect/'+SID+'/listen.json',{cache:'no-store'});j=await r.json();}catch(e){say('연결 재시도 중',true);setTimeout(()=>poll(g),1000);return;}
if(g!==gen)return;
if(j.status==='RUNNING'){render(j);
if(stalled(j)){if(!gaveUp){gaveUp=true;say('STT 응답이 멈춘 것 같습니다 — 음성이 끝났는데 결과가 오지 않습니다. 다시 누르면 처음부터 듣습니다.',false);enable(true);}
setTimeout(()=>poll(g),2000);return;}
say(LABEL[j.mode]||'',true);setTimeout(()=>poll(g),300);return;}
if(j.status==='DONE'){render(j);say('받아 적기 끝 — 대본과 비교하는 중',true);
if(audio&&!audio.paused&&!audio.ended){audio.addEventListener('ended',reload,{once:true});setTimeout(reload,Math.max(0,(audio.duration||0)-audio.currentTime)*1000+1500);}else{reload();}return;}
if(j.status==='ERROR'){say('듣기 중단 — '+(j.error||''),false);if(audio)audio.pause();enable(true);return;}
enable(true);}
btns.forEach(b=>b.addEventListener('click',async()=>{const mode=b.dataset.listen;const g=++gen;enable(false);heard.textContent='';
endedAt=0;lastSig='';lastChange=Date.now();gaveUp=false;
say(mode==='live'?'STT에 연결하는 중':'저장된 전사를 여는 중',true);
if(audio){try{audio.currentTime=0;}catch(e){}const p=audio.play();if(p&&p.catch)p.catch(()=>say('브라우저가 자동 재생을 막았습니다 — 플레이어의 ▶를 눌러 주세요',false));}
let r,j;try{r=await fetch('/collect/'+SID+'/listen?mode='+mode,{method:'POST'});j=await r.json();}catch(e){r={ok:false};j={error:{message_ko:'서버에 연결하지 못했습니다'}};}
if(!r.ok){say((j.error&&j.error.message_ko)||'시작하지 못했습니다',false);if(audio)audio.pause();enable(true);return;}
say(LABEL[mode],true);poll(g);}));
if(RUNNING){enable(false);poll(gen);}
})();
</script>"""


def collect_script_page(state: dict, contract: dict, sid: str, banner: str = "") -> str:
    from . import collect, stt
    from .sense import tally
    s = collect.get(sid, contract)
    sid = s["script_id"]
    a = collect.audio(s)
    notes = load_notes()
    done = collect.collected(notes).get(sid)
    live = collect.live_read(sid)
    live = live if (live and a and live.get("audio_sha256") == a["audio_sha256"]) else None
    running = bool(live and live["status"] == "RUNNING")
    heard = live if (live and live["status"] == "DONE" and live.get("rec")) else None
    stt_err = collect.stt_problem()
    saved = collect.saved_transcript(s) if a and not stt_err else None
    key = collect.can_call_models()
    labels = _listen_labels()
    it = s["intent"]
    thr = contract["threshold"]
    cur = next((r for r in tally(state) if (r["segment"], r["signal_type"]) == (it["segment"], it["signal_type"])), {"mentions": 0, "hcps": 0})
    extracted = bool(done and any(c["doc_id"] == done["doc_id"] for c in state["claims"] + state["safety_queue"]))
    heard_cer = stt.cer(heard["text"], s["text"]) if heard else (done["stt"].get("cer") if done else None)
    gh = collect.blob_url(sid)

    # journey — the five steps of this stage, same strip as a hypothesis's journey
    derived = [h["id"] for h in state["hypotheses"] if done and any(c.startswith(f'CLM-{done["doc_id"]}-') for c in h["field"]["claim_ids"])]
    steps = [
        ("① 대본 · GitHub", f'{sid} · {s["chars"]}자', "저장소 data/field_scripts", "done"),
        ("② 음성 · TTS", f'{a["duration_s"]:.1f}초 · {a["tts"]["chunks"]}조각' if a else "없음", "Chatterbox Multilingual", "done" if a else ("next" if key else "later")),
        ("③ 듣기 · 받아 적기 · STT", f"CER {round(heard_cer * 100, 1):.1f}%" if heard_cer is not None else ("듣는 중" if running else "대기"),
         "Parakeet · 스트리밍", "done" if (heard or done) else ("next" if a and not stt_err else "later")),
        ("④ 면담 기록 · 동의", done["doc_id"] if done else ("동의 확인" if heard else "대기"), (done["stt"]["consent_by"] if done else ("사람 차례" if heard else "")),
         "done" if done else ("human" if heard else "later")),
        ("⑤ 추출 → 가설", (", ".join(derived) if derived else "발언 카드 반영") if extracted else "대기", "① 추출 (기존 단계)" if done else "",
         "done" if extracted else ("next" if done else "later")),
    ]
    strip = ('<div class="strip j c5">' + "".join(f'<div class="{c}"><div class="k">{esc(k)}</div><b>{esc(v)}</b><div class="s">{esc(w)}</div></div>'
                                                   for k, v, w, c in steps) + "</div>")
    head = (f'<div class="eyebrow">Collect · {esc(sid)} · <a href="/collect">대본 목록</a></div><h1>{esc(s["title_ko"])}</h1>'
            f'<div class="sub">{esc(s["hcp_ref"])} · {esc(s["specialty"])} · {esc(s["date"])} · 합성 대본 (가상 의료진)'
            + (f' · <a href="{esc(gh)}" target="_blank" rel="noopener">GitHub에서 보기</a>' if gh else "") + "</div>")

    # audio + provenance
    if a:
        t = a["tts"]
        where = "저장소에 구워 둔 음성" if a["source"] == "baked" else "이 서버에서 새로 만든 음성"
        audio_card = (f'<div class="card"><b>② 음성 — 평가자가 듣는 파일, STT가 듣는 파일</b>'
                      f'<audio id="player" controls preload="auto" src="/collect/{esc(sid)}/audio.mp3?v={esc(a["audio_sha256"][:8])}"></audio>'
                      f'<div class="faint">{tag("fact")}{esc(t["model"])} · {esc(t["voice"].split(".", 1)[-1].replace(".Male", " 남성"))} · '
                      f'{a["duration_s"]:.1f}초 · {t["chunks"]}조각 · MP3 48 kbps · sha256 <span class="mono">{esc(a["audio_sha256"][:8])}</span> · '
                      f'만든 때 {esc((t.get("created_at") or a.get("created_at") or "")[:16].replace("T", " "))} · {where}</div></div>')
    elif key:
        audio_card = ('<div class="card"><div class="row"><div class="grow"><b>② 음성이 아직 없다</b> <span class="sub">Chatterbox Multilingual(ko-KR 남성 음성)이 대본을 읽어 MP3 하나로 만든다. 약 5~15초.</span></div>'
                      + button("음성 만들기 (Chatterbox TTS)", "/run/collect/tts", {"script": sid}) + "</div></div>")
    else:
        audio_card = '<div class="card"><b>② 음성이 아직 없다</b> <span class="faint">— 음성을 만들려면 NVIDIA_API_KEY 가 필요하다.</span></div>'

    # listen controls
    btns = []
    if a and key and not stt_err:
        btns.append('<button class="btn" type="button" data-listen="live">▶ 재생하며 받아 적기</button>')
    if a and saved:
        btns.append(f'<button class="btn{" ghost" if key else ""}" type="button" data-listen="replay">▶ 재생하며 받아 적기 (저장된 전사)</button>')
    if running:
        status = f'<span class="dot"></span>{esc(labels.get(live["mode"], ""))}'
    elif heard:
        status = f'받아 적기 끝 — {esc(labels.get(heard["mode"], ""))}'
    elif live and live["status"] == "ERROR":
        status = f'이전 듣기가 중단됐다 — {esc(live.get("error") or "")}'
    elif stt_err:
        status = "STT 설정을 고칠 때까지 들을 수 없다 — 위의 안내를 본다."
    elif btns:
        status = "재생을 누르면 음성이 나오고, 같은 순간부터 STT가 같은 파일을 실제 속도로 들으며 받아 적는다. 플레이어를 멈춰도 STT는 멈추지 않는다."
    elif a:
        status = "저장된 전사가 없고 키도 없어 들을 수 없다."
    else:
        status = ""
    listen_bar = (f'<div class="bar">{"".join(btns)}</div><div class="live" id="lstatus">{status}</div>')

    # the two texts side by side
    if heard:
        heard_html = miss_marks(heard["text"], s["text"])
    elif running:
        heard_html = esc(" ".join(live.get("finals") or [])) + (f'<span class="interim caret"> {esc(live.get("interim"))}</span>' if live.get("interim") else "")
    else:
        heard_html = '<span class="faint">재생하며 받아 적기를 누르면 STT가 받아 적은 글이 여기에 차례로 올라온다. 회색은 아직 확정되지 않은 부분이다.</span>'
    tts_notes = f'<div class="faint" style="margin-top:8px">{esc(s["tts_notes_ko"])}</div>' if s.get("tts_notes_ko") else ""
    texts = ('<div class="grid" style="grid-template-columns:1fr 1fr">'
             f'<div class="card"><b>대본 — TTS가 읽는 글</b> <span class="faint">{s["chars"]}자</span><div class="script-text" style="margin-top:6px">{esc(s["text"])}</div>{tts_notes}</div>'
             f'<div class="card"><b>STT가 받아 적는 글</b> <span class="faint">{esc(stt.ENGINES["parakeet"]["model"])}</span>'
             f'<div class="heard" id="heard" style="margin-top:6px">{heard_html}</div></div></div>')

    result = ""
    if heard:
        c = round(stt.cer(heard["text"], s["text"]) * 100, 1)
        result = (f'<div class="card">{tag("fact")}대본 대비 <b>CER {c:.1f}%</b> — 띄어쓰기·문장부호 제외, 코드 계산 · '
                  f'음성 {heard["rec"]["duration_s"]}초 → {len(heard["text"])}자 · {esc(heard["rec"]["model"])}'
                  f'<div class="faint" style="margin-top:4px"><mark class="miss">표시</mark>는 대본과 다르게 받아 적은 곳이다 (코드 비교). '
                  '저장하면 이 글이 그대로 면담 기록이 된다 — 틀린 글자도 고치지 않는다. 추출 모델이 그 글에서 인용을 고르고, 코드는 그 글에서 인용을 찾는다.</div></div>')

    if done:
        d = esc(done["doc_id"])
        gate = (f'<div class="card gate">{tag("action")}<b>수집됨 → <a href="/notes#{d}">{d}</a></b> (면담 기록에서 보기) · 동의 확인 {esc(done["stt"]["consent_by"])} · '
                f'{esc(done["stt"].get("transcribed_at", "")[:16].replace("T", " "))}'
                '<div class="faint" style="margin-top:4px">같은 대본은 다시 저장하지 않는다. 다시 듣는 것은 된다. '
                + ('다음: <a href="/collect#effect">이번 수집이 바꾼 것</a>' if extracted else '다음: <a href="/collect#next">대본 목록에서 ① 추출 실행</a>') + "</div></div>")
    elif heard:
        gate = (f'<div class="card gate"><div class="row"><div class="grow"><b>④ 사람 차례 — 녹음 동의 확인</b> <span class="sub">녹음 동의를 확인한 사람의 이름이 있어야 면담 기록이 된다.</span>'
                f'<div class="faint" style="margin-top:4px">{tag("fact")}저장되는 값: 의료진 {esc(s["hcp_ref"])} · {esc(s["specialty"])} · {esc(s["date"])} — 대본에 고정된 값이라 여기서 고치지 않는다. '
                '글은 위에 STT가 받아 적은 글 그대로다.</div></div>'
                + button("녹음 동의를 확인했습니다 — 면담 기록으로 저장", "/run/collect/save", {"script": sid}, [("consent_by", "동의 확인자 이름")], turn=True)
                + "</div></div>")
    else:
        gate = ""

    intent = (f'<div class="card"><b>작성 의도</b> <span class="faint">— 대본을 쓴 사람의 설계 메모. 면담 기록에도, 추출 모델에도 들어가지 않는다.</span>'
              f'<div style="margin-top:6px">{esc(it["segment"])} × {signal(it["signal_type"])} · {tag("pattern")}지금 집계 {cur["mentions"]}회/{cur["hcps"]}인 '
              f'<span class="faint">(문턱 {thr["min_mentions"]}회·{thr["min_hcps"]}인, 코드)</span></div><div class="sub" style="margin-top:4px">{esc(it["why_ko"])}</div></div>')
    js = COLLECT_JS % {"sid": json.dumps(sid), "labels": json.dumps(labels, ensure_ascii=False), "running": json.dumps(running),
                       "backstop_ms": int((stt.STALL_S + collect.WATCHDOG_S + 5) * 1000)}
    body = head + strip + _stt_problem_card(stt_err) + audio_card + listen_bar + texts + result + gate + intent
    return shell(f"{sid} 현장 수집", body, "/collect", banner, band=band(state), script=js)
