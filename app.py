import streamlit as st
import pandas as pd
import yfinance as yf
import urllib.request
import sqlite3
import json
import os
import plotly.graph_objects as go
from datetime import datetime
import time
import requests

# --- 基本設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100")
st.write("【API制限対策版】ブラウザ偽装リクエストを実装済み")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- ブラウザ偽装セッションの作成 ---
def get_browser_session():
    session = requests.Session()
    # Pythonライブラリではなく、WindowsのChromeブラウザとしてアクセスしているように見せかける
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8',
    })
    return session

# --- 共通関数：CAGR ---
def cagr(series):
    try:
        if len(series) < 5: return 0
        start = series.iloc[-5]
        end = series.iloc[0]
        if start <= 0: return 0
        return ((end/start)**(1/5)-1)*100
    except: return 0

def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t: return s
    return 2

# --- データベース初期化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )''')

# --- JPXマスターデータ取得 ---
@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    try:
        df = pd.read_excel(JPX_FILE)
    except:
        return {}
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    return {str(row["コード"]) + ".T": {"name": row["銘柄名"], "sector": row["33業種区分"]} for _, row in df.iterrows()}

# --- 10項目評価ロジック (API対策版) ---
def calculate_full_score_safe(ticker):
    session = get_browser_session()
    stock = yf.Ticker(ticker, session=session)
    
    try:
        # APIリクエストの間にわずかな「お作法」の待機を入れる
        info = stock.info
        time.sleep(0.5) 
        divs = stock.dividends
        time.sleep(0.5)
        inc = stock.income_stmt
        bal = stock.balance_sheet

        if inc.empty or bal.empty: return None, None

        # --- A. 配当 ---
        yearly_div = divs.resample("YE").sum() if not divs.empty else pd.Series()
        growth_years = 0
        if len(yearly_div) > 1:
            for i in range(1, len(yearly_div)):
                if yearly_div.iloc[-i] > yearly_div.iloc[-(i+1)]: growth_years += 1
                else: break
        d_cagr = cagr(yearly_div)
        payout = (info.get("payoutRatio") or 0) * 100
        
        # --- B. 収益 ---
        net_inc_series = inc.loc["Net Income"] if "Net Income" in inc.index else pd.Series()
        eps_cagr = cagr(net_inc_series)
        roe = (info.get("returnOnEquity") or 0) * 100
        retained = bal.loc["Retained Earnings"].iloc[0] if "Retained Earnings" in bal.index else 0
        latest_div = yearly_div.iloc[-1] if not yearly_div.empty else 0
        sustain = retained / (latest_div * info.get("sharesOutstanding", 1)) if latest_div > 0 else 0

        # --- C. 効率・割安 ---
        rev = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else pd.Series()
        rev_cagr = cagr(rev)
        op_margin = (info.get("operatingMargins") or 0) * 100
        mkt_cap = info.get("marketCap", 0)
        cash = bal.loc["Cash And Cash Equivalents"].iloc[0] if "Cash And Cash Equivalents" in bal.index else 0
        net_inc_val = net_inc_series.iloc[0] if not net_inc_series.empty else 0
        cn_per = (mkt_cap - cash) / net_inc_val if net_inc_val > 0 else 999
        yield_val = (info.get("dividendYield") or 0) * 100

        scores = {
            "連続増配年数": get_score(growth_years, [(10,10),(8,5),(6,3)]),
            "5年配当CAGR": get_score(d_cagr, [(10,15),(8,10),(6,5)]),
            "予想配当性向": get_score(60-payout, [(10,20),(8,10),(6,0)]),
            "純利益5年CAGR": get_score(eps_cagr, [(10,15),(8,10),(6,5)]),
            "ROE": get_score(roe, [(10,20),(8,15),(6,10)]),
            "配当維持可能年数": get_score(sustain, [(10,10),(8,5),(6,3)]),
            "売上5年CAGR": get_score(rev_cagr, [(10,10),(8,5),(6,3)]),
            "営業利益率": get_score(op_margin, [(10,20),(8,15),(6,10)]),
            "CN-PER": get_score(30-cn_per, [(10,15),(8,5),(6,0)]),
            "配当利回り": get_score(yield_val, [(10,5),(8,4),(6,3)])
        }
        return sum(scores.values()), scores
    except Exception as e:
        if "429" in str(e):
            st.warning("Yahoo Finance側で一時的に制限がかかりました。数分置いてください。")
        return None, None

def update_db(ticker):
    total, scores = calculate_full_score_safe(ticker)
    if total:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)", 
                         (ticker, total, json.dumps(scores), datetime.now()))

# --- UIメイン ---
init_db()
master = get_ticker_master()

with st.sidebar:
    st.header("⚙️ システム管理")
    st.write("制限回避のため、1回のリクエストをさらに低速化しています。")
    if st.button("未取得銘柄スキャン (3件ずつ)"):
        with sqlite3.connect(DB_PATH) as conn:
            exist = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
        targets = [t for t in master.keys() if t not in exist][:3]
        if targets:
            for t in targets:
                update_db(t)
                time.sleep(2) # 銘柄間でも2秒待つ
            st.success("完了！")
            st.rerun()

@st.fragment(run_every=300)
def ranking_board():
    st.header("📊 スコアランキング (TOP 50)")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM stocks", conn)
    
    if not df.empty:
        df = df.sort_values("total_score", ascending=False).head(50)
        df['銘柄名'] = df['ticker'].apply(lambda x: master.get(x, {}).get('name', '不明'))
        df['業種'] = df['ticker'].apply(lambda x: master.get(x, {}).get('sector', '不明'))
        
        try:
            # yf.downloadにもブラウザ偽装セッションを渡す
            session = get_browser_session()
            prices = yf.download(df['ticker'].tolist(), period="1d", session=session, progress=False)['Close'].iloc[-1]
            df['現在値'] = df['ticker'].map(prices).round(1)
            
            # width='stretch' (2026年仕様)
            st.dataframe(
                df[['total_score', '銘柄名', '業種', '現在値', 'ticker']].rename(columns={'total_score':'点数'}), 
                width='stretch', 
                hide_index=True
            )
        except:
            st.dataframe(df[['total_score', '銘柄名', '業種', 'ticker']], width='stretch', hide_index=True)
    else:
        st.info("サイドバーからスキャンしてください")

ranking_board()

# --- 個別分析 ---
st.divider()
code = st.text_input("銘柄コード入力 (例: 9432)")
if code:
    t = code if code.endswith(".T") else code + ".T"
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT score_json, total_score FROM stocks WHERE ticker=?", (t,)).fetchone()
    
    if row:
        scores = json.loads(row[0])
        st.subheader(f"{master.get(t, {}).get('name')} - {row[1]}点")
        
        categories = list(scores.keys())
        values = list(scores.values())
        fig = go.Figure(data=go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself'))
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 10])), showlegend=False)
        st.plotly_chart(fig)
        st.table(pd.DataFrame(scores.items(), columns=["指標", "点数"]))
    else:
        if st.button(f"{t} を今すぐ解析"):
            update_db(t)
            st.rerun()
