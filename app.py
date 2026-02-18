import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="Dividend Growth 100", layout="wide")

st.title("🇯🇵 Dividend Growth 100")
st.write("増配企業を100点満点で評価します")

ticker_input = st.text_input("銘柄コード（例: 9432）")

def cagr(series):
    try:
        if len(series) < 5:
            return 0
        start = series.iloc[-5]
        end = series.iloc[0]
        if start <= 0:
            return 0
        return ((end/start)**(1/5)-1)*100
    except:
        return 0

def score(value, thresholds):
    for s, t in thresholds:
        if value >= t:
            return s
    return 2

if ticker_input:
    ticker = ticker_input + ".T"

    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        dividends = stock.dividends
        income_stmt = stock.income_stmt
        balance = stock.balance_sheet

        # ---------------- 配当 ----------------
        yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()

        growth_years = 0
        for i in range(1, len(yearly_div)):
            if yearly_div.iloc[i] > yearly_div.iloc[i-1]:
                growth_years += 1

        div_cagr = cagr(yearly_div)

        payout = (info.get("payoutRatio") or 0) * 100

        # ---------------- EPS代替（純利益） ----------------
        net_income_series = income_stmt.loc["Net Income"] if "Net Income" in income_stmt.index else pd.Series()
        eps_cagr = cagr(net_income_series)

        roe = (info.get("returnOnEquity") or 0) * 100

        retained = balance.loc["Retained Earnings"][0] if "Retained Earnings" in balance.index else 0
        annual_div = yearly_div.iloc[0] if len(yearly_div)>0 else 1
        sustain = retained/annual_div if annual_div>0 else 0

        revenue_series = income_stmt.loc["Total Revenue"] if "Total Revenue" in income_stmt.index else pd.Series()
        revenue_cagr = cagr(revenue_series)

        op_margin = (info.get("operatingMargins") or 0) * 100

        market_cap = info.get("marketCap",0)
        cash = balance.loc["Cash And Cash Equivalents"][0] if "Cash And Cash Equivalents" in balance.index else 0
        net_income = net_income_series.iloc[0] if len(net_income_series)>0 else 1
        cn_per = (market_cap - cash) / net_income if net_income != 0 else 999

        dividend_yield = (info.get("dividendYield") or 0) * 100

        scores = {
            "連続増配年数": score(growth_years, [(10,10),(8,5),(6,3)]),
            "5年配当CAGR": score(div_cagr, [(10,15),(8,10),(6,5)]),
            "予想配当性向": score(60-payout, [(10,20),(8,10),(6,0)]),
            "純利益5年CAGR": score(eps_cagr, [(10,15),(8,10),(6,5)]),
            "ROE": score(roe, [(10,20),(8,15),(6,10)]),
            "配当維持可能年数": score(sustain, [(10,10),(8,5),(6,3)]),
            "売上5年CAGR": score(revenue_cagr, [(10,10),(8,5),(6,3)]),
            "営業利益率": score(op_margin, [(10,20),(8,15),(6,10)]),
            "CN-PER": score(30-cn_per, [(10,15),(8,5),(6,0)]),
            "配当利回り": score(dividend_yield, [(10,5),(8,4),(6,3)])
        }

        total = sum(scores.values())

        st.metric("総合スコア", f"{total} / 100")
        st.dataframe(pd.DataFrame(scores.items(), columns=["指標","点数"]))

    except Exception as e:
        st.error("データ取得に失敗しました")
