# main.py
import streamlit as st
import pandas as pd
from datetime import datetime
import requests
import json
import traceback

# try imports
try:
    import google.generativeai as genai
except Exception:
    # some installs use "from google import generativeai as genai"
    try:
        from google import generativeai as genai  # type: ignore
    except Exception:
        genai = None  # type: ignore

st.set_page_config(page_title="Dairy Waste Orchestrator", page_icon="🧀", layout="wide")
st.title("Dairy Waste Orchestrator — Robust Gemini caller")

# -------------------------
# Secrets / config
# -------------------------
GEMINI_API_KEY = st.secrets.get("GEMINI_API_KEY")
N8N_WEBHOOK_URL = st.secrets.get("N8N_WEBHOOK_URL")

if genai is None:
    st.error(
        "google-generativeai package is not installed or could not be imported. "
        "Add `google-generativeai` to requirements.txt and redeploy."
    )
else:
    if not GEMINI_API_KEY:
        st.warning("GEMINI_API_KEY not found in Streamlit secrets — Gemini features disabled.")
    else:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
        except Exception as e:
            st.error(f"Failed to configure Gemini: {e}")

# choose a model compatible with your installed SDK
# you may change this if you upgraded the package
MODEL_NAME = "models/text-bison-001"

# create a model object if possible (some older SDKs don't use this constructor)
gemini_model = None
if genai is not None:
    try:
        gemini_model = genai.GenerativeModel(MODEL_NAME)
    except Exception:
        # ignore if constructor not supported by this version
        gemini_model = None

# -------------------------
# helper: robust model caller
# -------------------------
def extract_text_from_response(resp):
    """Try several common attributes to get text from a response object."""
    if resp is None:
        return None
    # direct string
    if isinstance(resp, str):
        return resp
    # dict-like
    try:
        if isinstance(resp, dict):
            # try common keys
            for k in ("text", "result", "output", "content"):
                if k in resp and isinstance(resp[k], str):
                    return resp[k]
            # sometimes the model returns nested
            if "candidates" in resp and isinstance(resp["candidates"], list) and resp["candidates"]:
                cand = resp["candidates"][0]
                if isinstance(cand, dict) and "output" in cand:
                    return cand["output"]
    except Exception:
        pass
    # object with attributes
    for attr in ("text", "result", "output", "content"):
        if hasattr(resp, attr):
            val = getattr(resp, attr)
            if isinstance(val, str):
                return val
            # some .result may itself be an object with .text
            if hasattr(val, "text"):
                t = getattr(val, "text")
                if isinstance(t, str):
                    return t
    # last resort: stringify
    try:
        s = str(resp)
        if s:
            return s
    except Exception:
        pass
    return None


def call_model(prompt: str, model_obj=None, model_name: str = MODEL_NAME, timeout_secs: int = 30):
    """
    Robust wrapper: try multiple methods across SDK versions:
      1) model.generate_content(prompt)  (some newer SDKs)
      2) model.generate_text(prompt=...) (some versions)
      3) model.predict(prompt=...)       (older v1beta)
      4) genai.generate_text(...)        (top-level helper)
    Returns (success:bool, text:str or error message)
    """
    if genai is None:
        return False, "google.generativeai package not available."

    # Try using a model object first if provided
    methods_tried = []
    # 1) generate_content (may accept single string or dict)
    try:
        if model_obj is not None and hasattr(model_obj, "generate_content"):
            methods_tried.append("model.generate_content")
            resp = model_obj.generate_content(prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"model.generate_content -> EXC: {e}")

    # 2) generate_text
    try:
        if model_obj is not None and hasattr(model_obj, "generate_text"):
            methods_tried.append("model.generate_text")
            # some variants expect keyword 'prompt' or positional
            try:
                resp = model_obj.generate_text(prompt=prompt)
            except TypeError:
                resp = model_obj.generate_text(prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"model.generate_text -> EXC: {e}")

    # 3) predict (older v1beta)
    try:
        if model_obj is not None and hasattr(model_obj, "predict"):
            methods_tried.append("model.predict")
            # older SDKs expect model.predict(prompt=...)
            try:
                resp = model_obj.predict(prompt=prompt)
            except TypeError:
                resp = model_obj.predict(prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"model.predict -> EXC: {e}")

    # 4) top-level helper functions (some SDKs provide genai.generate_text or genai.generate_content)
    try:
        if hasattr(genai, "generate_content"):
            methods_tried.append("genai.generate_content")
            resp = genai.generate_content(prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"genai.generate_content -> EXC: {e}")

    try:
        if hasattr(genai, "generate_text"):
            methods_tried.append("genai.generate_text")
            resp = genai.generate_text(prompt=prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"genai.generate_text -> EXC: {e}")

    # 5) last attempt: top-level 'predict' (rare)
    try:
        if hasattr(genai, "predict"):
            methods_tried.append("genai.predict")
            resp = genai.predict(prompt=prompt)
            text = extract_text_from_response(resp)
            if text:
                return True, text
    except Exception as e:
        methods_tried.append(f"genai.predict -> EXC: {e}")

    # nothing worked
    return False, "No supported model method succeeded. Methods tried: " + ", ".join(methods_tried)


# -------------------------
# Simple UI: Upload CSV and test AI
# -------------------------
st.sidebar.header("Settings")
st.sidebar.write("Put `GEMINI_API_KEY` and `N8N_WEBHOOK_URL` into Streamlit secrets.")

uploaded_file = st.file_uploader("Upload inventory CSV (must contain: product, quantity, expiry_date, price)", type=["csv"])
if not uploaded_file:
    st.info("Upload a CSV to test AI recommendations and alerts.")
    st.stop()

try:
    df = pd.read_csv(uploaded_file)
except Exception as e:
    st.error(f"Could not read CSV: {e}")
    st.stop()

# normalize names
df.columns = [c.strip().lower() for c in df.columns]
# Accept either expiry_date or days_remaining style; if expiry_date present compute days_remaining
if "expiry_date" in df.columns:
    df["expiry_date"] = pd.to_datetime(df["expiry_date"], errors="coerce")
    df["days_remaining"] = (df["expiry_date"] - datetime.now()).dt.days
elif "days_remaining" in df.columns:
    df["days_remaining"] = pd.to_numeric(df["days_remaining"], errors="coerce").fillna(9999).astype(int)
else:
    st.error("CSV must include either `expiry_date` or `days_remaining` column.")
    st.stop()

st.subheader("Inventory preview")
st.dataframe(df.head(200))

risky_df = df[df["days_remaining"] <= 3].copy()
st.write(f"Risky items (days_remaining <= 3): {len(risky_df)}")
if not risky_df.empty:
    st.dataframe(risky_df)

# ---------- AI Recommendation ----------
st.markdown("---")
st.subheader("AI Recommendation")

question = st.text_input("Ask something about this inventory (e.g., which items to discount?)", value="Which items should I discount to reduce waste?")
if st.button("Run AI Recommendation"):
    # build prompt
    inventory_text = "\n".join(
        f"{r.product if 'product' in r else '<unknown>'}, qty {r.quantity if 'quantity' in r else ''}, days_remaining {r.days_remaining if 'days_remaining' in r else ''}, price {r.price if 'price' in r else ''}"
        for _, r in df.iterrows()
    )

    prompt = f"""You are a retail inventory expert. The user question is:
{question}

Inventory:
{inventory_text}

Give 4 concise actionable recommendations (bullet points)."""
    # call model using our robust wrapper
    success, out = call_model(prompt, model_obj=gemini_model, model_name=MODEL_NAME)
    if success:
        st.success("AI response:")
        st.text(out)
    else:
        st.error("AI call failed:")
        st.text(out)
        st.error("Full traceback (if available) printed to logs.")

        # helpful debug message to show what methods were tried will be in out string
        st.info("If nothing here works, try upgrading the google-generativeai package:")
        st.code("pip install --upgrade google-generativeai", language="bash")
        st.write("Also ensure your GEMINI_API_KEY has correct permissions and billing is enabled.")

# ---------- Auto Alert to n8n ----------
st.markdown("---")
st.subheader("Automatic alert to n8n (JSON payload)")
if N8N_WEBHOOK_URL:
    if not risky_df.empty:
        if st.button("Send alert to n8n now"):
            payload = {"risk_score": int(max(0, 100 - (len(risky_df) / max(1, len(df)) * 100))), "risky_items": risky_df.to_dict(orient="records")}
            try:
                resp = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=15)
                st.write("n8n status:", resp.status_code)
                st.write(resp.text)
            except Exception as e:
                st.error(f"Failed to post to n8n: {e}")
    else:
        st.info("No risky items to send.")
else:
    st.info("N8N webhook not configured in secrets (N8N_WEBHOOK_URL).")

st.markdown("---")
st.caption("This app uses a robust method to call whatever model method your installed google-generativeai package supports.")
