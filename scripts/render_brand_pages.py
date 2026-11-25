"""Render the README's brand pages (hero, pillars, architecture, social card).

HTML/CSS is used instead of hand-placed SVG text so the typography, letter
spacing and optical alignment are real. The page is rendered at 2× and
downsampled to 2000px wide, keeping the PNG small enough for a README while
staying crisp on retina displays. A WebP copy is written for the READMEs; the
PNG stays as the master for slides and GitHub social previews.

Usage::

    python scripts/render_brand_pages.py

Requires Playwright + Chromium (development-only). All copy is kept here so
``README.md`` and ``README_zh.md`` never grow stale illustrations.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "brand"

MARK = (BRAND / "mark.svg").read_text(encoding="utf-8")

FONT = '"Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif'
MONO = '"DejaVu Sans Mono", "SFMono-Regular", "Cascadia Mono", monospace'


def _page_css() -> str:
    return f"""
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; background: #090a0d; }}
    body {{ font-family: {FONT}; color: #f7f1e8; -webkit-font-smoothing: antialiased; }}
    .page {{
      position: relative; overflow: hidden;
      background:
        radial-gradient(900px 560px at 78% 44%, rgba(255,122,26,.19), transparent 66%),
        radial-gradient(700px 430px at 8% 4%, rgba(255,183,116,.08), transparent 72%),
        linear-gradient(145deg, #0b0d11 0%, #090a0d 52%, #0c0b0b 100%);
    }}
    .page::before {{
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background-image: radial-gradient(circle, rgba(255,236,214,.115) 1px, transparent 1.4px);
      background-size: 28px 28px;
      mask-image: linear-gradient(110deg, rgba(0,0,0,.85) 0%, rgba(0,0,0,.35) 50%, transparent 82%);
    }}
    .page::after {{
      content: ""; position: absolute; inset: 0; pointer-events: none;
      background: radial-gradient(ellipse at center, transparent 52%, rgba(0,0,0,.52) 100%);
    }}
    .mark {{ line-height: 0; }}
    .mark svg {{ display: block; width: 100%; height: 100%; }}
    .wordmark {{ letter-spacing: -0.045em; font-weight: 900; }}
    .kicker {{
      display: inline-flex; align-items: center; gap: 10px;
      color: #c8b8a6; font-family: {MONO}; font-size: 11px;
      letter-spacing: .22em; text-transform: uppercase;
    }}
    .kicker i {{
      width: 7px; height: 7px; border-radius: 50%; background: #ff8a3d;
      box-shadow: 0 0 16px rgba(255,138,61,.9);
    }}
    .chip {{
      display: inline-flex; align-items: center; gap: 8px;
      border: 1px solid rgba(255,236,214,.14); border-radius: 999px;
      background: rgba(255,244,230,.035); color: #cfc2b3;
      font-family: {MONO}; font-size: 10px; letter-spacing: .14em;
      text-transform: uppercase; padding: 9px 14px;
    }}
    .chip b {{ color: #ff9a53; font-weight: 500; }}
    """


def hero_html(lang: str) -> str:
    if lang == "zh":
        kicker = "OPEN SOURCE · 本地优先 · MIT"
        headline = "一个 Agent 循环，<br><em>穿过你所有的屏幕。</em>"
        lede = (
            "把浏览器变成执行面。每次写入先过审批与沙箱，每一步都收进可回滚的账本。<br>"
            "代码、密钥与上下文，始终在你自己手里。"
        )
        chips = ["浏览器原生", "审批即刹车", "可自托管", "随时回滚"]
        caption = "一条循环 · 一本账"
    else:
        kicker = "OPEN SOURCE · LOCAL-FIRST · MIT"
        headline = "One agent loop.<br><em>Every surface you code on.</em>"
        lede = (
            "The browser becomes an execution surface.<br>"
            "Every write passes approval and a sandbox; every turn can be taken back.<br>"
            "Your code, keys and context stay on your machine."
        )
        chips = ["Browser-native", "Approval-gated", "Self-hosted", "Rollback"]
        caption = "ONE LOOP · ONE LEDGER"
    chips_html = "".join(f'<span class="chip"><b>·</b>{c}</span>' for c in chips)
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>
    {_page_css()}
    .page {{ width: 1600px; height: 640px; padding: 64px 74px 44px; }}
    .hero {{ position: relative; z-index: 2; display: flex; height: 100%; }}
    .left {{ width: 850px; padding-top: 6px; }}
    .brandrow {{ display: flex; align-items: center; gap: 17px; margin-top: 30px; }}
    .brandrow .mark {{ width: 76px; height: 76px; }}
    .brandtype {{ display: flex; flex-direction: column; gap: 1px; }}
    .brandtype .wordmark {{ font-size: 56px; line-height: 1; color: #f8f2e9; }}
    .brandtype .desc {{
      margin-left: 2px; margin-top: 7px; color: #9f9387; font-family: {MONO};
      font-size: 9px; letter-spacing: .30em;
    }}
    h1 {{
      margin: 36px 0 0; font-size: 59px; line-height: 1.13;
      font-weight: 900; letter-spacing: -.045em; color: #fbf6ed;
    }}
    h1 em {{ font-style: normal; color: #ff9a53; }}
    p.lede {{
      margin: 25px 0 0; width: 720px; color: #b9ada0;
      font-size: 18px; line-height: 1.85; letter-spacing: .01em;
    }}
    .chips {{ display: flex; gap: 9px; margin-top: 30px; flex-wrap: wrap; }}
    .stage {{
      position: absolute; right: -56px; top: -12px; width: 780px; height: 610px;
    }}
    .stage .halo {{
      position: absolute; left: 160px; top: 120px; width: 470px; height: 360px;
      background: radial-gradient(ellipse, rgba(255,122,26,.30), transparent 65%);
      filter: blur(16px); }}
    .stage svg.orbits {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    .stage .core {{
      position: absolute; left: 298px; top: 218px; width: 184px; height: 184px;
      filter: drop-shadow(0 24px 54px rgba(255,122,26,.36));
    }}
    .stage .looplabel {{
      position: absolute; left: 298px; top: 424px; width: 184px; text-align: center;
      font-family: {MONO}; font-size: 9px; letter-spacing: .25em; color: #82776c;
    }}
    .stage .tag {{
      position: absolute; font-family: {MONO}; font-size: 10px;
      letter-spacing: .18em; color: #b7aa9c;
    }}
    .stage .tag span {{ color: #ff9a53; margin-right: 8px; }}
    .t1 {{ left: 83px; top: 128px; }}
    .t2 {{ right: 52px; top: 225px; }}
    .t3 {{ right: 92px; top: 395px; }}
    .t4 {{ left: 105px; top: 420px; }}
    footer {{
      position: absolute; z-index: 3; left: 74px; right: 74px; bottom: 32px;
      display: flex; justify-content: space-between; color: #6d645b;
      font-family: {MONO}; font-size: 10px; letter-spacing: .12em;
    }}
    footer b {{ color: #9b8f83; font-weight: 500; }}
    </style></head><body><div class="page"><div class="hero">
      <section class="left">
        <div class="kicker"><i></i>{kicker}</div>
        <div class="brandrow"><div class="mark">{MARK}</div>
          <div class="brandtype"><span class="wordmark">spark</span><span class="desc">AI CODING AGENT</span></div>
        </div>
        <h1>{headline}</h1>
        <p class="lede">{lede}</p>
        <div class="chips">{chips_html}</div>
      </section>
      <section class="stage">
        <div class="halo"></div>
        <svg class="orbits" viewBox="0 0 780 610" fill="none">
          <ellipse cx="390" cy="310" rx="330" ry="166" transform="rotate(-24 390 310)" stroke="#ff9a53" stroke-opacity=".34" stroke-width="1.1"/>
          <ellipse cx="390" cy="310" rx="258" ry="120" transform="rotate(28 390 310)" stroke="#f7e6d2" stroke-opacity=".13" stroke-width="1"/>
          <ellipse cx="390" cy="310" rx="205" ry="205" stroke="#f7e6d2" stroke-opacity=".055" stroke-width="1"/>
          <circle cx="92" cy="212" r="4.5" fill="#ffb173"/>
          <circle cx="92" cy="212" r="12" stroke="#ff8a3d" stroke-opacity=".25"/>
          <circle cx="684" cy="185" r="3.5" fill="#f7e6d2" fill-opacity=".75"/>
          <circle cx="672" cy="431" r="4" fill="#ff8a3d"/>
          <circle cx="134" cy="424" r="3" fill="#f7e6d2" fill-opacity=".5"/>
        </svg>
        <div class="core">{MARK}</div>
        <div class="looplabel">{caption}</div>
        <div class="tag t1"><span>01</span>PLAN</div>
        <div class="tag t2"><span>02</span>ACT</div>
        <div class="tag t3"><span>03</span>VERIFY</div>
        <div class="tag t4"><span>04</span>ROLL BACK</div>
      </section>
    </div>
    <footer><span>github.com/badhope/spark</span><span><b>v0.15.9</b> · MIT · 2000+ tests green</span></footer>
    </div></body></html>"""


def pillars_html(lang: str) -> str:
    if lang == "zh":
        kicker = "THE NON-NEGOTIABLES"
        title = "三条底线，不拿来当卖点。"
        items = [
            ("01", "密钥留在你手里", "任意 OpenAI 兼容端点，或完全本地模型。没有暗箱转发，没有替你决定。", "key"),
            ("02", "写入之前，先过闸门", "审批、沙箱、规则三层逐级收窄；deny 永远优先，克隆来的仓库不能给自己放行。", "gate"),
            ("03", "每一步，都退得回去", "每个回合快照工作区，文件和对话一起回滚，非 git 目录同样有效。", "rewind"),
        ]
    else:
        kicker = "THE NON-NEGOTIABLES"
        title = "Three promises we do not trade away."
        items = [
            ("01", "Your keys stay yours", "Any OpenAI-compatible endpoint, or a fully local model. No hidden relay, no decision made for you.", "key"),
            ("02", "Nothing writes without a gate", "Approval, sandbox and rules narrow in three layers. Deny always wins; a cloned repo cannot grant itself access.", "gate"),
            ("03", "Every turn has a way back", "Each turn snapshots the workspace. Files and conversation roll back together, even outside git.", "rewind"),
        ]
    icons = {
        "key": '<svg viewBox="0 0 48 48" fill="none" stroke="#ff9a53" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="20" r="8"/><path d="M24 26l13 13M31 33l5-5M34 36l4-4"/></svg>',
        "gate": '<svg viewBox="0 0 48 48" fill="none" stroke="#ff9a53" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M24 6l15 6v11c0 9-6 15-15 19C15 38 9 32 9 23V12z"/><path d="M17 24l5 5 10-11"/></svg>',
        "rewind": '<svg viewBox="0 0 48 48" fill="none" stroke="#ff9a53" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 18h13a9 9 0 010 18H18"/><path d="M19 12l-6 6 6 6"/><path d="M31 30l6 6-6 6" opacity=".45"/></svg>',
    }
    cards = "".join(
        f"""<div class="card"><div class="cardtop"><div class="icon">{icons[kind]}</div><span class="num">{num}</span></div>
        <h2>{title_}</h2><p>{body}</p></div>"""
        for num, title_, body, kind in items
    )
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>
    {_page_css()}
    .page {{ width: 1600px; height: 440px; padding: 52px 64px 40px; }}
    .head {{ position: relative; z-index: 2; display: flex; align-items: flex-end; justify-content: space-between; }}
    .head .kicker {{ margin-bottom: 12px; }}
    .title {{ margin: 0; font-size: 38px; letter-spacing: -.035em; font-weight: 900; }}
    .title span {{ color: #ff9a53; }}
    .note {{ color: #756b61; font-family: {MONO}; font-size: 10px; letter-spacing: .16em; }}
    .grid {{ position: relative; z-index: 2; display: flex; gap: 22px; margin-top: 36px; }}
    .card {{
      width: 486px; height: 252px; padding: 27px 30px 24px; border-radius: 18px;
      border: 1px solid rgba(255,236,214,.12);
      background: linear-gradient(155deg, rgba(255,244,230,.055), rgba(255,244,230,.018) 58%, rgba(255,122,26,.025));
      box-shadow: inset 0 1px 0 rgba(255,255,255,.05);
    }}
    .cardtop {{ display: flex; align-items: center; justify-content: space-between; }}
    .icon {{
      width: 52px; height: 52px; padding: 9px; border-radius: 14px;
      border: 1px solid rgba(255,154,83,.24); background: rgba(255,122,26,.08);
    }}
    .icon svg {{ width: 100%; height: 100%; }}
    .num {{ color: #6d6257; font-family: {MONO}; font-size: 10px; letter-spacing: .18em; }}
    h2 {{ margin: 22px 0 0; font-size: 23px; line-height: 1.2; letter-spacing: -.02em; }}
    p {{ margin: 12px 0 0; color: #a89c8f; font-size: 14px; line-height: 1.75; }}
    </style></head><body><div class="page"><div class="head">
      <div><div class="kicker"><i></i>{kicker}</div><h2 class="title">{title}</h2></div>
      <div class="note">APPROVAL · SANDBOX · RULES · SNAPSHOTS</div>
    </div><div class="grid">{cards}</div></div></body></html>"""


def architecture_html(lang: str) -> str:
    if lang == "zh":
        kicker = "ARCHITECTURE · ONE LOOP"
        title = "六个入口，同一条循环。"
        subtitle = "不是六套彼此漂移的 Agent，而是一套执行面、一本账、一组护栏。"
        surfaces = [("WEB", "浏览器 UI"), ("TUI / CLI", "终端与脚本"), ("DESKTOP", "原生窗口"), ("IDE / ACP", "编辑器内"), ("MOBILE", "手机与 PWA"), ("HTTP API", "自建前端")]
        loop_title = "THE AGENT LOOP"
        loop_sub = "plan · act · approve · verify · checkpoint · roll back"
        stages = ["PLAN", "ACT", "APPROVE", "VERIFY", "CHECKPOINT"]
        cards = [
            ("TOOLS", "工具面", ["Browser · Files · Shell", "LSP · MCP · Skills", "Workflow · Sub-agents"]),
            ("GUARDRAILS", "护栏", ["Approval modes", "Sandbox levels", "Wildcard rules · Deny wins"]),
            ("LEDGER", "记忆与账本", ["Sessions · Context", "Workspace snapshots", "Rollback · Audit trail"]),
        ]
        note = "EVERY SURFACE SHARES THE SAME LOOP, APPROVALS AND LEDGER"
    else:
        kicker = "ARCHITECTURE · ONE LOOP"
        title = "Six surfaces. One loop."
        subtitle = "Not six drifting agents — one execution surface, one ledger, one set of guardrails."
        surfaces = [("WEB", "Browser UI"), ("TUI / CLI", "Terminal & scripts"), ("DESKTOP", "Native window"), ("IDE / ACP", "Inside the editor"), ("MOBILE", "Phone & PWA"), ("HTTP API", "Your own frontend")]
        loop_title = "THE AGENT LOOP"
        loop_sub = "plan · act · approve · verify · checkpoint · roll back"
        stages = ["PLAN", "ACT", "APPROVE", "VERIFY", "CHECKPOINT"]
        cards = [
            ("TOOLS", "Capability", ["Browser · Files · Shell", "LSP · MCP · Skills", "Workflow · Sub-agents"]),
            ("GUARDRAILS", "Guardrails", ["Approval modes", "Sandbox levels", "Wildcard rules · Deny wins"]),
            ("LEDGER", "Memory & ledger", ["Sessions · Context", "Workspace snapshots", "Rollback · Audit trail"]),
        ]
        note = "EVERY SURFACE SHARES THE SAME LOOP, APPROVALS AND LEDGER"

    surface_icons = [
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5"><rect x="5" y="7" width="26" height="22" rx="3"/><path d="M5 13h26M9 10h.01M12 10h.01"/></svg>',
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5" stroke-linecap="round"><path d="M10 12l7 6-7 6M19 25h8"/><rect x="4" y="6" width="28" height="24" rx="3"/></svg>',
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5"><rect x="5" y="7" width="26" height="17" rx="2"/><path d="M14 30h8M18 24v6"/></svg>',
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5" stroke-linecap="round"><path d="M13 11l-6 7 6 7M23 11l6 7-6 7M20 8l-4 20"/></svg>',
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5"><rect x="10" y="4" width="16" height="28" rx="4"/><path d="M16 28h4"/></svg>',
        '<svg viewBox="0 0 36 36" fill="none" stroke="#ff9a53" stroke-width="1.5" stroke-linecap="round"><path d="M13 9l-7 9 7 9M23 9l7 9-7 9M21 6l-6 24"/></svg>',
    ]
    surface_html = "".join(
        f'<div class="surface"><div class="sicon">{surface_icons[i]}</div><div><span class="sname">{name}</span><span class="sdesc">{desc}</span></div></div>'
        for i, (name, desc) in enumerate(surfaces)
    )
    stages_html = "".join(f'<span class="stagepill">{i + 1:02d} {s}</span>' for i, s in enumerate(stages))
    cards_html = "".join(
        f'<div class="ledgercard"><span class="ck">{k}</span><h3>{h}</h3>' + "".join(f"<p>{line}</p>" for line in lines) + "</div>"
        for k, h, lines in cards
    )
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>
    {_page_css()}
    .page {{ width: 1600px; height: 900px; padding: 48px 80px 38px; }}
    .head {{ position: relative; z-index: 2; display: flex; justify-content: space-between; align-items: flex-end; }}
    .head .kicker {{ margin-bottom: 11px; }}
    h1 {{ margin: 0; font-size: 44px; line-height: 1.1; letter-spacing: -.045em; font-weight: 900; }}
    h1 span {{ color: #ff9a53; }}
    .sub {{ margin: 12px 0 0; color: #9f9387; font-size: 14px; }}
    .stamp {{ color: #6f655b; font-family: {MONO}; font-size: 9px; letter-spacing: .17em; text-align: right; line-height: 1.8; }}
    .surfaces {{ position: relative; z-index: 2; display: flex; gap: 17px; margin-top: 34px; }}
    .surface {{
      width: 224px; height: 92px; padding: 17px 16px; border-radius: 14px;
      border: 1px solid rgba(255,236,214,.12);
      background: linear-gradient(150deg, rgba(255,244,230,.055), rgba(255,244,230,.016));
      display: flex; align-items: center; gap: 13px;
    }}
    .sicon {{ width: 36px; height: 36px; flex: none; opacity: .9; }}
    .sname {{ display: block; font-family: {MONO}; font-size: 10px; letter-spacing: .13em; color: #f2e8dc; }}
    .sdesc {{ display: block; margin-top: 5px; color: #85796d; font-size: 11px; }}
    .beam {{ position: relative; z-index: 2; height: 86px; margin: 0 4px; }}
    .beam svg {{ width: 100%; height: 100%; }}
    .loopwrap {{ position: relative; z-index: 2; margin-top: -4px; }}
    .loop {{
      height: 174px; border-radius: 22px; padding: 28px 34px;
      border: 1px solid rgba(255,154,83,.28);
      background: radial-gradient(600px 220px at 20% 10%, rgba(255,122,26,.13), transparent 70%), linear-gradient(140deg, rgba(255,244,230,.055), rgba(255,122,26,.035));
      box-shadow: 0 24px 70px -50px rgba(255,122,26,.7), inset 0 1px 0 rgba(255,255,255,.06);
      display: flex; align-items: center; gap: 26px;
    }}
    .loop .mark {{ width: 82px; height: 82px; flex: none; }}
    .loopcopy {{ width: 395px; }}
    .loopcopy .k {{ color: #ff9a53; font-family: {MONO}; font-size: 9px; letter-spacing: .2em; }}
    .loopcopy h2 {{ margin: 7px 0 0; font-size: 30px; letter-spacing: -.035em; }}
    .loopcopy p {{ margin: 9px 0 0; color: #9d9185; font-family: {MONO}; font-size: 10px; letter-spacing: .07em; }}
    .stages {{ display: flex; flex-wrap: wrap; gap: 7px; width: 470px; }}
    .stagepill {{
      border: 1px solid rgba(255,236,214,.13); border-radius: 999px; padding: 8px 12px;
      color: #c5b7a8; font-family: {MONO}; font-size: 9px; letter-spacing: .09em;
      background: rgba(0,0,0,.22);
    }}
    .ledgers {{ position: relative; z-index: 2; display: flex; gap: 22px; margin-top: 24px; }}
    .ledgercard {{
      width: 486px; height: 192px; border-radius: 18px; padding: 22px 26px;
      border: 1px solid rgba(255,236,214,.10);
      background: linear-gradient(150deg, rgba(255,244,230,.04), rgba(255,244,230,.012));
    }}
    .ck {{ color: #ff9a53; font-family: {MONO}; font-size: 9px; letter-spacing: .18em; }}
    h3 {{ margin: 6px 0 10px; font-size: 20px; letter-spacing: -.02em; }}
    p {{ margin: 5px 0; color: #9c9084; font-size: 12px; }}
    p::before {{ content: "·"; color: #ff8a3d; margin-right: 8px; }}
    .note {{ position: absolute; z-index: 2; left: 80px; right: 80px; bottom: 25px; color: #635a51; font-family: {MONO}; font-size: 9px; letter-spacing: .12em; text-align: center; }}
    </style></head><body><div class="page"><div class="head">
      <div><div class="kicker"><i></i>{kicker}</div><h1>{title}</h1><p class="sub">{subtitle}</p></div>
      <div class="stamp">SPARK / AI CODING AGENT<br>LOCAL-FIRST · SELF-HOSTED</div>
    </div>
    <div class="surfaces">{surface_html}</div>
    <div class="beam"><svg viewBox="0 0 1440 86" fill="none">
      <path d="M70 0V16C70 42 116 48 240 48H1200C1324 48 1370 42 1370 16V0" stroke="#ff9a53" stroke-opacity=".22"/>
      <path d="M680 48H760" stroke="#ff9a53" stroke-opacity=".45"/>
      <circle cx="720" cy="48" r="3" fill="#ff9a53"/>
      <path d="M720 48V82" stroke="#ff9a53" stroke-opacity=".45"/>
    </svg></div>
    <div class="loopwrap"><div class="loop"><div class="mark">{MARK}</div>
      <div class="loopcopy"><span class="k">ONE ENGINE</span><h2>{loop_title}</h2><p>{loop_sub}</p></div>
      <div class="stages">{stages_html}</div>
    </div></div>
    <div class="ledgers">{cards_html}</div>
    <div class="note">{note}</div>
    </div></body></html>"""


def social_html() -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
    {_page_css()}
    .page {{ width: 1280px; height: 640px; padding: 64px 72px; }}
    .wrap {{ position: relative; z-index: 2; display: flex; height: 100%; }}
    .left {{ width: 760px; padding-top: 14px; }}
    .brandrow {{ display: flex; align-items: center; gap: 16px; }}
    .mark {{ width: 78px; height: 78px; }}
    .wordmark {{ font-size: 62px; line-height: .9; }}
    .desc {{ margin: 9px 0 0 2px; color: #a19487; font-family: {MONO}; font-size: 9px; letter-spacing: .29em; }}
    h1 {{ margin: 48px 0 0; font-size: 55px; line-height: 1.12; letter-spacing: -.045em; }}
    h1 em {{ font-style: normal; color: #ff9a53; }}
    .lede {{ margin: 21px 0 0; width: 650px; color: #b6a99c; font-size: 16px; line-height: 1.8; }}
    .chips {{ display: flex; gap: 8px; margin-top: 26px; }}
    .chips .chip {{ font-size: 9px; }}
    .stage {{ position: absolute; right: -50px; top: 10px; width: 590px; height: 520px; }}
    .stage .halo {{ position: absolute; left: 100px; top: 80px; width: 370px; height: 300px; background: radial-gradient(ellipse, rgba(255,122,26,.30), transparent 66%); filter: blur(14px); }}
    .stage svg {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
    .stage .core {{ position: absolute; left: 216px; top: 168px; width: 156px; height: 156px; filter: drop-shadow(0 20px 46px rgba(255,122,26,.4)); }}
    .bottom {{ position: absolute; z-index: 2; left: 72px; right: 72px; bottom: 34px; display: flex; justify-content: space-between; color: #70665c; font-family: {MONO}; font-size: 9px; letter-spacing: .13em; }}
    .bottom b {{ color: #a19487; font-weight: 500; }}
    </style></head><body><div class="page"><div class="wrap"><div class="left">
      <div class="brandrow"><div class="mark">{MARK}</div><div><div class="wordmark">spark</div><div class="desc">AI CODING AGENT</div></div></div>
      <h1>One agent loop.<br><em>Every surface.</em></h1>
      <p class="lede">Browser-native, approval-gated, self-hosted. Your code, keys and context stay on your machine — every turn can be rolled back.</p>
      <div class="chips"><span class="chip"><b>·</b>OPEN SOURCE</span><span class="chip"><b>·</b>MIT</span><span class="chip"><b>·</b>SELF-HOSTED</span></div>
    </div><div class="stage"><div class="halo"></div>
      <svg viewBox="0 0 590 520" fill="none">
        <ellipse cx="294" cy="246" rx="250" ry="128" transform="rotate(-24 294 246)" stroke="#ff9a53" stroke-opacity=".33"/>
        <ellipse cx="294" cy="246" rx="196" ry="92" transform="rotate(28 294 246)" stroke="#f7e6d2" stroke-opacity=".12"/>
        <circle cx="52" cy="158" r="3.5" fill="#ffb173"/><circle cx="530" cy="140" r="3" fill="#f7e6d2" fill-opacity=".7"/><circle cx="506" cy="348" r="3.5" fill="#ff8a3d"/>
      </svg>
      <div class="core">{MARK}</div>
    </div></div>
    <div class="bottom"><span>github.com/badhope/spark</span><b>ONE LOOP · EVERY SURFACE · YOUR RULES</b></div>
    </div></body></html>"""


def render(
    html: str,
    name: str,
    width: int,
    height: int,
    out_width: int,
    *,
    out_dir: Path = BRAND,
    write_webp: bool = True,
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/local/bin/chromium")
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        page.set_content(html, wait_until="load")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(350)
        tmp = BRAND / f".{name}.2x.png"
        page.screenshot(path=str(tmp), full_page=False, type="png")
        browser.close()

    img = Image.open(tmp).convert("RGB")
    if img.width != out_width:
        ratio = out_width / img.width
        img = img.resize((out_width, round(img.height * ratio)), Image.LANCZOS)
    out = out_dir / name
    img.save(out, optimize=True)
    extra = ""
    if write_webp:
        webp = out.with_suffix(".webp")
        img.save(webp, "WEBP", quality=90, method=6)
        extra = f"  (webp {webp.stat().st_size // 1024} KB)"
    tmp.unlink(missing_ok=True)
    print(f"  {name}  {img.width}×{img.height}  {out.stat().st_size // 1024} KB{extra}")


def main() -> int:
    BRAND.mkdir(exist_ok=True)
    render(hero_html("zh"), "hero-zh.png", 1600, 640, 2000)
    render(hero_html("en"), "hero-en.png", 1600, 640, 2000)
    render(pillars_html("zh"), "pillars-zh.png", 1600, 440, 2000)
    render(pillars_html("en"), "pillars-en.png", 1600, 440, 2000)
    render(architecture_html("zh"), "architecture-zh.png", 1600, 900, 2000)
    render(architecture_html("en"), "architecture-en.png", 1600, 900, 2000)
    render(social_html(), "social-preview.png", 1280, 640, 1280, out_dir=ROOT, write_webp=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
