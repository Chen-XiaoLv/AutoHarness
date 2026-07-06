import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


# ============================================================
# 固定实验数据
# ============================================================

RUN_DATA = [
    {"round": 0, "action": "BaseLine", "type": "baseline", "dev_em": 0.5069, "test_em": 0.4837, "F1": 0.63159},
    {"round": 1, "action": "Task Discover", "type": "accept", "dev_em": 0.6698, "memory_em": 0.92},
    {"round": 2, "action": "CONTINUE_SKILL_EVOLUTION", "type": "reject", "dev_em": 0.6558, "memory_em": 0.92},
    {"round": 3, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "accept", "dev_em": 0.7070, "memory_em": 0.90},
    {"round": 4, "action": "RERUN_EVALUATION", "type": "reject", "dev_em": 0.6419, "memory_em": 0.84},
    {"round": 5, "action": "CONTINUE_SKILL_EVOLUTION", "type": "accept", "dev_em": 0.7256, "memory_em": 0.92},
    {"round": 6, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "accept", "dev_em": 0.7333, "memory_em": 0.93},
    {"round": 7, "action": "CONTINUE_SKILL_EVOLUTION", "type": "accept", "dev_em": 0.7349, "memory_em": 0.92},
    {"round": 8, "action": "CONTINUE_SKILL_EVOLUTION", "type": "reject", "dev_em": 0.6744, "memory_em": 0.92},
    {"round": 9, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "accept", "dev_em": 0.7364, "memory_em": 0.92},
    {"round": 10, "action": "CONTINUE_SKILL_EVOLUTION", "type": "reject", "dev_em": 0.7116, "memory_em": 0.92},
    {"round": 11, "action": "CONTINUE_SKILL_EVOLUTION", "type": "accept", "dev_em": 0.7209, "memory_em": 0.89},
    {"round": 12, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "accept", "dev_em": 0.7302, "memory_em": 0.94},
    {"round": 13, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "reject", "dev_em": 0.7163, "memory_em": 0.93},
    {"round": 14, "action": "RERUN_EVALUATION", "type": "reject", "dev_em": 0.7163, "memory_em": 0.93},
    {"round": 15, "action": "RERUN_EVALUATION", "type": "accept", "dev_em": 0.7441, "memory_em": 0.95},
    {"round": 16, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "reject", "dev_em": 0.7070, "memory_em": 0.85},
    {"round": 17, "action": "RERUN_EVALUATION", "type": "reject", "dev_em": 0.7349, "memory_em": 0.90},
    {"round": 18, "action": "CONTINUE_SKILL_EVOLUTION", "type": "reject", "dev_em": 0.6837, "memory_em": 0.93},
    {"round": 19, "action": "ADD_OR_UPDATE_FEWSHOT", "type": "reject", "dev_em": 0.7070, "memory_em": 0.92},
    {"round": 20, "action": "RERUN_EVALUATION", "type": "reject", "dev_em": 0.7070, "memory_em": 0.92},
    {"round": 21, "action": "RERUN_EVALUATION", "type": "reject", "dev_em": 0.7070, "memory_em": 0.92},
]

FINAL_TEST_EM = 0.7395348


# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="AutoHarness Dashboard",
    page_icon="🧠",
    layout="wide",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>
.stApp {
    background:
        radial-gradient(circle at 15% 10%, rgba(56, 189, 248, 0.18), transparent 28%),
        radial-gradient(circle at 85% 15%, rgba(168, 85, 247, 0.20), transparent 30%),
        linear-gradient(135deg, #050816 0%, #08111f 48%, #0f1028 100%);
    color: #e5e7eb;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
}

h1, h2, h3 {
    color: #f9fafb;
}

[data-testid="stMetric"] {
    background: linear-gradient(145deg, rgba(15, 23, 42, 0.88), rgba(30, 41, 59, 0.62));
    border: 1px solid rgba(129, 140, 248, 0.32);
    border-radius: 18px;
    padding: 1rem 1.1rem;
    box-shadow:
        0 18px 45px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

[data-testid="stMetricLabel"] {
    color: #a5b4fc !important;
}

[data-testid="stMetricValue"] {
    color: #f8fafc !important;
    text-shadow: 0 0 24px rgba(56, 189, 248, 0.26);
}

.hero {
    padding: 1.6rem 1.8rem;
    border-radius: 26px;
    background:
        linear-gradient(135deg, rgba(14, 165, 233, 0.18), rgba(168, 85, 247, 0.15)),
        rgba(15, 23, 42, 0.72);
    border: 1px solid rgba(168, 85, 247, 0.34);
    box-shadow:
        0 0 42px rgba(56, 189, 248, 0.12),
        0 0 56px rgba(168, 85, 247, 0.12);
}

.hero-title {
    font-size: 2.4rem;
    font-weight: 850;
    line-height: 1.1;
    background: linear-gradient(90deg, #38bdf8, #c4b5fd, #fbcfe8);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.hero-subtitle {
    color: #cbd5e1;
    font-size: 1rem;
    margin-top: 0.5rem;
}

.badge {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 0.35rem 0.75rem;
    font-size: 0.82rem;
    color: #dbeafe;
    background: rgba(59, 130, 246, 0.12);
    border: 1px solid rgba(96, 165, 250, 0.32);
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
    font-size: 1.25rem;
    font-weight: 750;
    color: #f8fafc;
    margin: 1.2rem 0 0.8rem 0;
}

.neon-card {
    background: linear-gradient(145deg, rgba(15, 23, 42, 0.78), rgba(17, 24, 39, 0.52));
    border: 1px solid rgba(129, 140, 248, 0.30);
    border-radius: 20px;
    padding: 1.1rem;
    box-shadow:
        0 0 28px rgba(56, 189, 248, 0.08),
        0 0 36px rgba(168, 85, 247, 0.08),
        0 22px 50px rgba(0, 0, 0, 0.30);
}

hr {
    border-color: rgba(129, 140, 248, 0.22) !important;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# 数据处理
# ============================================================

df = pd.DataFrame(RUN_DATA)
df["dev_em_pct"] = df["dev_em"] * 100
df["memory_em_pct"] = df["memory_em"] * 100
df["status"] = df["type"].map({
    "accept": "Accepted",
    "reject": "Rejected",
    "baseline": "Baseline",
}).fillna("N/A")

baseline_dev = float(df.iloc[0]["dev_em"])
latest_dev = float(df.iloc[-1]["dev_em"])
best_idx = df["dev_em"].idxmax()
best_row = df.loc[best_idx]
best_dev = float(best_row["dev_em"])

accepted_count = int((df["type"] == "accept").sum())
rejected_count = int((df["type"] == "reject").sum())

skill_rounds = int((df["action"] == "CONTINUE_SKILL_EVOLUTION").sum())
fewshot_rounds = int((df["action"] == "ADD_OR_UPDATE_FEWSHOT").sum())
rerun_rounds = int((df["action"] == "RERUN_EVALUATION").sum())


# ============================================================
# Plotly 样式
# ============================================================

def style_plotly(fig, height=420):
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


# ============================================================
# Hero
# ============================================================

st.markdown(
    """
<div class="hero">
    <div class="hero-title">AutoHarness Evolution Dashboard</div>
    <div class="hero-subtitle">
        Task Discover → Plan Agent → Skill Evolution → Few-shot Optimization → Gate
    </div>
    <div style="margin-top: 1rem;">
        <span class="badge badge-purple">⚙️ Executor: mimo-v2.5</span>
        <span class="badge badge-green">🚦 Memory Gate: 70%</span>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

st.write("")


# ============================================================
# 核心指标
# ============================================================

st.markdown('<div class="section-title">核心指标</div>', unsafe_allow_html=True)

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Baseline Dev EM", f"{baseline_dev:.2%}")
c2.metric("Best Dev EM", f"{best_dev:.2%}", delta=f"{best_dev - baseline_dev:+.2%}")
c3.metric("Latest Dev EM", f"{latest_dev:.2%}", delta=f"{latest_dev - baseline_dev:+.2%}")
c4.metric("Final Test EM", f"{FINAL_TEST_EM:.2%}", delta=f"{FINAL_TEST_EM - 0.4837:+.2%}")
c5.metric("Total Rounds", len(df) - 1)

c6, c7, c8, c9, c10 = st.columns(5)

c6.metric("Best Round", f"Round {int(best_row['round'])}")
c7.metric("Best Action", best_row["action"])
c8.metric("Accepted", accepted_count)
c9.metric("Rejected", rejected_count)
c10.metric("Final Gain", f"{FINAL_TEST_EM - 0.4837:+.2%}")

st.divider()


# ============================================================
# Dev EM 折线图
# ============================================================

st.markdown('<div class="section-title">Dev EM 变化曲线</div>', unsafe_allow_html=True)

fig = go.Figure()

fig.add_trace(
    go.Scatter(
        x=df["round"],
        y=df["dev_em_pct"],
        mode="lines+markers",
        name="Dev EM",
        line=dict(width=4, color="#38bdf8"),
        marker=dict(size=9, color="#38bdf8"),
        customdata=df[["action", "type", "memory_em_pct"]],
        hovertemplate=(
            "Round %{x}<br>"
            "Dev EM: %{y:.2f}%<br>"
            "Action: %{customdata[0]}<br>"
            "Type: %{customdata[1]}<br>"
            "Memory EM: %{customdata[2]:.2f}%"
            "<extra></extra>"
        ),
    )
)

accepted_df = df[df["type"] == "accept"]
rejected_df = df[df["type"] == "reject"]
baseline_df = df[df["type"] == "baseline"]

fig.add_trace(
    go.Scatter(
        x=accepted_df["round"],
        y=accepted_df["dev_em_pct"],
        mode="markers",
        name="Accepted",
        marker=dict(size=15, color="#22c55e", symbol="circle"),
        hovertemplate="Round %{x}<br>Accepted<br>Dev EM: %{y:.2f}%<extra></extra>",
    )
)

fig.add_trace(
    go.Scatter(
        x=rejected_df["round"],
        y=rejected_df["dev_em_pct"],
        mode="markers",
        name="Rejected",
        marker=dict(size=15, color="#ef4444", symbol="x"),
        hovertemplate="Round %{x}<br>Rejected<br>Dev EM: %{y:.2f}%<extra></extra>",
    )
)

fig.add_trace(
    go.Scatter(
        x=baseline_df["round"],
        y=baseline_df["dev_em_pct"],
        mode="markers",
        name="Baseline",
        marker=dict(size=16, color="#facc15", symbol="diamond"),
        hovertemplate="Baseline<br>Dev EM: %{y:.2f}%<extra></extra>",
    )
)

fig.add_hline(
    y=baseline_dev * 100,
    line_dash="dot",
    line_color="#94a3b8",
    annotation_text=f"Baseline {baseline_dev:.2%}",
)

fig.add_hline(
    y=best_dev * 100,
    line_dash="dash",
    line_color="#22c55e",
    annotation_text=f"Best {best_dev:.2%}",
)

fig.update_layout(
    title="Dev EM per Round",
    xaxis_title="Round",
    yaxis_title="Dev EM (%)",
    yaxis_range=[45, 78],
)

st.plotly_chart(style_plotly(fig, 480), use_container_width=True)


# ============================================================
# Memory EM 曲线 + Action 统计
# ============================================================

left, right = st.columns([1.2, 1])

with left:
    st.markdown('<div class="section-title">Memory EM 变化曲线</div>', unsafe_allow_html=True)

    mem_df = df.dropna(subset=["memory_em"]).copy()

    fig_mem = go.Figure()
    fig_mem.add_trace(
        go.Scatter(
            x=mem_df["round"],
            y=mem_df["memory_em_pct"],
            mode="lines+markers",
            name="Memory EM",
            line=dict(width=4, color="#a855f7"),
            marker=dict(size=9, color="#a855f7"),
            customdata=mem_df[["action", "type"]],
            hovertemplate=(
                "Round %{x}<br>"
                "Memory EM: %{y:.2f}%<br>"
                "Action: %{customdata[0]}<br>"
                "Type: %{customdata[1]}"
                "<extra></extra>"
            ),
        )
    )

    fig_mem.add_hline(
        y=70,
        line_dash="dash",
        line_color="#ef4444",
        annotation_text="Memory Gate 70%",
    )

    fig_mem.update_layout(
        title="Memory EM per Round",
        xaxis_title="Round",
        yaxis_title="Memory EM (%)",
        yaxis_range=[65, 100],
    )

    st.plotly_chart(style_plotly(fig_mem, 420), use_container_width=True)

with right:
    st.markdown('<div class="section-title">Action 分布</div>', unsafe_allow_html=True)

    action_count = (
        df[df["round"] != 0]["action"]
        .value_counts()
        .reset_index()
    )
    action_count.columns = ["action", "count"]

    fig_action = px.pie(
        action_count,
        names="action",
        values="count",
        title="Planner Action Distribution",
        hole=0.45,
    )

    st.plotly_chart(style_plotly(fig_action, 420), use_container_width=True)


st.divider()


# ============================================================
# Action 效果分析
# ============================================================

st.markdown('<div class="section-title">不同 Action 的平均 Dev EM</div>', unsafe_allow_html=True)

action_perf = (
    df[df["round"] != 0]
    .groupby("action", as_index=False)
    .agg(
        avg_dev_em=("dev_em_pct", "mean"),
        max_dev_em=("dev_em_pct", "max"),
        rounds=("round", "count"),
    )
)

fig_bar = px.bar(
    action_perf,
    x="action",
    y="avg_dev_em",
    text="avg_dev_em",
    title="Average Dev EM by Planner Action",
    labels={
        "action": "Action",
        "avg_dev_em": "Average Dev EM (%)",
    },
)

fig_bar.update_traces(
    texttemplate="%{text:.2f}%",
    textposition="outside",
)

fig_bar.update_layout(yaxis_range=[60, 78])

st.plotly_chart(style_plotly(fig_bar, 420), use_container_width=True)


# ============================================================
# 详细表格
# ============================================================

st.markdown('<div class="section-title">Round Trace 明细</div>', unsafe_allow_html=True)

display_df = df.copy()
display_df["dev_em"] = display_df["dev_em"].map(lambda x: f"{x:.2%}")
display_df["memory_em"] = display_df["memory_em"].map(
    lambda x: "N/A" if pd.isna(x) else f"{x:.2%}"
)

display_df = display_df[
    ["round", "action", "type", "dev_em", "memory_em"]
]

st.dataframe(
    display_df,
    use_container_width=True,
    height=500,
)


# ============================================================
# 总结
# ============================================================

st.markdown('<div class="section-title">实验结论</div>', unsafe_allow_html=True)

st.markdown(
    f"""
<div class="neon-card">
    <p>
    本次 AutoHarness 实验从 Baseline 的 <b>{baseline_dev:.2%}</b> Dev EM 出发，
    经过 Task Discover、Skill Evolution、Few-shot Optimization 和 Rerun Evaluation 等多轮 Planner 决策，
    最佳 Dev EM 达到 <b>{best_dev:.2%}</b>，对应 <b>Round {int(best_row["round"])}</b>，
    最终 Test EM 达到 <b>{FINAL_TEST_EM:.2%}</b>。
    </p>
    <p>
    从过程看，Task Discover 带来了第一次显著提升，Few-shot 在多个轮次中提供了快速收益，
    Skill Evolution 则承担了持续吸收 Bad Case 经验的作用。
    Gate 与 Memory EM 曲线显示，大部分接受版本都能维持较高的历史正确样本稳定性，
    说明防退化机制在本轮实验中基本有效。
    </p>
</div>
""",
    unsafe_allow_html=True,
)
