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
    # データが2点以上、かつ最初の値が0や負でないことを確認
    if series is None or len(series) < 2: return 0
    start_val = series.iloc[0]
    end_val = series.iloc[-1]
    
    if start_val <= 0 or end_val <= 0: return 0
    
    years = len(series) - 1
    if years < 1: return 0
    
    return ((end_val / start_val) ** (1 / years) - 1) * 100

def get_score(value, thresholds):
    for s, t in thresholds:
        if value >= t: return s
    return 0

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
    stock = yf.Ticker(ticker)
    # 円グラフの軸と表示順を完全に固定
    fixed_keys = [
        "連続増配年数", "5年配当CAGR", "純利益5年CAGR", "売上5年CAGR",
        "ROE", "営業利益率", "配当利回り", "予想配当性向"
    ]
    
    try:
        # 1. データの取得（待機時間を入れてAPI制限を回避）
        info = stock.info
        time.sleep(1.2)
        inc = stock.income_stmt
        if inc is None or inc.empty: 
            inc = stock.quarterly_income_stmt
        
        bal = stock.balance_sheet
        if bal is None or bal.empty:
            bal = stock.quarterly_balance_sheet
            
        divs = stock.dividends
        splits = stock.splits
        time.sleep(1.0)

        # 補助関数：日本株特有の項目名揺れに対応し、古い順にソートして取得
        def get_clean_ts(df, keywords):
            if df is None or df.empty: return pd.Series()
            for kw in keywords:
                # 大文字小文字・空白を無視してマッチング
                matches = [i for i in df.index if kw.lower().replace(" ", "") in i.lower().replace(" ", "")]
                if matches:
                    series = df.loc[matches[0]]
                    if isinstance(series, pd.DataFrame): series = series.iloc[0]
                    # 日付を古い順（昇順）に並び替え、欠損値を除く
                    return series.sort_index(ascending=True).dropna()
            return pd.Series()

        # --- A. 時系列データの抽出 (CAGR・増配判定用) ---
        net_inc_ts = get_clean_ts(inc, ["Net Income", "Controlling Interests", "NetIncome"])
        rev_ts = get_clean_ts(inc, ["Total Revenue", "Net Sales", "Operating Revenue"])
        
        # 配当データの処理（分割補正付き）
        growth_years = 0
        d_cagr_val = 0
        latest_div_sum = 0
        if not divs.empty:
            yearly_div = divs.sort_index(ascending=True).resample("YE").sum()
            confirmed_div = yearly_div[yearly_div.index.year < 2026] # 2026年(今年)の端数を除外
            
            if not confirmed_div.empty:
                latest_div_sum = confirmed_div.iloc[-1]
                # 株式分割の補正（日本アクア等の異常値対策）
                if not splits.empty:
                    last_split_date = splits.index[-1]
                    if confirmed_div.index[-1] < last_split_date:
                        latest_div_sum = latest_div_sum / splits.iloc[-1]

                if len(confirmed_div) > 1:
                    # 最新年から遡って連続増配をカウント
                    for i in range(1, len(confirmed_div)):
                        if confirmed_div.iloc[-i] >= confirmed_div.iloc[-(i+1)]:
                            growth_years += 1
                        else: break
                    d_cagr_val = cagr(confirmed_div)

        # --- B. 単一指標の算出 (営業利益率・利回りの復活) ---
        hist = stock.history(period="1d")
        current_price = hist['Close'].iloc[-1] if not hist.empty else 1
        
        # 1. 営業利益率：infoが空なら損益計算書から自前計算
        op_margin = (info.get("operatingMargins") or 0) * 100
        if op_margin == 0 and not inc.empty:
            op_inc_ts = get_clean_ts(inc, ["Operating Income", "Operating Profit", "OperatingProfit"])
            if not op_inc_ts.empty and not rev_ts.empty:
                op_margin = (op_inc_ts.iloc[-1] / rev_ts.iloc[-1] * 100) if rev_ts.iloc[-1] != 0 else 0

        # 2. 配当利回り：実績ベースを優先的に算出
        y_val = (latest_div_sum / current_price * 100) if (latest_div_sum > 0 and current_price > 0) else (info.get("dividendYield", 0) * 100)
        
        # 3. ROE：infoが空なら自前計算
        roe = (info.get("returnOnEquity") or 0) * 100
        if roe == 0 and not net_inc_ts.empty and not bal.empty:
            equity_ts = get_clean_ts(bal, ["Stockholders Equity", "Total Equity", "Common Stock Equity"])
            if not equity_ts.empty:
                roe = (net_inc_ts.iloc[-1] / equity_ts.iloc[-1] * 100) if equity_ts.iloc[-1] != 0 else 0
        
        # 4. 配当性向
        payout = (info.get("payoutRatio") or 0) * 100

        # --- C. スコアリング (OrderedDictで円グラフの順番を固定) ---
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

    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        # エラー時もグラフが壊れないよう0点の固定辞書を返す
        return 0, OrderedDict({k: 0 for k in fixed_keys})
        
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
    
    # 指標の並び順を定義（12時方向から時計回り）
    fixed_keys = [
        "連続増配年数", "5年配当CAGR", "純利益5年CAGR", "売上5年CAGR",
        "ROE", "営業利益率", "配当利回り", "予想配当性向"
    ]
    
    # JSONからスコアを取得
    raw_scores = json.loads(row_data['score_json'])
    
    # 1. 順序を固定したリストを作成
    categories = fixed_keys
    values = [raw_scores.get(k, 0) for k in categories]
    
# 1. レーダーチャート
    with col1:
        st.write("📈 指標別スコア")
        
        fig_radar = go.Figure(data=go.Scatterpolar(
            r=values + [values[0]],
            theta=categories + [categories[0]],
            fill='toself',
            fillcolor='rgba(31, 119, 180, 0.4)',
            line_color='#1f77b4'
        ))
        
        fig_radar.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True, 
                    range=[0, 10],
                    tickfont=dict(size=10),
                    gridcolor="lightgrey"
                ),
                angularaxis=dict(
                    direction="clockwise", # 時計回りに設定
                    period=len(categories),
                    gridcolor="lightgrey"
                )
            ),
            showlegend=False,
            height=400,
            margin=dict(l=60, r=60, t=40, b=40),
            dragmode=False
        )
        st.plotly_chart(fig_radar, use_container_width=True, config={'staticPlot': True})

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
# 3. 指標スコア詳細テーブル (チャートの順番と一致させる)
    st.write("📝 評価指標スコア詳細")
    table_data = []
    for k in fixed_keys:
        score_val = raw_scores.get(k, 0)
        # 点数に応じて絵文字を付与
        status = "✅" if score_val >= 8 else "◯" if score_val >= 6 else "△"
        table_data.append({
            "判定": status,
            "評価項目": k,
            "獲得点数": f"{score_val} / 10"
        })
    
    st.table(pd.DataFrame(table_data))

# --- 最後にこれを呼び出す ---
ranking_board()
