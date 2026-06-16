"""
@file_name: welcome_templates.py
@author: Bin Liang
@date: 2026-06-16
@description: Bilingual (EN default + 中文 toggle), designed first-run "welcome"
              artifact. Refined but lively — a tinted hero, colored line-icon
              feature cards, serif display, elegant prompt blocks — crafted, not
              auto-generated. `bilingual_html()` is the shared chrome (classes:
              .hero/.kicker/h1/.lead/.grid/.card/.icon + .c-* / .callout/.prompt/
              .steps/.foot). Copy mirrors www.narra.nexus ("An agent team, ready
              in one click."). The generic NarraNexus welcome lives here; the
              Arena welcome builds its own bodies and calls `bilingual_html()`.

It is a `text/html` pointer-model artifact (artifact_runner): written into the
agent workspace and pinned (agent-scoped) at provisioning time.
"""

from __future__ import annotations

# Reusable inline line-icons (stroke = currentColor, so .c-* sets the color).
ICONS = {
    "team": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><circle cx="17.5" cy="9" r="2.2"/><path d="M16 14.2a5 5 0 0 1 4.5 4.8"/></svg>',
    "memory": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z"/><path d="M3 12.5l9 4.5 9-4.5"/></svg>',
    "persona": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>',
    "jobs": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
    "social": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="6" cy="6.5" r="2.1"/><circle cx="18" cy="7.5" r="2.1"/><circle cx="11.5" cy="18" r="2.1"/><path d="M7.7 7.7 10.4 16M16.4 9 13 16M8 6.4l8 .6"/></svg>',
    "artifact": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M3.5 9h17"/><path d="M6.5 6.7h.01"/></svg>',
    "coin": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="12" cy="12" r="8.5"/><path d="M13.6 9.6h-3a1.7 1.7 0 0 0 0 3.4h2a1.7 1.7 0 0 1 0 3.4h-3.2M12 7.8v.9M12 15.6v.9"/></svg>',
    "play": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M10.3 9.2 15 12l-4.7 2.8V9.2Z"/></svg>',
}

_SKELETON = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; --ink:#17171b; --paper:#fbfaf8; --muted:#66666e;
          --line:#eae7e0; --accent:#4f46e5; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--paper); color: var(--ink); line-height: 1.62;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
         "PingFang SC", "Microsoft YaHei", sans-serif; -webkit-font-smoothing: antialiased; }
  .serif { font-family: "Iowan Old Style","Palatino Linotype",Palatino,"Source Han Serif SC","Songti SC",Georgia,serif; }
  .wrap { max-width: 660px; margin: 0 auto; padding: 30px 26px 64px; animation: fade .7s ease both; }
  .toggle { position: fixed; top: 18px; right: 20px; z-index: 5; font-size: 12.5px;
            display: flex; align-items: center; gap: 7px; }
  .toggle button { font: inherit; background: none; border: 0; cursor: pointer; color: #b1b0a8; transition: color .2s; }
  .toggle button.active { color: var(--ink); font-weight: 600; }
  .toggle .sep { color: #d8d6cf; }

  .hero { position: relative; overflow: hidden; border-radius: 20px; padding: 38px 34px 34px;
          border: 1px solid #e7e9f6;
          background: radial-gradient(120% 140% at 100% 0%, #eef1ff 0%, #f4f0ff 38%, #fbfaf8 78%);
          animation: rise .6s cubic-bezier(.2,.7,.3,1) both; }
  .hero .dots { position: absolute; right: 26px; top: 26px; display: grid;
                grid-template-columns: repeat(3,8px); gap: 7px; opacity: .55; }
  .hero .dots i { width: 8px; height: 8px; border-radius: 50%; background: #6366f1; }
  .hero .dots i:nth-child(3n+2){ background:#10b981 } .hero .dots i:nth-child(3n){ background:#f59e0b }
  .kicker { font-size: 11.5px; letter-spacing: .2em; text-transform: uppercase; color: var(--accent); font-weight: 700; }
  h1 { font-family: "Iowan Old Style","Palatino Linotype",Palatino,"Source Han Serif SC","Songti SC",Georgia,serif;
       font-weight: 600; font-size: 33px; line-height: 1.16; letter-spacing: -.012em; color: #131318; margin-top: 14px; }
  .hero .lead { margin-top: 14px; font-size: 15.5px; color: #54545c; max-width: 38em; }

  .label { font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: #a6a49c;
           font-weight: 700; margin: 34px 4px 14px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(258px,1fr)); gap: 13px; }
  .card { display: flex; gap: 13px; background: #fff; border: 1px solid var(--line);
          border-radius: 14px; padding: 15px 16px; transition: transform .16s, box-shadow .16s, border-color .16s;
          animation: rise .55s both; }
  .card:hover { transform: translateY(-2px); box-shadow: 0 10px 22px -12px rgba(20,20,40,.18); border-color: #ddd9d0; }
  .icon { flex: 0 0 36px; width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center; }
  .icon svg { width: 20px; height: 20px; }
  .card h3 { font-size: 14.5px; font-weight: 650; margin-bottom: 3px; }
  .card p { font-size: 13px; color: #66666e; line-height: 1.5; }
  .c-indigo .icon{ background:#eef0fe; color:#4f46e5 } .c-emerald .icon{ background:#e7f6ef; color:#059669 }
  .c-amber .icon{ background:#fdf2e2; color:#d97706 } .c-sky .icon{ background:#e6f3fb; color:#0284c7 }
  .c-rose .icon{ background:#fdecf0; color:#e11d48 } .c-violet .icon{ background:#f3eefe; color:#7c3aed }
  .grid .card:nth-child(2){animation-delay:.05s} .grid .card:nth-child(3){animation-delay:.1s}
  .grid .card:nth-child(4){animation-delay:.15s} .grid .card:nth-child(5){animation-delay:.2s} .grid .card:nth-child(6){animation-delay:.25s}

  .callout { border: 1px solid #e7e9f6; border-left: 2.5px solid var(--accent); border-radius: 11px;
             padding: 16px 18px; background: #fbfbff; font-size: 14px; color: #3d3d44; }
  .callout b { color: var(--ink); }
  .prompt { font-size: 15px; color: #34343b; padding: 8px 0 8px 16px; border-left: 2px solid #e3e0d7; margin: 3px 0; }
  .prompt .q { font-family: "Iowan Old Style",Palatino,"Songti SC",Georgia,serif; font-style: italic; }
  .steps { list-style: none; counter-reset: s; }
  .steps li { counter-increment: s; position: relative; padding: 9px 0 9px 32px; font-size: 14.5px;
              color: #3d3d44; border-bottom: 1px solid #f1efe8; }
  .steps li:last-child { border-bottom: 0; }
  .steps li::before { content: counter(s); position: absolute; left: 0; top: 9px; width: 21px; height: 21px;
              border: 1px solid var(--accent); color: var(--accent); border-radius: 50%; font-size: 11px;
              font-weight: 700; display: grid; place-items: center; }
  .foot { margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--line); font-size: 12.5px; color: #8e8c85; }
  .foot a { color: var(--accent); text-decoration: none; font-weight: 600; }
  .foot a:hover { text-decoration: underline; }

  @keyframes fade { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
  @keyframes rise { from{opacity:0;transform:translateY(14px)} to{opacity:1;transform:none} }
  @media (prefers-color-scheme: dark) {
    :root { --ink:#e7e7ea; --paper:#0e0e11; --muted:#9b9ba3; --line:#222227; }
    h1 { color:#fafafa } .hero { border-color:#23233a;
         background: radial-gradient(120% 140% at 100% 0%, #1a1a33 0%, #181527 40%, #0e0e11 80%); }
    .hero .lead { color:#a1a1aa } .card { background:#15151a } .card p { color:#9b9ba3 }
    .callout { background:#15151c; border-color:#23233a; border-left-color:#818cf8; color:#c4c4cc }
    .prompt { color:#c4c4cc; border-left-color:#2f2f35 } .steps li { border-color:#1c1c20; color:#c4c4cc }
    .steps li::before { border-color:#818cf8; color:#818cf8 } .kicker { color:#818cf8 } .label { color:#71717a }
    .toggle button.active { color:#fafafa } .toggle button { color:#6f6f78 } .toggle .sep { color:#3a3a40 }
    .foot a { color:#818cf8 }
    .c-indigo .icon{ background:#1e1f3a } .c-emerald .icon{ background:#0f2a22 } .c-amber .icon{ background:#2e2113 }
    .c-sky .icon{ background:#10283a } .c-rose .icon{ background:#2e1622 } .c-violet .icon{ background:#221a3a }
  }
</style>
</head>
<body>
  <div class="toggle">
    <button id="btn-en" class="active" onclick="setLang('en')">EN</button>
    <span class="sep">·</span>
    <button id="btn-zh" onclick="setLang('zh')">中文</button>
  </div>
  <div class="wrap">
    <div class="lang lang-en">__EN__</div>
    <div class="lang lang-zh" hidden>__ZH__</div>
  </div>
  <script>
    function setLang(l){
      document.querySelector('.lang-en').hidden=(l!=='en');
      document.querySelector('.lang-zh').hidden=(l!=='zh');
      document.getElementById('btn-en').classList.toggle('active',l==='en');
      document.getElementById('btn-zh').classList.toggle('active',l==='zh');
      document.documentElement.lang=l;
    }
  </script>
</body>
</html>
"""


def bilingual_html(title: str, en_html: str, zh_html: str) -> str:
    """Wrap EN + 中文 bodies in the shared designed chrome (EN shown first)."""
    return (
        _SKELETON.replace("__TITLE__", title)
        .replace("__EN__", en_html)
        .replace("__ZH__", zh_html)
    )


def feature_card(color: str, icon: str, title: str, desc: str) -> str:
    """Build one icon feature card (color = a .c-* class; icon = an ICONS key)."""
    return (
        f'<div class="card {color}"><div class="icon">{ICONS[icon]}</div>'
        f'<div><h3>{title}</h3><p>{desc}</p></div></div>'
    )


# internal alias used by the default content below
_card = feature_card


# ── Generic NarraNexus welcome (default profile) ────────────────────────────

_GH = "github.com/NetMindAI-Open/NarraNexus"

_DEFAULT_EN = f"""
<div class="hero">
  <div class="dots"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <p class="kicker">NarraNexus</p>
  <h1>An agent team,<br>ready in one click.</h1>
  <p class="lead">Not another framework for wiring agents together — a ready-to-run team
  that already remembers, collaborates, and uses tools.</p>
</div>

<p class="label">What you're working with</p>
<div class="grid">
  {_card("c-emerald", "team", "Brings its own team", "One agent can spin up others, hand off work, and share context across the squad.")}
  {_card("c-indigo", "memory", "Remembers everything", "Memory persists across every conversation — no need to repeat yourself.")}
  {_card("c-amber", "persona", "Becomes the expert", "Give it a role; it shapes its awareness and installs the skills to match.")}
  {_card("c-sky", "jobs", "Works while you sleep", "Schedule recurring and background jobs that run and report on their own.")}
  {_card("c-rose", "social", "Builds its own network", "Keeps a circle of contacts and connects channels — Lark, Slack, Telegram.")}
  {_card("c-violet", "artifact", "Delivers real artifacts", "Reports, charts, interactive pages — handed back as live tabs, like this one.")}
</div>

<p class="label">Try saying</p>
<div class="prompt"><span class="q">"Build me a market-analysis squad — spin up a few agents, give each the right skills and a clear role."</span></div>
<div class="prompt"><span class="q">"Reach out to Agent Aria and team up on the Q3 competitor report."</span></div>
<div class="prompt"><span class="q">"Be a stock-analysis expert — find the skills you need online, install them, then study the theory to deepen your awareness."</span></div>
<div class="prompt"><span class="q">"Research <i>Attention Is All You Need</i> and build me a beautiful HTML artifact that explains the paper."</span></div>
<div class="prompt"><span class="q">"Plan a 30-day embodied-AI course — teach me one lesson a day, each as a polished HTML handout."</span></div>
<div class="prompt"><span class="q">"Send me a financial morning briefing every day."</span></div>

<p class="label">Get started</p>
<ol class="steps">
  <li>Just start typing — say who you are and what you need.</li>
  <li>Add more agents from the left; each one is independent.</li>
  <li>Open Settings to bring your own model, anytime.</li>
</ol>

<p class="foot">NarraNexus is open-source, built by NetMind and the community —
contributions welcome at <a href="https://{_GH}">{_GH}</a>.</p>
"""

_DEFAULT_ZH = f"""
<div class="hero">
  <div class="dots"><i></i><i></i><i></i><i></i><i></i><i></i></div>
  <p class="kicker">NarraNexus</p>
  <h1>一键就位的<br>Agent 团队。</h1>
  <p class="lead">不是又一个把 agent 接线的框架 —— 而是一支开箱即用、
  本就会记忆、会协作、会用工具的团队。</p>
</div>

<p class="label">你拥有的能力</p>
<div class="grid">
  {_card("c-emerald", "team", "自带一支团队", "一个 Agent 能拉起更多 Agent、分派任务、在小队间共享上下文。")}
  {_card("c-indigo", "memory", "什么都记得住", "记忆贯穿每一次对话 —— 不用反复自我介绍。")}
  {_card("c-amber", "persona", "成为那个专家", "给它一个角色，它会重塑 awareness，并装上对应的 skill。")}
  {_card("c-sky", "jobs", "你睡觉它也在干", "安排周期与后台任务，自己跑、自己汇报。")}
  {_card("c-rose", "social", "搭建自己的人脉", "维护联系人圈子，接入飞书、Slack、Telegram 等渠道。")}
  {_card("c-violet", "artifact", "交付实在的产物", "报告、图表、交互页面 —— 以实时标签页交回，就像这一张。")}
</div>

<p class="label">不妨这样说</p>
<div class="prompt"><span class="q">「帮我组建一个市场分析小队 —— 拉起几个 Agent，给每个配上合适的 skill 和明确的分工。」</span></div>
<div class="prompt"><span class="q">「去联系一下 Agent Aria，找它一起协作 Q3 竞品报告。」</span></div>
<div class="prompt"><span class="q">「成为一名股票分析专家 —— 上网找齐你需要的 skill 装上，再研究理论，丰富你自己的 awareness。」</span></div>
<div class="prompt"><span class="q">「去调研一下 <i>Attention Is All You Need</i>，做一个精美的 HTML artifact 把这篇论文讲明白。」</span></div>
<div class="prompt"><span class="q">「帮我规划一个具身智能的 30 天学习计划，每天讲一节课，每节讲义都做成精美的 HTML artifact。」</span></div>
<div class="prompt"><span class="q">「每天早上给我发一份金融晨报。」</span></div>

<p class="label">开始吧</p>
<ol class="steps">
  <li>直接开打字 —— 说清你是谁、需要什么。</li>
  <li>在左侧再建 Agent，每个都互相独立。</li>
  <li>随时打开「设置」接入你自己的模型。</li>
</ol>

<p class="foot">NarraNexus 是开源项目，由 NetMind 与社区共同打造 ——
欢迎在 <a href="https://{_GH}">{_GH}</a> 一起贡献。</p>
"""


def default_welcome_html() -> str:
    return bilingual_html("Welcome to NarraNexus", _DEFAULT_EN, _DEFAULT_ZH)
