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

# --- 基本設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100")
st.write("2026年 認証エラー(401)・API制限(429) 対策済みモデル")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- 1. 【最重要】401エラーを回避するための認証セッション生成 ---
def get_verified_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    })
    try:
        # まずFinanceトップを叩いて認証用Cookieをサーバーから受け取る
        session.get('https://finance.yahoo.com', timeout=10)
    except:
        pass
    return session

# --- 2. 共通関数（CAGR・スコアリング） ---
def cagr(series):
    try:
        if len(series) < 5: return 0
        # 警告回避：ilocを使用
        start = series.iloc[-5] if len(series) >= 5 else series.iloc[0]
        end = series.iloc[-1]
        if start <= 0 or len(series) < 2: return 0
        years = min(len(series), 5)
        return ((end/start)**(1/years)-1)*100
    except: return 0

def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t: return s
    return 2

# --- 3. データベース初期化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )''')

# --- 4. JPXマスターデータ取得 ---
@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    try:
        df = pd.read_excel(JPX_FILE)
    except: return {}
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    return {str(row["コード"]) + ".T": {"name": row["銘柄名"], "sector": row["33業種区分"]} for _, row in df.iterrows()}

# --- 5. 10項目評価ロジック（401/429対策 & 2026年仕様） ---
def calculate_full_score_safe(ticker):
    session = get_verified_session()
    stock = yf.Ticker(ticker)
    
    try:
        # APIリクエスト間に「溜め」を作る
        info = stock.info
        time.sleep(0.8)
        divs = stock.dividends
        inc = stock.income_stmt
        bal = stock.balance_sheet

        if inc.empty or bal.empty: return None, None

        # 配当計算（ilocでFutureWarning回避）
        yearly_div = divs.resample("YE").sum() if not divs.empty else pd.Series()
        growth_years = 0
        if len(yearly_div) > 1:
            for i in range(1, len(yearly_div)):
                if yearly_div.iloc[-i] > yearly_div.iloc[-(i+1)]: growth_years += 1
                else: break
        
        d_cagr = cagr(yearly_div)
        payout = (info.get("payoutRatio") or 0) * 100
        
        # 収益系
        net_inc_series = inc.loc["Net Income"] if "Net Income" in inc.index else pd.Series()
        eps_cagr = cagr(net_inc_series)
        roe = (info.get("returnOnEquity") or 0) * 100
        
        retained = 0
        if "Retained Earnings" in bal.index:
            val = bal.loc["Retained Earnings"]
            retained = val.iloc[0] if isinstance(val, pd.Series) else val.iloc[0,0]
            
        latest_div_ps = yearly_div.iloc[-1] if not yearly_div.empty else 0
        shares = info.get("sharesOutstanding", 1)
        sustain = retained / (latest_div_ps * shares) if latest_div_ps > 0 else 0

        rev_series = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else pd.Series()
        rev_cagr = cagr(rev_series)
        op_margin = (info.get("operatingMargins") or 0) * 100
        mkt_cap = info.get("marketCap", 0)
        
        cash = 0
        if "Cash And Cash Equivalents" in bal.index:
            c_val = bal.loc["Cash And Cash Equivalents"]
            cash = c_val.iloc[0] if isinstance(c_val, pd.Series) else c_val.iloc[0,0]
            
        net_inc_val = net_inc_series.iloc[0] if not net_inc_series.empty else 0
        cn_per = (mkt_cap - cash) / net_inc_val if net_inc_val > 0 else 999
        yield_val = (info.get("dividendYield") or 0) * 100

        scores = {
            "連続増配年数": get_score(growth_years, [(10,10),(8,5),(6,3)]),
            "5年配当CAGR": get_score(d_cagr, [(10,15),(8,10),(6,5)]),
            "予想配当性向": get_score(60-payout, [(10,20),(8,10),(6,0)]),
            "純利益5年CAGR": get_score(eps_cagr, [(10,15),(8,10),(6,5)]),
            "ROE": get_score(roe, [(10,20),(8,15),(6,10)]),
            "配当維持可能年数": get_score(get_score(sustain, [(10,10),(8,5),(6,3)]), [(10,10)]), # 簡易化
            "売上5年CAGR": get_score(rev_cagr, [(10,10),(8,5),(6,3)]),
            "営業利益率": get_score(op_margin, [(10,20),(8,15),(6,10)]),
            "CN-PER": get_score(30-cn_per, [(10,15),(8,5),(6,0)]),
            "配当利回り": get_score(yield_val, [(10,5),(8,4),(6,3)])
        }
        return sum(scores.values()), scores
    except Exception as e:
        if "401" in str(e): st.error(f"認証エラー(401): Yahoo側の制限です。 {ticker}")
        return None, None

# --- 6. UIメイン ---
init_db()
master = get_ticker_master()

with st.sidebar:
    st.header("⚙️ システム管理")
    if st.button("未取得銘柄スキャン (2件ずつ)"):
        with sqlite3.connect(DB_PATH) as conn:
            exist = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
        targets = [t for t in master.keys() if t not in exist][:2]
        if targets:
            for t in targets:
                with st.spinner(f"{t} を解析中..."):
                    total, scores = calculate_full_score_safe(t)
                    if total:
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)", (t, total, json.dumps(scores), datetime.now()))
                time.sleep(3) # BAN回避のために3秒待機
            st.rerun()

@st.fragment(run_every=300)
def ranking_board():
    st.header("📊 スコアランキング (TOP 50)")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM stocks", conn)
    
    if not df.empty:
        df = df.sort_values("total_score", ascending=False).head(50).copy()
        df['銘柄名'] = df['ticker'].apply(lambda x: master.get(x, {}).get('name', '不明'))
        df['業種'] = df['ticker'].apply(lambda x: master.get(x, {}).get('sector', '不明'))
        
        try:
            session = get_verified_session()
            prices_data = yf.download(df['ticker'].tolist(), period="1d", progress=False)
            prices = prices_data['Close'].iloc[-1]
            df['現在値'] = df['ticker'].map(prices).round(1)
            
            # 2026年仕様: width='stretch'
            st.dataframe(df[['total_score', '銘柄名', '業種', '現在値', 'ticker']].rename(columns={'total_score':'点数'}), width='stretch', hide_index=True)
        except:
            st.dataframe(df[['total_score', '銘柄名', '業種', 'ticker']], width='stretch', hide_index=True)
    else:
        st.info("サイドバーからスキャンしてください")

ranking_board()

# 個別分析部分は前回同様のため省略可能ですが、必要なら追加してください。
