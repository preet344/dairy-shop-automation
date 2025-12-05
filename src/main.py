# main.py
import streamlit as st
import pandas as pd
from datetime import datetime
import requests
import json
import traceback

# Gemini import - works on Streamlit Cloud if the package is installed via requirements.txt
try:
    from google import generativeai as genai
    GEMINI_AVAILABLE = True
except Exception:
    GEMINI_AVAILABLE = False

# ---------------- PAGE CONFIG ----------------
st.set_page_config(page_title="Dairy Waste Orchestrator (Option 2)",
                   page_icon="🧀", layout="wide")

st.title("Dairy Waste Orchestrator — Option 2 (days_remaining)")

# ---------------- SECRETS / CONFIG ----------------
N8N_WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL", None)
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY", None)

SMTP_HOST = st.secrets.get("SMTP_HOST", None)
SMTP_PORT = int(st.secrets.get("SMTP_PORT", 0)) if st.secrets.get("SMTP_PORT") else None
SMTP_USER = st.secrets.get("SMTP_USER", None)
SMTP_PASSWORD = st.secrets.get("SMTP_PASSWORD", None)
ALERT_RECEIVER = st.secrets.get("ALERT_RECEIVER", None)
ALERT_SENDER = st.secrets.get("ALERT_SENDER", None)

# Configure Gemini if available and key present
if GEMINI_AVAILABLE and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # prefer model "gemini-1.5-pro"
        try:
            gemini_model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                generation_config={"response_mime_type": "application/json"},
            )
        except Exception:
            # fallback to a generic constructor if API differs
            gemini_model = genai.GenerativeModel("gemini-1.5-pro")
        GEMINI_READY = True
    except Exception:
        GEMINI_READY = False
else:
    GEMINI_READY = False

# ---------------- SESSION STATE ----------------
if "structured_json" not in st.session_state:
    st.session_state.structured_json = None
if "extracted_text" not in st.session_state:
    st.session_state.extracted_text = ""
if "last_auto_alert" not in st.session_state:
    st.session_state.last_auto_alert = None

# ---------------- HELPERS ----------------
def normalize_df_columns(df):
    """Lowercase column names for robust access."""
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def find_risky_rows(df):
    """Return rows where days_remaining <= 3 (safe with missing column)."""
    if "days_remaining" not in df.columns:
        return pd.DataFrame()
    # ensure numeric
    df["days_remaining"] = pd.to_numeric(df["days_remaining"], errors="coerce").fillna(9999).astype(int)
    risky = df[df["days_remaining"] <= 3].copy()
    return risky

def send_to_n8n(payload: dict):
    """Send JSON payload to n8n webhook if configured. Returns tuple(success, response_text)."""
    if not N8N_WEBHOOK_URL:
        return False, "N8N_WEBHOOK_URL not configured in secrets."
    try:
        resp = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
        return (resp.status_code >= 200 and resp.status_code < 300), f"{resp.status_code} {resp.text}"
    except Exception as e:
        return False, f"Request error: {e}"

def send_email_smtp(subject: str, body: str):
    """Optional direct SMTP send if SMTP credentials exist. Returns tuple(success, message)."""
    if not (SMTP_HOST and SMTP_PORT and SMTP_USER and SMTP_PASSWORD and ALERT_RECEIVER and ALERT_SENDER):
        return False, "SMTP credentials or addresses not fully configured in secrets."
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = ALERT_SENDER
        msg["To"] = ALERT_RECEIVER
        msg.set_content(body)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            smtp.ehlo()
            if SMTP_PORT == 587:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        return True, "Email sent via SMTP."
    except Exception as e:
        return False, f"SMTP send error: {e}"

def build_alert_payload(risk_score, risky_df):
    """Construct the JSON payload to send to n8n / email body."""
    return {
        "alert_time": datetime.utcnow().isoformat() + "Z",
        "risk_score": int(risk_score),
        "risky_items": risky_df.to_dict(orient="records"),
    }

# ---------------- UI LAYOUT ----------------
col_left, col_right = st.columns([2, 1])

with col_left:
    st.header("Upload CSV (expects `product, quantity, days_remaining, price`)")
    uploaded_file = st.file_uploader("Upload inventory CSV", type=["csv"], accept_multiple_files=False)

    df = None
    risky_df = pd.DataFrame()
    risk_score = 100

    if uploaded_file:
        try:
            df = pd.read_csv(uploaded_file)
            df = normalize_df_columns(df)
            # If user used 'days_left' name, accept it too
            if "days_left" in df.columns and "days_remaining" not in df.columns:
                df = df.rename(columns={"days_left": "days_remaining"})
            # If product column missing, try to infer
            if "product" not in df.columns:
                st.warning("Uploaded CSV does not contain a 'product' column. Please include it.")
            # Find risky rows
            risky_df = find_risky_rows(df)
            total_items = len(df)
            risky_items = len(risky_df)
            if total_items > 0:
                risk_ratio = risky_items / total_items
                risk_score = int(max(0, 100 - risk_ratio * 100))
            else:
                risk_score = 100

            st.success(f"Loaded {total_items} items — Risk score: {risk_score}")
            # Show preview
            preview_cols = [c for c in ["product", "quantity", "days_remaining", "price"] if c in df.columns]
            if preview_cols:
                st.dataframe(df[preview_cols].head(200), use_container_width=True)
            else:
                st.dataframe(df.head(200), use_container_width=True)
        except Exception as e:
            st.error(f"Error reading CSV: {e}")
            st.exception(traceback.format_exc())

    # AUTO ALERT — trigger when risky items exist
    st.markdown("---")
    st.subheader("Automatic Alerting")

    if uploaded_file and (risky_df is not None) and (not risky_df.empty):
        st.info(f"{len(risky_df)} items with days_remaining <= 3 detected. Sending alert...")

        payload = build_alert_payload(risk_score, risky_df)
        ok, resp = send_to_n8n(payload)
        st.session_state.last_auto_alert = {"time": datetime.utcnow().isoformat() + "Z", "n8n_ok": ok, "n8n_resp": resp}

        if ok:
            st.success("Auto alert posted to n8n webhook.")
        else:
            st.error(f"n8n webhook failed: {resp}")

        # Optionally also send SMTP directly
        smtp_ok, smtp_msg = send_email_smtp(
            subject="⚠️ Dairy Inventory Alert — Items Near Expiry",
            body=f"Risk score: {risk_score}\n\nItems:\n{json.dumps(payload['risky_items'], indent=2, default=str)}"
        )

        if smtp_ok:
            st.success("Email sent via SMTP.")
        else:
            if "SMTP" in smtp_msg:
                st.info("SMTP not fully configured or failed; see message below.")
            st.warning(f"SMTP result: {smtp_msg}")

    else:
        st.info("No items expiring in 3 or fewer days detected (or no file uploaded).")

    # Manual "send alert" button (explicit)
    if uploaded_file and (risky_df is not None) and (not risky_df.empty):
        if st.button("📤 Resend Alert to n8n (manual)"):
            payload = build_alert_payload(risk_score, risky_df)
            ok, resp = send_to_n8n(payload)
            st.write("n8n response:", resp)
            st.session_state.last_auto_alert = {"time": datetime.utcnow().isoformat() + "Z", "n8n_ok": ok, "n8n_resp": resp}

    st.markdown("---")
    st.subheader("Gemini Structured Extraction (optional)")
    st.write("Gemini will only run if you provided GEMINI_API_KEY in secrets. The app will handle errors gracefully.")

    question = st.text_input("Ask a question about the inventory (e.g. Which items are highest risk?)")
    run_gemini = st.button("Run Gemini Extraction", disabled=(not uploaded_file or not question.strip()))

    if run_gemini:
        if not GEMINI_READY:
            st.error("Gemini is not configured or unavailable. Check GEMINI_API_KEY in secrets and package installation.")
        else:
            try:
                # Build simple text representation from rows
                extracted_text = "\n".join(
                    f"{row.get('product','<no-product>')} | qty={row.get('quantity','')} | days_remaining={row.get('days_remaining','')} | price={row.get('price','')}"
                    for _, row in df.iterrows()
                )
                st.session_state.extracted_text = extracted_text

                # JSON schema for expected output
                json_schema = {
                    "title": "Inventory QA extraction",
                    "type": "object",
                    "properties": {
                        "key_fields": {
                            "type": "array",
                            "description": "Top 5 key items or fields relevant to the question",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field_name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "reason": {"type": "string"},
                                }
                            }
                        },
                        "summary": {"type": "string"},
                        "risk_score": {"type": "integer"},
                        "recommended_action": {"type": "string"}
                    },
                    "required": ["key_fields", "summary"]
                }

                prompt = f"""You are an inventory analyst. The user question is:
Question: {question}

Inventory (one product per line):
{extracted_text}

Return JSON that conforms to this schema:
{json.dumps(json_schema)}
"""

                # Use the model; guard exceptions
                try:
                    # Some client versions require generating as a single-string request.
                    response = gemini_model.generate_content(prompt)
                    # Response may contain text property or json; attempt to parse
                    raw = getattr(response, "text", None) or str(response)
                    st.session_state.structured_json = raw
                    # Try to pretty show JSON
                    try:
                        st.json(json.loads(raw))
                    except Exception:
                        st.text_area("Gemini raw output", raw, height=240)
                except Exception as ge:
                    st.error(f"Gemini generate failed: {ge}")
                    st.exception(traceback.format_exc())
            except Exception as e:
                st.error(f"Failed to run Gemini extraction: {e}")
                st.exception(traceback.format_exc())

with col_right:
    st.header("Snapshot & Logs")
    st.metric("Calculated Risk Score", risk_score)

    if st.session_state.get("last_auto_alert"):
        la = st.session_state.last_auto_alert
        st.write("Last alert sent at (UTC):", la["time"])
        st.write("n8n ok:", la["n8n_ok"])
        st.write("n8n response:", la["n8n_resp"])

    st.markdown("---")
    st.write("Gemini status:")
    if GEMINI_AVAILABLE:
        st.write("gemini package available")
        st.write("configured:" , GEMINI_READY)
    else:
        st.write("gemini package not installed; Gemini features disabled")

    st.markdown("---")
    st.write("Helpful notes:")
    st.write("- Ensure CSV column `days_remaining` exists (integer). If you used `days_left`, this app will accept it.")
    st.write("- Configure N8N_WEBHOOK_URL in Streamlit secrets to receive automatic notifications.")
    st.write("- If you want the app itself to send emails, configure SMTP_* and ALERT_RECEIVER/SENDER in secrets.")
    st.write("- If using Streamlit Cloud, add `google-generativeai` and other libs to requirements.txt")

# ---------------- END OF APP ----------------
