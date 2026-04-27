import io
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(page_title="MV360 Sorting / QC Dashboard", layout="wide")
st.title("MV360 Sorting / QC Dashboard")
st.caption("Upload an MV360 TXT report and optional USDA/sample summary CSV.")

# Map machine classes to business / USDA-style buckets.
# Adjust these mappings as your classification naming gets finalized.
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


def clean_number(value):
    """Convert values like '501.94 g' or '70.31%' into floats."""
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


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
            table_start = i + 2  # next non-blank line after this is header in current reports
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
        raise ValueError("Could not find 'Disaggregated Information' table in TXT report.")

    table_text = "\n".join(lines[table_start:])
    df = pd.read_csv(io.StringIO(table_text), sep="\t")
    df.columns = [c.strip() for c in df.columns]

    # Normalize known typo from report: 'Lenght' -> 'Length'
    if "Lenght" in df.columns:
        df = df.rename(columns={"Lenght": "Length"})

    numeric_cols = [
        "Length", "Width", "Thick", "Area", "Cmpct.", "Circ.", "Ratio",
        "Weight", "Accepted Color", "Hull_Color", "Chip_Color"
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "Class" in df.columns:
        df["Class"] = df["Class"].astype(str).str.strip()
        df["USDA Bucket"] = df["Class"].map(CLASS_MAP).fillna("Other / Unmapped")

    for key in ["Processed Units", "Estimated Weight (g.)", "Units/Oz"]:
        if key in metadata:
            metadata[key] = clean_number(metadata[key])

    return metadata, rgb_hsv, df


def parse_summary_csv(uploaded_file):
    raw = uploaded_file.getvalue().decode("utf-8", errors="replace")
    df = pd.read_csv(io.StringIO(raw), header=None)

    grade = None
    if str(df.iloc[0, 0]).strip() == "SAMPLE MEETS USDA GRADE :":
        grade = str(df.iloc[0, 1]).strip()

    # Rows 11-19 in your sample contain the summary categories.
    # This also works by scanning for rows where col 1 has 'g' and col 2 has '%'.
    rows = []
    for _, row in df.iterrows():
        label = row.iloc[0]
        weight = row.iloc[1] if len(row) > 1 else None
        pct = row.iloc[2] if len(row) > 2 else None
        if pd.isna(label):
            continue
        if isinstance(weight, str) and "g" in weight and isinstance(pct, str) and "%" in pct:
            rows.append({
                "Category": str(label).strip(),
                "Weight g": clean_number(weight),
                "Weight %": clean_number(pct),
            })

    return grade, pd.DataFrame(rows)


def make_class_summary(df):
    summary = (
        df.groupby(["Class", "USDA Bucket"], dropna=False)
        .agg(Units=("ID", "count"), Weight_g=("Weight", "sum"))
        .reset_index()
        .sort_values("Weight_g", ascending=False)
    )
    total_weight = summary["Weight_g"].sum()
    total_units = summary["Units"].sum()
    summary["Weight %"] = (summary["Weight_g"] / total_weight * 100).round(2) if total_weight else 0
    summary["Unit %"] = (summary["Units"] / total_units * 100).round(2) if total_units else 0
    return summary


txt_file = st.sidebar.file_uploader("MV360 TXT report", type=["txt"])
summary_file = st.sidebar.file_uploader("Optional sample summary CSV", type=["csv"])

if not txt_file:
    st.info("Upload the MV360 TXT report to begin.")
    st.stop()

metadata, rgb_hsv, units_df = parse_mv360_txt(txt_file)
class_summary = make_class_summary(units_df)

summary_grade = None
summary_df = pd.DataFrame()
if summary_file:
    summary_grade, summary_df = parse_summary_csv(summary_file)

# Sidebar filters
st.sidebar.divider()
st.sidebar.subheader("Filters")
classes = sorted(units_df["Class"].dropna().unique())
selected_classes = st.sidebar.multiselect("Classes", classes, default=classes)
filtered_df = units_df[units_df["Class"].isin(selected_classes)]
filtered_summary = make_class_summary(filtered_df)

batch = metadata.get("Batch", units_df["Batch"].iloc[0] if "Batch" in units_df.columns and len(units_df) else "")
report_id = metadata.get("ID", "")
report_date = metadata.get("Fecha", "")
report_time = metadata.get("Hora", "")

st.subheader("Report")
st.write(f"**Batch:** {batch}  |  **Report ID:** {report_id}  |  **Date/Time:** {report_date} {report_time}")

if summary_grade:
    st.success(f"Sample meets USDA grade: {summary_grade}")

# KPI cards
processed_units = int(metadata.get("Processed Units") or len(units_df))
est_weight = metadata.get("Estimated Weight (g.)") or units_df["Weight"].sum()
accept_weight = filtered_summary.loc[filtered_summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"].sum()
reject_weight = filtered_summary.loc[~filtered_summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"].sum()
accept_pct = accept_weight / (accept_weight + reject_weight) * 100 if (accept_weight + reject_weight) else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Processed Units", f"{processed_units:,}")
c2.metric("Estimated Weight", f"{est_weight:,.2f} g")
c3.metric("Accept Group Weight", f"{accept_weight:,.2f} g")
c4.metric("Reject / Defect Weight", f"{reject_weight:,.2f} g")
c5.metric("Accept Group %", f"{accept_pct:.2f}%")

# Charts
left, right = st.columns(2)
with left:
    st.subheader("Class Breakdown by Weight")
    fig = px.pie(
        filtered_summary,
        names="Class",
        values="Weight_g",
        hole=0.35,
    )
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("USDA Bucket Breakdown")
    bucket_summary = (
        filtered_summary.groupby("USDA Bucket", as_index=False)
        .agg(Weight_g=("Weight_g", "sum"), Units=("Units", "sum"))
        .sort_values("Weight_g", ascending=False)
    )
    fig2 = px.bar(bucket_summary, x="USDA Bucket", y="Weight_g", text="Weight_g")
    fig2.update_traces(texttemplate="%{text:.1f} g", textposition="outside")
    st.plotly_chart(fig2, use_container_width=True)

st.subheader("Machine Vision Feature Distributions")
feature = st.selectbox(
    "Choose feature",
    [c for c in ["Weight", "Length", "Width", "Thick", "Area", "Accepted Color", "Hull_Color", "Chip_Color"] if c in filtered_df.columns],
)
fig3 = px.histogram(filtered_df, x=feature, color="Class", nbins=40, marginal="box")
st.plotly_chart(fig3, use_container_width=True)

st.subheader("Class Summary")
st.dataframe(filtered_summary, use_container_width=True)

if not summary_df.empty:
    st.subheader("Uploaded Sample Summary CSV")
    st.dataframe(summary_df, use_container_width=True)

st.subheader("Raw Disaggregated Unit Data")
st.dataframe(filtered_df, use_container_width=True)

csv = filtered_df.to_csv(index=False).encode("utf-8")
st.download_button("Download filtered raw data as CSV", csv, "filtered_mv360_data.csv", "text/csv")
 import streamlit as st
from pathlib import Path
from PIL import Image
import pandas as pd
import re

# Folder where images will be saved
IMAGE_DIR = Path("sample_images")
IMAGE_DIR.mkdir(exist_ok=True)

def safe_filename(name):
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", name)

st.title("Sample Image Upload")

# Example unit/sample selector
sample_id = st.text_input("Sample ID", value="Sample_001")
unit_id = st.text_input("Unit ID", value="Unit_0001")

uploaded_images = st.file_uploader(
    "Upload image(s) for this unit",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True
)

saved_paths = []

if uploaded_images:
    unit_folder = IMAGE_DIR / safe_filename(sample_id) / safe_filename(unit_id)
    unit_folder.mkdir(parents=True, exist_ok=True)

    for img_file in uploaded_images:
        file_path = unit_folder / safe_filename(img_file.name)

        with open(file_path, "wb") as f:
            f.write(img_file.getbuffer())

        saved_paths.append(str(file_path))

        image = Image.open(img_file)
        st.image(image, caption=f"{unit_id} - {img_file.name}", use_container_width=True)

    st.success(f"Saved {len(saved_paths)} image(s).")

    # Example metadata table
    image_log = pd.DataFrame({
        "Sample ID": [sample_id] * len(saved_paths),
        "Unit ID": [unit_id] * len(saved_paths),
        "Image Path": saved_paths
    })

    st.dataframe(image_log)
    
