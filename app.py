import streamlit as st
import pandas as pd
import yfinance as yf
import sqlite3
import json
import os
import time
import urllib.request
import plotly.graph_objects as go
from datetime import datetime
from collections import OrderedDict

st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100 - 安定版")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"


# ==========================
# 安全ユーティリティ
# ==========================

def safe_float(x):
    try:
        return float(x)
    except:
        return 0.0


def safe_price(stock):
    try:
        hist = stock.history(period="5d")
        if hist.empty:
            return 0
        return float(hist["Close"].dropna().iloc[-1])
    except:
        return 0


def get_score(value, thresholds):
    for score, threshold in thresholds:
        if value >= threshold:
            return score
    return 0


# ==========================
# DB初期化
# ==========================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )
        """)


# ==========================
# マスター取得（壊れない版）
# ==========================

@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)

    try:
        df = pd.read_excel(JPX_FILE)
        df.columns = df.columns.str.strip()

        col_market = [c for c in df.columns if "市場" in c][0]
        col_code   = [c for c in df.columns if "コード" in c][0]
        col_name   = [c for c in df.columns if "銘柄名" in c][0]

        df = df[df[col_market].astype(str).str.contains("内国株式", na=False)]

        return {
            str(row[col_code]).zfill(4) + ".T": row[col_name]
            for _, row in df.iterrows()
        }

    except:
        return {}


# ==========================
# メインスコア計算（完全安定版）
# ==========================

def calculate_full_score_safe(ticker):

    keys = [
        "連続増配年数","5年配当CAGR","純利益5年CAGR",
        "売上5年CAGR","営業利益率","配当利回り"
    ]

    scores = OrderedDict({k:0 for k in keys})

    try:
        stock = yf.Ticker(ticker)

        # ===== 配当 =====
        divs = stock.dividends
        growth_years = 0
        d_cagr = 0
        yield_val = 0

        if divs is not None and not divs.empty:

            yearly = divs.groupby(divs.index.year).sum()

            # 未確定年除外
            current_year = datetime.now().year
            yearly = yearly[yearly.index < current_year]

            # 異常値除外
            yearly = yearly[yearly < 1000]

            if len(yearly) > 0:

                latest_div = float(yearly.iloc[-1])

                price = safe_price(stock)

                if price > 0 and latest_div > 0:
                    yield_val = latest_div / price * 100

                    # 異常利回りカット
                    if yield_val > 20:
                        yield_val = 0

                # 連続増配
                for i in range(1,len(yearly)):
                    if yearly.iloc[-i] >= yearly.iloc[-(i+1)]:
                        growth_years += 1
                    else:
                        break

                # 5年CAGR
                if len(yearly) >= 5:
                    start = yearly.iloc[-5]
                    end = yearly.iloc[-1]
                    if start > 0:
                        d_cagr = ((end/start)**(1/4)-1)*100

        # ===== 財務 =====
        inc = stock.income_stmt
        net_cagr = 0
        rev_cagr = 0
        op_margin = 0

        if inc is not None and not inc.empty:

            try:
                net = inc.loc["Net Income"].sort_index()
                rev = inc.loc["Total Revenue"].sort_index()
            except:
                net = pd.Series()
                rev = pd.Series()

            if len(net) >= 5:
                start = net.iloc[-5]
                end = net.iloc[-1]
                if start > 0:
                    net_cagr = ((end/start)**(1/4)-1)*100

            if len(rev) >= 5:
                start = rev.iloc[-5]
                end = rev.iloc[-1]
                if start > 0:
                    rev_cagr = ((end/start)**(1/4)-1)*100

            try:
                op = inc.loc["Operating Income"].iloc[-1]
                r = inc.loc["Total Revenue"].iloc[-1]
                if r != 0:
                    op_margin = op/r*100
            except:
                pass

        # ===== スコア =====
        scores["連続増配年数"] = get_score(growth_years, [(10,10),(8,5),(6,3)])
        scores["5年配当CAGR"] = get_score(d_cagr, [(10,15),(8,10),(6,5)])
        scores["純利益5年CAGR"] = get_score(net_cagr, [(10,15),(8,10),(6,5)])
        scores["売上5年CAGR"] = get_score(rev_cagr, [(10,10),(8,5),(6,3)])
        scores["営業利益率"] = get_score(op_margin, [(10,20),(8,15),(6,10)])
        scores["配当利回り"] = get_score(yield_val, [(10,5),(8,4),(6,3)])

        total = int(sum(scores.values()))

        return total, {k:int(v) for k,v in scores.items()}

    except:
        return 0, scores


# ==========================
# UI
# ==========================

init_db()
master = get_ticker_master()

st.sidebar.write(f"銘柄数: {len(master)}")

if st.sidebar.button("3銘柄スキャン"):
    for t in list(master.keys())[:3]:
        total, sc = calculate_full_score_safe(t)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)",
                (t, int(total), json.dumps(sc), datetime.now())
            )

        time.sleep(2)

    st.rerun()


# ==========================
# ランキング
# ==========================

with sqlite3.connect(DB_PATH) as conn:
    df = pd.read_sql("SELECT * FROM stocks", conn)

if df.empty:
    st.info("まだデータがありません")
else:
    df = df.sort_values("total_score", ascending=False)
    df["銘柄名"] = df["ticker"].map(master)

    st.dataframe(df[["total_score","銘柄名","ticker"]].rename(columns={"total_score":"点数"}), hide_index=True)

    selected = st.selectbox("詳細表示", df["ticker"])

    # ===== 詳細 =====
    row = df[df["ticker"]==selected].iloc[0]
    scores = json.loads(row["score_json"])

    st.subheader("📊 スコア詳細")
    st.table(pd.DataFrame(scores.items(), columns=["項目","点数"]))

    st.subheader("📈 過去配当推移")
    stock = yf.Ticker(selected)
    divs = stock.dividends

    if divs is not None and not divs.empty:
        yearly = divs.groupby(divs.index.year).sum()
        yearly = yearly[yearly.index < datetime.now().year]

        fig = go.Figure()
        fig.add_bar(x=yearly.index.astype(str), y=yearly.values)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("配当データなし")
