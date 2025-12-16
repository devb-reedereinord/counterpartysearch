import re
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from typing import Optional

st.set_page_config(page_title="Counterparty / Charterer Search", layout="wide")

# -----------------------------
# CONFIG (put these in secrets)
# -----------------------------
# .streamlit/secrets.toml should contain:
# [gcp_service_account]
# type="service_account"
# project_id="..."
# private_key_id="..."
# private_key="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
# client_email="..."
# client_id="..."
# token_uri="https://oauth2.googleapis.com/token"
#
# [app]
# spreadsheet_id="YOUR_SHEET_ID"
# worksheet_name="Sheet1"

SPREADSHEET_ID = st.secrets["app"]["spreadsheet_id"]
WORKSHEET_NAME = st.secrets["app"].get("worksheet_name", "Sheet1")

# -----------------------------
# Helpers
# -----------------------------
def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    cols = list(df.columns)
    lowered = {c.lower(): c for c in cols}
    for cand in candidates:
        cand_l = cand.lower()
        if cand_l in lowered:
            return lowered[cand_l]
    for c in cols:
        c_l = c.lower()
        for cand in candidates:
            if cand.lower() in c_l:
                return c
    return None

def _rating_badge(value: str) -> str:
    v = (value or "").strip().upper()
    if v in {"A", "APPROVED", "GREEN"}:
        return "✅ **A (Approved)**"
    if v in {"B", "CONDITIONAL", "AMBER"}:
        return "🟡 **B (Conditional)**"
    if v in {"C", "RESTRICTED", "RED"}:
        return "🟠 **C (Restricted)**"
    if v in {"D", "REJECT", "BLACK"}:
        return "⛔ **D (Do not approve)**"
    return f"**{value}**" if value else "—"

@st.cache_data(ttl=60, show_spinner=False)
def load_from_google_sheet(spreadsheet_id: str, worksheet_name: str) -> pd.DataFrame:
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    # Clean up strings
    for c in df.columns:
        df[c] = (
            df[c].astype("string").fillna("")
            .map(lambda x: re.sub(r"[ \t]+", " ", str(x)).strip())
        )

    return df

# -----------------------------
# Load
# -----------------------------
df = load_from_google_sheet(SPREADSHEET_ID, WORKSHEET_NAME)
if df.empty:
    st.error("Google Sheet is empty or could not be read.")
    st.stop()

# Identify columns
col_charterer = _find_col(df, ["Charterer"])
col_company = _find_col(df, ["Company"])
col_owner = _find_col(df, ["Parent Company/Ownership", "Ownership", "Parent Company"])
col_address = _find_col(df, ["Address"])
col_sp = _find_col(df, ["S&P", "S&P Rating"])
col_moodys = _find_col(df, ["Moody", "Moody's"])
col_infospec = _find_col(df, ["InfoSpectrum", "Info Spectrum"])
col_dynamar = _find_col(df, ["Dynamar"])
col_sanctions = _find_col(df, ["Sanctions Check", "Sanction Check", "Sanctions"])
col_comments = _find_col(df, ["Comment", "Comments"])

# Your requirement: approval rating is under Excel Column D (currently named "Pool Agreement")
col_approval = _find_col(df, ["Approval Rating", "Approval", "Pool Agreement"])

if not col_charterer:
    st.error("Couldn't find a 'Charterer' column in the Google Sheet.")
    st.stop()

# -----------------------------
# UI
# -----------------------------
st.title("Counterparty (Charterer) Search")
st.caption("Source: Counterparty List")

c1, c2 = st.columns([2, 1])
with c1:
    search_text = st.text_input("Search charterer / any info", placeholder="e.g., VITOL, ARAMCO, Trafigura…")
with c2:
    show_only = st.checkbox("Only show matches", value=True)

df_work = df.copy()

if search_text.strip():
    s = search_text.strip().lower()
    charterer_series = df_work[col_charterer].astype("string").fillna("")
    m_charterer = charterer_series.str.lower().str.contains(re.escape(s), na=False)

    if m_charterer.any():
        df_filtered = df_work[m_charterer].copy()
    else:
        joined = df_work.astype("string").fillna("").agg(" | ".join, axis=1).str.lower()
        df_filtered = df_work[joined.str.contains(re.escape(s), na=False)].copy()
else:
    df_filtered = df_work.copy()

if not show_only and search_text.strip():
    df_filtered = df_work.copy()

options = df_filtered[col_charterer].dropna().astype("string")
options = options[options.str.len() > 0]
options_unique = sorted(options.unique().tolist(), key=lambda x: x.lower())

selected = st.selectbox("Select charterer", ["(select)"] + options_unique)

if selected != "(select)":
    row = df_work[df_work[col_charterer].astype("string") == selected].head(1)
    if not row.empty:
        r = row.iloc[0]

        top1, top2, top3 = st.columns([1.2, 1.2, 1.6])
        with top1:
            st.subheader("Approval rating")
            st.markdown(_rating_badge(str(r.get(col_approval, "")) if col_approval else ""))

        with top2:
            st.subheader("Charterer / Company")
            st.write(f"**Charterer:** {r.get(col_charterer, '—')}")
            if col_company: st.write(f"**Company:** {r.get(col_company, '—')}")

        with top3:
            st.subheader("Ownership / Address")
            if col_owner: st.write(f"**Ownership:** {r.get(col_owner, '—')}")
            if col_address:
                st.write("**Address:**")
                st.code(r.get(col_address, "—") or "—", language="text")

        st.divider()

        a, b, c = st.columns(3)
        with a:
            st.subheader("Credit ratings")
            st.write(f"**S&P:** {r.get(col_sp, '—') if col_sp else '—'}")
            st.write(f"**Moody's:** {r.get(col_moodys, '—') if col_moodys else '—'}")

        with b:
            st.subheader("Third-party checks")
            st.write(f"**InfoSpectrum:** {r.get(col_infospec, '—') if col_infospec else '—'}")
            st.write(f"**Dynamar:** {r.get(col_dynamar, '—') if col_dynamar else '—'}")
            st.write(f"**Sanctions check:** {r.get(col_sanctions, '—') if col_sanctions else '—'}")

        with c:
            st.subheader("Comments")
            st.write(r.get(col_comments, "—") if col_comments else "—")

st.subheader("All / Filtered counterparties")

display_cols = list(df_filtered.columns)
if col_approval and col_approval in display_cols:
    display_cols.remove(col_approval)
    display_cols = [col_approval] + display_cols

st.dataframe(df_filtered[display_cols], use_container_width=True, hide_index=True)


