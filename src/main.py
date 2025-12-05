import streamlit as st
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import requests

# ---------------- CONFIG ----------------
st.set_page_config(
    page_title="Dairy Waste Orchestrator",
    page_icon="🧀",
    layout="wide",
)

# --------- GOOGLE GEMINI SETUP ----------
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# Use a valid model for generate_content
gemini_model = genai.GenerativeModel("gemini-1.5-flash")

# ---------- N8N WEBHOOK ----------
N8N_WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL", None)

# ---------- UI HEADER ----------
st.title("🧀 Dairy Waste Orchestrator – Smart Expiry Alerts")

st.write("Upload your dairy inventory CSV to automatically detect high-risk expiry stock.")

# ---------- FILE UPLOAD ----------
uploaded_file = st.file_uploader(
    "Upload Inventory CSV",
    type=["csv"],
    help="Must contain columns: product, quantity, expiry_date, price"
)

if uploaded_file:
    df = pd.read_csv(uploaded_file)

    # Convert date column if needed
    df["expiry_date"] = pd.to_datetime(df["expiry_date"])
    df["days_remaining"] = (df["expiry_date"] - datetime.now()).dt.days

    # Identify risk products
    risky_df = df[df["days_remaining"] <= 3].copy()

    # Risk Score
    total_items = len(df)
    risky_items = len(risky_df)
    risk_score = int(max(0, 100 - (risky_items / total_items) * 100)) if total_items else 100

    # Display risk level
    st.subheader("📊 Risk Score")
    st.metric("Calculated Score", f"{risk_score}")

    st.subheader("🚨 Products expiring in ≤ 3 days")
    if risky_df.empty:
        st.success("🎉 No high-risk dairy products!")
    else:
        st.dataframe(risky_df, use_container_width=True)

        st.download_button(
            label="📥 Download High-Risk Report",
            data=risky_df.to_csv(index=False),
            file_name="high_risk_dairy_items.csv",
            mime="text/csv"
        )

    st.markdown("---")
    st.subheader("🤖 AI Suggested Action (Gemini)")
    
    if st.button("Generate AI Recommendation"):
        inventory_text = "\n".join(
            f"{row.product}, qty {row.quantity}, price ₹{row.price}, expires in {row.days_remaining} days"
            for row in df.itertuples()
        )

        prompt = f"""
        You are an inventory waste reduction expert.
        Here is the stock list:\n{inventory_text}\n
        Give a clear recommendation on which items to discount or sell fast.
        Return 3–6 bullet points.
        """

        try:
            response = gemini_model.generate_content(prompt)
            st.write(response.text)

        except Exception as e:
            st.error(f"Gemini Error: {str(e)}")

    st.markdown("---")
    st.subheader("📧 Auto Email Alert (via n8n)")

    if N8N_WEBHOOK_URL is None:
        st.warning("⚠️ Add N8N_WEBHOOK_URL to Streamlit Secrets to enable email automation.")
    else:
        if st.button("Send Email Alert Now", type="primary", disabled=risky_df.empty):
            payload = risky_df.to_dict(orient="records")
            try:
                res = requests.post(N8N_WEBHOOK_URL, json={"items": payload})
                if res.status_code == 200:
                    st.success("📩 Email Alert Sent Successfully!")
                else:
                    st.error(f"Webhook Error: {res.text}")
            except Exception as e:
                st.error(f"Error sending webhook: {str(e)}")

else:
    st.info("📌 Upload a CSV to begin risk analysis.")

# Sidebar Info
with st.sidebar:
    st.header("Risk Levels")
    st.write("🟢 75–100 → Excellent")
    st.write("🟡 50–74 → Attention Required")
    st.write("🔴 0–49 → High Risk!")
    st.markdown("---")
    st.caption("Powered by Google Gemini + n8n automation")
