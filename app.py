import io
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image

st.set_page_config(page_title="MV360 Sorting / QC Dashboard", layout="wide")
st.title("MV360 Sorting / QC Dashboard")
st.caption("Upload an MV360 TXT report and optional USDA/sample summary CSV.")

# =========================
# CONFIG
# =========================
IMAGE_DIR = Path("sample_images")
IMAGE_DIR.mkdir(exist_ok=True)

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


# =========================
# HELPERS
# =========================
def clean_number(value):
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def safe_filename(name):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)


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

    if table_start is None:
        raise ValueError("Could not find table.")

    table_text = "\n".join(lines[table_start:])
    df = pd.read_csv(io.StringIO(table_text), sep="\t")
    df.columns = [c.strip() for c in df.columns]

    if "Lenght" in df.columns:
        df = df.rename(columns={"Lenght": "Length"})

    if "Class" in df.columns:
        df["Class"] = df["Class"].astype(str).str.strip()
        df["USDA Bucket"] = df["Class"].map(CLASS_MAP).fillna("Other")

    df["Weight"] = pd.to_numeric(df["Weight"], errors="coerce")

    return metadata, rgb_hsv, df


def make_class_summary(df):
    summary = (
        df.groupby(["Class", "USDA Bucket"])
        .agg(Units=("ID", "count"), Weight_g=("Weight", "sum"))
        .reset_index()
    )

    total_weight = summary["Weight_g"].sum()
    summary["Weight %"] = (summary["Weight_g"] / total_weight * 100).round(2)

    return summary


# =========================
# FILE UPLOAD
# =========================
txt_file = st.sidebar.file_uploader("MV360 TXT report", type=["txt"])

if not txt_file:
    st.info("Upload TXT report to begin.")
    st.stop()

metadata, rgb_hsv, units_df = parse_mv360_txt(txt_file)
summary = make_class_summary(units_df)

batch = metadata.get("Batch", "Unknown")

# =========================
# KPIs
# =========================
st.subheader("Report Info")
st.write(f"Batch: {batch}")

accept_weight = summary.loc[summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"].sum()
reject_weight = summary.loc[~summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"].sum()

col1, col2 = st.columns(2)
col1.metric("Accept Weight", f"{accept_weight:.2f} g")
col2.metric("Reject Weight", f"{reject_weight:.2f} g")

# =========================
# CHARTS
# =========================
st.subheader("Class Breakdown")

fig = px.pie(summary, names="Class", values="Weight_g")
st.plotly_chart(fig, use_container_width=True)

# =========================
# DATA TABLE
# =========================
st.subheader("Raw Data")
st.dataframe(units_df, use_container_width=True)

# =========================
# DOWNLOAD
# =========================
csv = units_df.to_csv(index=False).encode("utf-8")
st.download_button("Download CSV", csv, "data.csv")

# =========================
# IMAGE UPLOAD SECTION
# =========================
st.divider()
st.subheader("Upload Sample Images")

sample_id = st.text_input("Sample ID", value=str(batch))
unit_id = st.text_input("Unit ID", value="Unit_001")

uploaded_images = st.file_uploader(
    "Upload image(s)",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True
)

if uploaded_images:
    unit_folder = IMAGE_DIR / safe_filename(sample_id) / safe_filename(unit_id)
    unit_folder.mkdir(parents=True, exist_ok=True)

    saved_paths = []

    for img_file in uploaded_images:
        file_path = unit_folder / safe_filename(img_file.name)

        with open(file_path, "wb") as f:
            f.write(img_file.getbuffer())

        saved_paths.append(str(file_path))

        image = Image.open(img_file)
        st.image(image, caption=img_file.name)

    st.success(f"{len(saved_paths)} image(s) saved")

    df_log = pd.DataFrame({
        "Sample ID": [sample_id] * len(saved_paths),
        "Unit ID": [unit_id] * len(saved_paths),
        "Image Path": saved_paths
    })

    st.dataframe(df_log)
