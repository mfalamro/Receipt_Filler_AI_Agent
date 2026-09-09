import base64
import io
import pathlib
import re
import sys

import pandas as pd
import pypdfium2 as pdfium
import requests
import streamlit as st


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
# N8N WEBHOOKS
# =========================================================

# Paste the Production URLs from the activated n8n workflows.
# These are public once the app is on GitHub, and the webhooks
# have no Header Auth.

GENERATE_WEBHOOK_URL = (
    "https://muneeraalamro.app.n8n.cloud/webhook/receipt-generate"
)


EMAIL_WEBHOOK_URL = (
    "https://muneeraalamro.app.n8n.cloud/webhook/receipt-email"
)


# =========================================================
# PATHS AND CONSTANTS
# =========================================================

PROJECT_DIR = pathlib.Path(__file__).parent.resolve()

ASSETS_DIR = PROJECT_DIR / "assets"

TEMPLATES_DIR = PROJECT_DIR / "templates"

FONTS_DIR = ASSETS_DIR / "fonts"

APP_LOGO_PATH = ASSETS_DIR / "APP_LOGO.png"

SIDE_LOGO_PATH = ASSETS_DIR / "SIDE_LOGO.png"


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
# UI COPY
# =========================================================

UI_COPY = {
    "en": {
        "generate": "Generate receipt",
        "template": "Template",
        "upload": "Upload a template PDF, or pick one below",
        "no_templates": (
            "No templates found. Add a PDF to the templates/ folder, "
            "or upload one above."
        ),
        "template_preview": "Template preview",
        "details": "Receipt details",
        "details_placeholder": (
            "Receipt for 3 mechanical keyboards at 120 SAR each, "
            "sold to Acme Company, paid in cash today."
        ),
        "pdf_language": "Receipt language",
        "need_template": "Choose or upload a template PDF first.",
        "need_details": "Describe the receipt before generating it.",
        "spinner": "Filling the template...",
        "success": "Receipt generated.",
        "empty_preview": "Your receipt will appear here.",
        "download": "Download PDF",
        "email": "Email",
        "email_help": (
            "The sender is the Gmail account authorized in n8n. "
            "The recipient is whatever you enter here."
        ),
        "email_to": "Recipient email",
        "send_email": "Send email",
        "invalid_email": "Enter a valid recipient email address.",
        "sending": "Sending...",
        "sent": "Receipt sent.",
        "email_unset": "Set EMAIL_WEBHOOK_URL at the top of app.py to enable emailing.",
        "line_items": "Line items",
        "warnings": "Warnings",
        "no_warnings": "No warnings for this receipt.",
        "ui_language": "App language",
        "vat_treatment": "VAT treatment",
        "vat_rate": "VAT rate (%)",
        "currency_note": f"Currency is fixed to {CURRENCY}.",
        "receipt_number": "Receipt number (optional)",
        "receipt_number_help": (
            "Leave empty to assign the next number automatically. "
            "A number you supply is rejected if it already exists."
        ),
        "history": "This session",
        "preview_fail": "The PDF was produced but could not be previewed on screen.",
        "subtotal": "Subtotal",
        "vat": "VAT",
        "total": "Total",
    },
    "ar": {
        "generate": "إنشاء فاتورة",
        "template": "القالب",
        "upload": "ارفع قالب PDF أو اختر من القائمة",
        "no_templates": (
            "لا توجد قوالب. أضف ملف PDF إلى مجلد templates/ أو ارفعه أعلاه."
        ),
        "template_preview": "معاينة القالب",
        "details": "بيانات الفاتورة",
        "details_placeholder": (
            "فاتورة بثلاثة لوحات مفاتيح ميكانيكية بسعر ١٢٠ ريالاً للواحدة، "
            "بيعت لشركة أكمي، دفعت نقداً اليوم."
        ),
        "pdf_language": "لغة الفاتورة",
        "need_template": "اختر قالباً أو ارفعه أولاً.",
        "need_details": "اكتب بيانات الفاتورة قبل الإنشاء.",
        "spinner": "جارٍ تعبئة القالب...",
        "success": "تم إنشاء الفاتورة.",
        "empty_preview": "ستظهر فاتورتك هنا.",
        "download": "تنزيل PDF",
        "email": "البريد",
        "email_help": (
            "المرسل هو حساب جيميل المصرّح في n8n. "
            "المستلم هو ما تدخله هنا."
        ),
        "email_to": "بريد المستلم",
        "send_email": "إرسال",
        "invalid_email": "أدخل بريداً إلكترونياً صالحاً.",
        "sending": "جارٍ الإرسال...",
        "sent": "تم إرسال الفاتورة.",
        "email_unset": "عيّن EMAIL_WEBHOOK_URL في أعلى app.py لتفعيل البريد.",
        "line_items": "البنود",
        "warnings": "تنبيهات",
        "no_warnings": "لا تنبيهات لهذه الفاتورة.",
        "ui_language": "لغة التطبيق",
        "vat_treatment": "معاملة الضريبة",
        "vat_rate": "نسبة الضريبة (%)",
        "currency_note": f"العملة ثابتة: {CURRENCY}.",
        "receipt_number": "رقم الفاتورة (اختياري)",
        "receipt_number_help": (
            "اتركه فارغاً ليُعيَّن الرقم التالي تلقائياً. "
            "الرقم الذي تدخله يُرفض إن كان موجوداً."
        ),
        "history": "هذه الجلسة",
        "preview_fail": "أُنشئ ملف PDF وتعذرت معاينته على الشاشة.",
        "subtotal": "المجموع الفرعي",
        "vat": "الضريبة",
        "total": "الإجمالي",
    },
}


VAT_KEYS = ["standard", "zero_rated", "exempt"]

VAT_LABELS = {
    "standard": {"en": "Standard rate", "ar": "سعر قياسي"},
    "zero_rated": {"en": "Zero-rated", "ar": "صفرية"},
    "exempt": {"en": "Exempt", "ar": "معفاة"},
}


SUBTITLE_EN = (
    "Choose a template, type your fields and generate your "
    "downloadable receipt."
)

SUBTITLE_AR = (
    "اختر قالبًا، وأدخل بياناتك، ثم أنشئ فاتورتك وحمّلها."
)


# Palette and chrome used to live in .streamlit/config.toml.
# Streamlit has no Python API for that file, so the same values
# are applied here as CSS. Nothing in .streamlit is required.

THEME = {
    "page": "#faf9f6",
    "sidebar": "#f3eee6",
    "text": "#3a372f",
    "muted": "#7a7568",
    "paper": "#ffffff",
    "hairline": "#e8e4da",
    "primary": "#7d8451",
    "primary_hover": "#6a7044",
    "on_primary": "#ffffff",
    "banner": "#faf9f6",
    "sidebar_text": "#3a372f",
    "radius": "10px",
}


def apply_theme(ui_lang):
    """
    Light paper-studio chrome. No black surfaces. Olive is an
    accent on primary buttons only. Hides Streamlit chrome.
    """

    direction = "rtl" if ui_lang == "ar" else "ltr"

    page = THEME["page"]
    sidebar = THEME["sidebar"]
    text = THEME["text"]
    muted = THEME["muted"]
    paper = THEME["paper"]
    hairline = THEME["hairline"]
    primary = THEME["primary"]
    primary_hover = THEME["primary_hover"]
    on_primary = THEME["on_primary"]
    banner = THEME["banner"]
    sidebar_text = THEME["sidebar_text"]
    radius = THEME["radius"]

    st.markdown(
        f"""
<style>
    @import url("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Arabic:wght@400;500;600&display=swap");

    #MainMenu {{visibility: hidden;}}
    header[data-testid="stHeader"] {{display: none !important;}}
    footer {{visibility: hidden;}}
    .stDeployButton, [data-testid="stAppDeployButton"],
    .stAppDeployButton {{display: none !important;}}
    div[data-testid="stToolbar"] {{display: none !important;}}
    div[data-testid="stDecoration"] {{display: none !important;}}
    div[data-testid="stStatusWidget"] {{display: none !important;}}
    a[href*="streamlit.io"] {{display: none !important;}}
    html, body, .stApp {{
        font-family: "IBM Plex Sans", "IBM Plex Sans Arabic",
            "Segoe UI", sans-serif;
    }}
    .stApp {{
        background: {page};
        color: {text};
        direction: {direction};
        --primary-color: {primary};
        --background-color: {page};
        --secondary-background-color: {paper};
        --text-color: {text};
    }}
    [data-testid="stAppViewContainer"] {{
        background: {page};
    }}
    .block-container {{
        padding-top: 0.9rem;
        padding-bottom: 2.75rem;
        max-width: 1320px;
    }}
    [data-testid="stCaptionContainer"],
    .stCaption {{
        color: {muted} !important;
        font-size: 0.8rem;
    }}
    [data-testid="stSubheader"] {{
        font-size: 1.05rem !important;
        font-weight: 600 !important;
        color: {text} !important;
        margin-bottom: 0.4rem !important;
    }}
    [data-testid="stWidgetLabel"] p {{
        font-size: 0.9rem;
        font-weight: 500;
        color: {text};
    }}
    .stApp a {{
        color: {primary};
    }}
    .fatoura-banner {{
        background: {banner};
        margin: -0.4rem 0 0.4rem 0;
        padding: 0.4rem 1rem 0.2rem 1rem;
        text-align: center;
    }}
    .fatoura-banner img {{
        max-width: 420px;
        width: 100%;
        height: auto;
        display: inline-block;
    }}
    .fatoura-subtitle {{
        text-align: center;
        color: {muted};
        font-size: 0.95rem;
        line-height: 1.6;
        margin: 0 0 1.6rem 0;
    }}
    .fatoura-subtitle span {{
        display: block;
    }}
    .fatoura-subtitle span + span {{
        margin-top: 0.15rem;
        font-family: "IBM Plex Sans Arabic", "IBM Plex Sans", sans-serif;
    }}
    .fatoura-card-title {{
        font-size: 1.45rem;
        font-weight: 600;
        color: {text};
        margin: 0 0 1.1rem 0;
        letter-spacing: -0.01em;
    }}
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background: {paper};
        border: 1px solid rgba(90, 84, 64, 0.08) !important;
        border-radius: 12px !important;
        box-shadow: 0 10px 32px rgba(90, 84, 64, 0.07);
        padding: 1.4rem 1.55rem 1.65rem;
    }}
    [data-testid="stColumn"]:nth-child(2)
    [data-testid="stVerticalBlockBorderWrapper"] {{
        min-height: 680px;
    }}
    .fatoura-empty {{
        color: {muted};
        text-align: center;
        min-height: 560px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 0.9rem;
        font-size: 0.98rem;
    }}
    .fatoura-empty svg {{
        width: 54px;
        height: 54px;
        stroke: {muted};
        fill: none;
        opacity: 0.7;
    }}
    div.stButton > button {{
        border-radius: {radius};
        font-weight: 600;
        letter-spacing: 0.01em;
        padding: 0.55rem 1rem;
    }}
    div.stButton > button[kind="primary"] {{
        background-color: {primary};
        border: 1px solid {primary};
        color: {on_primary};
    }}
    div.stButton > button[kind="primary"]:hover {{
        background-color: {primary_hover};
        border-color: {primary_hover};
        color: {on_primary};
    }}
    [data-testid="stFileUploaderDropzone"] {{
        background: {page};
        border: 1.5px dashed {hairline};
        border-radius: {radius};
        padding: 1.1rem 1rem;
    }}
    [data-testid="stFileUploaderDropzone"] button {{
        background-color: {primary} !important;
        color: {on_primary} !important;
        border: none !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
    }}
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stSelectbox"] > div {{
        border-radius: {radius};
        background: {paper};
        border-color: {hairline};
    }}
    [data-testid="stTextInput"] input:focus,
    [data-testid="stNumberInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus {{
        border-color: {primary};
        box-shadow: 0 0 0 1px {primary};
    }}
    div[role="radiogroup"] label {{
        border-radius: 8px !important;
    }}
    [data-testid="stRadio"] [aria-checked="true"] {{
        background-color: {primary} !important;
        color: {on_primary} !important;
        border-color: {primary} !important;
    }}
    [data-baseweb="radio"] input:checked + div,
    [data-baseweb="checkbox"] input:checked + div {{
        background-color: {primary} !important;
        border-color: {primary} !important;
    }}
    [data-testid="stExpander"] {{
        border: 1px solid {hairline};
        border-radius: {radius};
        background: {paper};
    }}
    [data-testid="stSidebar"] {{
        background: {sidebar};
        border-right: 1px solid {hairline};
        direction: {direction};
        color: {sidebar_text};
    }}
    [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{
        color: {sidebar_text} !important;
    }}
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
        color: {muted} !important;
    }}
    [data-testid="stSidebar"] hr {{
        border-color: {hairline};
    }}
    [data-testid="stSidebar"] button {{
        border-radius: {radius};
    }}
</style>
        """,
        unsafe_allow_html=True,
    )


def banner_html():
    encoded = base64.b64encode(
        APP_LOGO_PATH.read_bytes()
    ).decode("ascii")

    return (
        '<div class="fatoura-banner">'
        f'<img src="data:image/png;base64,{encoded}" '
        'alt="fatoura | فاتورة">'
        "</div>"
    )


def empty_preview_html(message):
    return (
        '<div class="fatoura-empty">'
        '<svg viewBox="0 0 24 24" aria-hidden="true">'
        '<path stroke-width="1.4" stroke-linecap="round" '
        'stroke-linejoin="round" d="M7 3.5h7.5L19 8v12.5H7z"/>'
        '<path stroke-width="1.4" stroke-linecap="round" '
        'd="M14.5 3.5V8H19M9 12h6M9 15.5h6"/>'
        "</svg>"
        f"<p>{message}</p>"
        "</div>"
    )


# =========================================================
# PAGE CONFIG
# =========================================================

_page_icon = (
    str(SIDE_LOGO_PATH)
    if SIDE_LOGO_PATH.exists()
    else None
)

st.set_page_config(
    page_title="Fatoura | فاتورة",
    page_icon=_page_icon,
    layout="wide"
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
# SIDEBAR
# =========================================================

with st.sidebar:

    if SIDE_LOGO_PATH.exists():

        st.image(
            str(SIDE_LOGO_PATH),
            width=48
        )


    ui_choice = st.radio(
        "App language / لغة التطبيق",
        ["English", "العربية"],
        horizontal=True,
        key="ui_language_choice"
    )

    ui_lang = (
        "ar"
        if ui_choice == "العربية"
        else "en"
    )

    t = UI_COPY[ui_lang]


    st.divider()


    vat_key = st.selectbox(
        t["vat_treatment"],
        VAT_KEYS,
        format_func=lambda key: VAT_LABELS[key][ui_lang],
        key="vat_key"
    )


    vat_rate = st.number_input(
        t["vat_rate"],
        min_value=0.0,
        max_value=100.0,
        value=15.0,
        step=0.5,
        disabled=(vat_key != "standard"),
        key="vat_rate"
    )


    st.caption(
        t["currency_note"]
    )


    receipt_number_override = st.text_input(
        t["receipt_number"],
        placeholder="INV-247",
        help=t["receipt_number_help"],
        key="receipt_number_override"
    )


    st.divider()

    st.caption(
        t["history"]
    )


    history = st.session_state.get(
        "history",
        []
    )


    if history:

        for position, entry in enumerate(reversed(history)):

            st.download_button(
                f"{entry['receipt_number']}.pdf",
                data=entry["pdf_bytes"],
                file_name=f"{entry['receipt_number']}.pdf",
                mime="application/pdf",
                key=f"download_history_{position}"
            )


if vat_key == "standard":

    effective_vat_rate = float(vat_rate)
    vat_mode = "standard"

else:

    effective_vat_rate = 0.0
    vat_mode = vat_key


apply_theme(ui_lang)


# =========================================================
# BANNER
# =========================================================

if APP_LOGO_PATH.exists():

    st.markdown(
        banner_html(),
        unsafe_allow_html=True
    )


st.markdown(
    '<p class="fatoura-subtitle">'
    f"<span>{SUBTITLE_EN}</span>"
    f"<span>{SUBTITLE_AR}</span>"
    "</p>",
    unsafe_allow_html=True
)


# =========================================================
# TWO COLUMNS
# =========================================================

compose_col, preview_col = st.columns(
    [1, 1],
    gap="large"
)


with compose_col, st.container(border=True):

    st.markdown(
        f'<p class="fatoura-card-title">{t["template"]}</p>',
        unsafe_allow_html=True
    )


    template_files = sorted(
        TEMPLATES_DIR.glob("*.pdf")
    )


    uploaded_template = st.file_uploader(
        t["upload"],
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
            t["template"],
            [path.name for path in template_files],
            key="template_choice"
        )

        template_name = chosen_name
        template_bytes = (
            TEMPLATES_DIR / chosen_name
        ).read_bytes()


    else:

        st.warning(
            t["no_templates"]
        )


    template_images = []
    template_text = ""


    if template_bytes:

        try:

            template_images, template_text = read_template_pdf(
                template_bytes
            )


            with st.expander(
                t["template_preview"],
                expanded=False
            ):

                for page_png in template_images:

                    st.image(
                        page_png,
                        width=420
                    )


        except Exception as template_error:

            st.error(
                f"{template_error}"
            )

            template_bytes = None


    pdf_language_label = st.radio(
        t["pdf_language"],
        ["English", "العربية"],
        horizontal=True,
        key="receipt_language"
    )

    language = (
        "ar"
        if pdf_language_label == "العربية"
        else "en"
    )


    details = st.text_area(
        t["details"],
        height=160,
        placeholder=t["details_placeholder"],
        key="receipt_details"
    )


    generate_button = st.button(
        t["generate"],
        type="primary",
        use_container_width=True
    )


    if generate_button:

        if not template_bytes:

            st.warning(
                t["need_template"]
            )


        elif not details.strip():

            st.warning(
                t["need_details"]
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


            try:

                with st.spinner(
                    t["spinner"]
                ):

                    response = requests.post(
                        GENERATE_WEBHOOK_URL,
                        json=payload,
                        timeout=REQUEST_TIMEOUT_SECONDS
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
                                    t["success"]
                                )


                            except Exception as render_error:

                                st.error(
                                    "The agent's markup could not be "
                                    f"rendered: {render_error}"
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


with preview_col, st.container(border=True):

    receipt = st.session_state.get(
        "receipt"
    )

    pdf_bytes = st.session_state.get(
        "pdf_bytes"
    )


    if receipt and pdf_bytes:

        receipt_number = receipt.get(
            "receipt_number",
            "unnumbered"
        )


        try:

            for page_png in pdf_page_images(pdf_bytes):

                st.image(
                    page_png,
                    use_container_width=True
                )


        except Exception as preview_error:

            st.info(
                f"{t['preview_fail']} {preview_error}"
            )


        st.download_button(
            t["download"],
            data=pdf_bytes,
            file_name=f"{receipt_number}.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
            key="download_current"
        )


        st.subheader(
            t["email"]
        )


        if not EMAIL_WEBHOOK_URL:

            st.info(
                t["email_unset"]
            )


        else:

            st.caption(
                t["email_help"]
            )


            recipient = st.text_input(
                t["email_to"],
                value=receipt.get("buyer_email", "") or "",
                placeholder="buyer@example.com",
                key="email_recipient"
            )


            send_button = st.button(
                t["send_email"],
                key="send_email"
            )


            if send_button:

                if "@" not in recipient:

                    st.warning(
                        t["invalid_email"]
                    )


                else:

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


                    try:

                        with st.spinner(
                            t["sending"]
                        ):

                            email_response = requests.post(
                                EMAIL_WEBHOOK_URL,
                                files=files,
                                data=form_fields,
                                timeout=REQUEST_TIMEOUT_SECONDS
                            )


                        if email_response.status_code == 200:

                            st.success(
                                t["sent"]
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


        line_items = receipt.get(
            "line_items",
            []
        )


        with st.expander(
            t["line_items"],
            expanded=False
        ):

            if line_items:

                st.dataframe(
                    pd.DataFrame(line_items),
                    use_container_width=True
                )


            totals = receipt.get(
                "totals",
                {}
            )

            subtotal = float(totals.get("subtotal") or 0)
            vat_amount = float(totals.get("vat_amount") or 0)
            total = float(totals.get("total") or 0)

            metric1, metric2, metric3 = st.columns(3)

            metric1.metric(
                t["subtotal"],
                f"{subtotal:,.2f} {CURRENCY}"
            )

            metric2.metric(
                t["vat"],
                f"{vat_amount:,.2f} {CURRENCY}"
            )

            metric3.metric(
                t["total"],
                f"{total:,.2f} {CURRENCY}"
            )


        notes = receipt.get("notes") or ""

        with st.expander(
            t["warnings"],
            expanded=False
        ):

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

                st.caption(
                    t["no_warnings"]
                )


    else:

        st.markdown(
            empty_preview_html(t["empty_preview"]),
            unsafe_allow_html=True
        )
