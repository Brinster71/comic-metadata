# comic-metadata

Simple web app for manually editing comic metadata and generating hardlinked library layouts.

## Features

- Scan a file or folder for comic files (`.cbz`, `.cbr`, `.pdf`, `.epub`).
- View and edit metadata in a browser table.
- Save metadata back to files (`.cbz` writes `ComicInfo.xml` inside archive).
- Define a naming template from metadata fields.
- Build a new folder / filename structure using hardlinks to original files.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
python app.py
```

Then open <http://localhost:8000>.

## Naming template fields

- `{Series}`
- `{Title}`
- `{IssueNumber}`
- `{Volume}`
- `{Writer}`
- `{Publisher}`
- `{Year}`
- `{ext}`

Default template:

```txt
{Series}/{Volume}/{IssueNumber} - {Title}{ext}
```
