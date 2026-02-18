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

# --- サイドバー：自動巡回スキャン機能 ---
with st.sidebar:
    st.header("⚙️ データ収集エンジン")
    
    # 1. DBから現在の収集状況を確認
    try:
        with sqlite3.connect(DB_PATH) as conn:
            exist_df = pd.read_sql("SELECT ticker FROM stocks", conn)
            exist_tickers = exist_df['ticker'].tolist()
    except:
        exist_tickers = []
    
    total_count = len(master)
    collected_count = len(exist_tickers)
    progress_percent = collected_count / total_count if total_count > 0 else 0
    
    st.write(f"📊 収集済み: {collected_count} / {total_count} 銘柄")
    st.progress(progress_percent)

    st.divider()

    # 2. 自動巡回モードのスイッチ
    st.subheader("🚀 オートパイロット")
    auto_mode = st.toggle("自動巡回スキャンを開始", help="ONにすると10秒おきに3銘柄ずつ解析し、自動で画面を更新して次の銘柄へ進みます。")

    if auto_mode:
        # まだ取得していない銘柄をリストアップ
        remaining_tickers = [t for t in master.keys() if t not in exist_tickers]
        
        if remaining_tickers:
            targets = remaining_tickers[:3] # 負荷を抑えるため1回3銘柄
            st.info(f"解析中...残り {len(remaining_tickers)} 銘柄")
            st.code(", ".join(targets))
            
            # 1銘柄ずつ処理
            for t in targets:
                with st.status(f"解析中: {t}", expanded=False) as status:
                    total, sc = calculate_full_score_safe(t)
                    if total:
                        with sqlite3.connect(DB_PATH) as conn:
                            conn.execute("INSERT OR REPLACE INTO stocks VALUES (?,?,?,?)", 
                                         (t, total, json.dumps(sc), datetime.now()))
                        status.update(label=f"✅ {t} 完了 (Score: {total})", state="complete")
                    else:
                        status.update(label=f"⚠️ {t} スキップ (データ不足)", state="error")
                
                # API制限回避のための「溜め」
                time.sleep(10) 
            
            # 全3銘柄終わったら自動でリロードして次の3銘柄へ
            st.rerun()
        else:
            st.success("🎉 全銘柄の解析が完了しました！")
            st.balloons()
    else:
        st.write("😴 スキャン停止中。")
        st.caption("スイッチをONにすると解析を開始します。ブラウザを閉じずに放置してください。")

@st.fragment(run_every=300)
def ranking_board():
    st.header("📊 総合スコアランキング (TOP 50)")
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql("SELECT * FROM stocks", conn)
    
    if not df.empty:
        df = df.sort_values("total_score", ascending=False).head(50).copy()
        df['銘柄名'] = df['ticker'].apply(lambda x: master.get(x, {}).get('name', '不明'))
        df['業種'] = df['ticker'].apply(lambda x: master.get(x, {}).get('sector', '不明'))
        
        try:
            # 最新株価をバルク取得
            prices_data = yf.download(df['ticker'].tolist(), period="1d", progress=False)
            prices = prices_data['Close'].iloc[-1]
            df['現在値'] = df['ticker'].map(prices).round(1)
            
            # 2026年仕様: 選択機能を有効にしたテーブル
            display_df = df[['total_score', '銘柄名', '業種', '現在値', 'ticker']].rename(columns={'total_score':'点数'})
            
            event = st.dataframe(
                display_df,
                width='stretch',
                hide_index=True,
                on_select="rerun", # 選択時にリロードして下の詳細を表示
                selection_mode="single-row" # 1件ずつ選択
            )
            
            # 銘柄が選択された場合の詳細表示
            if event.selection.rows:
                selected_idx = event.selection.rows[0]
                selected_ticker = display_df.iloc[selected_idx]['ticker']
                show_details(selected_ticker, df[df['ticker'] == selected_ticker].iloc[0])

        except Exception as e:
            st.error(f"表示エラー: {e}")
    else:
        st.info("サイドバーのスキャンを実行してデータを蓄積してください。")

# --- 詳細表示用関数 ---
def show_details(ticker, row_data):
    st.divider()
    name = master.get(ticker, {}).get('name', '不明')
    st.subheader(f"🔍 {name} ({ticker}) の詳細分析")
    
    col1, col2 = st.columns([1, 1])
    
    # 1. レーダーチャート (操作無効化設定)
    with col1:
        st.write("📈 指標別スコア")
        scores = json.loads(row_data['score_json'])
        categories = list(scores.keys())
        values = list(scores.values())
        
        fig_radar = go.Figure(data=go.Scatterpolar(
            r=values + [values[0]],
            theta=categories + [categories[0]],
            fill='toself',
            line_color='#1f77b4'
        ))
        fig_radar.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 10])),
            showlegend=False,
            height=400,
            # configで「変形・操作」を禁止するため、ここでは最小限の余白設定
            margin=dict(l=40, r=40, t=40, b=40),
            dragmode=False # ドラッグによる移動・変形を禁止
        )
        # config={'staticPlot': True} を指定すると、一切のズーム・変形ができなくなります
        st.plotly_chart(fig_radar, width='stretch', config={'staticPlot': True})

    # 2. 配当推移グラフと利回りの補正
    with col2:
        st.write("💰 配当金の推移 (10年)")
        try:
            stock = yf.Ticker(ticker)
            divs = stock.dividends
                    
        # --- 2. 配当推移グラフ（Plotly版）の修正 ---
        # st.bar_chart ではなく Plotly を使うことで詳細な制御が可能になります
            if not divs.empty:
                yearly_divs = divs.resample("YE").sum().tail(10)
                fig_div = go.Figure(data=[go.Bar(
                    x=yearly_divs.index.year, 
                    y=yearly_divs.values,
                    marker_color='#1f77b4',
                    hovertemplate='西暦: %{x}<br>配当金: %{y}円<extra></extra>' # チップをカスタマイズ
                )])
                fig_div.update_layout(
                    height=300,
                    margin=dict(l=20, r=20, t=20, b=20),
                    dragmode=False, # 移動禁止
                    xaxis=dict(fixedrange=True), # X軸のズーム禁止
                    yaxis=dict(fixedrange=True), # Y軸のズーム禁止
                )
                st.plotly_chart(
                    fig_div, 
                    width='stretch', 
                    config={'displayModeBar': False} # ツールバーを隠してスッキリさせる
                )
                    
                    # 利回りの計算を厳格化 (700%などの異常値対策)
                    info = stock.info
                    raw_yield = info.get('dividendYield')
                    
                    if raw_yield is not None:
                        # 1.0(100%)を超える場合は、すでに100掛けされていると判断して補正
                        actual_yield = raw_yield if raw_yield < 1.0 else raw_yield / 100
                        display_yield = actual_yield * 100
                        
                        # 万が一、補正後も30%を超えるようなら「異常値」として警告表示
                        if display_yield > 30:
                            st.metric("予想配当利回り", "データ異常", delta=f"{display_yield:.1f}% ?", delta_color="inverse")
                        else:
                            st.metric("予想配当利回り", f"{display_yield:.2f} %")
                    else:
                        st.metric("予想配当利回り", "--- %")
                else:
                    st.info("配当データが見つかりませんでした。")
            except:
                st.error("データの取得に失敗しました。")

    # 3. 指標データ
    st.write("📝 評価指標スコア詳細")
    st.table(pd.DataFrame(scores.items(), columns=["評価項目", "獲得点数"]))

# --- 最後にこれを呼び出す ---
ranking_board()
