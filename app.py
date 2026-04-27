import io
import re
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image

# ----------------------------
# PAGE CONFIG
# ----------------------------
st.set_page_config(page_title="MV360 Sorting / QC Dashboard", layout="wide")

# ----------------------------
# BRANDING + FONT (PASTE AT TOP ALWAYS)
# ----------------------------
st.markdown("""
<style>

/* Import IBM Plex Mono */
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');

/* Global font */
html, body, [class*="css"] {
    font-family: 'IBM Plex Mono', monospace;
}

/* Headers */
h1, h2, h3, h4 {
    color: #de5307;
    font-weight: 600;
}

/* Text */
p, span, label {
    color: #4f5358;
}

/* Buttons */
.stButton>button {
    background-color: #de5307;
    color: white;
    border-radius: 8px;
    border: none;
    padding: 0.5em 1em;
    font-family: 'IBM Plex Mono', monospace;
}
.stButton>button:hover {
    background-color: #b84405;
}

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #f5f6f7;
}

/* Metric cards */
[data-testid="metric-container"] {
    background-color: #ffffff;
    border: 1px solid #e6e6e6;
    padding: 15px;
    border-radius: 10px;
    font-family: 'IBM Plex Mono', monospace;
}

/* Tables */
thead tr th {
    background-color: #4f5358 !important;
    color: white !important;
}

/* Progress bars */
.stProgress > div > div > div > div {
    background-color: #de5307;
}

</style>
""", unsafe_allow_html=True)

# ----------------------------
# HEADER
# ----------------------------
st.markdown("""
<h1 style='margin-bottom: 0;'>MV360 Sorting / QC Dashboard</h1>
<p style='margin-top: 0;'>Upload an MV360 TXT report and optional unit images.</p>
""", unsafe_allow_html=True)

# ----------------------------
# CONSTANTS
# ----------------------------
CLASS_MAP = {
    "Accept": "Accept (perfect kernel)",
    "Dbl": "Doubles",
    "CyS_L": "Chip & Scratch (6.4mm)",
    "CyS_S": "Chip & Scratch (3.2-6.4mm)",
    "FM": "FM",
    "brk": "Split & Broken",
    "Broken": "Split & Broken",
    "Split": "Split & Broken",
    "Spot": "Other Defects",
    "Insect": "Serious Defects",
    "Frass": "Serious Defects",
}

ACCEPT_GROUPS = {
    "Accept (perfect kernel)",
    "Chip & Scratch (6.4mm)",
    "Chip & Scratch (3.2-6.4mm)",
}

# ----------------------------
# FUNCTIONS (UNCHANGED)
# ----------------------------
def clean_number(value):
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def normalize_unit_id(value):
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return str(int(match.group(0))).zfill(5)


def extract_unit_id_from_image_name(filename):
    stem = Path(filename).stem
    parts = stem.split("_")
    raw_id = parts[-1] if len(parts) > 1 else re.findall(r"\d+", stem)[-1]
    return normalize_unit_id(raw_id)


def parse_mv360_txt(uploaded_file):
    raw = uploaded_file.getvalue().decode("utf-8", errors="replace")
    lines = [line.rstrip("\n") for line in raw.splitlines()]

    metadata = {}
    rgb_hsv = {}
    table_start = None

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if line.strip() == "Disaggregated Information":
            table_start = i + 2
            break

        parts = line.split("\t")
        if len(parts) >= 2:
            key = parts[0].strip()
            val = parts[1].strip()
            if key.startswith("AVG "):
                rgb_hsv[key.replace("AVG ", "")] = clean_number(val)
            else:
                metadata[key] = val

    table_text = "\n".join(lines[table_start:])
    df = pd.read_csv(io.StringIO(table_text), sep="\t")
    df.columns = [c.strip() for c in df.columns]

    if "Lenght" in df.columns:
        df = df.rename(columns={"Lenght": "Length"})

    if "ID" in df.columns:
        df["Unit ID"] = df["ID"].apply(normalize_unit_id)

    if "Class" in df.columns:
        df["Class"] = df["Class"].astype(str).str.strip()
        df["USDA Bucket"] = df["Class"].map(CLASS_MAP).fillna("Other / Unmapped")

    return metadata, rgb_hsv, df


# ----------------------------
# UI FLOW (UNCHANGED)
# ----------------------------
txt_file = st.sidebar.file_uploader("MV360 TXT report", type=["txt"])
image_files = st.sidebar.file_uploader(
    "Unit images",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
)

if not txt_file:
    st.info("Upload the MV360 TXT report to begin.")
    st.stop()

metadata, rgb_hsv, units_df = parse_mv360_txt(txt_file)

st.success("Report loaded successfully.")
