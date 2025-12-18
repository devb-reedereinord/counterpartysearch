import re
from typing import Optional, List, Tuple

import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Counterparty / Charterer Search", layout="wide")

SPREADSHEET_ID = st.secrets["app"]["spreadsheet_id"]
WORKSHEET_NAME = st.secrets["app"].get("worksheet_name", "Sheet1")

STATUS_OPTIONS = ["Approved", "Pending", "Rejected"]

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

def _normalize_status(value: str) -> str:
    """Map sheet values to one of STATUS_OPTIONS when possible."""
    v = (value or "").strip().lower()
    if v in {"approved", "approve", "a"}:
        return "Approved"
    if v in {"pending", "in review", "review"}:
        return "Pending"
    if v in {"rejected", "reject", "declined"}:
        return "Rejected"
    return (value or "").strip()

def _get_gspread_client(write: bool) -> gspread.Client:
    scopes = ["https://www.googleapis.com/auth/spreadsheets"] if write else [
        "https://www.googleapis.com/auth/spreadsheets.readonly"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    return gspread.authorize(creds)

def _colnum_to_a1(col_num_1_based: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA ..."""
    s = ""
    n = col_num_1_based
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

@st.cache_data(ttl=60, show_spinner=False)
def load_sheet(spreadsheet_id: str, worksheet_name: str) -> Tuple[pd.DataFrame, List[str]]:
    """
    Returns:
      df: DataFrame of sheet contents with an extra '_rownum' column for editing
      headers: list of header names in sheet order
    """
    gc = _get_gspread_client(write=False)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    values = ws.get_all_values()
    if not values or len(values) < 2:
        return pd.DataFrame(), []

    headers = [h.strip() for h in values[0]]
    rows = values[1:]  # data rows

    df = pd.DataFrame(rows, columns=headers)

    # Clean up
    for c in df.columns:
        df[c] = df[c].map(_clean_cell).astype("string")

    # Add actual sheet row number (header is row 1, first data row is row 2)
    df["_rownum"] = [i + 2 for i in range(len(df))]

    return df, headers

def append_row(spreadsheet_id: str, worksheet_name: str, headers: List[str], row_dict: dict) -> None:
    gc = _get_gspread_client(write=True)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    new_row = [_clean_cell(row_dict.get(h, "")) for h in headers]
    ws.append_row(new_row, value_input_option="USER_ENTERED")

def update_row(spreadsheet_id: str, worksheet_name: str, headers: List[str], rownum: int, row_dict: dict) -> None:
    """
    Updates a full row (all columns) at sheet row number = rownum.
    """
    gc = _get_gspread_client(write=True)
    sh = gc.open_by_key(spreadsheet_id)
    ws = sh.worksheet(worksheet_name)

    row_values = [_clean_cell(row_dict.get(h, "")) for h in headers]
    last_col_letter = _colnum_to_a1(len(headers))
    rng = f"A{rownum}:{last_col_letter}{rownum}"
    ws.update(rng, [row_values], value_input_option="USER_ENTERED")

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
# Load data
# -----------------------------
df, headers = load_sheet(SPREADSHEET_ID, WORKSHEET_NAME)
if df.empty:
    st.error("Google Sheet is empty or could not be read.")
    st.stop()

# Column mapping
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

# Validate must-have columns
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

# Search: prefer charterer matches; else full-row search
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

# Selector (Charterer)
options = df_filtered[col_charterer].dropna().astype("string")
options = options[options.str.len() > 0]
options_unique = sorted(options.unique().tolist(), key=lambda x: x.lower())

selected = st.selectbox("Select charterer / counterparty", ["(select)"] + options_unique)

# -----------------------------
# Details view
# -----------------------------
if selected != "(select)":
    matches = df_work[df_work[col_charterer].astype("string") == selected].copy()

    # If duplicates exist, let user select which row
    if len(matches) > 1:
        st.info(f"Found {len(matches)} matching rows for this charterer (duplicates). Select the correct one:")
        match_labels = []
        for _, rr in matches.iterrows():
            label = f"Row {rr['_rownum']} | {rr.get(col_company, '')} | {rr.get(col_status, '')}"
            match_labels.append(label)
        pick = st.selectbox("Pick record", match_labels)
        picked_rownum = int(pick.split("|")[0].replace("Row", "").strip())
        row = matches[matches["_rownum"] == picked_rownum].head(1)
    else:
        row = matches.head(1)

    if row.empty:
        st.warning("No exact match found. Try searching again.")
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
# Admin: Add + Edit (password protected)
# -----------------------------
with st.expander("🔒 Admin: Add / Edit counterparties", expanded=False):
    if not is_admin_unlocked():
        admin_login_ui()
        st.stop()

    st.success("Admin is unlocked for this session.")

    tab_add, tab_edit = st.tabs(["➕ Add new", "✏️ Edit existing"])

    # ---------- ADD ----------
    with tab_add:
        st.caption("Adds a new row to the Google Sheet (appends).")

        with st.form("add_counterparty_form", clear_on_submit=True):
            new_data = {}

            # Status dropdown
            current_status = ""
            new_data[col_status] = st.selectbox("Status", STATUS_OPTIONS, index=0)

            # Priority fields
            priority = [col_charterer, col_company, col_owner, col_address]
            for c in priority:
                if c and c in headers:
                    if c == col_address:
                        new_data[c] = st.text_area(c, value="")
                    else:
                        new_data[c] = st.text_input(c, value="")

            # Remaining columns (exclude those already handled)
            handled = set([col_status] + priority)
            for c in headers:
                if c in handled:
                    continue
                if any(k in c.lower() for k in ["address", "comment", "remarks", "notes", "pool"]):
                    new_data[c] = st.text_area(c, value="")
                else:
                    new_data[c] = st.text_input(c, value="")

            submitted = st.form_submit_button("Add to Google Sheet")

            if submitted:
                if not _clean_cell(new_data.get(col_charterer, "")):
                    st.error("Charterer is required.")
                else:
                    try:
                        append_row(SPREADSHEET_ID, WORKSHEET_NAME, headers=headers, row_dict=new_data)
                        st.success("Added successfully. Refreshing…")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to append row. Error: {e}")

    # ---------- EDIT ----------
    with tab_edit:
        st.caption("Edits an existing row in-place (updates the full row).")

        # Choose record to edit (handles duplicates)
        df_edit = df_work.copy()
        df_edit["_label"] = (
            "Row " + df_edit["_rownum"].astype(str)
            + " | " + df_edit[col_charterer].astype("string")
            + " | " + df_edit[col_company].astype("string")
            + " | " + df_edit[col_status].astype("string")
        )
        labels = df_edit["_label"].tolist()
        selected_label = st.selectbox("Select record to edit", ["(select)"] + labels)

        if selected_label != "(select)":
            rownum = int(selected_label.split("|")[0].replace("Row", "").strip())
            rec = df_edit[df_edit["_rownum"] == rownum].head(1)
            if rec.empty:
                st.error("Could not load the selected record.")
            else:
                rr = rec.iloc[0]
                existing = {h: rr.get(h, "") for h in headers}

                with st.form("edit_counterparty_form", clear_on_submit=False):
                    edited = {}

                    # Status dropdown (preselect if matches)
                    existing_status = _normalize_status(existing.get(col_status, ""))
                    if existing_status in STATUS_OPTIONS:
                        idx = STATUS_OPTIONS.index(existing_status)
                    else:
                        idx = 0
                    edited[col_status] = st.selectbox("Status", STATUS_OPTIONS, index=idx)

                    # Priority fields
                    for c in [col_charterer, col_company, col_owner, col_address]:
                        if c and c in headers:
                            if c == col_address:
                                edited[c] = st.text_area(c, value=existing.get(c, ""))
                            else:
                                edited[c] = st.text_input(c, value=existing.get(c, ""))

                    # Remaining columns
                    handled = set([col_status, col_charterer, col_company, col_owner, col_address])
                    for c in headers:
                        if c in handled:
                            continue
                        if any(k in c.lower() for k in ["address", "comment", "remarks", "notes", "pool"]):
                            edited[c] = st.text_area(c, value=existing.get(c, ""))
                        else:
                            edited[c] = st.text_input(c, value=existing.get(c, ""))

                    colA, colB = st.columns([1, 2])
                    with colA:
                        save = st.form_submit_button("Save changes")
                    with colB:
                        st.caption("Saving overwrites the entire row in Google Sheets for this record.")

                    if save:
                        try:
                            update_row(SPREADSHEET_ID, WORKSHEET_NAME, headers=headers, rownum=rownum, row_dict=edited)
                            st.success("Updated successfully. Refreshing…")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to update row. Error: {e}")
