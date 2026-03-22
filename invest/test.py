import streamlit as st
import akshare as ak
import pandas as pd

st.title("智投分析助手 - 中国股市 & 宏观看板")

# 1. 获取美元、黄金实时简报
st.sidebar.header("全球宏观动态")
gold_price = ak.fx_spot_quote() # 示例接口
st.sidebar.metric("黄金现货", "2000 USD", "+0.5%")

# 2. 获取 A 股特定公司基本面
code = st.text_input("输入股票代码 (如 600519):", "600519")
if st.button("分析公司"):
    # 获取财务摘要
    df_finance = ak.stock_financial_analysis_indicator(symbol=code)
    st.write("最新财务指标：", df_finance.iloc[0])
    
    # 获取实时价格
    df_spot = ak.stock_zh_a_spot_em()
    stock_info = df_spot[df_spot['代码'] == code]
    st.metric(label="当前价格", value=stock_info['最新价'].values[0])
