# AI Receipt Filler

A Streamlit app that fills in receipt templates. You pick a template PDF, describe the receipt in plain language, and an n8n AI Agent reads the template with a vision model, writes the filled document, and the app renders it to a downloadable PDF in Arabic or English.

## How it works

Streamlit is a thin client. It holds no OpenAI key and makes no model calls; all of the intelligence lives in the n8n workflow. The app's only real computation is turning the agent's HTML into a PDF.

```
Streamlit                          n8n                       Google
---------                          ---                       ------
pick template PDF
rasterize page 1 to PNG
extract template text
press Generate Receipt   ------>   validate request
                                   read the log  <---------- Sheets
                                   assign receipt number
                                   AI Agent reads the
                                   template image + text,
                                   writes the HTML
                                   recompute the totals
                                   append a log row --------> Sheets
render HTML to PDF       <------   return JSON
preview + download
press Email Receipt      ------>   Gmail sends the PDF
```

Because the agent authors the document rather than filling placeholders, the fields are not fixed anywhere in the code. Anything your description mentions can appear on the receipt, and a new template needs no code change.

## Setup

### 1. Create the environment

`pip install` alone is not enough. WeasyPrint is a Python wrapper around the Pango, HarfBuzz and Cairo C libraries, which pip cannot install. The middle command below is what supplies them, from conda-forge, with no `apt` and no root access:

```bash
conda create -n receipt-filler python=3.13
conda install -n receipt-filler -c conda-forge pango cairo harfbuzz glib fontconfig
conda activate receipt-filler && pip install -r requirements.txt
```

On a machine that already has those libraries system-wide, `pip install -r requirements.txt` on its own is enough.

### 2. Check that Arabic renders

Do this before setting anything up in n8n. It costs nothing, calls no API, and if the fonts are wrong then everything downstream is wasted effort:

```bash
python app.py --selftest
```

This writes `selftest_arabic.pdf` and `selftest_english.pdf`. Open the Arabic one and confirm the letters are **joined** and read **right to left**. Unjoined, left-to-right Arabic means the font stack is not being applied.

Two font families are bundled in `assets/fonts/` because one is not enough: Noto Kufi Arabic contains no Latin letters at all, so English receipts would come out as empty boxes without DejaVu Sans. They are committed to the repo so output is identical on your laptop and on Streamlit Cloud, rather than depending on the host's fonts.

### 3. Set up n8n

Import both workflow files into n8n, then create four credentials:

| Credential | Used by | Notes |
|---|---|---|
| Header Auth | both Webhook nodes | Name the header `X-API-Key` and invent a long random value. Requests without it are rejected before the model runs, so they cost nothing. |
| OpenAI | `OpenAI Chat Model` | Needs GPT-4o access, because reading the template requires vision. |
| Google Sheets OAuth2 | `Read Receipt Log`, `Append Receipt Log` | |
| Gmail OAuth2 | `Send Receipt` | Whichever account you authorize here becomes the **sender** of every receipt. The recipient is whatever you type in the app. |

Then create a Google Sheet with a tab named `Receipts` and exactly these headers in row 1:

```
Receipt Number | Date | Seller | Buyer | Subtotal | VAT | Total | Currency | Template | Language
```

The header text must match, because the `Append Receipt Log` node maps incoming fields to columns by name.

Finally, open each Google Sheets node and re-select your spreadsheet and the `Receipts` tab from the dropdowns. The imported file carries an ID placeholder, and n8n needs to resolve the real one. Then activate both workflows and copy their production webhook URLs.

### 4. Configure the app

```bash
cp .env.example .env
```

Fill in the two webhook URLs and the same `X-API-Key` value you set in n8n. No OpenAI key goes here.

### 5. Run

```bash
streamlit run app.py
```

## Deploying to Streamlit Community Cloud

Push this folder to GitHub and point Streamlit Cloud at `app.py`. Two things matter:

`packages.txt` must be present. Streamlit Cloud runs `apt-get install` on each line before starting the app, and it is the only way WeasyPrint's C libraries get onto their server. Without it the app crashes on `import weasyprint`.

There is no `.env` file on Cloud. Paste the same three keys into the app's Secrets console instead. Streamlit exposes root-level secrets as environment variables as well, so `os.getenv()` picks them up and the code is unchanged.

Do not add an `environment.yml`. Streamlit Cloud uses only the first dependency file it finds, so a conda file at the repo root could be chosen instead of `requirements.txt` and trigger a slow, duplicated build.

## Making your own template

Templates are ordinary PDFs in `templates/`. The app lists whatever it finds there, and you can also upload one from the sidebar for a one-off, which is useful on Cloud where the filesystem is ephemeral.

`templates/reference_receipt.html` is a worked example with its rendered `reference_receipt.pdf`. Copy the HTML, edit it, print it to PDF, and drop the PDF in the folder:

```bash
google-chrome --headless --no-pdf-header-footer \
  --print-to-pdf=templates/my_receipt.pdf templates/my_receipt.html
```

It is a filled-in example rather than a placeholder file on purpose: the agent looks at the rendered page, so a complete document communicates the structure better than `{{ }}` markers would. Keep templates plain. The simpler the structure, the more reliably the agent reproduces it.

You do not need to set fonts, page size, margins or text direction in a template. The app owns those, and it overrides any font the agent asks for, since a font named in the markup will not exist on the server.

## Things worth knowing

**The layout can drift.** The agent writes the document each time, so two runs on the same data can differ slightly in spacing or wording, and a model can occasionally break a table. This is the cost of letting it add fields the template never had. The app pins the page size, fonts and text direction so failures stay cosmetic. The **Document HTML** tab shows exactly what the model wrote, which is where to look when a layout comes out wrong.

**The arithmetic is not the model's.** The workflow recomputes the subtotal, VAT and total from the line items and uses those everywhere. If the printed document disagrees by more than a cent, the difference is reported in the **AI Notes** tab rather than quietly accepted.

**Expect 15 to 40 seconds** per receipt. A GPT-4o vision pass that also writes a full document is not fast.

**Duplicate receipt numbers are refused.** Leave the number field empty and the workflow assigns the next one in sequence. Supply one like `INV-247` and it is rejected if the log already has it, rather than silently renumbered.

**If n8n is unreachable, no receipt is produced.** There is no local fallback, deliberately, so the sequence and the Sheets log stay authoritative.

## Files

| File | Purpose |
|---|---|
| `app.py` | The entire application |
| `Receipt Filler Agent.json` | n8n workflow: validate, number, compose, log |
| `Email Receipt.json` | n8n workflow: email the finished PDF |
| `templates/` | Template PDFs, plus the reference HTML source |
| `assets/fonts/` | Bundled Arabic and Latin fonts |
| `requirements.txt` | Python dependencies |
| `packages.txt` | Native libraries, read only by Streamlit Cloud |
| `.env.example` | The environment variables to fill in |
