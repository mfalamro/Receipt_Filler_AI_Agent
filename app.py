import base64
import io
import os
import pathlib
import re
import sys

import pandas as pd
import pypdfium2 as pdfium
import requests
import streamlit as st

from dotenv import load_dotenv


# =========================================================
# WEASYPRINT IMPORT
# =========================================================

# WeasyPrint is a Python wrapper around the Pango, HarfBuzz and
# Cairo C libraries. "pip install weasyprint" always succeeds, but
# the import fails if those libraries are missing from the machine,
# or if the API we call was removed in a newer WeasyPrint.
# The real exception is shown under the error banner.

try:

    from weasyprint import HTML
    from weasyprint.urls import URLFetcher

    WEASYPRINT_IMPORT_ERROR = None


except Exception as weasyprint_error:

    HTML = None
    URLFetcher = None

    WEASYPRINT_IMPORT_ERROR = str(weasyprint_error)


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv()


GENERATE_WEBHOOK_URL = os.getenv(
    "N8N_GENERATE_WEBHOOK_URL"
)


EMAIL_WEBHOOK_URL = os.getenv(
    "N8N_EMAIL_WEBHOOK_URL"
)


N8N_API_KEY = os.getenv(
    "N8N_API_KEY"
)


# =========================================================
# PATHS AND CONSTANTS
# =========================================================

PROJECT_DIR = pathlib.Path(__file__).parent.resolve()

TEMPLATES_DIR = PROJECT_DIR / "templates"

FONTS_DIR = PROJECT_DIR / "assets" / "fonts"


# Receipts are issued in Saudi riyals only.
CURRENCY = "SAR"


# Only the first page is sent as an image, because that is the page
# the workflow's vision message reads and because base64 images are
# the bulk of the request. Text is cheap, so a few pages of it are
# sent in case a template's terms run over.
MAX_TEMPLATE_IMAGE_PAGES = 1

MAX_TEMPLATE_TEXT_PAGES = 3


# 2x gives the model a readable image without a huge payload.
TEMPLATE_RENDER_SCALE = 2


# Generous, because the n8n AI Agent reads the template with a
# vision model and then writes a whole document.
REQUEST_TIMEOUT_SECONDS = 180


# =========================================================
# FONTS
# =========================================================

# Both families are committed to the repo so a receipt looks the
# same here, on another laptop, and on Streamlit Cloud, instead of
# depending on whatever fonts the host happens to have installed.
#
# Two families are needed, not one: Noto Kufi Arabic contains no
# Latin letters at all (its character set covers Arabic plus
# digits), so English receipts would render as empty boxes without
# DejaVu Sans. DejaVu covers Latin and has some Arabic, but its
# Arabic shaping is poorer, so the preferred order flips with the
# chosen language.

ARABIC_FONT_REGULAR = FONTS_DIR / "NotoKufiArabic-Regular.ttf"

ARABIC_FONT_BOLD = FONTS_DIR / "NotoKufiArabic-Bold.ttf"

LATIN_FONT_REGULAR = FONTS_DIR / "DejaVuSans.ttf"

LATIN_FONT_BOLD = FONTS_DIR / "DejaVuSans-Bold.ttf"


# =========================================================
# HELPERS — TEMPLATE READING
# =========================================================

def read_template_pdf(pdf_bytes):
    """
    Turn a template PDF into the two things the agent receives:
    page images, which carry the layout and styling, and the
    extracted text, which guarantees labels are transcribed
    exactly rather than guessed from pixels.

    Returns (list_of_png_bytes, extracted_text).
    """

    document = pdfium.PdfDocument(pdf_bytes)

    page_images = []

    page_texts = []


    for page_index in range(len(document)):

        if page_index >= max(
            MAX_TEMPLATE_IMAGE_PAGES,
            MAX_TEMPLATE_TEXT_PAGES
        ):
            break


        page = document[page_index]


        if page_index < MAX_TEMPLATE_IMAGE_PAGES:

            bitmap = page.render(
                scale=TEMPLATE_RENDER_SCALE
            )


            png_buffer = io.BytesIO()

            bitmap.to_pil().save(
                png_buffer,
                format="PNG"
            )

            page_images.append(
                png_buffer.getvalue()
            )


        if page_index < MAX_TEMPLATE_TEXT_PAGES:

            # get_text_bounded() rather than get_text_range(),
            # because the latter is limited to UCS-2.
            page_texts.append(
                page.get_textpage().get_text_bounded()
            )


    extracted_text = "\n\n".join(
        page_texts
    ).strip()


    return page_images, extracted_text


def png_to_data_url(png_bytes):
    """
    Data URLs are what the n8n AI Agent node expects for image
    content parts.
    """

    encoded = base64.b64encode(
        png_bytes
    ).decode("ascii")


    return f"data:image/png;base64,{encoded}"


def pdf_page_images(pdf_bytes, max_pages=3):
    """
    Rasterize a finished receipt for on-screen preview.

    A PNG is used rather than an embedded PDF viewer because
    browsers disagree about whether they will display a base64
    data: URL in an iframe, and a silently blank preview is worse
    than no preview.
    """

    document = pdfium.PdfDocument(pdf_bytes)

    page_count = min(
        len(document),
        max_pages
    )


    images = []


    for page_index in range(page_count):

        bitmap = document[page_index].render(
            scale=2
        )


        png_buffer = io.BytesIO()

        bitmap.to_pil().save(
            png_buffer,
            format="PNG"
        )

        images.append(
            png_buffer.getvalue()
        )


    return images


# =========================================================
# HELPERS — PDF RENDERING
# =========================================================

def document_stylesheet(language):
    """
    The shell the app owns regardless of what the agent writes:
    page size, margins, fonts and text direction.
    """

    if language == "ar":

        font_stack = (
            '"Noto Kufi Arabic", "DejaVu Sans", sans-serif'
        )

        text_direction = "rtl"


    else:

        font_stack = (
            '"DejaVu Sans", "Noto Kufi Arabic", sans-serif'
        )

        text_direction = "ltr"


    # font-family is forced with !important because the agent
    # writes the markup and may well ask for a font that does not
    # exist on the machine. On Streamlit Cloud that would fall back
    # to something without Arabic glyphs, which is exactly the
    # failure this app cannot afford.

    return f"""
@page {{
    size: A4;
    margin: 15mm;
}}

@font-face {{
    font-family: "Noto Kufi Arabic";
    src: url("{ARABIC_FONT_REGULAR.as_uri()}");
    font-weight: 400;
}}

@font-face {{
    font-family: "Noto Kufi Arabic";
    src: url("{ARABIC_FONT_BOLD.as_uri()}");
    font-weight: 700;
}}

@font-face {{
    font-family: "DejaVu Sans";
    src: url("{LATIN_FONT_REGULAR.as_uri()}");
    font-weight: 400;
}}

@font-face {{
    font-family: "DejaVu Sans";
    src: url("{LATIN_FONT_BOLD.as_uri()}");
    font-weight: 700;
}}

* {{
    font-family: {font_stack} !important;
}}

html, body {{
    direction: {text_direction};
    margin: 0;
    padding: 0;
}}
"""


def sanitize_agent_html(markup):
    """
    The markup is written by a language model, so anything that
    could pull in remote content or execute is removed. WeasyPrint
    runs no JavaScript, which limits the exposure to begin with.
    """

    cleaned = markup


    for tag in ("script", "iframe", "object", "embed"):

        cleaned = re.sub(
            rf"<\s*{tag}\b[^>]*>.*?<\s*/\s*{tag}\s*>",
            "",
            cleaned,
            flags=re.IGNORECASE | re.DOTALL
        )

        cleaned = re.sub(
            rf"<\s*/?\s*{tag}\b[^>]*>",
            "",
            cleaned,
            flags=re.IGNORECASE
        )


    # <link> would fetch a remote stylesheet.
    cleaned = re.sub(
        r"<\s*link\b[^>]*>",
        "",
        cleaned,
        flags=re.IGNORECASE
    )


    # An <img> pointing at a remote host is dropped outright rather
    # than left for the URL fetcher to refuse, so that a stray logo
    # cannot decide whether the whole receipt renders. Inline
    # data: images and local files are kept.
    cleaned = re.sub(
        r"<\s*img\b[^>]*\bsrc\s*=\s*[\"']?\s*https?:[^>]*>",
        "",
        cleaned,
        flags=re.IGNORECASE
    )


    return cleaned


def build_document(agent_html, language):
    """
    Combine the agent's markup with the app's stylesheet.

    The agent may return either a bare fragment or a complete HTML
    document, so both are handled: a full document gets the
    stylesheet injected into its head, a fragment gets wrapped.
    Either way the app's rules land last and win.
    """

    body = sanitize_agent_html(
        agent_html
    )

    stylesheet = document_stylesheet(
        language
    )

    style_block = f"<style>{stylesheet}</style>"


    has_html_element = re.search(
        r"<\s*html",
        body,
        flags=re.IGNORECASE
    )


    if not has_html_element:

        return (
            "<!DOCTYPE html>"
            f"<html><head><meta charset=\"utf-8\">{style_block}</head>"
            f"<body>{body}</body></html>"
        )


    if re.search(r"<\s*/\s*head\s*>", body, flags=re.IGNORECASE):

        return re.sub(
            r"<\s*/\s*head\s*>",
            style_block + "</head>",
            body,
            count=1,
            flags=re.IGNORECASE
        )


    # A document with <html> but no <head>.
    return re.sub(
        r"(<\s*html[^>]*>)",
        r"\1" + f"<head><meta charset=\"utf-8\">{style_block}</head>",
        body,
        count=1,
        flags=re.IGNORECASE
    )


def render_pdf(agent_html, language):
    """
    Render the agent's HTML to PDF bytes.
    """

    document = build_document(
        agent_html,
        language
    )


    # WeasyPrint 70 replaced the old default_url_fetcher function
    # with URLFetcher. allowed_protocols keeps the same rule we
    # had before: bundled fonts (file:) and inline images (data:),
    # and nothing that would reach out over the network.
    fetcher = URLFetcher(
        allowed_protocols=("file", "data")
    )


    # The trailing slash matters: without it a relative path in the
    # markup would resolve against the parent directory.
    return HTML(
        string=document,
        base_url=PROJECT_DIR.as_uri() + "/",
        url_fetcher=fetcher
    ).write_pdf()


# =========================================================
# SELFTEST
# =========================================================

# Runs before any Streamlit call, so "python app.py --selftest"
# never touches the UI. Its whole purpose is to prove that Arabic
# letters join and flow right to left before any n8n setup, at no
# token cost.

SELFTEST_ARABIC_HTML = """
<h1>فاتورة ضريبية</h1>
<p>مؤسسة طويق للتجارة — الرياض، المملكة العربية السعودية</p>
<p>الرقم الضريبي: 300012345600003</p>
<table border="1" cellpadding="6" style="border-collapse: collapse; width: 100%;">
  <tr>
    <th>الوصف</th>
    <th>الكمية</th>
    <th>السعر</th>
    <th>المبلغ</th>
  </tr>
  <tr>
    <td>لوحة مفاتيح ميكانيكية</td>
    <td>3</td>
    <td>120.00</td>
    <td>360.00</td>
  </tr>
</table>
<p>المجموع الفرعي: 360.00 ريال</p>
<p>ضريبة القيمة المضافة (15%): 54.00 ريال</p>
<p><strong>الإجمالي: 414.00 ريال</strong></p>
"""


SELFTEST_ENGLISH_HTML = """
<h1>Tax Invoice</h1>
<p>Tuwaiq Trading Est. — Riyadh, Saudi Arabia</p>
<p>VAT No. 300012345600003</p>
<table border="1" cellpadding="6" style="border-collapse: collapse; width: 100%;">
  <tr>
    <th>Description</th>
    <th>Qty</th>
    <th>Unit Price</th>
    <th>Amount</th>
  </tr>
  <tr>
    <td>Mechanical keyboard</td>
    <td>3</td>
    <td>120.00</td>
    <td>360.00</td>
  </tr>
</table>
<p>Subtotal: 360.00 SAR</p>
<p>VAT (15%): 54.00 SAR</p>
<p><strong>Total: 414.00 SAR</strong></p>
"""


def run_selftest():
    """
    Write one Arabic and one English PDF next to app.py.
    """

    if WEASYPRINT_IMPORT_ERROR:

        print("WeasyPrint could not be imported:")
        print(f"  {WEASYPRINT_IMPORT_ERROR}")
        print()
        print("Its C libraries are missing. With conda:")
        print(
            "  conda install -c conda-forge "
            "pango cairo harfbuzz glib fontconfig"
        )

        return 1


    for missing in [
        path
        for path in (
            ARABIC_FONT_REGULAR,
            ARABIC_FONT_BOLD,
            LATIN_FONT_REGULAR,
            LATIN_FONT_BOLD
        )
        if not path.exists()
    ]:

        print(f"Missing bundled font: {missing}")

        return 1


    for language, markup, filename in (
        ("ar", SELFTEST_ARABIC_HTML, "selftest_arabic.pdf"),
        ("en", SELFTEST_ENGLISH_HTML, "selftest_english.pdf")
    ):

        output_path = PROJECT_DIR / filename

        output_path.write_bytes(
            render_pdf(markup, language)
        )

        print(f"Wrote {output_path}")


    print()
    print(
        "Open selftest_arabic.pdf and check that the Arabic letters "
        "are joined and read right to left."
    )

    return 0


if __name__ == "__main__" and "--selftest" in sys.argv:

    sys.exit(
        run_selftest()
    )


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="AI Receipt Filler",
    page_icon="🧾",
    layout="wide"
)


# =========================================================
# APP HEADER
# =========================================================

st.title("🧾 AI Receipt Filler")

st.write(
    "Choose a receipt template, describe the receipt in plain "
    "language, and an AI agent fills it in and returns a "
    "downloadable PDF."
)


# =========================================================
# STARTUP CHECKS
# =========================================================

if WEASYPRINT_IMPORT_ERROR:

    st.error(
        "WeasyPrint could not be imported, so no PDF can be "
        "produced. The caption below is the real error. On "
        "Streamlit Cloud this is usually a missing packages.txt "
        "(Pango/Cairo) or a WeasyPrint API mismatch."
    )

    st.caption(
        f"Import error: {WEASYPRINT_IMPORT_ERROR}"
    )

    st.stop()


if not GENERATE_WEBHOOK_URL:

    st.error(
        "N8N_GENERATE_WEBHOOK_URL is not set. Add it to your .env "
        "file, or to the Secrets console if you are on Streamlit "
        "Community Cloud."
    )

    st.stop()


# =========================================================
# SESSION STATE
# =========================================================

if "receipt" not in st.session_state:
    st.session_state["receipt"] = None


if "pdf_bytes" not in st.session_state:
    st.session_state["pdf_bytes"] = None


if "history" not in st.session_state:
    st.session_state["history"] = []


# =========================================================
# TEMPLATE SELECTION
# =========================================================

st.subheader("1. Template")


template_files = sorted(
    TEMPLATES_DIR.glob("*.pdf")
)


uploaded_template = st.file_uploader(
    "Upload a template PDF, or pick one below",
    type=["pdf"],
    key="template_upload"
)


template_bytes = None

template_name = None


if uploaded_template is not None:

    template_bytes = uploaded_template.getvalue()

    template_name = uploaded_template.name


elif template_files:

    chosen_name = st.selectbox(
        "Template",
        [path.name for path in template_files],
        key="template_choice"
    )

    template_name = chosen_name

    template_bytes = (
        TEMPLATES_DIR / chosen_name
    ).read_bytes()


else:

    st.warning(
        "No templates found. Add a PDF to the templates/ folder, "
        "or upload one above."
    )


# Reading the template is also the first validation of the file, so
# a corrupt PDF is reported here rather than mid-request.

template_images = []

template_text = ""


if template_bytes:

    try:

        template_images, template_text = read_template_pdf(
            template_bytes
        )


        with st.expander(
            f"Preview of {template_name}"
        ):

            for page_png in template_images:

                st.image(
                    page_png,
                    width=520
                )


            if template_text:

                st.caption(
                    "Text extracted from the template and sent to "
                    "the agent alongside the images:"
                )

                st.code(
                    template_text
                )


    except Exception as template_error:

        st.error(
            f"Could not read that PDF: {template_error}"
        )

        template_bytes = None


# =========================================================
# RECEIPT INPUTS
# =========================================================

st.subheader("2. Receipt details")


details = st.text_area(
    "Describe the receipt",
    height=140,
    placeholder=(
        "Receipt for 3 mechanical keyboards at 120 SAR each and "
        "2 USB-C cables at 35 SAR each, sold to Acme Company, "
        "paid in cash today. Seller is Tuwaiq Trading Est., "
        "VAT number 300012345600003."
    ),
    key="receipt_details"
)


column_left, column_right = st.columns(2)


with column_left:

    language_label = st.radio(
        "Receipt language",
        ["English", "العربية"],
        horizontal=True,
        key="receipt_language"
    )

    language = (
        "ar"
        if language_label == "العربية"
        else "en"
    )


    receipt_number_override = st.text_input(
        "Receipt number (optional)",
        placeholder="INV-247",
        help=(
            "Leave empty to let the workflow assign the next "
            "number automatically. A number you supply is "
            "rejected if it already exists."
        ),
        key="receipt_number_override"
    )


with column_right:

    vat_treatment = st.selectbox(
        "VAT treatment",
        ["Standard rate", "Zero-rated", "Exempt"],
        key="vat_treatment"
    )


    vat_rate = st.number_input(
        "VAT rate (%)",
        min_value=0.0,
        max_value=100.0,
        value=15.0,
        step=0.5,
        disabled=(vat_treatment != "Standard rate"),
        key="vat_rate"
    )


    st.caption(
        f"Currency is fixed to {CURRENCY}."
    )


if vat_treatment == "Standard rate":

    effective_vat_rate = float(vat_rate)

    vat_mode = "standard"


else:

    effective_vat_rate = 0.0

    vat_mode = (
        "zero_rated"
        if vat_treatment == "Zero-rated"
        else "exempt"
    )


# =========================================================
# GENERATE BUTTON
# =========================================================

st.divider()


generate_button = st.button(
    "Generate Receipt",
    type="primary",
    use_container_width=True
)


# =========================================================
# SEND TO N8N
# =========================================================

if generate_button:

    if not template_bytes:

        st.warning(
            "Choose or upload a template PDF first."
        )


    elif not details.strip():

        st.warning(
            "Describe the receipt before generating it."
        )


    else:

        payload = {
            "details": details.strip(),
            "language": language,
            "currency": CURRENCY,
            "vat_mode": vat_mode,
            "vat_rate": effective_vat_rate,
            "receipt_number": receipt_number_override.strip(),
            "template_name": template_name,
            "template_text": template_text,
            "template_images": [
                png_to_data_url(page_png)
                for page_png in template_images
            ]
        }


        headers = {}

        if N8N_API_KEY:

            headers["X-API-Key"] = N8N_API_KEY


        try:

            with st.spinner(
                "The agent is reading your template and writing "
                "the receipt..."
            ):

                response = requests.post(
                    GENERATE_WEBHOOK_URL,
                    json=payload,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS
                )


            st.write(
                "Status Code:",
                response.status_code
            )


            if response.status_code == 200:

                try:

                    result = response.json()


                except Exception:

                    result = None

                    st.error(
                        "n8n returned a response, but it was not "
                        "valid JSON."
                    )

                    st.code(
                        response.text
                    )


                if result is not None:

                    if result.get("ok") is False:

                        # A validation failure or a duplicate
                        # receipt number. No PDF is produced.
                        st.error(
                            result.get(
                                "error",
                                "The workflow rejected the request."
                            )
                        )


                    elif not result.get("html"):

                        st.error(
                            "The workflow did not return any "
                            "document markup."
                        )

                        st.json(
                            result
                        )


                    else:

                        try:

                            pdf_bytes = render_pdf(
                                result["html"],
                                result.get("language", language)
                            )


                            st.session_state["receipt"] = result

                            st.session_state["pdf_bytes"] = pdf_bytes


                            st.session_state["history"].append(
                                {
                                    "receipt_number": result.get(
                                        "receipt_number",
                                        "unnumbered"
                                    ),
                                    "pdf_bytes": pdf_bytes
                                }
                            )


                            st.success(
                                "Receipt generated successfully"
                            )


                        except Exception as render_error:

                            st.error(
                                "The agent's markup could not be "
                                f"rendered: {render_error}"
                            )

                            st.code(
                                result["html"],
                                language="html"
                            )


            else:

                st.error(
                    "n8n returned an error"
                )

                st.code(
                    response.text
                )


        except requests.exceptions.Timeout:

            st.error(
                "The request timed out. The agent may still be "
                "running in n8n."
            )


        except requests.exceptions.RequestException as request_error:

            st.error(
                f"Connection error: {request_error}"
            )


        except Exception as unexpected_error:

            st.error(
                f"Unexpected error: {unexpected_error}"
            )


# =========================================================
# RESULTS
# =========================================================

receipt = st.session_state.get(
    "receipt"
)

pdf_bytes = st.session_state.get(
    "pdf_bytes"
)


if receipt and pdf_bytes:

    st.divider()


    (
        tab_receipt,
        tab_fields,
        tab_notes,
        tab_html,
        tab_email
    ) = st.tabs(
        [
            "Receipt",
            "Fields",
            "AI Notes",
            "Document HTML",
            "Email Receipt"
        ]
    )


    # =====================================================
    # TAB 1 — RECEIPT
    # =====================================================

    with tab_receipt:

        receipt_number = receipt.get(
            "receipt_number",
            "unnumbered"
        )


        st.subheader(
            f"Receipt {receipt_number}"
        )


        st.download_button(
            "Download PDF",
            data=pdf_bytes,
            file_name=f"{receipt_number}.pdf",
            mime="application/pdf",
            type="primary",
            key="download_current"
        )


        try:

            for page_png in pdf_page_images(pdf_bytes):

                st.image(
                    page_png,
                    width=620
                )


        except Exception as preview_error:

            st.info(
                "The PDF was produced but could not be previewed "
                f"on screen: {preview_error}"
            )


    # =====================================================
    # TAB 2 — FIELDS
    # =====================================================

    with tab_fields:

        st.header(
            "Extracted Fields"
        )


        totals = receipt.get(
            "totals",
            {}
        )


        metric1, metric2, metric3 = st.columns(3)


        # "or 0" rather than a dict default, so that an explicit
        # null from the workflow formats instead of raising.
        subtotal = float(
            totals.get("subtotal") or 0
        )

        vat_amount = float(
            totals.get("vat_amount") or 0
        )

        total = float(
            totals.get("total") or 0
        )


        metric1.metric(
            "Subtotal",
            f"{subtotal:,.2f} {CURRENCY}"
        )


        metric2.metric(
            "VAT",
            f"{vat_amount:,.2f} {CURRENCY}"
        )


        metric3.metric(
            "Total",
            f"{total:,.2f} {CURRENCY}"
        )


        # -------------------------------------------------
        # LINE ITEMS
        # -------------------------------------------------

        line_items = receipt.get(
            "line_items",
            []
        )


        if line_items:

            st.divider()

            st.subheader(
                "Line Items"
            )

            st.dataframe(
                pd.DataFrame(line_items),
                use_container_width=True
            )


        # -------------------------------------------------
        # HEADER FIELDS
        # -------------------------------------------------

        fields = receipt.get(
            "fields",
            {}
        )


        if fields:

            st.divider()

            st.subheader(
                "Header Fields"
            )

            st.json(
                fields
            )


        with st.expander(
            "View Full Agent Response"
        ):

            st.json(
                receipt
            )


    # =====================================================
    # TAB 3 — AI NOTES
    # =====================================================

    with tab_notes:

        st.header(
            "AI Notes"
        )


        notes = receipt.get(
            "notes"
        )


        if notes:

            if isinstance(notes, list):

                for note in notes:

                    st.write(
                        f"• {note}"
                    )


            else:

                st.write(
                    notes
                )


        else:

            st.info(
                "The agent returned no notes for this receipt."
            )


    # =====================================================
    # TAB 4 — DOCUMENT HTML
    # =====================================================

    with tab_html:

        st.header(
            "Document HTML"
        )

        st.write(
            "The markup the agent wrote. Useful when a layout "
            "comes out wrong and you want to see exactly why."
        )

        st.code(
            receipt.get("html", ""),
            language="html"
        )


    # =====================================================
    # TAB 5 — EMAIL RECEIPT
    # =====================================================

    with tab_email:

        st.header(
            "Email Receipt"
        )


        if not EMAIL_WEBHOOK_URL:

            st.info(
                "Set N8N_EMAIL_WEBHOOK_URL in your .env file to "
                "enable emailing."
            )


        else:

            st.write(
                "The sender is whichever account you authorized "
                "in n8n. The recipient is whatever you enter here."
            )


            recipient = st.text_input(
                "Recipient email",
                value=receipt.get("buyer_email", "") or "",
                placeholder="buyer@example.com",
                key="email_recipient"
            )


            send_button = st.button(
                "Send Email",
                key="send_email"
            )


            if send_button:

                if "@" not in recipient:

                    st.warning(
                        "Enter a valid recipient email address."
                    )


                else:

                    receipt_number = receipt.get(
                        "receipt_number",
                        "receipt"
                    )


                    files = {
                        "data": (
                            f"{receipt_number}.pdf",
                            pdf_bytes,
                            "application/pdf"
                        )
                    }


                    form_fields = {
                        "recipient": recipient,
                        "receipt_number": receipt_number
                    }


                    headers = {}

                    if N8N_API_KEY:

                        headers["X-API-Key"] = N8N_API_KEY


                    try:

                        with st.spinner(
                            "Sending..."
                        ):

                            email_response = requests.post(
                                EMAIL_WEBHOOK_URL,
                                files=files,
                                data=form_fields,
                                headers=headers,
                                timeout=REQUEST_TIMEOUT_SECONDS
                            )


                        st.write(
                            "Status Code:",
                            email_response.status_code
                        )


                        if email_response.status_code == 200:

                            st.success(
                                f"Receipt sent to {recipient}"
                            )


                        else:

                            st.error(
                                "n8n returned an error"
                            )

                            st.code(
                                email_response.text
                            )


                    except requests.exceptions.Timeout:

                        st.error(
                            "The email request timed out."
                        )


                    except requests.exceptions.RequestException as email_error:

                        st.error(
                            f"Connection error: {email_error}"
                        )


                    except Exception as unexpected_error:

                        st.error(
                            f"Unexpected error: {unexpected_error}"
                        )


# =========================================================
# THIS SESSION'S RECEIPTS
# =========================================================

history = st.session_state.get(
    "history",
    []
)


if history:

    st.divider()

    st.subheader(
        "This Session's Receipts"
    )


    for position, entry in enumerate(reversed(history)):

        st.download_button(
            f"Download {entry['receipt_number']}.pdf",
            data=entry["pdf_bytes"],
            file_name=f"{entry['receipt_number']}.pdf",
            mime="application/pdf",
            key=f"download_history_{position}"
        )
