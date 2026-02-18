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

# --- 基本設定 ---
st.set_page_config(page_title="Dividend Growth 100 RT", layout="wide")
st.title("🇯🇵 Dividend Growth 100 (準リアルタイム)")
st.write("財務スコア（DB）と最新株価（リアルタイム）を融合して評価します")

DB_PATH = "stock_data.db"
JPX_FILE = "jpx_list.xls"

# --- 1. データベース初期化 ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS stocks (
            ticker TEXT PRIMARY KEY,
            total_score INTEGER,
            score_json TEXT,
            last_update TIMESTAMP
        )''')

# --- 2. JPXマスターデータ取得 (銘柄名・業種対応) ---
@st.cache_data
def get_ticker_master():
    if not os.path.exists(JPX_FILE):
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        urllib.request.urlretrieve(url, JPX_FILE)
    
    # xlrdがインストールされている必要があります
    try:
        df = pd.read_excel(JPX_FILE)
    except ImportError:
        st.error("ライブラリ 'xlrd' が足りません。 pip install xlrd を実行してください。")
        return {}

    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    
    master = {}
    for _, row in df.iterrows():
        ticker = str(row["コード"]) + ".T"
        master[ticker] = {
            "name": row["銘柄名"],
            "sector": row["33業種区分"]
        }
    return master

# --- 3. 財務スコア計算ロジック (重い処理) ---
def calculate_fundamental_score(ticker):
    try:
        stock = yf.Ticker(ticker)
        # 連続増配年数の計算（過去の配当データを使用）
        dividends = stock.dividends
        yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()
        
        growth_years = 0
        if len(yearly_div) > 1:
            for i in range(1, len(yearly_div)):
                if yearly_div.iloc[i] > yearly_div.iloc[i-1]: growth_years += 1
        
        # サンプルスコアリング（他の財務指標もここに追加可能）
        s_growth = 10 if growth_years >= 10 else (8 if growth_years >= 5 else 6)
        scores = {"連続増配年数": s_growth}
        total = sum(scores.values())
        
        return total, scores
    except:
        return None, None

def update_ticker_in_db(ticker):
    total, scores = calculate_fundamental_score(ticker)
    if total is not None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO stocks (ticker, total_score, score_json, last_update) VALUES (?, ?, ?, ?)",
                         (ticker, total, json.dumps(scores), datetime.now()))

# --- 4. メイン処理準備 ---
init_db()
master_data = get_ticker_master()
all_tickers = list(master_data.keys())

# サイドバー：DB更新用
with st.sidebar:
    st.header("⚙️ データ更新")
    st.write("新しい銘柄の財務データをDBに保存します。")
    if st.button("未取得銘柄をスキャン (20件ずつ)"):
        with sqlite3.connect(DB_PATH) as conn:
            exist = pd.read_sql("SELECT ticker FROM stocks", conn)['ticker'].tolist()
        new_tickers = list(set(all_tickers) - set(exist))[:20]
        
        if new_tickers:
            progress_bar = st.progress(0)
            for i, t in enumerate(new_tickers):
                update_ticker_in_db(t)
                progress_bar.progress((i + 1) / len(new_tickers))
            st.success(f"{len(new_tickers)}件更新しました。")
            st.rerun()
        else:
            st.info("全ての銘柄が登録済みです。")

# --- 5. 準リアルタイム・ランキング表示 (5分自動更新) ---
@st.fragment(run_every=300)
def show_ranking_board():
    st.header("📊 スコアランキング (TOP 50)")
    
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT ticker, total_score FROM stocks", conn)
    
    if not df.empty:
        # スコア順に並び替え
        top_df = df.sort_values("total_score", ascending=False).head(50).copy()
        
        # 銘柄名と業種をマッピング
        top_df['銘柄名'] = top_df['ticker'].apply(lambda x: master_data.get(x, {}).get('name', '不明'))
        top_df['業種'] = top_df['ticker'].apply(lambda x: master_data.get(x, {}).get('sector', '不明'))
        
        top_tickers = top_df['ticker'].tolist()
        
        try:
            # 最新株価を「一括」で取得 (爆速 & BAN対策)
            prices = yf.download(top_tickers, period="1d", interval="1m", progress=False)['Close'].iloc[-1]
            top_df['現在値'] = top_df['ticker'].map(prices).round(1)
            
            # カラムを整理して表示
            display_cols = ['total_score', '銘柄名', '業種', '現在値', 'ticker']
            st.dataframe(
                top_df[display_cols].rename(columns={'total_score': '総合点', 'ticker': 'コード'}), 
                use_container_width=True, 
                hide_index=True
            )
            st.caption(f"最終更新時刻: {datetime.now().strftime('%H:%M:%S')} (5分おきに自動更新中)")
        except:
            st.warning("リアルタイム株価の取得に失敗しました。DBデータのみ表示します。")
            st.dataframe(top_df[['total_score', '銘柄名', '業種', 'ticker']], hide_index=True)
    else:
        st.info("左側のスキャンボタンを押して、まずDBにデータを溜めてください。")

show_ranking_board()

# --- 6. 個別銘柄検索 ---
st.divider()
st.header("🔎 個別詳細分析")
search_code = st.text_input("銘柄コードを入力してください (例: 9432)")

if search_code:
    t_code = search_code if search_code.endswith(".T") else search_code + ".T"
    if t_code in master_data:
        st.subheader(f"{master_data[t_code]['name']} ({master_data[t_code]['sector']})")
        
        with st.spinner("詳細データを取得中..."):
            s = yf.Ticker(t_code)
            col1, col2, col3 = st.columns(3)
            col1.metric("現在値", f"¥{s.fast_info.get('last_price', 0):.1f}")
            # info取得は慎重に (個別ページのみ実行)
            info = s.info
            col2.metric("配当利回り", f"{info.get('dividendYield', 0)*100:.2f}%")
            col3.metric("PER", f"{info.get('trailingPE', 0):.1f}倍")
            
            # 直近1ヶ月のチャート
            hist = s.history(period="1mo")
            st.line_chart(hist['Close'])
    else:
        st.error("有効な銘柄コードではありません。")
