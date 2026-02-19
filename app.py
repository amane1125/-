import streamlit as st
import pandas as pd
import yfinance as yf
import sqlite3
import json
from datetime import datetime
from collections import OrderedDict
import plotly.graph_objects as go

st.set_page_config(page_title="Dividend Growth 100", layout="wide")
st.title("🇯🇵 Dividend Growth 100 - yfinance最新版対応")

DB_PATH = "stock_data.db"


# ==========================
# ユーティリティ
# ==========================

def safe_price(ticker):
    try:
        data = yf.download(ticker, period="5d", progress=False)
        if data.empty:
            return 0
        return float(data["Close"].iloc[-1])
    except:
        return 0


def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t:
            return s
    return 0


def cagr_5(series):
    if series is None or len(series) < 5:
        return 0
    series = series.sort_index()
    last5 = series.tail(5)
    start = last5.iloc[0]
    end = last5.iloc[-1]
    if start <= 0 or end <= 0:
        return 0
    return ((end/start)**(1/4)-1)*100


# ==========================
# 最新版 財務取得
# ==========================

def get_financial_series(stock, candidates):

    df = stock.financials  # ← 最新版ではこれ

    if df is None or df.empty:
        return pd.Series()

    df = df.sort_index(axis=1)

    for name in df.index:
        clean_name = name.lower().replace(" ", "")
        for cand in candidates:
            if cand.lower().replace(" ", "") in clean_name:
                return df.loc[name].sort_index()

    return pd.Series()


# ==========================
# スコア計算
# ==========================

def calculate_score(ticker):

    keys = [
        "連続増配年数",
        "5年配当CAGR",
        "純利益5年CAGR",
        "売上5年CAGR",
        "営業利益率",
        "配当利回り"
    ]

    scores = OrderedDict({k:0 for k in keys})

    try:
        stock = yf.Ticker(ticker)

        # ===== 配当 =====
        divs = stock.dividends

        growth_years = 0
        div_cagr = 0
        yield_val = 0

        if divs is not None and not divs.empty:

            yearly = divs.groupby(divs.index.year).sum()
            yearly = yearly[yearly.index < datetime.now().year]
            yearly = yearly[yearly > 0]

            if len(yearly) > 0:

                latest_div = yearly.iloc[-1]
                price = safe_price(ticker)

                if price > 0:
                    yield_val = latest_div / price * 100
                    if yield_val > 20:
                        yield_val = 0

                for i in range(1,len(yearly)):
                    if yearly.iloc[-i] >= yearly.iloc[-(i+1)]:
                        growth_years += 1
                    else:
                        break

                div_cagr = cagr_5(yearly)

        # ===== 財務 =====
        net = get_financial_series(stock, [
            "Net Income",
            "NetIncome",
            "Profit Attributable"
        ])

        revenue = get_financial_series(stock, [
            "Total Revenue",
            "Revenue",
            "Net Sales"
        ])

        operating = get_financial_series(stock, [
            "Operating Income",
            "Operating Profit"
        ])

        net_cagr = cagr_5(net)
        rev_cagr = cagr_5(revenue)

        op_margin = 0
        if not operating.empty and not revenue.empty:
            r = revenue.iloc[-1]
            if r != 0:
                op_margin = operating.iloc[-1] / r * 100

        # ===== スコア =====
        scores["連続増配年数"] = get_score(growth_years, [(10,10),(8,5),(6,3)])
        scores["5年配当CAGR"] = get_score(div_cagr, [(10,15),(8,10),(6,5)])
        scores["純利益5年CAGR"] = get_score(net_cagr, [(10,15),(8,10),(6,5)])
        scores["売上5年CAGR"] = get_score(rev_cagr, [(10,10),(8,5),(6,3)])
        scores["営業利益率"] = get_score(op_margin, [(10,20),(8,15),(6,10)])
        scores["配当利回り"] = get_score(yield_val, [(10,5),(8,4),(6,3)])

        total = int(sum(scores.values()))

        return total, scores

    except Exception as e:
        return 0, scores


# ==========================
# UI
# ==========================

ticker = st.text_input("銘柄コード（例: 7203.T）")

if st.button("分析"):
    total, sc = calculate_score(ticker)

    st.subheader(f"総合スコア: {total}")
    st.table(pd.DataFrame(sc.items(), columns=["項目","点数"]))

    # 配当推移
    stock = yf.Ticker(ticker)
    divs = stock.dividends

    if divs is not None and not divs.empty:
        yearly = divs.groupby(divs.index.year).sum()
        yearly = yearly[yearly.index < datetime.now().year]

        fig = go.Figure()
        fig.add_bar(x=yearly.index.astype(str), y=yearly.values)
        st.plotly_chart(fig, use_container_width=True)
