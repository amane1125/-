import requests
import pandas as pd
from io import BytesIO
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
import os

# =========================
# ① JPXから全銘柄取得
# =========================
def get_all_tickers():
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.csv"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()  # HTTPエラーなら止める

    df = pd.read_csv(BytesIO(response.content), encoding="shift_jis")

    df = df[df["市場・商品区分"].str.contains("内国株式", na=False)]

    tickers = df["コード"].astype(str) + ".T"

    return set(tickers)
# =========================
# ② 個別銘柄取得
# =========================
def fetch_stock(ticker):
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        return {
            "ticker": ticker,
            "PER": info.get("trailingPE"),
            "配当利回り": info.get("dividendYield"),
            "時価総額": info.get("marketCap")
        }

    except Exception:
        return None


# =========================
# ③ 既存データ読み込み
# =========================
if os.path.exists("stock_data.csv"):
    old_df = pd.read_csv("stock_data.csv")
    old_tickers = set(old_df["ticker"])
else:
    old_df = pd.DataFrame()
    old_tickers = set()


# =========================
# ④ 差分抽出
# =========================
new_tickers = get_all_tickers()
add_tickers = new_tickers - old_tickers

print("新規銘柄数:", len(add_tickers))


# =========================
# ⑤ 並列取得
# =========================
with ThreadPoolExecutor(max_workers=10) as executor:
    new_data = list(executor.map(fetch_stock, add_tickers))

new_df = pd.DataFrame([d for d in new_data if d is not None])


# =========================
# ⑥ 結合保存
# =========================
final_df = pd.concat([old_df, new_df])
final_df.to_csv("stock_data.csv", index=False)

print("更新完了")
