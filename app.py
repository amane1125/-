import streamlit as st
import pandas as pd
import yfinance as yf
from io import BytesIO
import requests
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import json
import plotly.graph_objects as go

st.set_page_config(page_title="Dividend Growth 100", layout="wide")
st.title("🇯🇵 Dividend Growth 100")
st.write("増配企業を100点満点で評価します")

DB_PATH = "stock_data.db"

# ------------------------
# 共通関数
# ------------------------
def cagr(series):
    try:
        if len(series) < 5:
            return 0
        start = series.iloc[-5]
        end = series.iloc[0]
        if start <= 0:
            return 0
        return ((end / start) ** (1 / 5) - 1) * 100
    except:
        return 0

def score(value, thresholds):
    for s, t in thresholds:
        if value >= t:
            return s
    return 2

# ------------------------
# JPX Excelから全銘柄取得
# ------------------------
def get_all_tickers():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    df = pd.read_excel(BytesIO(response.content))
    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]
    tickers = df["コード"].astype(str) + ".T"
    return set(tickers)

# ------------------------
# SQLite 初期化
# ------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS stocks (
        ticker TEXT PRIMARY KEY,
        total_score INTEGER,
        score_json TEXT,
        last_update TIMESTAMP
    )''')
    conn.commit()
    return conn

# ------------------------
# 個別銘柄評価
# ------------------------
def calculate_score_raw(code):
    stock = yf.Ticker(code)
    info = stock.info
    dividends = stock.dividends
    income_stmt = stock.income_stmt
    balance = stock.balance_sheet

    # 配当データ
    yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()
    growth_years = 0
    for i in range(1, len(yearly_div)):
        if yearly_div.iloc[i] > yearly_div.iloc[i-1]:
            growth_years += 1
    
    div_cagr = cagr(yearly_div)
    payout = (info.get("payoutRatio") or 0) * 100

    # 利益・ROE
    net_income_series = income_stmt.loc["Net Income"] if "Net Income" in income_stmt.index else pd.Series()
    eps_cagr = cagr(net_income_series)
    roe = (info.get("returnOnEquity") or 0) * 100
    
    # 維持可能性
    retained = balance.loc["Retained Earnings"][0] if "Retained Earnings" in balance.index else 0
    annual_div = yearly_div.iloc[0] if len(yearly_div) > 0 else 1
    sustain = retained / annual_div if annual_div > 0 else 0

    # 売上・利益率
    revenue_series = income_stmt.loc["Total Revenue"] if "Total Revenue" in income_stmt.index else pd.Series()
    revenue_cagr = cagr(revenue_series)
    
    if "Operating Income" in income_stmt.index and "Total Revenue" in income_stmt.index:
        op_income = income_stmt.loc["Operating Income"]
        total_revenue = income_stmt.loc["Total Revenue"]
        op_margin = (op_income.iloc[0] / total_revenue.iloc[0]) * 100 if total_revenue.iloc[0] != 0 else None
    else:
        op_margin = None

    # バリュエーション (CN-PER)
    market_cap = info.get("marketCap", 0)
    cash = balance.loc["Cash And Cash Equivalents"][0] if "Cash And Cash Equivalents" in balance.index else 0
    net_income = net_income_series.iloc[0] if len(net_income_series) > 0 else 1
    cn_per = (market_cap - cash) / net_income if net_income != 0 else 999

    # 利回り
    dividend_yield_raw = info.get("dividendYield")
    if dividend_yield_raw is not None and dividend_yield_raw > 0:
        dividend_yield = dividend_yield_raw * 100 if dividend_yield_raw < 1 else dividend_yield_raw
    else:
        if len(yearly_div) > 0 and info.get("regularMarketPrice"):
            dividend_yield = (yearly_div.iloc[0] / info["regularMarketPrice"]) * 100
        else:
            dividend_yield = 0

    raw_values = {
        "連続増配年数": growth_years,
        "5年配当CAGR": div_cagr,
        "予想配当性向": 60 - payout,
        "純利益5年CAGR": eps_cagr,
        "ROE": roe,
        "配当維持可能年数": sustain,
        "売上5年CAGR": revenue_cagr,
        "営業利益率": op_margin,
        "CN-PER": 30 - cn_per,
        "配当利回り": dividend_yield
    }
    
    # スコアリング
    scores = {
        "連続増配年数": score(growth_years, [(10, 10), (8, 5), (6, 3)]),
        "5年配当CAGR": score(div_cagr, [(10, 15), (8, 10), (6, 5)]),
        "予想配当性向": score(60 - payout, [(10, 20), (8, 10), (6, 0)]),
        "純利益5年CAGR": score(eps_cagr, [(10, 15), (8, 10), (6, 5)]),
        "ROE": score(roe, [(10, 20), (8, 15), (6, 10)]),
        "配当維持可能年数": score(sustain, [(10, 10), (8, 5), (6, 3)]),
        "売上5年CAGR": score(revenue_cagr, [(10, 10), (8, 5), (6, 3)]),
        "営業利益率": score(op_margin, [(10, 20), (8, 15), (6, 10)]) if op_margin is not None else 2,
        "CN-PER": score(30 - cn_per, [(10, 15), (8, 5), (6, 0)]),
        "配当利回り": score(dividend_yield, [
            (10, 4.5), (9, 4.25), (8, 4.0), (7, 3.75), (6, 3.5),
            (5, 3.25), (4, 3.0), (3, 2.75), (2, 2.5)
        ])
    }
    
    total = sum([v for v in scores.values() if isinstance(v, int)])
    return total, scores, raw_values

# ------------------------
# キャッシュ更新
# ------------------------
def fetch_and_cache(ticker, conn):
    try:
        total, scores, _ = calculate_score_raw(ticker)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO stocks (ticker, total_score, score_json, last_update)
                     VALUES (?, ?, ?, CURRENT_TIMESTAMP)''',
                  (ticker, total, json.dumps(scores)))
        conn.commit()
        return {"ticker": ticker, "total_score": total, **scores}
    except:
        return None

# ------------------------
# メイン処理
# ------------------------
conn = init_db()
st.write("取得中です。初回は数分かかる場合があります…")

try:
    all_tickers = get_all_tickers()
except:
    st.error("JPXデータ取得に失敗しました")
    all_tickers = set()

cur = conn.cursor()
cur.execute("SELECT ticker FROM stocks")
old_tickers = set([row[0] for row in cur.fetchall()])

add_tickers = list(all_tickers - old_tickers)[:20]  # デモ用に数を制限（必要に応じて解除）
st.write(f"新規銘柄数: {len(add_tickers)}")

if add_tickers:
    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(lambda t: fetch_and_cache(t, conn), add_tickers))

# ランキング表示
st.header("📊 ランキング分析")
cur.execute("SELECT ticker, total_score, last_update FROM stocks ORDER BY total_score DESC")
rows = cur.fetchall()
if rows:
    df_display = pd.DataFrame(rows, columns=["ticker", "総合点", "最終更新"])
    st.dataframe(df_display, use_container_width=True)

# 個別銘柄分析
st.header("🔎 個別銘柄分析")
ticker_input = st.text_input("銘柄コード（例: 9432）")
if ticker_input:
    ticker = ticker_input if ticker_input.endswith(".T") else ticker_input + ".T"
    
    try:
        total, scores, raw_values = calculate_score_raw(ticker)
        
        st.metric("総合スコア", f"{total} / 100")
        
        col1, col2 = st.columns(2)
        with col1:
            st.write("### 指標別スコア")
            st.dataframe(pd.DataFrame(scores.items(), columns=["指標", "点数"]), use_container_width=True)
        with col2:
            st.write("### 実際の数値")
            st.dataframe(pd.DataFrame(raw_values.items(), columns=["指標", "値"]), use_container_width=True)
        
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
        st.error(f"データの取得に失敗しました: {e}")
