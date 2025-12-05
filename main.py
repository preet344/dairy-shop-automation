import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import os

st.title("🧀 Dairy Shop Waste Prevention")

# Upload inventory
uploaded_file = st.file_uploader("Upload inventory CSV", type="csv")

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    
    # Calculate expiry days
    df['expiry_days'] = (pd.to_datetime(df['expiry_date']) - datetime.now()).dt.days
    
    # Flag 3-day expiry items
    risky_items = df[df['expiry_days'] <= 3]
    
    st.subheader("⚠️ Products Expiring in 3 Days")
    st.dataframe(risky_items[['product', 'quantity', 'expiry_days']])
    
    st.download_button(
        "📥 Download Report",
        risky_items.to_csv(index=False),
        "expiry_report.csv"
    )
