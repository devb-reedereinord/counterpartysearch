import re
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from typing import Optional

st.set_page_config(page_title="Counterparty / Charterer Search", layout="wide")

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

def _status_badge(value: str) -> str:
    v = (value or "").strip().upper()
    if v in {"APPROVED", "APPROVE", "A"}:
        return "✅ **APPROVED**"
    if v in {"PENDING", "IN REVIEW", "REVIEW"}:
        return "🟡 **PENDING**"
    if v in {"REJECTED", "REJECT", "DECLINED"}:
        return "⛔ **REJECTED**"
    return f"**{value}**" if value else "—"

@st.cache_data(ttl=60, show_spinner=False)
def load_from_google_sheet(spreadsheet_id: str, worksheet_name: str) -> pd.DataFrame:
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
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
col_status = _find_col(df, ["Status"])  # Column L in your file
col_charterer = _find_col(df, ["Charterer"])
col_company = _find_col(df, ["Company"])
col_owner = _find_col(df, ["Parent Company/Ownership", "Ownership", "Parent Company"])
col_address = _find_col(df, ["Address"])

# Pool Agreement (Column D) is explanatory detail
col_pool = _find_col(df, ["Pool Agreement"])

if not col_charterer:
    st.error("Couldn't find a 'Charterer' column in the Google Sheet.")
    st.stop()
if not col_status:
    st.error("Couldn't find a 'Status' column in the Google Sheet (expected Column L).")
    st.stop()

# -----------------------------
# UI
# -----------------------------
st.title("Counterparty (Charterer) Search")
st.caption("Source: Counterparty List")

c1, c2 = st.columns([2, 1])
with c1:
    search_text = st.text_input(
        "Search charterer / any info",
        placeholder="e.g., VITOL, ARAMCO, Trafigura…"
    )
with c2:
    show_only = st.checkbox("Only show matches", value=True)

df_work = df.copy()

# Search logic (charterer first, else full-row search)
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

# Selector
options = df_filtered[col_charterer].dropna().astype("string")
options = options[options.str.len() > 0]
options_unique = sorted(options.unique().tolist(), key=lambda x: x.lower())

selected = st.selectbox("Select charterer", ["(select)"] + options_unique)

# Detail panel
if selected != "(select)":
    row = df_work[df_work[col_charterer].astype("string") == selected].head(1)
    if not row.empty:
        r = row.iloc[0]

        top1, top2 = st.columns([1.2, 2.8])

        with top1:
            st.subheader("Status")
            st.markdown(_status_badge(str(r.get(col_status, ""))))

        with top2:
            st.subheader("Counterparty details")
            st.write(f"**Charterer:** {r.get(col_charterer, '—')}")
            if col_company:
                st.write(f"**Company:** {r.get(col_company, '—')}")
            if col_owner:
                st.write(f"**Parent company / ownership:** {r.get(col_owner, '—')}")
            if col_address:
                st.write("**Address:**")
                st.code(r.get(col_address, "—") or "—", language="text")

            # Optional: show pool agreement clause as explanation
            if col_pool:
                pool_val = r.get(col_pool, "")
                if str(pool_val).strip():
                    st.write("**Pool Agreement (Clause):**")
                    st.code(pool_val, language="text")

st.subheader("All / Filtered counterparties")

# TABLE: Only show requested columns
table_cols = []
for c in [col_status, col_charterer, col_company, col_owner, col_address]:
    if c and c in df_filtered.columns:
        table_cols.append(c)

# If any are missing, still show what we have (but ideally these exist)
st.dataframe(df_filtered[table_cols], use_container_width=True, hide_index=True)



