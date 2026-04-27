import io
import re
import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from PIL import Image

st.set_page_config(page_title="MV360 Sorting / QC Dashboard", layout="wide")
st.title("MV360 Sorting / QC Dashboard")
st.caption("Upload an MV360 TXT report and optional unit images.")

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
    if pd.isna(value):
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def normalize_unit_id(value):
    """
    Converts IDs to the 5-digit format used in the TXT report.
    Examples:
    00001 -> 00001
    000001 -> 00001
    1 -> 00001
    """
    match = re.search(r"\d+", str(value))
    if not match:
        return None
    return str(int(match.group(0))).zfill(5)


def extract_unit_id_from_image_name(filename):
    """
    Example:
    20260427101546_000001.jpg -> 00001
    """
    stem = Path(filename).stem
    parts = stem.split("_")

    if len(parts) > 1:
        raw_id = parts[-1]
    else:
        matches = re.findall(r"\d+", stem)
        raw_id = matches[-1] if matches else ""

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

    if table_start is None:
        raise ValueError("Could not find 'Disaggregated Information' table.")

    table_text = "\n".join(lines[table_start:])
    df = pd.read_csv(io.StringIO(table_text), sep="\t")
    df.columns = [c.strip() for c in df.columns]

    if "Lenght" in df.columns:
        df = df.rename(columns={"Lenght": "Length"})

    numeric_cols = [
        "Length",
        "Width",
        "Thick",
        "Area",
        "Cmpct.",
        "Circ.",
        "Ratio",
        "Weight",
        "Accepted Color",
        "Hull_Color",
        "Chip_Color",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "ID" in df.columns:
        df["Unit ID"] = df["ID"].apply(normalize_unit_id)

    if "Class" in df.columns:
        df["Class"] = df["Class"].astype(str).str.strip()
        df["USDA Bucket"] = df["Class"].map(CLASS_MAP).fillna("Other / Unmapped")

    for key in ["Processed Units", "Estimated Weight (g.)", "Units/Oz"]:
        if key in metadata:
            metadata[key] = clean_number(metadata[key])

    return metadata, rgb_hsv, df


def make_class_summary(df):
    summary = (
        df.groupby(["Class", "USDA Bucket"], dropna=False)
        .agg(Units=("ID", "count"), Weight_g=("Weight", "sum"))
        .reset_index()
        .sort_values("Weight_g", ascending=False)
    )

    total_weight = summary["Weight_g"].sum()
    total_units = summary["Units"].sum()

    summary["Weight %"] = (
        summary["Weight_g"] / total_weight * 100
    ).round(2) if total_weight else 0

    summary["Unit %"] = (
        summary["Units"] / total_units * 100
    ).round(2) if total_units else 0

    return summary


def save_uploaded_images(uploaded_images):
    """
    Saves images temporarily and returns:
    {
        "00001": "/tmp/....jpg",
        "00002": "/tmp/....jpg"
    }
    """
    image_map = {}

    if not uploaded_images:
        return image_map

    temp_dir = Path(tempfile.mkdtemp(prefix="mv360_images_"))

    for img_file in uploaded_images:
        unit_id = extract_unit_id_from_image_name(img_file.name)

        if not unit_id:
            continue

        suffix = Path(img_file.name).suffix.lower()
        file_path = temp_dir / f"{unit_id}{suffix}"

        with open(file_path, "wb") as f:
            f.write(img_file.getbuffer())

        image_map[unit_id] = str(file_path)

    return image_map


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
image_map = save_uploaded_images(image_files)

if image_map:
    units_df["Image Path"] = units_df["Unit ID"].map(image_map)
    units_df["Has Image"] = units_df["Image Path"].notna()
else:
    units_df["Image Path"] = None
    units_df["Has Image"] = False

class_summary = make_class_summary(units_df)

st.sidebar.divider()
st.sidebar.subheader("Filters")

classes = sorted(units_df["Class"].dropna().unique())
selected_classes = st.sidebar.multiselect("Classes", classes, default=classes)

show_only_with_images = st.sidebar.checkbox("Show only units with images", value=False)

filtered_df = units_df[units_df["Class"].isin(selected_classes)]

if show_only_with_images:
    filtered_df = filtered_df[filtered_df["Has Image"]]

filtered_summary = make_class_summary(filtered_df)

batch = metadata.get(
    "Batch",
    units_df["Batch"].iloc[0] if "Batch" in units_df.columns and len(units_df) else "",
)
report_id = metadata.get("ID", "")
report_date = metadata.get("Fecha", "")
report_time = metadata.get("Hora", "")

st.subheader("Report")
st.write(
    f"**Batch:** {batch}  |  "
    f"**Report ID:** {report_id}  |  "
    f"**Date/Time:** {report_date} {report_time}"
)

if image_map:
    matched_count = units_df["Has Image"].sum()
    st.success(f"Matched {matched_count} image(s) to unit rows.")

processed_units = int(metadata.get("Processed Units") or len(units_df))
est_weight = metadata.get("Estimated Weight (g.)") or units_df["Weight"].sum()

accept_weight = filtered_summary.loc[
    filtered_summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"
].sum()

reject_weight = filtered_summary.loc[
    ~filtered_summary["USDA Bucket"].isin(ACCEPT_GROUPS), "Weight_g"
].sum()

accept_pct = (
    accept_weight / (accept_weight + reject_weight) * 100
    if (accept_weight + reject_weight)
    else 0
)

c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Processed Units", f"{processed_units:,}")
c2.metric("Estimated Weight", f"{est_weight:,.2f} g")
c3.metric("Accept Group Weight", f"{accept_weight:,.2f} g")
c4.metric("Reject / Defect Weight", f"{reject_weight:,.2f} g")
c5.metric("Accept Group %", f"{accept_pct:.2f}%")
c6.metric("Images Matched", f"{int(units_df['Has Image'].sum()):,}")

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

feature_options = [
    c
    for c in [
        "Weight",
        "Length",
        "Width",
        "Thick",
        "Area",
        "Accepted Color",
        "Hull_Color",
        "Chip_Color",
    ]
    if c in filtered_df.columns
]

if feature_options:
    feature = st.selectbox("Choose feature", feature_options)
    fig3 = px.histogram(filtered_df, x=feature, color="Class", nbins=40, marginal="box")
    st.plotly_chart(fig3, use_container_width=True)

st.subheader("Class Summary")
st.dataframe(filtered_summary, use_container_width=True)

st.subheader("Unit Data with Images")

display_cols = [
    c
    for c in [
        "Unit ID",
        "ID",
        "Class",
        "USDA Bucket",
        "Weight",
        "Length",
        "Width",
        "Thick",
        "Image Path",
        "Has Image",
    ]
    if c in filtered_df.columns
]

st.dataframe(filtered_df[display_cols], use_container_width=True)

st.subheader("Unit Image Viewer")

unit_ids_with_images = filtered_df.loc[filtered_df["Has Image"], "Unit ID"].dropna().tolist()

if unit_ids_with_images:
    selected_unit = st.selectbox("Select Unit ID", unit_ids_with_images)

    selected_row = filtered_df[filtered_df["Unit ID"] == selected_unit].iloc[0]
    image_path = selected_row["Image Path"]

    col_img, col_data = st.columns([1, 1])

    with col_img:
        st.image(
            image_path,
            caption=f"Unit {selected_unit}",
            use_container_width=True,
        )

    with col_data:
        st.write("### Unit Details")
        st.write(selected_row[display_cols])
else:
    st.info("No matched unit images available.")

st.subheader("Raw Disaggregated Unit Data")
st.dataframe(filtered_df, use_container_width=True)

csv = filtered_df.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download filtered raw data as CSV",
    csv,
    "filtered_mv360_data.csv",
    "text/csv",
)
