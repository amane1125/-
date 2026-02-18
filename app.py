import streamlit as st
import pandas as pd
import yfinance as yf
import urllib.request
import sqlite3
import json
import os
import plotly.graph_objects as go
from datetime import datetime

# --- 基本設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100 (100点満点評価)")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- 共通関数：CAGR (年平均成長率) ---
def cagr(series):
    try:
        if len(series) < 5: return 0
        start = series.iloc[-5]
        end = series.iloc[0]
        if start <= 0: return 0
        return ((end/start)**(1/5)-1)*100
    except:
        return 0

# --- 共通関数：スコアリング ---
def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t: return s
    return 2 # 最低点

# --- 1. データベース初期化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )''')

# --- 2. JPXマスターデータ取得 ---
@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    try:
        df = pd.read_excel(JPX_FILE)
    except ImportError:
        st.error("xlrdをインストールしてください: pip install xlrd")
        return {}
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    return {str(row["コード"]) + ".T": {"name": row["銘柄名"], "sector": row["33業種区分"]} for _, row in df.iterrows()}

# --- 3. 10項目・100点満点評価ロジック ---
def calculate_full_score(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        divs = stock.dividends
        inc = stock.income_stmt
        bal = stock.balance_sheet

        if inc.empty or bal.empty: return None, None

        # --- A. 配当系 ---
        yearly_div = divs.resample("YE").sum() if not divs.empty else pd.Series()
        growth_years = 0
        if len(yearly_div) > 1:
            for i in range(1, len(yearly_div)):
                if yearly_div.iloc[-i] > yearly_div.iloc[-(i+1)]: growth_years += 1
                else: break
        
        d_cagr = cagr(yearly_div)
        payout = (info.get("payoutRatio") or 0) * 100
        
        # --- B. 収益・財務系 ---
        net_income = inc.loc["Net Income"] if "Net Income" in inc.index else pd.Series()
        eps_cagr = cagr(net_income)
        roe = (info.get("returnOnEquity") or 0) * 100
        
        retained = bal.loc["Retained Earnings"].iloc[0] if "Retained Earnings" in bal.index else 0
        latest_div_sum = yearly_div.iloc[-1] if not yearly_div.empty else 0
        sustain = retained / (latest_div_sum * info.get("sharesOutstanding", 1)) if latest_div_sum > 0 else 0

        rev = inc.loc["Total Revenue"] if "Total Revenue" in inc.index else pd.Series()
        rev_cagr = cagr(rev)
        op_margin = (info.get("operatingMargins") or 0) * 100
        
        # --- C. バリュエーション系 ---
        mkt_cap = info.get("marketCap", 0)
        cash = bal.loc["Cash And Cash Equivalents"].iloc[0] if "Cash And Cash Equivalents" in bal.index else 0
        net_inc_val = net_income.iloc[0] if not net_income.empty else 0
        cn_per = (mkt_cap - cash) / net_inc_val if net_inc_val > 0 else 999
        yield_val = (info.get("dividendYield") or 0) * 100

        # --- 10指標スコアリング ---
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
    except:
        return None, None

# --- 4. 更新処理 ---
def update_ticker(ticker):
    total, scores = calculate_full_score(ticker)
    if total:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)", (ticker, total, json.dumps(scores), datetime.now()))

# --- 5. UI構築 ---
init_db()
master = get_ticker_master()

with st.sidebar:
    st.header("⚙️ 運営")
    if st.button("未取得銘柄スキャン (10件)"):
        with sqlite3.connect(DB_PATH) as conn:
            exist = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
        targets = [t for t in master.keys() if t not in exist][:10]
        for t in targets: update_ticker(t)
        st.rerun()

@st.fragment(run_every=300)
def ranking():
    st.header("📊 総合スコアランキング (TOP 50)")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM stocks", conn)
    if not df.empty:
        df = df.sort_values("total_score", ascending=False).head(50)
        df['銘柄名'] = df['ticker'].apply(lambda x: master.get(x, {}).get('name', '不明'))
        df['業種'] = df['ticker'].apply(lambda x: master.get(x, {}).get('sector', '不明'))
        
        # 株価一括取得
        prices = yf.download(df['ticker'].tolist(), period="1d", progress=False)['Close'].iloc[-1]
        df['現在値'] = df['ticker'].map(prices).round(1)
        
        st.dataframe(df[['total_score', '銘柄名', '業種', '現在値', 'ticker']].rename(columns={'total_score':'点数'}), use_container_width=True, hide_index=True)
    else:
        st.info("スキャンしてください")

ranking()

# --- 6. 個別分析 & レーダーチャート ---
st.divider()
code = st.text_input("銘柄コードを入力 (例: 9432)")
if code:
    t = code if code.endswith(".T") else code + ".T"
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT score_json, total_score FROM stocks WHERE ticker=?", (t,)).fetchone()
    
    if row:
        scores = json.loads(row[0])
        st.subheader(f"{master.get(t, {}).get('name')} - 総合点: {row[1]}/100")
        
        # レーダーチャート
        fig = go.Figure(data=go.Scatterpolar(r=list(scores.values()) + [list(scores.values())[0]], 
                                            theta=list(scores.keys()) + [list(scores.keys())[0]], fill='toself'))
        fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 10])), showlegend=False)
        st.plotly_chart(fig)
        st.table(pd.DataFrame(scores.items(), columns=["指標", "点数"]))
    else:
        st.warning("DBに未登録です。サイドバーでスキャンするか、一度計算されるまでお待ちください。")
