Python utility for:

* Merging CANedge MF4 log files
* Decoding CAN traffic using DBC files
* Exporting decoded MF4 files

This project uses:

* Python
* `uv` for environment/package management
* `asammdf==8.5.2`

> NOTE:
> This project currently depends on `asammdf==8.5.2`.
>
> Newer versions of `asammdf` (such as 8.8.x+) introduced stricter merge behavior and may fail when concatenating MF4 files with differing channel group counts.
>
> Do not upgrade `asammdf` unless the merge workflow is revalidated.

---

# Repository Structure

```text
repo/
│
├── parseCANEdge.py
├── requirements.txt
├── README.md
│
├── raw/
├── dbc/
├── merged/
├── decoded/
└── csv/
```

---

# Installing uv

If you already have Python + pip installed:

```powershell
pip install uv
```

Verify installation:

```powershell
uv --version
```
---

# Creating the Virtual Environment

From the repository root:

```powershell
uv venv .venv
```

Activate the virtual environment:

```powershell
.\.venv\Scripts\activate
```

You should now see:

```text
(.venv)
```

in your terminal prompt.

---

# Installing Dependencies

Install all required packages:

```powershell
uv pip install -r requirements.txt
```

Verify the required asammdf version:

```powershell
python -c "import asammdf; print(asammdf.__version__)"
```

Expected output:

```text
8.5.2
```

---

# Running the Script

Place:

* raw MF4 files in `./raw`
* DBC files in `./dbc`

Then, open the repository folder in File Explorer and drag and drop the folder containing your MF4 files onto parseCANEdge.bat.

ALTERNATIVELY, after placing the raw files and dbc files, run:

```powershell
python .\parseCANEdge.py --mf4 raw

---

# Example Commands

## Basic Run

```powershell
python .\parseCANEdge.py --mf4 raw
```

## Specify Output Name

```powershell
python .\parseCANEdge.py ^
    --mf4 raw ^
    --merged-output merged_test ^
    --decoded-output decoded_test
```

---

# Output Folders

## `merged/`

Contains merged MF4 outputs.

Example:

```text
merged.mf4
merged.0.mf4
merged.1.mf4
```

If a filename already exists, `asammdf` may automatically append `.0`, `.1`, etc.

---

## `decoded/`

Contains decoded MF4 files.

Example:

```text
merged_decoded.mf4
merged.0_decoded.mf4
```

---

# Updating Dependencies

If dependencies change:

```powershell
uv pip freeze > requirements.txt
```

---

# Recreating the Environment

After cloning the repository:

```powershell
git clone <repo_url>
cd <repo_name>

uv venv .venv
.\.venv\Scripts\activate

uv pip install -r requirements.txt
```

---

# Troubleshooting

## Merge Failures

If merge operations suddenly begin failing:

1. Verify the installed `asammdf` version:

```powershell
python -c "import asammdf; print(asammdf.__version__)"
```

2. Ensure it is exactly:

```text
8.5.2
```

3. If necessary:

```powershell
uv pip install asammdf==8.5.2
```

---

## Existing Output Files

If output filenames already exist, `asammdf` may create:

```text
merged.0.mf4
merged.1.mf4
```

The script automatically detects the actual saved filename and decodes the correct merged file.

---

