import streamlit as st
import pandas as pd
import yfinance as yf
from io import BytesIO
import requests
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import json
import plotly.graph_objects as go

# ------------------------
# 設定・初期化
# ------------------------
st.set_page_config(page_title="Dividend Growth 100", layout="wide")
st.title("🇯🇵 Dividend Growth 100")
st.write("増配企業を100点満点で評価します")

DB_PATH = "stock_data.db"

# ------------------------
# 共通ロジック関数
# ------------------------
def cagr(series):
    try:
        if len(series) < 5:
            return 0
        # yfinanceのdfは[0]が最新、[-5]が古いデータの場合があるため向きに注意
        start = series.iloc[-5]
        end = series.iloc[0]
        if start <= 0:
            return 0
        return ((end / start) ** (1 / 5) - 1) * 100
    except:
        return 0

def get_score_from_value(value, thresholds):
    if value is None:
        return 0
    for s, t in thresholds:
        if value >= t:
            return s
    return 2

# ------------------------
# データ取得・計算関数
# ------------------------
def get_stock_metrics(code):
    """
    yfinanceからデータを取得し、生の指標数値を計算する共通関数
    """
    stock = yf.Ticker(code)
    info = stock.info
    dividends = stock.dividends
    income_stmt = stock.income_stmt
    balance = stock.balance_sheet

    # 配当データ
    yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()
    growth_years = 0
    if len(yearly_div) > 1:
        # 新しい順に並んでいる可能性があるため反転して計算
        rev_div = yearly_div.iloc[::-1]
        for i in range(1, len(rev_div)):
            if rev_div.iloc[i] > rev_div.iloc[i-1]:
                growth_years += 1
            else:
                break

    div_cagr_val = cagr(yearly_div)
    payout = (info.get("payoutRatio") or 0) * 100

    # 利益・ROE
    net_income_series = income_stmt.loc["Net Income"] if "Net Income" in income_stmt.index else pd.Series()
    eps_cagr_val = cagr(net_income_series)
    roe = (info.get("returnOnEquity") or 0) * 100
    
    # 配当維持可能年数
    retained = balance.loc["Retained Earnings"][0] if "Retained Earnings" in balance.index else 0
    annual_div_total = yearly_div.iloc[0] if len(yearly_div) > 0 else 1
    sustain = retained / annual_div_total if annual_div_total > 0 else 0

    # 売上・利益率
    revenue_series = income_stmt.loc["Total Revenue"] if "Total Revenue" in income_stmt.index else pd.Series()
    revenue_cagr_val = cagr(revenue_series)
    
    if "Operating Income" in income_stmt.index and "Total Revenue" in income_stmt.index:
        op_income = income_stmt.loc["Operating Income"]
        total_revenue = income_stmt.loc["Total Revenue"]
        op_margin = (op_income.iloc[0] / total_revenue.iloc[0]) * 100 if total_revenue.iloc[0] != 0 else 0
    else:
        op_margin = 0

    # バリュエーション (CN-PER)
    market_cap = info.get("marketCap", 0)
    cash = balance.loc["Cash And Cash Equivalents"][0] if "Cash And Cash Equivalents" in balance.index else 0
    net_income_latest = net_income_series.iloc[0] if len(net_income_series) > 0 else 1
    cn_per = (market_cap - cash) / net_income_latest if net_income_latest != 0 else 999

    # 利回り
    dy_raw = info.get("dividendYield")
    if dy_raw:
        dividend_yield = dy_raw * 100 if dy_raw < 1 else dy_raw
    else:
        price = info.get("regularMarketPrice")
        dividend_yield = (yearly_div.iloc[0] / price * 100) if (len(yearly_div) > 0 and price) else 0

    metrics = {
        "連続増配年数": growth_years,
        "5年配当CAGR": div_cagr_val,
        "予想配当性向": 60 - payout,
        "純利益5年CAGR": eps_cagr_val,
        "ROE": roe,
        "配当維持可能年数": sustain,
        "売上5年CAGR": revenue_cagr_val,
        "営業利益率": op_margin,
        "CN-PER": 30 - cn_per,
        "配当利回り": dividend_yield
    }
    return metrics

def calculate_score(metrics):
    """
    生の指標からスコア(各項目10点満点)を算出
    """
    scores = {
        "連続増配年数": get_score_from_value(metrics["連続増配年数"], [(10,10),(8,5),(6,3)]),
        "5年配当CAGR": get_score_from_value(metrics["5年配当CAGR"], [(10,15),(8,10),(6,5)]),
        "予想配当性向": get_score_from_value(metrics["予想配当性向"], [(10,20),(8,10),(6,0)]),
        "純利益5年CAGR": get_score_from_value(metrics["純利益5年CAGR"], [(10,15),(8,10),(6,5)]),
        "ROE": get_score_from_value(metrics["ROE"], [(10,20),(8,15),(6,10)]),
        "配当維持可能年数": get_score_from_value(metrics["配当維持可能年数"], [(10,10),(8,5),(6,3)]),
        "売上5年CAGR": get_score_from_value(metrics["売上5年CAGR"], [(10,10),(8,5),(6,3)]),
        "営業利益率": get_score_from_value(metrics["営業利益率"], [(10,20),(8,15),(6,10)]),
        "CN-PER": get_score_from_value(metrics["CN-PER"], [(10,15),(8,5),(6,0)]),
        "配当利回り": get_score_from_value(metrics["配当利回り"], [
            (10,4.5), (9,4.25), (8,4.0), (7,3.75), (6,3.5), (5,3.25), (4,3.0), (3,2.75), (2,2.5)
        ])
    }
    total = sum(scores.values())
    return total, scores

# ------------------------
# データ取得・DB関連
# ------------------------
def get_all_tickers():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    df = pd.read_excel(BytesIO(response.content))
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    tickers = df["コード"].astype(str) + ".T"
    return set(tickers)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stocks (
                    ticker TEXT PRIMARY KEY,
                    total_score INTEGER,
                    score_json TEXT,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )''')
    conn.commit()
    return conn

def fetch_and_cache(ticker, conn):
    try:
        metrics = get_stock_metrics(ticker)
        total, scores = calculate_score(metrics)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO stocks (ticker, total_score, score_json)
                     VALUES (?, ?, ?)''', (ticker, total, json.dumps(scores)))
        conn.commit()
        return {"ticker": ticker, "total_score": total}
    except:
        return None

# ------------------------
# メイン処理
# ------------------------
conn = init_db()

# サイドバーまたは上部でデータ更新
if st.button("全銘柄データを更新 (時間がかかります)"):
    try:
        all_tickers = get_all_tickers()
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM stocks")
        old_tickers = set([row[0] for row in cur.fetchall()])
        add_tickers = all_tickers - old_tickers
        
        st.write(f"新規銘柄 {len(add_tickers)} 件を取得中...")
        with ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(lambda t: fetch_and_cache(t, conn), add_tickers))
        st.success("更新完了")
    except Exception as e:
        st.error(f"データ取得エラー: {e}")

# ランキング表示
st.header("📊 ランキング分析")
df_db = pd.read_sql("SELECT ticker, total_score FROM stocks ORDER BY total_score DESC", conn)
st.dataframe(df_db, use_container_width=True)

# 個別分析
st.header("🔎 個別銘柄分析")
ticker_input = st.text_input("銘柄コード（例: 9432）")
if ticker_input:
    ticker = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
    
    with st.spinner("データ解析中..."):
        try:
            # 常に最新の計算結果を表示
            metrics = get_stock_metrics(ticker)
            total, scores = calculate_score(metrics)

            st.metric("総合スコア", f"{total} / 100")

            col1, col2 = st.columns(2)
            with col1:
                st.write("### 指標別スコア")
                st.table(pd.DataFrame(scores.items(), columns=["指標", "点数"]))
            
            with col2:
                st.write("### 実際の数値")
                st.table(pd.DataFrame(metrics.items(), columns=["指標", "値"]))

            # レーダーチャート
            categories = list(scores.keys())
            values = list(scores.values())
            fig = go.Figure()
            fig.add_trace(go.Scatterpolar(
                r=values + [values[0]],
                theta=categories + [categories[0]],
                fill='toself'
            ))
            fig.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0, 10])),
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"銘柄データの取得に失敗しました。コードを確認してください。: {e}")
