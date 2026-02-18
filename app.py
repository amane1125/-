import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

st.set_page_config(page_title="Dividend Growth Analyzer", layout="wide")

st.title("🇯🇵 Dividend Growth 100点モデル")
st.write("持続的に増配できる企業を評価します")

ticker_input = st.text_input("銘柄コードを入力（例: 9432）")

def cagr(start, end, years):
    if start <= 0 or years == 0:
        return 0
    return ((end / start) ** (1/years) - 1) * 100

def score_scale(value, thresholds):
    for score, limit in thresholds:
        if value >= limit:
            return score
    return 2

if ticker_input:
    ticker = ticker_input + ".T"
    stock = yf.Ticker(ticker)

    try:
        info = stock.info
        dividends = stock.dividends
        financials = stock.financials
        earnings = stock.earnings
        balance = stock.balance_sheet
        cashflow = stock.cashflow

        # -------- 安定性 --------

        yearly_div = dividends.resample("Y").sum()
        growth_years = 0
        for i in range(1, len(yearly_div)):
            if yearly_div.iloc[i] > yearly_div.iloc[i-1]:
                growth_years += 1

        div_cagr = 0
        if len(yearly_div) >= 5:
            div_cagr = cagr(yearly_div.iloc[-5], yearly_div.iloc[-1], 5)

        # -------- 増配余力 --------

        payout = (info.get("payoutRatio") or 0) * 100

        eps_cagr = 0
        if len(earnings) >= 5:
            eps_cagr = cagr(
                earnings["Earnings"].iloc[0],
                earnings["Earnings"].iloc[-1],
                5
            )

        roe_avg = (info.get("returnOnEquity") or 0) * 100

        retained_earnings = balance.loc["Retained Earnings"][0] if "Retained Earnings" in balance.index else 0
        annual_div = yearly_div.iloc[-1] if len(yearly_div) > 0 else 1
        sustain_years = retained_earnings / annual_div if annual_div > 0 else 0

        # -------- 収益力 --------

        revenue_cagr = 0
        if len(financials.columns) >= 5:
            revenue_cagr = cagr(
                financials.loc["Total Revenue"][4],
                financials.loc["Total Revenue"][0],
                5
            )

        op_margin = (info.get("operatingMargins") or 0) * 100

        # -------- 割安度 --------

        market_cap = info.get("marketCap", 0)
        cash = balance.loc["Cash"][0] if "Cash" in balance.index else 0
        net_income = financials.loc["Net Income"][0] if "Net Income" in financials.index else 1
        cn_per = (market_cap - cash) / net_income if net_income != 0 else 999

        dividend_yield = (info.get("dividendYield") or 0) * 100

        scores = {
            "連続増配年数": score_scale(growth_years, [(10,10),(8,5),(6,3),(4,1)]),
            "5年配当CAGR": score_scale(div_cagr, [(10,15),(8,10),(6,5)]),
            "予想配当性向": score_scale(60-payout, [(10,20),(8,10),(6,0)]),
            "EPS5年CAGR": score_scale(eps_cagr, [(10,15),(8,10),(6,5)]),
            "ROE": score_scale(roe_avg, [(10,20),(8,15),(6,10)]),
            "配当維持可能年数": score_scale(sustain_years, [(10,10),(8,5),(6,3)]),
            "売上5年CAGR": score_scale(revenue_cagr, [(10,10),(8,5),(6,3)]),
            "営業利益率": score_scale(op_margin, [(10,20),(8,15),(6,10)]),
            "CN-PER": score_scale(30-cn_per, [(10,15),(8,5),(6,0)]),
            "配当利回り": score_scale(dividend_yield, [(10,5),(8,4),(6,3)])
        }

        total_score = sum(scores.values())

        st.metric("総合スコア", f"{total_score} / 100")

        df = pd.DataFrame(scores.items(), columns=["指標", "点数"])
        st.dataframe(df, use_container_width=True)

    except Exception as e:
        st.error("データ取得に失敗しました")
