import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os

st.set_page_config(page_title="Dividend Growth Pro", layout="wide")
st.title("🇯🇵 Dividend Growth Pro")

DB_FILE = "stock_db.csv"

# =============================
# CAGR計算
# =============================
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

# =============================
# 単銘柄データ取得
# =============================
def fetch_stock(code):
    try:
        stock = yf.Ticker(code + ".T")
        info = stock.info
        dividends = stock.dividends
        income = stock.income_stmt
        balance = stock.balance_sheet

        yearly_div = dividends.resample("YE").sum() if not dividends.empty else pd.Series()
        growth_years = sum(yearly_div.iloc[i] > yearly_div.iloc[i-1] for i in range(1,len(yearly_div)))
        div_cagr = cagr(yearly_div)
        payout = (info.get("payoutRatio") or 0) * 100

        net_income = income.loc["Net Income"] if "Net Income" in income.index else pd.Series()
        eps_cagr = cagr(net_income)

        roe = (info.get("returnOnEquity") or 0) * 100

        retained = balance.loc["Retained Earnings"][0] if "Retained Earnings" in balance.index else 0
        annual_div = yearly_div.iloc[0] if len(yearly_div)>0 else 1
        sustain = retained/annual_div if annual_div>0 else 0

        revenue = income.loc["Total Revenue"] if "Total Revenue" in income.index else pd.Series()
        revenue_cagr = cagr(revenue)

        op_margin = (info.get("operatingMargins") or 0) * 100

        market_cap = info.get("marketCap",0)
        cash = balance.loc["Cash And Cash Equivalents"][0] if "Cash And Cash Equivalents" in balance.index else 0
        ni = net_income.iloc[0] if len(net_income)>0 else 1
        cn_per = (market_cap - cash)/ni if ni!=0 else 999

        dividend_yield = (info.get("dividendYield") or 0) * 100

        return {
            "code":code,
            "name":info.get("shortName"),
            "sector":info.get("sector"),
            "market_cap":market_cap,
            "dividend_years":growth_years,
            "dividend_cagr_5y":div_cagr,
            "payout_ratio":payout,
            "eps_cagr_5y":eps_cagr,
            "roe_5y":roe,
            "dividend_sustain":sustain,
            "revenue_cagr_5y":revenue_cagr,
            "op_margin_5y":op_margin,
            "cn_per":cn_per,
            "dividend_yield":dividend_yield
        }
    except:
        return None

# =============================
# DB構築（初回のみ）
# =============================
def build_db(codes):
    data=[]
    for code in codes:
        d=fetch_stock(code)
        if d:
            data.append(d)
    df=pd.DataFrame(data)
    df.to_csv(DB_FILE,index=False)
    return df

# =============================
# スコアリング
# =============================
def normalize(val, target):
    return min(val/target,1)*10 if val>0 else 0

def calculate_scores(df):
    df["s1"]=df["dividend_years"].apply(lambda x:normalize(x,15))
    df["s2"]=df["dividend_cagr_5y"].apply(lambda x:normalize(x,10))
    df["s3"]=df["payout_ratio"].apply(lambda x:10 if 30<=x<=60 else 6)
    df["s4"]=df["eps_cagr_5y"].apply(lambda x:normalize(x,12))
    df["s5"]=df["roe_5y"].apply(lambda x:normalize(x,18))
    df["s6"]=df["dividend_sustain"].apply(lambda x:normalize(x,10))
    df["s7"]=df["revenue_cagr_5y"].apply(lambda x:normalize(x,8))
    df["s8"]=df["op_margin_5y"].apply(lambda x:normalize(x,15))
    df["s9"]=df["cn_per"].apply(lambda x:normalize(15/x if x>0 else 0,1))
    df["s10"]=df["dividend_yield"].apply(lambda x:normalize(x,4))

    df["total_raw"]=df[[f"s{i}" for i in range(1,11)]].sum(axis=1)

    # 業種補正
    sector_mean=df.groupby("sector")["total_raw"].mean()
    df["total_score"]=df.apply(lambda x:x["total_raw"]-sector_mean.get(x["sector"],0)+50,axis=1)

    return df

# =============================
# DBロード
# =============================
if os.path.exists(DB_FILE):
    df=pd.read_csv(DB_FILE)
else:
    st.warning("初回起動：データ構築中（時間かかります）")
    codes=["7203","9432","8306","2914"]  # ←ここに銘柄リスト
    df=build_db(codes)

df=calculate_scores(df)

# =============================
# UI
# =============================
st.sidebar.header("フィルター")
min_yield=st.sidebar.slider("最低利回り",0.0,8.0,0.0)
df=df[df["dividend_yield"]>=min_yield]

ranking=df.sort_values("total_score",ascending=False)

st.subheader("🏆 ランキング")
st.dataframe(ranking[["code","name","sector","total_score","dividend_yield"]].head(50),use_container_width=True)

selected=st.selectbox("銘柄選択",ranking["code"])
row=ranking[ranking["code"]==selected].iloc[0]

categories=[f"s{i}" for i in range(1,11)]
values=[row[c] for c in categories]

fig=go.Figure()
fig.add_trace(go.Scatterpolar(r=values+ [values[0]],theta=categories+ [categories[0]],fill="toself"))
fig.update_layout(polar=dict(radialaxis=dict(range=[0,10])))
st.plotly_chart(fig,use_container_width=True)
