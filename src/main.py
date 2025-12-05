import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import google.generativeai as genai

# ========================
# Gemini Configuration
# ========================
genai.configure(api_key="YOUR_GEMINI_API_KEY")
gemini_model = genai.GenerativeModel("models/text-bison-001")  # Compatible with v1beta

# ========================
# Database Setup
# ========================
conn = sqlite3.connect("inventory.db", check_same_thread=False)
c = conn.cursor()
c.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product TEXT,
        quantity INTEGER,
        expiry_date TEXT,
        price REAL
    )
""")
conn.commit()

# ========================
# Streamlit UI
# ========================
st.set_page_config(page_title="Dairy Inventory Management", layout="wide")
st.title("🥛 Dairy Inventory & Expiry Tracking System")

menu = st.sidebar.radio("Menu", ["Add Product", "View Inventory"])

# ========================
# Add Product Page
# ========================
if menu == "Add Product":
    st.subheader("➕ Add New Product")
    product = st.text_input("Product Name")
    quantity = st.number_input("Quantity", min_value=1)
    expiry_date = st.date_input("Expiry Date")
    price = st.number_input("Price ₹", min_value=0.0)

    if st.button("📌 Save Product"):
        c.execute(
            "INSERT INTO inventory (product, quantity, expiry_date, price) VALUES (?, ?, ?, ?)",
            (product, quantity, expiry_date.strftime("%Y-%m-%d"), price),
        )
        conn.commit()
        st.success("Product saved successfully!")

# ========================
# View Inventory Page
# ========================
elif menu == "View Inventory":
    st.subheader("📋 Current Inventory Status")
    df = pd.read_sql_query("SELECT * FROM inventory", conn)

    if not df.empty:
        df["expiry_date"] = pd.to_datetime(df["expiry_date"])
        df["days_remaining"] = (df["expiry_date"] - datetime.now()).dt.days

        st.dataframe(df)

        # 🔴 Highlight products expiring soon
        st.warning("⚠ Items expiring in next 3 days:")
        expiring = df[df["days_remaining"] <= 3]

        if not expiring.empty:
            st.dataframe(expiring)

        # ========================
        # AI Recommendation (Gemini)
        # ========================
        st.subheader("🤖 AI Suggested Actions")
        if st.button("Generate AI Recommendation"):

            inventory_text = "\n".join(
                f"{row.product}, qty {row.quantity}, ₹{row.price}, expires in {row.days_remaining} days"
                for row in df.itertuples()
            )

            prompt = f"""
            You are an expert in retail waste reduction. 
            Here is current dairy stock:\n{inventory_text}

            Provide 5 actionable steps to prevent inventory loss:
            - Which items to discount?
            - Which items to bundle/sell fast?
            - Any suggestions for near expiry products?
            """

            try:
                response = gemini_model.predict(prompt=prompt)
                st.write(response.text)
            except Exception as e:
                st.error(f"Gemini Error: {e}")

    else:
        st.info("No items found. Add items from the sidebar.")

