import streamlit as st
import pandas as pd
import yfinance as yf
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import json
import os
import plotly.graph_objects as go
from datetime import datetime

# --- 設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100 (準リアルタイム)")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- データベース初期化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            dividend_yield REAL,
            last_update TIMESTAMP
        )''')

# --- JPXリスト取得 ---
@st.cache_data
def get_all_tickers():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    df = pd.read_excel(JPX_FILE)
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    return (df["コード"].astype(str) + ".T").tolist()

# --- 財務スコア計算（重い処理） ---
def calculate_fundamental_score(ticker):
    try:
        stock = yf.Ticker(ticker)
        # 財務データのみ取得（infoは最小限に）
        income = stock.income_stmt
        dividends = stock.dividends
        balance = stock.balance_sheet
        
        # 連続増配年数
        yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()
        growth_years = 0
        if len(yearly_div) > 1:
            for i in range(1, len(yearly_div)):
                if yearly_div.iloc[i] > yearly_div.iloc[i-1]: growth_years += 1
        
        # スコアリングロジック（簡略化して安定性を向上）
        s_growth = 10 if growth_years >= 10 else (8 if growth_years >= 5 else 6)
        
        scores = {"連続増配": s_growth} # 他の指標も同様に追加可能
        total = sum(scores.values())
        
        return total, scores
    except:
        return None, None

# --- 更新処理（スレッド用） ---
def update_ticker(ticker):
    total, scores = calculate_fundamental_score(ticker)
    if total is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO stocks (ticker, total_score, score_json, last_update) VALUES (?, ?, ?, ?)",
                         (ticker, total, json.dumps(scores), datetime.now()))

# --- メインUI ---
init_db()
all_tickers = get_all_tickers()

# サイドバー：データ管理
with st.sidebar:
    st.header("⚙️ データ管理")
    if st.button("未取得銘柄をスキャン (初回・更新)"):
        with sqlite3.connect(DB_PATH) as conn:
            exist = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
        new_tickers = list(set(all_tickers) - set(exist))[:20] # 一回のスキャン数を制限してBAN防止
        
        if new_tickers:
            with st.spinner(f"{len(new_tickers)}件取得中..."):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    list(executor.map(update_ticker, new_tickers))
            st.success("更新完了！")
        else:
            st.info("全ての銘柄がDBに存在します")

# --- 準リアルタイム・ランキング表示（Fragment機能） ---
@st.fragment(run_every=300) # 5分ごとに自動更新
def show_ranking():
    st.header("📊 準リアルタイム・ランキング")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT ticker, total_score FROM stocks", conn)
    
    if not df.empty:
        # 上位50件を抽出
        top_df = df.sort_values("total_score", ascending=False).head(50)
        top_tickers = top_df['ticker'].tolist()
        
        try:
            # 【重要】株価データのみを一括で高速ダウンロード
            prices = yf.download(top_tickers, period="1d", interval="1m", progress=False)['Close'].iloc[-1]
            
            top_df['現在値'] = top_df['ticker'].map(prices).round(1)
            # 簡易的な前日比（もし取得できれば）
            st.dataframe(top_df[['ticker', 'total_score', '現在値']], use_container_width=True)
            st.caption(f"最終更新: {datetime.now().strftime('%H:%M:%S')} (5分ごとに自動更新)")
        except Exception as e:
            st.warning("株価のリアルタイム取得に失敗しました。DBのデータを表示します。")
            st.dataframe(top_df)
    else:
        st.info("サイドバーから『スキャン』を実行してデータを蓄積してください。")

show_ranking()

# --- 個別銘柄分析 ---
st.header("🔎 個別銘柄分析")
code = st.text_input("銘柄コードを入力 (例: 9432)")
if code:
    ticker = code if code.endswith(".T") else code + ".T"
    with st.spinner("詳細データを取得中..."):
        # 詳細分析は個別にTicker.infoを叩く
        s = yf.Ticker(ticker)
        st.subheader(f"{s.info.get('longName', ticker)}")
        col1, col2 = st.columns(2)
        col1.metric("現在値", f"¥{s.fast_info.get('last_price', 0):.1f}")
        col1.metric("配当利回り", f"{s.info.get('dividendYield', 0)*100:.2f}%")
        
        # 簡易チャート
        hist = s.history(period="1mo")
        st.line_chart(hist['Close'])
