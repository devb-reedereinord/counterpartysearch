import re
from typing import Optional, List

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Counterparty / Charterer Search", layout="wide")

SPREADSHEET_ID = st.secrets["app"]["spreadsheet_id"]
WORKSHEET_NAME = st.secrets["app"].get("worksheet_name", "Sheet1")

# -----------------------------
# Helpers
# -----------------------------
def _clean_cell(x: object) -> str:
    s = "" if x is None else str(x)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s

def _find_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
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

def _get_gspread_client(write: bool) -> gspread.Client:
    # READ ONLY:  spreadsheets.readonly
    # READ/WRITE: spreadsheets
    scopes = ["https://www.googleapis.com/auth/spreadsheets"] if write else [
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=60, show_spinner=False)
def load_from_google_sheet(spreadsheet_id: str, worksheet_name: str) -> pd.DataFrame:
    gc = _get_gspread_client(write=False)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)

    for c in df.columns:
        df[c] = df[c].map(_clean_cell).astype("string")

    return df

def append_row_to_google_sheet(spreadsheet_id: str, worksheet_name: str, headers: List[str], row_dict: dict) -> None:
    """
    Appends one row to the sheet in the exact header order.
    """
    gc = _get_gspread_client(write=True)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    new_row = [ _clean_cell(row_dict.get(h, "")) for h in headers ]
    ws.append_row(new_row, value_input_option="USER_ENTERED")

def is_admin_unlocked() -> bool:
    return bool(st.session_state.get("admin_ok", False))

def admin_login_ui():
    st.subheader("Admin access")
    with st.form("admin_login", clear_on_submit=False):
        pw = st.text_input("Admin password", type="password")
        submitted = st.form_submit_button("Unlock admin")
        if submitted:
            if pw == st.secrets["admin"]["password"]:
                st.session_state["admin_ok"] = True
                st.success("Admin unlocked.")
            else:
                st.session_state["admin_ok"] = False
                st.error("Incorrect password.")

# -----------------------------
# Load
# -----------------------------
df = load_from_google_sheet(SPREADSHEET_ID, WORKSHEET_NAME)
if df.empty:
    st.error("Google Sheet is empty or could not be read.")
    st.stop()

headers = list(df.columns)

# -----------------------------
# Column mapping
# -----------------------------
col_status    = _find_col(df, ["Status"])
col_charterer = _find_col(df, ["Charterer"])
col_company   = _find_col(df, ["Company"])
col_owner     = _find_col(df, ["Parent Company/Ownership", "Ownership", "Parent Company"])
col_address   = _find_col(df, ["Address"])

# Detail-only fields
col_pool      = _find_col(df, ["Pool Agreement"])
col_sp        = _find_col(df, ["S&P", "S&P Rating", "S&P rating"])
col_moodys    = _find_col(df, ["Moody", "Moody's", "Moody's Rating"])
col_infospec  = _find_col(df, ["InfoSpectrum", "Info Spectrum", "Infospectrum Rating"])
col_dynamar   = _find_col(df, ["Dynamar", "Dynamar Rating"])
col_sanctions = _find_col(df, ["Sanctions Check", "Sanction Check", "Sanctions"])
col_comments  = _find_col(df, ["Comment", "Comments"])

required_missing = []
for name, c in [
    ("Status", col_status),
    ("Charterer", col_charterer),
    ("Company", col_company),
    ("Parent company/ownership", col_owner),
    ("Address", col_address),
]:
    if not c:
        required_missing.append(name)

if required_missing:
    st.error("Missing required columns in the Google Sheet: " + ", ".join(required_missing))
    st.stop()

# -----------------------------
# UI: Search
# -----------------------------
st.title("Counterparty (Charterer) Search")
st.caption("Source: Counterparty List (Google Sheet)")

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

selected = st.selectbox("Select charterer / counterparty", ["(select)"] + options_unique)

# -----------------------------
# Details view
# -----------------------------
if selected != "(select)":
    row = df_work[df_work[col_charterer].astype("string") == selected].head(1)

    if row.empty:
        st.warning("No exact match found (possible whitespace/duplicate formatting). Try searching again.")
    else:
        r = row.iloc[0]

        t1, t2, t3 = st.columns([1.1, 1.4, 1.5])

        with t1:
            st.subheader("Status")
            st.markdown(_status_badge(r.get(col_status, "")))

            st.subheader("Sanctions check")
            sanc = r.get(col_sanctions, "") if col_sanctions else ""
            st.write(sanc if str(sanc).strip() else "—")

        with t2:
            st.subheader("Counterparty")
            st.write(f"**Charterer:** {r.get(col_charterer, '—')}")
            st.write(f"**Company:** {r.get(col_company, '—')}")
            st.write(f"**Parent company / ownership:** {r.get(col_owner, '—')}")

            st.subheader("Address")
            st.code(r.get(col_address, "—") or "—", language="text")

            if col_pool:
                pool_val = r.get(col_pool, "")
                if str(pool_val).strip():
                    st.subheader("Pool agreement clause")
                    st.code(pool_val, language="text")

        with t3:
            st.subheader("Ratings")
            st.write(f"**S&P:** {r.get(col_sp, '—') if col_sp else '—'}")
            st.write(f"**Moody's:** {r.get(col_moodys, '—') if col_moodys else '—'}")
            st.write(f"**InfoSpectrum:** {r.get(col_infospec, '—') if col_infospec else '—'}")
            st.write(f"**Dynamar:** {r.get(col_dynamar, '—') if col_dynamar else '—'}")

        st.divider()
        st.subheader("Comments")
        if col_comments:
            comm = r.get(col_comments, "")
            st.write(comm if str(comm).strip() else "—")
        else:
            st.write("—")

# -----------------------------
# Table view (ONLY requested columns)
# -----------------------------
st.subheader("All / Filtered counterparties")
table_cols = [col_status, col_charterer, col_company, col_owner, col_address]
table_cols = [c for c in table_cols if c and c in df_filtered.columns]
st.dataframe(df_filtered[table_cols], use_container_width=True, hide_index=True)

# -----------------------------
# Admin: Add new row (password protected)
# -----------------------------
with st.expander("🔒 Admin: Add new counterparty", expanded=False):
    if not is_admin_unlocked():
        admin_login_ui()
    else:
        st.success("Admin is unlocked for this session.")
        st.caption("This form will append a new row to the Google Sheet using the sheet's header order.")

        # Dynamic form: one field per column
        with st.form("add_counterparty_form", clear_on_submit=True):
            new_data = {}

            # Make a nicer layout: key columns first, then the rest
            priority = [col_status, col_charterer, col_company, col_owner, col_address]
            priority = [c for c in priority if c]

            ordered_cols = []
            for c in priority:
                if c in headers:
                    ordered_cols.append(c)
            for c in headers:
                if c not in ordered_cols:
                    ordered_cols.append(c)

            for c in ordered_cols:
                # Use multiline for longer text-ish columns
                if c and any(k in c.lower() for k in ["address", "comment", "remarks", "notes", "pool"]):
                    new_data[c] = st.text_area(c, value="")
                else:
                    new_data[c] = st.text_input(c, value="")

            submitted = st.form_submit_button("Add to Google Sheet")

            if submitted:
                # Basic validation: require at least Charterer
                if not _clean_cell(new_data.get(col_charterer, "")):
                    st.error("Charterer is required.")
                else:
                    try:
                        append_row_to_google_sheet(
                            SPREADSHEET_ID,
                            WORKSHEET_NAME,
                            headers=headers,
                            row_dict=new_data
                        )
                        st.success("Added successfully. Refreshing data…")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to append row. Error: {e}")



