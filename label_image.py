"""
label_image.py  –  Add product images to Flipkart shipping label PDFs
Drop this file into your picklist project and add it to your navigation.

Requirements (add to requirements.txt if not already present):
    pymupdf>=1.24.0
    pdfplumber>=0.10.0
    requests>=2.31.0
    Pillow>=10.0.0

Usage:
    Upload a Flipkart multi-label PDF.
    The app auto-extracts Order IDs → fetches SKU + image from Firebase → stamps image on each label.
    Download the enhanced PDF.
"""

import streamlit as st
import fitz  # PyMuPDF
import pdfplumber
import re
import requests
import io
import tempfile
import os
from PIL import Image

# ── Reuse your existing Firebase connection ──────────────────────────────────
from firebase_utils import db  # your existing module

# ── CONFIGURE: adjust collection/field names to match your Firestore ─────────
ORDERS_COLLECTION   = "orders"     # collection where order_id is the document ID
SKU_FIELD           = "sku"        # field name inside the order document

# ⚠️  UPDATE THIS to match your products collection
# Option A – products collection where doc ID = SKU, with an image_url field
PRODUCTS_COLLECTION = "products"
IMAGE_URL_FIELD     = "image_url"  # or "image", "img_url" etc.

# Option B – if image is stored directly on the order doc, set this to True
IMAGE_ON_ORDER_DOC  = False
ORDER_IMAGE_FIELD   = "image_url"  # only used if IMAGE_ON_ORDER_DOC = True
# ─────────────────────────────────────────────────────────────────────────────

ORDER_ID_PATTERN = re.compile(r'\b(OD\d{15,20})\b')
SKU_PATTERN = re.compile(r"\|\s*([^|]+?)\s*\|")

# ── Firebase helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_image_url(order_id: str) -> tuple[str | None, str | None]:
    """Returns (sku, image_url) for an order_id. Cached for 5 minutes."""
    try:
        order_doc = db.collection(ORDERS_COLLECTION).document(order_id).get()
        if not order_doc.exists:
            return None, None
        order_data = order_doc.to_dict()
        sku = order_data.get(SKU_FIELD)

        if IMAGE_ON_ORDER_DOC:
            return sku, order_data.get(ORDER_IMAGE_FIELD)

        if not sku:
            return None, None

        sku_lower = sku.lower()
        product_doc = db.collection(PRODUCTS_COLLECTION).document(sku_lower).get()
        if not product_doc.exists:
            return sku, None
        return sku, product_doc.to_dict().get(IMAGE_URL_FIELD)

    except Exception as e:
        st.warning(f"Firebase error for {order_id}: {e}")
        return None, None


def download_image(url: str) -> bytes | None:
    """Download an image from URL and return as bytes."""
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None

def get_barcode_image(sku: str) -> bytes | None:
    """Generate a barcode image for the SKU using an online API."""
    try:
        # Using Barcode API from bwip-js (no API key required)
        api_url = (
            f"https://bwipjs-api.metafloor.com/"
            f"?bcid=datamatrix"
            f"&text={sku}"
            f"&scale=4"
            f"&paddingwidth=0"
            f"&paddingheight=0"
        )
        resp = requests.get(api_url, timeout=5)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None

# ── PDF processing ────────────────────────────────────────────────────────────

def extract_order_id(page_text: str) -> str | None:
    """Extract OD... order ID from label text."""
    match = ORDER_ID_PATTERN.search(page_text)
    return match.group(1) if match else None

def extract_sku_flipkart(page_text: str) -> str | None:
    match = SKU_PATTERN.search(page_text)
    if match:
        sku = match.group(1).strip()
        #remove 1 & 2 nd word
        parts = sku.split()
        if len(parts) > 2:
            sku = " ".join(parts[2:])
        # remove first character
        sku = sku[1:]
        return sku
    
def extract_sku_meesho(page_text: str) -> str | None:
    lines = page_text.split("\n")

    for i, line in enumerate(lines):
        if "SKU" in line and "Order No" in line:
            data_line = lines[i + 1].strip()

            # Step 1: Extract Order ID (most reliable)
            order_match = re.search(r'\d+_\d+', data_line)
            if not order_match:
                return None

            order_id = order_match.group()

            # Step 2: Remove order id from line
            left_part = data_line.replace(order_id, "").strip()

            # Step 3: Extract qty (number before last word = color)
            qty_match = re.search(r'(\d+)\s+\w+$', left_part)
            if not qty_match:
                return None

            qty = qty_match.group(1)

            # Step 4: Remove "qty + color" from end
            left_part = re.sub(r'\d+\s+\w+$', '', left_part).strip()

            # Step 5: Now remove SIZE (last remaining word)
            # remaining = SKU + SIZE → remove last word
            parts = left_part.split()
            if len(parts) < 2:
                return None

            parts = parts[:-2]  # remove last word (size)
            sku = " ".join(parts)  # everything except last word = SKU
            #split sku " "(space) and remove the last word
            return sku

    return None


def stamp_image_on_page(page: fitz.Page, img_bytes: bytes) -> bool:
    """
    Insert a small product image into the label page.
    Targets the blank space in the SKU description row.
    Returns True if successful.
    """
    try:
        rect = page.rect
        w, h = rect.width, rect.height

        # Flipkart label: SKU row is roughly 65-85% down the page
        # Place image in the right portion of the description cell
        # ~18% of the smaller dimension
        margin    = w * 0.04
        
        img_width = 40
        img_height = 40

        x1 = w - img_width - margin
        y1 = h * 0.64
        x2 = x1 + img_width
        y2 = y1 + img_height

        img_rect = fitz.Rect(x1, y1, x2, y2)

        page.insert_image(img_rect, stream=img_bytes, keep_proportion=True)

        # Draw a thin border around the image
        page.draw_rect(img_rect, color=(0.7, 0.7, 0.7), width=0.5)
        return True

    except Exception as e:
        return False

def stamp_image_on_page_meesho(page: fitz.Page, img_bytes: bytes) -> bool:
    """
    Insert a small product image into the label page.
    Targets the blank space in the SKU description row.
    Returns True if successful.
    """
    try:
        rect = page.rect
        w, h = rect.width, rect.height

        # Meesho label: use a larger image size than Flipkart
        margin = w * 0.02
        max_img_size = min(w * 0.26, h * 0.26, 170)

        img_width = max_img_size
        img_height = max_img_size

        x1 = w - (img_width*4.1)
        y1 = h * 0.35
        x2 = x1 + img_width
        y2 = y1 + img_height

        img_rect = fitz.Rect(x1, y1, x2, y2)

        page.insert_image(img_rect, stream=img_bytes, keep_proportion=True)

        # Draw a thin border around the image
        page.draw_rect(img_rect, color=(0.7, 0.7, 0.7), width=0.5)
        return True

    except Exception as e:
        return False


def process_pdf(pdf_bytes: bytes, platform: str) -> tuple[bytes, list[dict]]:
    """Main processing function that handles different platforms."""
    if platform == "flipkart":
        return process_pdf_flipkart(pdf_bytes)
    elif platform == "meesho":
        return process_pdf_meesho(pdf_bytes)
    
def prepare_barcode_image(
    img_bytes: bytes,
    padding: int = 20,
    background="white"
) -> bytes:
    """
    Add white background + quiet zone around barcode/QR image.
    Returns cleaned PNG bytes.
    """

    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

    # Create white background
    bg = Image.new(
        "RGB",
        (
            img.width + padding * 2,
            img.height + padding * 2
        ),
        background
    )

    # Paste barcode in center
    bg.paste(img, (padding, padding), img)

    output = io.BytesIO()
    bg.save(output, format="PNG")

    return output.getvalue()

def prepare_barcode_image_meesho(
    img_bytes: bytes,
    padding: int = 20,
    background="white"
) -> bytes:
    """
    Add white background + quiet zone around barcode/QR image.
    Returns cleaned PNG bytes.
    """

    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

    # Create white background
    bg = Image.new(
        "RGB",
        (
            img.width + padding * 2,
            img.height + padding * 2
        ),
        background
    )

    # Paste barcode in center
    bg.paste(img, (padding, padding), img)

    output = io.BytesIO()
    bg.save(output, format="PNG")

    return output.getvalue()

def crop_pdf(pdf_bytes: bytes,left,top,right,bottom) -> bytes:
    """
    Crop the PDF to remove extra margins (specific to Meesho labels).
    This helps ensure the stamped image fits well within the label area.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for page in doc:
        rect = page.rect
        w, h = rect.width, rect.height

        # Define crop box (adjust these values based on your label layout)
        crop_rect = fitz.Rect(
            w * left,  # left
            h * top,  # top
            w * right,  # right
            h * bottom   # bottom
        )
        page.set_cropbox(crop_rect)

    output_buf = io.BytesIO()
    doc.save(output_buf)
    doc.close()
    return output_buf.getvalue()
    
def process_pdf_meesho(uploaded_bytes: bytes) -> tuple[bytes, list[dict]]:
    """
    Process every page of the label PDF:
      1. Extract Order ID
      2. Fetch image URL from Firebase
      3. Stamp image onto the page
    Returns (modified_pdf_bytes, results_log).
    """

    uploaded_bytes = crop_pdf(uploaded_bytes, 0, 0, 1, 0.5)
    results = []

    # --- Extract text (pdfplumber is better for text positions) ---
    page_texts = []
    with pdfplumber.open(io.BytesIO(uploaded_bytes)) as plumber_pdf:
        for p in plumber_pdf.pages:
            page_texts.append(p.extract_text() or "")

    # --- Modify PDF (PyMuPDF for image stamping) ---
    doc = fitz.open(stream=uploaded_bytes, filetype="pdf")

    for i, page in enumerate(doc):
        text = page_texts[i] if i < len(page_texts) else ""
        sku = extract_sku_meesho(text)

        if not sku:
            results.append({"page": i + 1, "order_id": "—", "sku": "—", "status": "⚠️ SKU not found"})
            continue

        # img_bytes = download_image(image_url)
        img_bytes = get_barcode_image(sku)  # Use barcode image instead of product image
        #save image for debugging in images folder with filename as order_id.png
        # with tempfile.TemporaryDirectory() as tmpdir:
        #     img_path = os.path.join(tmpdir, f"{sku}.png")
        #     with open(img_path, "wb") as f:
        #         f.write(img_bytes)
        #     st.image(img_path, caption=f"Barcode for {sku}", width=200)
        if img_bytes:
            img_bytes = prepare_barcode_image_meesho(
                img_bytes,
                padding=5
        )
        if not img_bytes:
            results.append({"page": i + 1, "sku": sku, "status": "❌ Image download failed"})
            continue

        ok = stamp_image_on_page_meesho(page, img_bytes)
        status = "✅ Image added" if ok else "❌ Stamp failed"
        results.append({"page": i + 1, "sku": sku, "status": status})

    output_buf = io.BytesIO()
    doc.save(output_buf)
    doc.close()
    return output_buf.getvalue(), results

def process_pdf_flipkart(uploaded_bytes: bytes) -> tuple[bytes, list[dict]]:
    """
    Process every page of the label PDF:
      1. Extract Order ID
      2. Fetch image URL from Firebase
      3. Stamp image onto the page
    Returns (modified_pdf_bytes, results_log).
    """
    uploaded_bytes = crop_pdf(uploaded_bytes, 0.31, 0.03, 0.68, 0.46)
    results = []

    # --- Extract text (pdfplumber is better for text positions) ---
    page_texts = []
    with pdfplumber.open(io.BytesIO(uploaded_bytes)) as plumber_pdf:
        for p in plumber_pdf.pages:
            page_texts.append(p.extract_text() or "")

    # --- Modify PDF (PyMuPDF for image stamping) ---
    doc = fitz.open(stream=uploaded_bytes, filetype="pdf")

    for i, page in enumerate(doc):
        text = page_texts[i] if i < len(page_texts) else ""
        sku = extract_sku_flipkart(text)

        if not sku:
            results.append({"page": i + 1, "order_id": "—", "sku": "—", "status": "⚠️ SKU not found"})
            continue

        # img_bytes = download_image(image_url)
        img_bytes = get_barcode_image(sku)  # Use barcode image instead of product image
        #save image for debugging in images folder with filename as order_id.png
        # with tempfile.TemporaryDirectory() as tmpdir:
        #     img_path = os.path.join(tmpdir, f"{sku}.png")
        #     with open(img_path, "wb") as f:
        #         f.write(img_bytes)
        #     st.image(img_path, caption=f"Barcode for {sku}", width=200)
        if img_bytes:
            img_bytes = prepare_barcode_image(
                img_bytes,
                padding=15
        )
        if not img_bytes:
            results.append({"page": i + 1, "sku": sku, "status": "❌ Image download failed"})
            continue

        ok = stamp_image_on_page(page, img_bytes)
        status = "✅ Image added" if ok else "❌ Stamp failed"
        results.append({"page": i + 1, "sku": sku, "status": status})

    output_buf = io.BytesIO()
    doc.save(output_buf)
    doc.close()
    return output_buf.getvalue(), results


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def render_label_stamper_panel():
    st.set_page_config(
        page_title="Label Image Stamper",
        page_icon="🖨️",
        layout="centered",
    )

    # Mobile-friendly CSS
    st.markdown("""
        <style>
            .stButton > button {
                width: 100%;
                height: 3.2rem;
                font-size: 1.1rem;
                border-radius: 10px;
            }
            .stDownloadButton > button {
                width: 100%;
                height: 3.5rem;
                font-size: 1.15rem;
                background-color: #1a73e8;
                color: white;
                border-radius: 10px;
            }
            .result-box {
                padding: 0.4rem 0.8rem;
                border-radius: 8px;
                margin: 4px 0;
                font-size: 0.9rem;
            }
        </style>
    """, unsafe_allow_html=True)

    st.title("🖨️ Label Image Stamper")
    st.caption("Upload Flipkart label PDF → auto-adds product images → download for printing")

    uploaded = st.file_uploader(
        "📂 Upload Label PDF",
        type=["pdf"],
        help="Download the label PDF from Flipkart seller panel and upload here",
    )

    if uploaded is None:
        st.info("👆 Upload a label PDF to get started")
        return

    pdf_bytes = uploaded.read()
    total_pages = len(list(pdfplumber.open(io.BytesIO(pdf_bytes)).pages))
    st.success(f"📄 Loaded **{total_pages} label(s)**")

    platform = st.radio(
        "Select Platform",
        ["flipkart", "meesho"],
        index=0,
        format_func=lambda x: x.capitalize(),
        horizontal=True
    )

    if st.button("🚀 Process Labels", type="primary"):
        with st.spinner(f"Processing {total_pages} labels… fetching images…"):
            modified_pdf, results = process_pdf(pdf_bytes,platform)

        # Results table
        ok_count = sum(1 for r in results if "✅" in r["status"])
        st.markdown(f"### Results: {ok_count}/{total_pages} labels stamped")

        for r in results:
            color = "#d4edda" if "✅" in r["status"] else "#fff3cd" if "⚠️" in r["status"] else "#f8d7da"
            st.markdown(
                f'<div class="result-box" style="background:{color}">'
                f'<b>Page {r["page"]}</b> &nbsp;|&nbsp; {r["sku"]} &nbsp;|&nbsp; '
                f'</div>',
                unsafe_allow_html=True,
            )

        st.divider()
        st.download_button(
            label="⬇️ Download Enhanced PDF",
            data=modified_pdf,
            file_name=f"labels_with_images_{uploaded.name}",
            mime="application/pdf",
        )
        st.caption("Print this PDF directly. Product images are stamped on each label.")


if __name__ == "__main__":
    main()
