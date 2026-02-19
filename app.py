import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import sqlite3
import json
import os
import time
import urllib.request
import plotly.graph_objects as go
from datetime import datetime
from collections import OrderedDict

# --- 基本設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100")
st.write("2026年 認証エラー・分割バグ・データ欠損 対策済み完全版")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- 1. 共通関数（CAGR・スコアリング） ---
def cagr(series):
    if series is None or len(series) < 2: return 0
    start_val = series.iloc[0] # 古い順
    end_val = series.iloc[-1]  # 新しい順
    if start_val <= 0 or end_val <= 0: return 0
    years = len(series) - 1
    if years < 1: return 0
    return ((end_val / start_val) ** (1 / years) - 1) * 100

def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t: return s
    return 0

# --- 2. データベース & マスターデータ ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )''')

@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    try:
        df = pd.read_excel(JPX_FILE)
        df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
        return {str(row["コード"]) + ".T": {"name": row["銘柄名"], "sector": row["33業種区分"]} for _, row in df.iterrows()}
    except: return {}

# --- 3. 【核心】10項目評価ロジック ---
def calculate_full_score_safe(ticker):
    stock = yf.Ticker(ticker)
    fixed_keys = [
        "連続増配年数", "5年配当CAGR", "純利益5年CAGR", "売上5年CAGR",
        "ROE", "営業利益率", "配当利回り", "予想配当性向"
    ]
    
    try:
        info = stock.info
        time.sleep(1.2)
        inc = stock.income_stmt
        if inc is None or inc.empty: inc = stock.quarterly_income_stmt
        divs = stock.dividends
        splits = stock.splits
        time.sleep(1.0)

        def get_clean_ts(df, keywords):
            if df is None or df.empty: return pd.Series()
            for kw in keywords:
                matches = [i for i in df.index if kw.lower().replace(" ", "") in i.lower().replace(" ", "")]
                if matches:
                    series = df.loc[matches[0]]
                    if isinstance(series, pd.DataFrame): series = series.iloc[0]
                    return series.sort_index(ascending=True).dropna()
            return pd.Series()

        # A. 時系列データ
        net_inc_ts = get_clean_ts(inc, ["Net Income", "Controlling Interests", "NetIncome"])
        rev_ts = get_clean_ts(inc, ["Total Revenue", "Net Sales", "Operating Revenue"])
        
        # B. 配当計算 (分割補正 & 2026年問題回避)
        growth_years = 0
        d_cagr_val = 0
        latest_div_sum = 0
        if not divs.empty:
            yearly_div = divs.sort_index(ascending=True).resample("YE").sum()
            confirmed_div = yearly_div[yearly_div.index.year < 2026]
            if not confirmed_div.empty:
                latest_div_sum = confirmed_div.iloc[-1]
                if not splits.empty:
                    if confirmed_div.index[-1] < splits.index[-1]:
                        latest_div_sum = latest_div_sum / splits.iloc[-1]
                if len(confirmed_div) > 1:
                    for i in range(1, len(confirmed_div)):
                        if confirmed_div.iloc[-i] >= confirmed_div.iloc[-(i+1)]: growth_years += 1
                        else: break
                    d_cagr_val = cagr(confirmed_div)

        # C. 指標算出 (営業利益率・利回りの徹底取得)
        hist = stock.history(period="1d")
        current_price = hist['Close'].iloc[-1] if not hist.empty else 1
        op_margin = (info.get("operatingMargins") or 0) * 100
        if op_margin == 0 and not inc.empty:
            op_inc_ts = get_clean_ts(inc, ["Operating Income", "Operating Profit", "OperatingProfit"])
            if not op_inc_ts.empty and not rev_ts.empty:
                op_margin = (op_inc_ts.iloc[-1] / rev_ts.iloc[-1] * 100) if rev_ts.iloc[-1] != 0 else 0

        y_val = (latest_div_sum / current_price * 100) if latest_div_sum > 0 else (info.get("dividendYield", 0) * 100)
        roe = (info.get("returnOnEquity") or 0) * 100
        payout = (info.get("payoutRatio") or 0) * 100

        # D. スコアリング (OrderedDictで順番固定)
        scores = OrderedDict()
        scores["連続増配年数"] = get_score(growth_years, [(10, 10), (8, 5), (6, 3)])
        scores["5年配当CAGR"] = get_score(d_cagr_val, [(10, 15), (8, 10), (6, 5)])
        scores["純利益5年CAGR"] = get_score(cagr(net_inc_ts), [(10, 15), (8, 10), (6, 5)])
        scores["売上5年CAGR"] = get_score(cagr(rev_ts), [(10, 10), (8, 5), (6, 3)])
        scores["ROE"] = get_score(roe, [(10, 20), (8, 15), (6, 10)])
        scores["営業利益率"] = get_score(op_margin, [(10, 20), (8, 15), (6, 10)])
        scores["配当利回り"] = get_score(y_val, [(10, 5), (8, 4), (6, 3)])
        scores["予想配当性向"] = get_score(60 - payout, [(10, 20), (8, 10), (6, 0)])

        return sum(scores.values()), scores
    except:
        return 0, OrderedDict({k: 0 for k in fixed_keys})

# --- 4. UI メイン ---
init_db()
master = get_ticker_master()

with st.sidebar:
    st.header("⚙️ エンジン")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            exist_tickers = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
    except: exist_tickers = []
    
    st.write(f"📊 収集: {len(exist_tickers)} / {len(master)}")
    auto_mode = st.toggle("自動巡回スキャン開始")

    if auto_mode:
        remaining = [t for t in master.keys() if t not in exist_tickers]
        if remaining:
            targets = remaining[:3]
            for t in targets:
                total, sc = calculate_full_score_safe(t)
                if total > 0:
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)", (t, total, json.dumps(sc), datetime.now()))
                time.sleep(5)
            st.rerun()

# --- 5. ランキング & 詳細表示 ---
def ranking_board():
    st.header("📊 スコアランキング")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM stocks", conn)
    
    if not df.empty:
        df = df.sort_values("total_score", ascending=False).head(50)
        df['銘柄名'] = df['ticker'].apply(lambda x: master.get(x, {}).get('name', '不明'))
        
        event = st.dataframe(df[['total_score', '銘柄名', 'ticker']].rename(columns={'total_score':'点数'}), 
                             on_select="rerun", selection_mode="single-row", hide_index=True)
        
        if event.selection.rows:
            selected_ticker = df.iloc[event.selection.rows[0]]['ticker']
            show_details(selected_ticker, df[df['ticker'] == selected_ticker].iloc[0])

def show_details(ticker, row_data):
    st.divider()
    scores = json.loads(row_data['score_json'])
    fixed_keys = ["連続増配年数", "5年配当CAGR", "純利益5年CAGR", "売上5年CAGR", "ROE", "営業利益率", "配当利回り", "予想配当性向"]
    categories = fixed_keys
    values = [scores.get(k, 0) for k in categories]

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure(data=go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself'))
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 10])), showlegend=False, dragmode=False)
        st.plotly_chart(fig, config={'staticPlot': True})
    
    with c2:
        st.write("📝 スコア詳細")
        table_data = [{"判定": "✅" if scores.get(k,0)>=8 else "△", "項目": k, "点数": f"{scores.get(k,0)}/10"} for k in fixed_keys]
        st.table(pd.DataFrame(table_data))

ranking_board()
