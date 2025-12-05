import streamlit as st
import pandas as pd
from datetime import datetime
import google.generativeai as genai
import requests
import json

# ---------------- PAGE CONFIG ----------------
st.set_page_config(
    page_title="Dairy Waste Orchestrator",
    page_icon="🧀",
    layout="wide",
)

# ---------------- GEMINI & N8N SETUP ----------------
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

gemini_model = genai.GenerativeModel(
    "gemini-1.5-pro-latest",
    generation_config={"response_mime_type": "application/json"},
)

N8N_WEBHOOK_URL = st.secrets["N8N_WEBHOOK_URL"]


# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.title("Score Guide")
    st.markdown("**75–100:** Excellent stock ✅")
    st.markdown("**50–74:** Needs attention ⚠️")
    st.markdown("**0–49:** High risk ❌")
    st.markdown("---")
    st.markdown("**Why this tool?**")
    st.markdown("- Detect near-expiry dairy items")
    st.markdown("- Highlight quantity / pricing issues")
    st.markdown("- Auto-generate risk report")
    st.markdown("- Auto email alerts using n8n")


# ---------------- SESSION STATE ----------------
if "structured_json" not in st.session_state:
    st.session_state.structured_json = None
if "extracted_text" not in st.session_state:
    st.session_state.extracted_text = ""
if "question" not in st.session_state:
    st.session_state.question = ""
if "n8n_result" not in st.session_state:
    st.session_state.n8n_result = None


# ---------------- MAIN UI ----------------
st.title("Dairy Waste Orchestrator")

col_center, col_right = st.columns([2, 1])

uploaded_file = None
df = None
risky_df = None
risk_score = 0


# ======================================================
# INVENTORY + GEMINI + AUTO EMAIL
# ======================================================
with col_center:
    tab_text, tab_structured = st.tabs(
        ["🥛 Inventory & Alerts", "📊 Gemini Structured JSON"]
    )

    # ---------- TAB 1: UPLOAD & ANALYSE ----------
    with tab_text:
        st.subheader("1. Upload Inventory & Analyse Expiry")

        uploaded_file = st.file_uploader(
            "Upload inventory CSV",
            type="csv",
            help="Required columns: product, quantity, expiry_date, price",
        )

        if uploaded_file:
            df = pd.read_csv(uploaded_file)
            df["expiry_date"] = pd.to_datetime(df["expiry_date"])
            df["days_left"] = (df["expiry_date"] - datetime.now()).dt.days

            risky_df = df[df["days_left"] <= 3].copy()

            total_items = len(df)
            risky_items = len(risky_df)

            if total_items > 0:
                risk_ratio = risky_items / total_items
                risk_score = int(max(0, 100 - risk_ratio * 100))
            else:
                risk_score = 100

            # TEXT PREVIEW
            preview_lines = [
                f"{row.product} - Qty: {row.quantity} - Expiry: {row.expiry_date.date()} - Days left: {row.days_left} - Price: ₹{row.price}"
                for row in df.itertuples()
            ]

            st.text_area(
                "Inventory Text Preview",
                "\n".join(preview_lines),
                height=220,
            )

            st.subheader("Products expiring in 3 days or less")
            if not risky_df.empty:
                st.dataframe(
                    risky_df[
                        ["product", "quantity", "expiry_date", "days_left", "price"]
                    ],
                    use_container_width=True,
                )
            else:
                st.success("No items expiring in 3 days or less 😄")

            st.download_button(
                "📥 Download Risk Report (CSV)",
                risky_df.to_csv(index=False),
                file_name="dairy_expiry_risk_report.csv",
                mime="text/csv",
            )
        else:
            st.info("Upload an inventory CSV to start analysis.")

        # ---------- AUTO EMAIL TRIGGER ----------
        if risky_df is not None and not risky_df.empty:

            def send_auto_email_alert(risky_df, risk_score):
                payload = {
                    "risk_score": int(risk_score),
                    "risky_items": risky_df.to_dict(orient="records"),
                }

                try:
                    response = requests.post(N8N_WEBHOOK_URL, json=payload)
                    return response.text
                except Exception as e:
                    return f"Error sending email: {e}"

            auto_result = send_auto_email_alert(risky_df, risk_score)
            st.success("🚨 Auto email alert triggered via n8n!")
            st.text_area("n8n Auto Email Response", auto_result, height=150)

        # ---------- GEMINI EXTRACTION ----------
        st.markdown("---")
        st.subheader("2. Ask a Question (Gemini Extraction)")

        question = st.text_input(
            "Ask anything about this inventory (e.g., 'Which items are highest risk?')"
        )
        st.session_state.question = question

        if st.button(
            "Run Gemini Structured Extraction",
            disabled=not (uploaded_file and question),
        ):
            if not uploaded_file:
                st.error("Upload an inventory CSV first.")
            elif not question:
                st.error("Enter a question.")
            else:
                extracted_text = "\n".join(
                    f"{row.product} | qty={row.quantity} | expiry={row.expiry_date.date()} | days_left={row.days_left} | price={row.price}"
                    for row in df.itertuples()
                )
                st.session_state.extracted_text = extracted_text

                json_schema = {
                    "title": "Inventory QA extraction",
                    "type": "object",
                    "properties": {
                        "key_fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field_name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "reason": {"type": "string"},
                                },
                            },
                        },
                        "summary": {"type": "string"},
                        "risk_score": {"type": "integer"},
                        "recommended_action": {"type": "string"},
                    },
                    "required": ["key_fields", "summary", "risk_score"],
                }

                prompt = f"""
You are an expert inventory auditor.
Extract structured insights from this dairy inventory:

Inventory:
{extracted_text}

Question:
{question}

Follow the JSON schema strictly.
"""

                response = gemini_model.generate_content(
                    [prompt], generation_config={"response_schema": json_schema}
                )

                st.session_state.structured_json = response.text

    # ---------- TAB 2: STRUCTURED JSON ----------
    with tab_structured:
        st.subheader("Gemini Structured JSON Output")
        if st.session_state.structured_json:
            st.json(json.loads(st.session_state.structured_json))
        else:
            st.info("Run Gemini extraction to view structured output.")


# ======================================================
# RIGHT COLUMN – EXPIRY SNAPSHOT
# ======================================================
with col_right:
    st.subheader("Expiry Snapshot")

    st.metric("Risk Score", risk_score)

    if risk_score <= 49:
        st.error("High risk: Many items close to expiry")
    elif risk_score <= 74:
        st.warning("Needs attention")
    else:
        st.success("Excellent stock")
