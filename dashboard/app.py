import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent


def _discover_data_roots() -> dict[str, Path]:
    """扫描可用的数据目录：outputs/ 和 ckpt/*/*"""
    roots = {}
    outputs = ROOT / "outputs"
    if outputs.exists():
        for d in sorted(outputs.iterdir()):
            if d.is_dir() and (d / "Executor").exists():
                roots[f"outputs/{d.name}"] = d
    ckpt = ROOT / "ckpt"
    if ckpt.exists():
        for scenario in sorted(ckpt.iterdir()):
            if not scenario.is_dir():
                continue
            for d in sorted(scenario.iterdir()):
                if d.is_dir() and (d / "Executor").exists():
                    roots[f"ckpt/{scenario.name}/{d.name}"] = d
    return roots


# ── Data Loading ──────────────────────────────────────────────

def load_jsonl(path):
    rows = []
    if not Path(path).exists():
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _parse_critic_report(raw: dict) -> dict:
    """兼容旧格式: {"case_id":..., "critic": "{\"failure_type\":...}"}"""
    if "critic" in raw and isinstance(raw["critic"], str):
        try:
            parsed = json.loads(raw["critic"])
            parsed["case_id"] = raw.get("case_id")
            return parsed
        except Exception:
            pass
    return raw


def load_all_loops(data_root: Path):
    """读取 data_root/ 下的全部 round 数据。"""
    loops = {}
    executor_dir = data_root / "Executor"
    if not executor_dir.exists():
        return loops

    rounds = {}
    for f in sorted(executor_dir.glob("round_*.jsonl")):
        if "_bad" in f.name:
            continue
        round_num = f.stem.replace("round_", "")
        rows = load_jsonl(f)
        bad_path = executor_dir / f"round_{round_num}_bad.jsonl"
        bad_rows = load_jsonl(bad_path)

        n = len(rows)
        passed = sum(1 for r in rows if r.get("passed"))
        em_avg = sum(r.get("em", 0.0) for r in rows) / n if n else 0
        f1_avg = sum(r.get("f1", 0.0) for r in rows) / n if n else 0
        sub_em_avg = sum(r.get("sub_em", 0.0) for r in rows) / n if n else 0

        rounds[int(round_num)] = {
            "results": rows,
            "bad_cases": bad_rows,
            "total": n,
            "passed": passed,
            "em": em_avg,
            "f1": f1_avg,
            "sub_em": sub_em_avg,
        }

    if rounds:
        loops[data_root.name] = rounds

    return loops


def load_critic_reports(data_root: Path, loop_tag=None):
    """读取 data_root/Critic/ 下的全部归因报告。"""
    critic_dir = data_root / "Critic"
    if not critic_dir.exists():
        return []
    reports = []
    for f in sorted(critic_dir.glob("round_*.jsonl")):
        for raw in load_jsonl(f):
            reports.append(_parse_critic_report(raw))
    return reports


def load_evolver_records(data_root: Path):
    """读取 data_root/Evolver/ 下的全部进化记录。"""
    evolver_dir = data_root / "Evolver"
    if not evolver_dir.exists():
        return []
    records = []
    for f in sorted(evolver_dir.glob("round_*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            rec = json.load(fh)
            round_num = int(f.stem.replace("round_", ""))
            rec["round"] = round_num
            records.append(rec)
    return records


def load_gate_decisions(data_root: Path):
    """读取 data_root/Gate/ 下的全部 Gate 决策。"""
    gate_dir = data_root / "Gate"
    if not gate_dir.exists():
        return []
    records = []
    for f in sorted(gate_dir.glob("round_*.json")):
        with open(f, "r", encoding="utf-8") as fh:
            records.append(json.load(fh))
    return records


def load_events(data_root: Path):
    """读取 data_root/events.jsonl 结构化事件流。"""
    event_file = data_root / "events.jsonl"
    return load_jsonl(event_file)


# ── Page Config ───────────────────────────────────────────────

st.set_page_config(
    page_title="Self-Harness Dashboard",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Neon CSS ──────────────────────────────────────────────────

st.markdown(
    """
<style>
:root {
    --bg-0: #050816;
    --bg-1: #0b1026;
    --card: rgba(17, 24, 39, 0.72);
    --card-2: rgba(15, 23, 42, 0.86);
    --border: rgba(129, 140, 248, 0.28);
    --border-strong: rgba(168, 85, 247, 0.58);
    --text: #e5e7eb;
    --muted: #9ca3af;
    --blue: #38bdf8;
    --purple: #a855f7;
    --pink: #ec4899;
    --green: #22c55e;
    --red: #ef4444;
}

.stApp {
    background:
        radial-gradient(circle at 15% 10%, rgba(56, 189, 248, 0.18), transparent 28%),
        radial-gradient(circle at 85% 15%, rgba(168, 85, 247, 0.20), transparent 30%),
        radial-gradient(circle at 50% 100%, rgba(236, 72, 153, 0.10), transparent 30%),
        linear-gradient(135deg, #050816 0%, #08111f 48%, #0f1028 100%);
    color: var(--text);
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, rgba(7, 11, 25, 0.96), rgba(17, 24, 39, 0.92));
    border-right: 1px solid rgba(129, 140, 248, 0.22);
}

h1, h2, h3 {
    color: #f9fafb;
    letter-spacing: -0.04em;
}

h1 {
    font-size: 2.7rem !important;
    background: linear-gradient(90deg, #38bdf8, #a855f7, #ec4899);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    text-shadow: 0 0 35px rgba(168, 85, 247, 0.35);
}

[data-testid="stMetric"] {
    background: linear-gradient(145deg, rgba(15, 23, 42, 0.86), rgba(30, 41, 59, 0.58));
    border: 1px solid rgba(129, 140, 248, 0.32);
    border-radius: 18px;
    padding: 1.1rem 1.2rem;
    box-shadow:
        0 0 0 1px rgba(56, 189, 248, 0.04),
        0 18px 45px rgba(0, 0, 0, 0.36),
        inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

[data-testid="stMetricLabel"] {
    color: #a5b4fc !important;
    text-align: center;
    justify-content: center;
}

[data-testid="stMetricValue"] {
    color: #f8fafc !important;
    text-shadow: 0 0 24px rgba(56, 189, 248, 0.28);
    text-align: center;
}

[data-testid="stMetricDelta"] {
    color: #22c55e !important;
    text-align: center;
}

.neon-card {
    background: linear-gradient(145deg, rgba(15, 23, 42, 0.76), rgba(17, 24, 39, 0.48));
    border: 1px solid rgba(129, 140, 248, 0.30);
    border-radius: 22px;
    padding: 1.25rem;
    box-shadow:
        0 0 28px rgba(56, 189, 248, 0.08),
        0 0 36px rgba(168, 85, 247, 0.08),
        0 22px 50px rgba(0, 0, 0, 0.30);
    backdrop-filter: blur(16px);
}

.hero {
    position: relative;
    overflow: hidden;
    padding: 1.6rem 1.8rem;
    border-radius: 26px;
    background:
        linear-gradient(135deg, rgba(14, 165, 233, 0.16), rgba(168, 85, 247, 0.14)),
        rgba(15, 23, 42, 0.62);
    border: 1px solid rgba(168, 85, 247, 0.34);
    box-shadow:
        0 0 42px rgba(56, 189, 248, 0.12),
        0 0 56px rgba(168, 85, 247, 0.12);
}

.hero::before {
    content: "";
    position: absolute;
    inset: -2px;
    background: linear-gradient(90deg, transparent, rgba(56, 189, 248, 0.25), rgba(168, 85, 247, 0.25), transparent);
    filter: blur(20px);
    opacity: 0.38;
}

.hero-title {
    position: relative;
    font-size: 2.4rem;
    font-weight: 800;
    line-height: 1.1;
    background: linear-gradient(90deg, #e0f2fe, #c4b5fd, #fbcfe8);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.hero-subtitle {
    position: relative;
    color: #cbd5e1;
    font-size: 1rem;
    margin-top: 0.4rem;
}

.badge {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    border-radius: 999px;
    padding: 0.35rem 0.75rem;
    font-size: 0.82rem;
    color: #dbeafe;
    background: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(96, 165, 250, 0.32);
    box-shadow: 0 0 20px rgba(59, 130, 246, 0.16);
    margin-right: 0.45rem;
}

.badge-purple {
    background: rgba(168, 85, 247, 0.12);
    border: 1px solid rgba(192, 132, 252, 0.35);
    color: #ede9fe;
}

.badge-green {
    background: rgba(34, 197, 94, 0.12);
    border: 1px solid rgba(74, 222, 128, 0.32);
    color: #dcfce7;
}

.section-title {
    font-size: 1.24rem;
    font-weight: 750;
    color: #f8fafc;
    margin: 0.25rem 0 0.75rem 0;
}

.muted {
    color: #94a3b8;
    font-size: 0.92rem;
}

hr {
    border-color: rgba(129, 140, 248, 0.20) !important;
}

.stTabs [data-baseweb="tab-list"] {
    gap: 1rem;
    border-bottom: none !important;
}

.stTabs [data-baseweb="tab-highlight"] {
    display: none !important;
    background: transparent !important;
}

.stTabs [data-baseweb="tab-border"] {
    display: none !important;
}

.stTabs [data-baseweb="tab"] {
    position: relative;
    height: 64px;
    min-width: 260px;
    padding: 0 34px;
    justify-content: center;
    border-radius: 999px;
    background: linear-gradient(135deg, rgba(15,23,42,0.92), rgba(30,41,59,0.82));
    border: 1px solid rgba(96,165,250,0.25);
    color: white !important;
    font-size: 22px;
    font-weight: 650;
    transition: all .25s ease;
    box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.05),
        0 0 15px rgba(59,130,246,0.08);
}

.stTabs [data-baseweb="tab"]:hover {
    transform: translateY(-2px);
    border-color: rgba(56,189,248,0.55);
    box-shadow:
        0 0 20px rgba(56,189,248,0.20),
        0 0 40px rgba(56,189,248,0.10);
}

.stTabs [aria-selected="true"] {
    background: linear-gradient(135deg, rgba(56,189,248,0.22), rgba(168,85,247,0.22)) !important;
    border: 1px solid rgba(96,165,250,0.65) !important;
    color: white !important;
    box-shadow:
        0 0 18px rgba(56,189,248,0.45),
        0 0 42px rgba(56,189,248,0.28),
        inset 0 0 28px rgba(56,189,248,0.08);
}

.stTabs [aria-selected="true"]::after {
    content: "";
    position: absolute;
    left: 18%;
    bottom: -7px;
    width: 64%;
    height: 5px;
    border-radius: 999px;
    background: #38bdf8;
    box-shadow:
        0 0 10px #38bdf8,
        0 0 24px #38bdf8,
        0 0 48px #38bdf8;
}



[data-testid="stDataFrame"] {
    border-radius: 18px;
    overflow: hidden;
    border: 1px solid rgba(129, 140, 248, 0.22);
    box-shadow: 0 18px 45px rgba(0, 0, 0, 0.25);
}

.stCodeBlock {
    border-radius: 16px;
    border: 1px solid rgba(129, 140, 248, 0.22);
}

div[data-testid="stExpander"] {
    background: rgba(15, 23, 42, 0.52);
    border: 1px solid rgba(129, 140, 248, 0.22);
    border-radius: 16px;
}
section[data-testid="stSidebar"] * {
    color: white !important;
}
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
    color: white !important;

    text-shadow:
        0 0 8px rgba(56,189,248,0.8),
        0 0 18px rgba(168,85,247,0.6);
}



</style>
""",
    unsafe_allow_html=True,
)


def neon_container_start():
    st.markdown('<div class="neon-card">', unsafe_allow_html=True)


def neon_container_end():
    st.markdown('</div>', unsafe_allow_html=True)


def style_plotly(fig, height=390):
    fig.update_layout(
        template="plotly_dark",
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.35)",
        font=dict(color="#e5e7eb"),
        title=dict(font=dict(size=20, color="#f8fafc")),
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            bordercolor="rgba(129,140,248,0.18)",
            borderwidth=1,
        ),
        margin=dict(l=30, r=25, t=60, b=35),
    )
    fig.update_xaxes(
        gridcolor="rgba(148,163,184,0.12)",
        zerolinecolor="rgba(148,163,184,0.18)",
    )
    fig.update_yaxes(
        gridcolor="rgba(148,163,184,0.12)",
        zerolinecolor="rgba(148,163,184,0.18)",
    )
    return fig


# ── Sidebar: Data Source Selector ──────────────────────────────

data_roots = _discover_data_roots()
if data_roots:
    selected_key = st.sidebar.selectbox(
        "📁 Select Data Source",
        list(data_roots.keys()),
        index=len(data_roots) - 1,  # 默认选最新的
    )
    SELECTED_ROOT = data_roots[selected_key]
else:
    st.sidebar.warning("No data found in outputs/ or ckpt/")
    SELECTED_ROOT = ROOT / "outputs"

st.sidebar.markdown(f"**Path:** `{SELECTED_ROOT}`")


# ── Load Data ─────────────────────────────────────────────────

loops = load_all_loops(SELECTED_ROOT)

latest_loop_tag = max(loops.keys()) if loops else SELECTED_ROOT.name
latest_rounds = loops.get(latest_loop_tag, {}) if loops else {}
latest_round_num = max(latest_rounds.keys()) if latest_rounds else None

latest_score = None
latest_em = 0
latest_f1 = 0
latest_sub_em = 0
latest_bad_count = 0

if latest_round_num is not None:
    r = latest_rounds[latest_round_num]
    latest_em = r.get("em", 0)
    latest_f1 = r.get("f1", 0)
    latest_sub_em = r.get("sub_em", 0)
    latest_score = f"EM: {latest_em:.1%} | F1: {latest_f1:.4f} | Sub-EM: {latest_sub_em:.1%}"
    latest_bad_count = len(r["bad_cases"])


# ── Hero ──────────────────────────────────────────────────────

st.markdown(
    f"""
<div class="hero">
    <div class="hero-title">Self-Harness Evolution Dashboard</div>
    <div class="hero-subtitle">
        Execute → Evaluate → Reflect → Evolve → Gate
    </div>
    <div style="margin-top: 1rem;">
        <span class="badge">🧠 Executor: mimo-v2.5</span>
        <span class="badge badge-purple">⚖️ Critic/Evolver: mimo-v2.5</span>
        <span class="badge badge-green">🚦 Gate: metric-driven</span>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

st.write("")


# ── Top Metrics ───────────────────────────────────────────────

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Latest Loop", latest_loop_tag or "N/A")

# 从 events.jsonl 读取数据集名称
ds_name = "N/A"
events = load_events(SELECTED_ROOT)
for ev in events:
    if ev.get("event_type") == "loop_start":
        ds_name = ev.get("payload", {}).get("dataset", "N/A")
        break

col2.metric("Dataset", ds_name)
col3.metric("Latest Round", f"Round {latest_round_num}" if latest_round_num is not None else "N/A")
col4.metric("Latest EM", f"{latest_rounds[latest_round_num]['em']:.1%}" if latest_round_num else "N/A")
col5.metric("Total Loops", len(loops))

st.write("")


# ── Core Metrics ──────────────────────────────────────────────

st.markdown('<div class="section-title">核心指标</div>', unsafe_allow_html=True)

# 从 Gate 决策中读取 dev 指标
gate_data = {}
gate_records = load_gate_decisions(SELECTED_ROOT)
for g in gate_records:
    gate_data[g["round"]] = g

# 从 events.jsonl 读取 test eval 结果
test_evals = {}
events = load_events(SELECTED_ROOT)
for ev in events:
        if ev.get("event_type") == "test_eval":
            phase = ev.get("payload", {}).get("phase", "")
            if phase in ("initial", "final"):
                test_evals[phase] = ev["payload"]

if loops:
    total_rounds = sum(len(r) for r in loops.values())

    # Gate 级别的 best dev em
    best_gate_dev_em = 0
    best_gate_round = 0
    for rnd, g in gate_data.items():
        if g.get("candidate_dev_em", 0) > best_gate_dev_em and g.get("accepted"):
            best_gate_dev_em = g["candidate_dev_em"]
            best_gate_round = rnd

    # Row 1: EM 指标对比 + Dev + 总轮次
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Initial Test EM", f"{test_evals['initial']['em']:.1%}" if "initial" in test_evals else "N/A")
    c2.metric("Final Test EM", f"{test_evals['final']['em']:.1%}" if "final" in test_evals else "N/A")
    c3.metric("Best Dev EM", f"{best_gate_dev_em:.1%}", help=f"Round {best_gate_round} (candidate)" if best_gate_round else "")
    c4.metric("Total Rounds", total_rounds)

    # Row 2: F1 & Sub-EM 对比 + Gate 统计
    c5, c6, c7, c8, c9, c10 = st.columns(6)
    c5.metric("Initial Test F1", f"{test_evals['initial']['f1']:.1%}" if "initial" in test_evals else "N/A")
    c6.metric("Final Test F1", f"{test_evals['final']['f1']:.1%}" if "final" in test_evals else "N/A")
    c7.metric("Initial Sub-EM", f"{test_evals['initial']['sub_em']:.1%}" if "initial" in test_evals else "N/A")
    c8.metric("Final Sub-EM", f"{test_evals['final']['sub_em']:.1%}" if "final" in test_evals else "N/A")
    c9.metric("Gate Accepted", sum(1 for g in gate_data.values() if g.get("accepted")))
    c10.metric("Gate Rejected", sum(1 for g in gate_data.values() if not g.get("accepted")))

else:
    st.info("No data yet. Run `run_harness.py` first.")


st.divider()


# ── Charts: Gate Dev Metrics ──────────────────────────────────

st.markdown('<div class="section-title">Dev 指标变化曲线 (Gate)</div>', unsafe_allow_html=True)

if gate_data:
    gate_chart = []
    for rnd in sorted(gate_data.keys()):
        g = gate_data[rnd]
        gate_chart.append({
            "round": rnd,
            "Current Dev EM": round(g.get("current_dev_em", 0) * 100, 1),
            "Candidate Dev EM": round(g.get("candidate_dev_em", 0) * 100, 1),
            "Current Dev F1": round(g.get("current_dev_f1", 0) * 100, 1),
            "Candidate Dev F1": round(g.get("candidate_dev_f1", 0) * 100, 1),
            "Memory EM": round(g.get("candidate_memory_em", 0) * 100, 1),
            "accepted": g.get("accepted", False),
        })

    df_gate = pd.DataFrame(gate_chart)

    left, right = st.columns([1.45, 1])

    with left:
        em_df = df_gate.melt(
            id_vars=["round"],
            value_vars=["Current Dev EM", "Candidate Dev EM"],
            var_name="Metric",
            value_name="EM (%)",
        )
        color_map = {"Current Dev EM": "#39ff14", "Candidate Dev EM": "#f97316"}
        fig = px.line(
            em_df,
            x="round",
            y="EM (%)",
            color="Metric",
            markers=True,
            title="Dev EM per Round (Current vs Candidate)",
            labels={"round": "Round"},
            color_discrete_map=color_map,
        )
        # 计算纵坐标: min-10%, max+2%
        all_em_vals = em_df["EM (%)"].tolist()
        if all_em_vals:
            em_min = min(all_em_vals)
            em_max = max(all_em_vals)
            y_lo = max(0, em_min - 5)
            y_hi = min(100, em_max + 2)
            fig.update_layout(yaxis_range=[y_lo, y_hi])
        else:
            fig.update_layout(yaxis_range=[0, 100])
        st.plotly_chart(style_plotly(fig, 420), use_container_width=True)

    with right:
        mem_df = df_gate[["round", "Memory EM"]].copy()
        fig2 = px.line(
            mem_df,
            x="round",
            y="Memory EM",
            markers=True,
            title="Candidate Memory EM per Round",
            labels={"round": "Round"},
        )
        fig2.add_hline(y=70, line_dash="dash", line_color="#ef4444", annotation_text="Threshold 70%")
        fig2.update_layout(yaxis_range=[60, 100])
        st.plotly_chart(style_plotly(fig2, 420), use_container_width=True)

else:
    st.info("No Gate data available.")


# ── Charts: Train Metrics ─────────────────────────────────────

st.markdown('<div class="section-title">Train 指标变化曲线 (每轮 Rollout)</div>', unsafe_allow_html=True)

if latest_rounds:
    train_chart = []
    for rnd in sorted(latest_rounds.keys()):
        r = latest_rounds[rnd]
        train_chart.append({
            "round": rnd,
            "EM": round(r.get("em", 0) * 100, 1),
            "F1": round(r.get("f1", 0) * 100, 1),
            "Sub-EM": round(r.get("sub_em", 0) * 100, 1),
        })

    df_train = pd.DataFrame(train_chart)
    train_melted = df_train.melt(
        id_vars=["round"],
        value_vars=["EM", "F1", "Sub-EM"],
        var_name="Metric",
        value_name="Score (%)",
    )
    fig_train = px.line(
        train_melted,
        x="round",
        y="Score (%)",
        color="Metric",
        markers=True,
        title="Train EM / F1 / Sub-EM per Round",
        labels={"round": "Round"},
    )
    fig_train.update_layout(yaxis_range=[40, 75])
    st.plotly_chart(style_plotly(fig_train, 420), use_container_width=True)
else:
    st.info("No train data available.")


st.divider()


# ── Latest Evolution ──────────────────────────────────────────

st.markdown('<div class="section-title">最近一轮进化详情</div>', unsafe_allow_html=True)

if latest_rounds:
    col_a, col_b = st.columns([1.05, 1])

    with col_a:
        with st.container():
            st.markdown(
                f"""
<div class="neon-card">
    <div class="muted">Latest loop</div>
    <h3 style="margin-top: 0.2rem;">{latest_loop_tag}</h3>
    <p class="muted">Round {latest_round_num} · EM: {latest_em:.1%} · F1: {latest_f1:.4f} · Sub-EM: {latest_sub_em:.1%} · Bad cases {latest_bad_count}</p>
</div>
""",
                unsafe_allow_html=True,
            )

        st.write("")

        if latest_round_num is not None:
            r = latest_rounds[latest_round_num]
            if r["bad_cases"]:
                st.markdown("**Failed Questions Preview**")
                for bc in r["bad_cases"][:5]:
                    q = str(bc.get("question", ""))[:90]
                    expected = str(bc.get("expected", ""))
                    pred = str(bc.get("prediction", ""))[:100]

                    with st.expander(f"❌ {bc.get('id', 'unknown')} · {q}"):
                        st.markdown(f"**Expected:** `{expected}`")
                        st.markdown(f"**Prediction:** {pred}")
            else:
                st.success("No bad cases in the latest round.")

    with col_b:
        evolver_records = load_evolver_records(SELECTED_ROOT)

        st.markdown("**Evolution Actions**")
        if evolver_records:
            for record in evolver_records:
                rec_round = record.get("round", 0)
                gate_rec = gate_data.get(rec_round, {})
                gate_accepted = gate_rec.get("accepted", None)
                if gate_accepted is not True:
                    continue

                rec_badge = '<span class="badge badge-green">Accept</span>'

                for action in record.get("actions", []):
                    comp = action.get("target_component", "?")
                    act = action.get("evolve_action", "?")

                    skill_badge = ""
                    if comp == "skill" and action.get("skill_name"):
                        skill_badge = f'<span class="badge badge-green">{action["skill_name"]}</span>'

                    st.markdown(
                        f"""
<div class="neon-card" style="padding: 1rem; margin-bottom: 0.7rem;">
    {rec_badge}
    <span class="badge badge-purple">{comp}</span>
    <span class="badge">{act}</span>
    {skill_badge}
</div>
""",
                        unsafe_allow_html=True,
                    )
        else:
            st.info("No evolution records for this loop yet.")

        critic_reports = load_critic_reports(SELECTED_ROOT)
        if critic_reports:
            st.caption(f"Critic Reports: {len(critic_reports)} entries")

else:
    st.info("No loop data available yet.")


st.divider()


# ── Tabs ──────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["🧩 Bad Cases", "⚖️ Critic Reports", "🚦 Gate Decisions"])

with tab1:
    if latest_round_num is not None:
        bad = latest_rounds[latest_round_num]["bad_cases"]

        if bad:
            df_bad = pd.DataFrame(bad)
            display_cols = [
                c for c in ["id", "question", "expected", "predicted_answer", "prediction", "em", "f1", "sub_em", "passed"]
                if c in df_bad.columns
            ]
            st.dataframe(df_bad[display_cols], use_container_width=True, height=430)
        else:
            st.success("No bad cases in the latest round!")
    else:
        st.info("No data available.")

with tab2:
    critic_reports = load_critic_reports(SELECTED_ROOT)

    if critic_reports:
        # 兼容 batch 格式（failure_summary 列表）和平铺格式（failure_type 字段）
        batch_reports = [r for r in critic_reports if "failure_summary" in r]
        flat_reports = [r for r in critic_reports if "failure_type" in r]

        if batch_reports:
            # 展平 failure_summary 为表格行
            flat_summaries = []
            for report in batch_reports:
                batch_ids = report.get("batch_case_ids", [])
                batch_size = report.get("batch_size", len(batch_ids))
                for fs in report.get("failure_summary", []):
                    flat_summaries.append({
                        "batch_case_ids": ", ".join(str(x) for x in batch_ids[:5]) + ("..." if len(batch_ids) > 5 else ""),
                        "batch_size": batch_size,
                        "failure_type": fs.get("failure_type", "unknown"),
                        "count": fs.get("count", 0),
                        "description": fs.get("description", ""),
                    })

            if flat_summaries:
                df_critic = pd.DataFrame(flat_summaries)
                st.dataframe(df_critic, use_container_width=True, height=380)

                left, right = st.columns([1, 1])

                with left:
                    if "failure_type" in df_critic.columns:
                        type_counts = df_critic.groupby("failure_type")["count"].sum().reset_index()
                        fig3 = px.pie(
                            type_counts,
                            names="failure_type",
                            values="count",
                            title="Failure Type Distribution",
                            hole=0.45,
                        )
                        st.plotly_chart(style_plotly(fig3, 380), use_container_width=True)

                with right:
                    fig4 = px.bar(
                        df_critic,
                        y="batch_size",
                        x=list(range(len(df_critic))),
                        title="Failures per Batch",
                        labels={"x": "Batch", "y": "Cases"},
                    )
                    st.plotly_chart(style_plotly(fig4, 380), use_container_width=True)
            else:
                st.info("No failure summaries in critic reports.")

        elif flat_reports:
            df_critic = pd.DataFrame(flat_reports)
            display_cols = [
                c for c in ["case_id", "failure_type", "root_cause",
                             "target_component", "evolve_action", "confidence"]
                if c in df_critic.columns
            ]
            st.dataframe(df_critic[display_cols], use_container_width=True, height=380)

            left, right = st.columns([1, 1])

            with left:
                if "failure_type" in df_critic.columns:
                    fig3 = px.pie(
                        df_critic,
                        names="failure_type",
                        title="Failure Type Distribution",
                        hole=0.45,
                    )
                    st.plotly_chart(style_plotly(fig3, 380), use_container_width=True)

            with right:
                if "target_component" in df_critic.columns:
                    comp_count = df_critic["target_component"].value_counts().reset_index()
                    comp_count.columns = ["target_component", "count"]
                    fig4 = px.bar(
                        comp_count,
                        x="target_component",
                        y="count",
                        title="Target Component Distribution",
                    )
                    st.plotly_chart(style_plotly(fig4, 380), use_container_width=True)
        else:
            st.info("No valid critic reports found.")
    else:
        st.info("No critic reports available.")

with tab3:
    # Gate 决策
    gate_records = load_gate_decisions(SELECTED_ROOT)
    evolver_records = load_evolver_records(SELECTED_ROOT)

    if gate_records:
        st.markdown("**Gate Decisions**")
        df_gate = pd.DataFrame(gate_records)
        display_cols = [
            c for c in ["round",
                         "current_dev_em", "current_dev_f1",
                         "candidate_dev_em", "candidate_dev_f1",
                         "candidate_memory_em", "memory_threshold",
                         "memory_ok", "memory_size",
                         "accepted", "has_prompt", "has_skills"]
            if c in df_gate.columns
        ]
        if display_cols:
            st.dataframe(df_gate[display_cols], use_container_width=True)
        else:
            st.dataframe(df_gate, use_container_width=True)
    else:
        st.info("No gate decisions available.")

    if evolver_records:
        st.markdown("**Evolution Actions**")
        for i, record in enumerate(evolver_records, start=1):
            # 通过 round 号查 Gate 结果
            record_round = record.get("round", i)
            gate_rec = gate_data.get(record_round, {})
            gate_accepted = gate_rec.get("accepted", None)
            if gate_accepted is True:
                gate_badge = '<span class="badge badge-green">Accept</span>'
            elif gate_accepted is False:
                gate_badge = '<span class="badge" style="background:rgba(239,68,68,0.12);border:1px solid rgba(248,113,113,0.35);color:#fca5a5;">Reject</span>'
            else:
                gate_badge = '<span class="badge">N/A</span>'

            st.markdown(f"### Evolution Record #{i} {gate_badge}", unsafe_allow_html=True)

            for action in record.get("actions", []):
                comp = action.get("target_component", "?")
                act = action.get("evolve_action", "?")
                content = action.get("new_content", "")
                decision = "APPLIED" if content else "SKIPPED"

                skill_badge = ""
                if comp == "skill" and action.get("skill_name"):
                    skill_badge = f'<span class="badge badge-green">{action["skill_name"]}</span>'

                st.markdown(
                    f"""
<div class="neon-card" style="margin-bottom: 0.8rem;">
    <span class="badge badge-green">{decision}</span>
    <span class="badge">{comp}</span>
    <span class="badge badge-purple">{act}</span>
    {skill_badge}
</div>
""",
                    unsafe_allow_html=True,
                )

                if content:
                    with st.expander(f"View new content for {comp}"):
                        st.code(content, language="markdown")


# ── Sidebar ───────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Configuration")

    st.markdown("### API Settings")
    st.text_input("API Key", value="sk-***hidden***", type="password")
    st.text_input("Base URL", value="https://api.xiaomimimo.com/v1")
    st.text_input("Executor Model", value="mimo-v2.5")
    st.text_input("Critic/Evolver Model", value="mimo-v2.5")

    st.divider()

    st.markdown("### System Paths")
    st.caption(f"ROOT: {ROOT}")
    st.caption(f"Selected: {SELECTED_ROOT}")
    st.caption(f"DATA: {ROOT / 'data'}")

    st.divider()

    st.markdown("### Data Files")
    data_dir = ROOT / "data"
    if data_dir.exists():
        for f in sorted(data_dir.glob("*")):
            st.caption(f"• {f.name}")
    else:
        st.caption("No data directory found.")

    st.divider()

    st.markdown("### Executor Skills")
    skills_dir = ROOT / "Agent/Executor/Skills"
    if skills_dir.exists():
        skill_files = [f for f in sorted(skills_dir.glob("*.md")) if f.name != ".gitkeep"]
        if skill_files:
            for f in skill_files:
                content = f.read_text(encoding="utf-8").strip()
                lines = content.split("\n")
                title = lines[0] if lines else f.stem
                st.caption(f"• **{f.stem}**: {title[:60]}")
        else:
            st.caption("No Skills defined yet.")
    else:
        st.caption("Skills directory not found.")
