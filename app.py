import os
import re
import math
import hashlib
import hmac
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

import pandas as pd
import streamlit as st
import mysql.connector
from mysql.connector import Error
from PIL import Image
import pytesseract
from dotenv import load_dotenv
from streamlit_geolocation import streamlit_geolocation

load_dotenv()

st.set_page_config(
    page_title="AI Medicine Availability & Health Assistant",
    page_icon="💊",
    layout="wide",
)

# ---------------- DATABASE CONNECTION ----------------
def db_connection():
    """Connect using the existing .env configuration."""
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "medicine_assistant"),
        connection_timeout=10,
    )


def query_db(sql, params=(), fetch=True):
    conn = db_connection()
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        if fetch:
            return cur.fetchall()
        conn.commit()
        return None
    finally:
        if cur:
            cur.close()
        conn.close()


# ---------------- DISTANCE AND MAP HELPERS ----------------
def calculate_distance_km(lat1, lon1, lat2, lon2):
    """Return straight-line distance in kilometres using the Haversine formula."""
    values = (lat1, lon1, lat2, lon2)
    if any(value is None for value in values):
        return None
    try:
        lat1, lon1, lat2, lon2 = map(float, values)
    except (TypeError, ValueError):
        return None

    earth_radius_km = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * earth_radius_km * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_navigation_url(latitude, longitude, address):
    """Create a Google Maps directions URL for a pharmacy."""
    if latitude is not None and longitude is not None:
        try:
            destination = f"{float(latitude)},{float(longitude)}"
        except (TypeError, ValueError):
            destination = quote(str(address or ""), safe="")
    else:
        destination = quote(str(address or ""), safe="")

    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={destination}&travelmode=driving"
    )


# ---------------- PASSWORD HELPERS ----------------
def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex() + "$" + digest.hex()


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, AttributeError):
        return False


# ---------------- OCR HELPERS ----------------
STRENGTH_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|%|iu)\b", re.IGNORECASE
)
FORM_WORDS = [
    "tablets", "tablet", "tab", "capsules", "capsule", "cap",
    "syrup", "suspension", "injection", "cream", "ointment", "drops",
]


def parse_stock_text(raw_text):
    """Parse simple stock-list lines; chemists must review OCR before saving."""
    rows = []
    for line in raw_text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" |:-\t")
        if len(line) < 3:
            continue
        if re.search(r"^(medicine|drug|item|name|stock|quantity|qty|sr\.?\s*no)\b", line, re.I):
            continue

        qty_match = re.search(r"(\d+)\s*(?:units?|pcs?|strips?)?\s*$", line, re.I)
        quantity = int(qty_match.group(1)) if qty_match else 0
        body = line[:qty_match.start()].strip(" |:-") if qty_match else line

        strength_match = STRENGTH_RE.search(body)
        strength = strength_match.group(0).replace(" ", "") if strength_match else ""
        if strength_match:
            body = body[:strength_match.start()] + " " + body[strength_match.end():]

        form = ""
        for word in FORM_WORDS:
            if re.search(r"\b" + re.escape(word) + r"\b", body, re.I):
                form = word.title()
                body = re.sub(r"\b" + re.escape(word) + r"\b", "", body, flags=re.I)
                break

        name = re.sub(r"[^A-Za-z0-9 .()+/-]", " ", body)
        name = re.sub(r"\s+", " ", name).strip(" .-/")
        if name:
            rows.append({
                "medicine_name": name,
                "strength": strength,
                "form": form,
                "quantity": quantity,
            })

    return pd.DataFrame(rows, columns=["medicine_name", "strength", "form", "quantity"])


# ---------------- SESSION STATE ----------------
if "chemist" not in st.session_state:
    st.session_state.chemist = None
if "patient_search_submitted" not in st.session_state:
    st.session_state.patient_search_submitted = False
if "patient_search_term" not in st.session_state:
    st.session_state.patient_search_term = ""


# ---------------- APP HEADER ----------------
st.title("💊 AI Medicine Availability & Health Assistant")
st.caption("Find nearby medicine stock • Help pharmacies update inventory")

with st.sidebar:
    st.header("Navigation")
    page = st.radio("Choose a portal", ["Patient Search", "Chemist Portal"])
    st.divider()
    st.caption("Prototype note: stock results depend on the latest updates submitted by pharmacies.")


# ============================================================================
# PATIENT SEARCH PORTAL
# ============================================================================
if page == "Patient Search":
    st.subheader("📍 Your Current Location")
    st.write("Allow location access to discover nearby pharmacies.")

    # Keep the existing browser GPS component. The user may still search without GPS.
    location = streamlit_geolocation()
    user_lat = None
    user_lon = None

    if isinstance(location, dict):
        user_lat = location.get("latitude")
        user_lon = location.get("longitude")

    gps_available = user_lat is not None and user_lon is not None
    if gps_available:
        st.success("Your location has been detected successfully!")
        st.caption("Nearby pharmacies with saved coordinates will be sorted by distance.")
    else:
        st.info("Location is not available. You can still search medicines; results will not be distance-sorted.")

    st.divider()
    st.subheader("🔎 Find medicines near you")

    with st.form("patient_medicine_search", clear_on_submit=False):
        medicine_term = st.text_input(
            "Medicine name",
            value=st.session_state.patient_search_term,
            placeholder="e.g. Paracetamol",
        )
        search_submitted = st.form_submit_button(
            "Search availability",
            type="primary",
            use_container_width=False,
        )

    if search_submitted:
        st.session_state.patient_search_term = medicine_term.strip()
        st.session_state.patient_search_submitted = True

    if st.session_state.patient_search_submitted:
        search_term = st.session_state.patient_search_term
        if not search_term:
            st.warning("Please enter a medicine name to search.")
        else:
            try:
                results = query_db(
                    """
                    SELECT
                        m.medicine_id,
                        m.name AS medicine_name,
                        m.strength,
                        m.form,
                        p.pharmacy_id,
                        p.name AS pharmacy_name,
                        p.address,
                        p.contact,
                        p.latitude,
                        p.longitude,
                        p.opening_hours,
                        i.quantity,
                        i.last_updated
                    FROM inventory i
                    JOIN medicine m ON m.medicine_id = i.medicine_id
                    JOIN pharmacy p ON p.pharmacy_id = i.pharmacy_id
                    WHERE LOWER(m.name) LIKE LOWER(%s)
                      AND i.quantity > 0
                    ORDER BY m.name, p.name
                    """,
                    (f"%{search_term}%",),
                )

                if not results:
                    st.info("No matching medicine stock was found. Try another spelling or check again later.")
                else:
                    for item in results:
                        item["distance_km"] = calculate_distance_km(
                            user_lat, user_lon, item.get("latitude"), item.get("longitude")
                        )

                    # Pharmacies with known distances first; unknown distances last.
                    if gps_available:
                        results.sort(
                            key=lambda row: (
                                row["distance_km"] is None,
                                row["distance_km"] if row["distance_km"] is not None else float("inf"),
                            )
                        )

                    st.subheader(f"Availability results ({len(results)})")
                    st.caption("Stock is based on the pharmacy's latest submitted update. Please call before travelling to confirm availability.")

                    for item in results:
                        with st.container(border=True):
                            st.markdown(
                                f"**{item['medicine_name']} • {item.get('strength') or 'Strength not specified'} • {item.get('form') or 'Form not specified'}**"
                            )
                            left, right = st.columns([1.1, 1])
                            with left:
                                st.success(f"In stock: {int(item['quantity'])} unit(s)")
                                st.markdown(f"**Pharmacy:** {item['pharmacy_name']}")
                                st.markdown(f"**Address:** {item['address']}")
                            with right:
                                st.markdown(f"**Contact:** {item['contact']}")
                                if item["distance_km"] is not None:
                                    st.markdown(f"**Distance:** {item['distance_km']:.2f} km (straight-line estimate)")
                                else:
                                    st.caption("Distance unavailable: pharmacy coordinates have not been added.")
                                updated = item.get("last_updated")
                                if isinstance(updated, datetime):
                                    updated_text = updated.strftime("%d %b %Y, %I:%M %p")
                                else:
                                    updated_text = str(updated or "Not available")
                                st.markdown(f"**Last stock update:** {updated_text}")
                                if item.get("opening_hours"):
                                    st.markdown(f"**Opening hours:** {item['opening_hours']}")

                            maps_url = get_navigation_url(
                                item.get("latitude"), item.get("longitude"), item.get("address")
                            )
                            st.link_button(
                                "📍 Navigate to pharmacy",
                                maps_url,
                                use_container_width=True,
                            )

            except Error as e:
                st.error("Could not retrieve medicine availability from the database.")
                st.caption(f"Database details: {e}")


# ============================================================================
# CHEMIST PORTAL
# ============================================================================
else:
    if st.session_state.chemist is None:
        login_tab, register_tab = st.tabs(["Login", "Register pharmacy"])

        with login_tab:
            st.subheader("Chemist login")
            username = st.text_input("Username", key="login_user")
            password = st.text_input("Password", type="password", key="login_pass")
            if st.button("Login", type="primary"):
                try:
                    users = query_db(
                        """
                        SELECT u.user_id, u.username, u.password_hash, u.pharmacy_id,
                               p.name AS pharmacy_name
                        FROM app_user u
                        JOIN pharmacy p ON p.pharmacy_id = u.pharmacy_id
                        WHERE u.username = %s
                        """,
                        (username.strip(),),
                    )
                    if users and verify_password(password, users[0]["password_hash"]):
                        st.session_state.chemist = users[0]
                        st.rerun()
                    else:
                        st.error("Incorrect username or password.")
                except Error as e:
                    st.error("Could not connect to the database for login.")
                    st.caption(str(e))

        with register_tab:
            st.subheader("Create pharmacy account")
            st.caption("Use a unique username. The account is linked to the pharmacy you register here.")
            with st.form("register_pharmacy"):
                pharmacy_name = st.text_input("Pharmacy name")
                address = st.text_input("Full address")
                contact = st.text_input("Phone number")
                latitude = st.text_input("Pharmacy latitude (optional)")
                longitude = st.text_input("Pharmacy longitude (optional)")
                new_username = st.text_input("Choose username")
                new_password = st.text_input("Choose password", type="password")
                confirm_password = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Register pharmacy")

            if submitted:
                if not all([
                    pharmacy_name.strip(), address.strip(), contact.strip(),
                    new_username.strip(), new_password,
                ]):
                    st.error("Please fill all required fields.")
                elif new_password != confirm_password:
                    st.error("Passwords do not match.")
                elif len(new_password) < 8:
                    st.error("Use a password with at least 8 characters.")
                else:
                    conn = None
                    cur = None
                    try:
                        lat = float(latitude) if latitude.strip() else None
                        lon = float(longitude) if longitude.strip() else None
                        if lat is not None and not -90 <= lat <= 90:
                            raise ValueError("Latitude must be between -90 and 90.")
                        if lon is not None and not -180 <= lon <= 180:
                            raise ValueError("Longitude must be between -180 and 180.")

                        conn = db_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """INSERT INTO pharmacy(name, address, contact, latitude, longitude)
                               VALUES(%s, %s, %s, %s, %s)""",
                            (pharmacy_name.strip(), address.strip(), contact.strip(), lat, lon),
                        )
                        pharmacy_id = cur.lastrowid
                        cur.execute(
                            """INSERT INTO app_user(username, password_hash, pharmacy_id)
                               VALUES(%s, %s, %s)""",
                            (new_username.strip(), hash_password(new_password), pharmacy_id),
                        )
                        conn.commit()
                        st.success("Pharmacy registered. Please log in.")
                    except (Error, ValueError) as e:
                        if conn:
                            conn.rollback()
                        st.error(f"Could not register pharmacy: {e}")
                    finally:
                        if cur:
                            cur.close()
                        if conn:
                            conn.close()

    else:
        chemist = st.session_state.chemist
        st.success(f"Logged in: {chemist['pharmacy_name']}")
        if st.button("Logout"):
            st.session_state.chemist = None
            st.session_state.pop("ocr_rows", None)
            st.session_state.pop("ocr_raw", None)
            st.rerun()

        st.subheader("Upload stock photograph")
        st.write("Take a clear, straight photograph of a printed or handwritten stock list. Ensure medicine names and quantities are readable.")
        uploaded = st.file_uploader("Choose stock image", type=["png", "jpg", "jpeg"])

        if uploaded:
            try:
                stock_image = Image.open(BytesIO(uploaded.getvalue())).convert("RGB")
                st.image(stock_image, caption="Uploaded stock sheet", width=550)
                if st.button("Extract stock using OCR"):
                    raw = pytesseract.image_to_string(stock_image, config="--psm 6")
                    st.session_state.ocr_raw = raw
                    st.session_state.ocr_rows = parse_stock_text(raw)
                    st.rerun()
            except Exception as e:
                st.error("OCR could not run. Check that Tesseract OCR is installed and configured.")
                st.caption(str(e))

        if "ocr_raw" in st.session_state:
            with st.expander("View extracted OCR text"):
                st.text(st.session_state.ocr_raw or "No text detected.")

        if "ocr_rows" in st.session_state:
            st.subheader("Review extracted stock before saving")
            st.warning("OCR can misread handwriting. Correct every row and quantity before saving.")
            edited = st.data_editor(
                st.session_state.ocr_rows,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "medicine_name": st.column_config.TextColumn("Medicine name", required=True),
                    "strength": st.column_config.TextColumn("Strength"),
                    "form": st.column_config.TextColumn("Form"),
                    "quantity": st.column_config.NumberColumn("Quantity", min_value=0, step=1, required=True),
                },
                key="stock_editor",
            )

            if st.button("Confirm and update my inventory", type="primary"):
                clean = edited.copy()
                clean["medicine_name"] = clean["medicine_name"].fillna("").astype(str).str.strip()
                clean["strength"] = clean["strength"].fillna("").astype(str).str.strip()
                clean["form"] = clean["form"].fillna("").astype(str).str.strip()
                clean["quantity"] = pd.to_numeric(clean["quantity"], errors="coerce").fillna(-1).astype(int)
                clean = clean[(clean["medicine_name"] != "") & (clean["quantity"] >= 0)]

                if clean.empty:
                    st.error("There are no valid rows to save.")
                else:
                    conn = None
                    cur = None
                    try:
                        conn = db_connection()
                        cur = conn.cursor()
                        for _, item in clean.iterrows():
                            cur.execute(
                                """SELECT medicine_id FROM medicine
                                   WHERE LOWER(name) = LOWER(%s)
                                     AND COALESCE(LOWER(strength), '') = LOWER(%s)
                                     AND COALESCE(LOWER(form), '') = LOWER(%s)
                                   LIMIT 1""",
                                (item["medicine_name"], item["strength"], item["form"]),
                            )
                            found = cur.fetchone()
                            if found:
                                medicine_id = found[0]
                            else:
                                cur.execute(
                                    "INSERT INTO medicine(name, strength, form) VALUES(%s, %s, %s)",
                                    (item["medicine_name"], item["strength"] or None, item["form"] or None),
                                )
                                medicine_id = cur.lastrowid

                            # Update existing pharmacy/medicine row, otherwise insert it.
                            cur.execute(
                                """SELECT inventory_id FROM inventory
                                   WHERE medicine_id = %s AND pharmacy_id = %s
                                   LIMIT 1""",
                                (medicine_id, chemist["pharmacy_id"]),
                            )
                            existing = cur.fetchone()
                            if existing:
                                cur.execute(
                                    """UPDATE inventory SET quantity = %s, last_updated = NOW()
                                       WHERE inventory_id = %s""",
                                    (int(item["quantity"]), existing[0]),
                                )
                            else:
                                cur.execute(
                                    """INSERT INTO inventory(medicine_id, pharmacy_id, quantity, last_updated)
                                       VALUES(%s, %s, %s, NOW())""",
                                    (medicine_id, chemist["pharmacy_id"], int(item["quantity"])),
                                )

                        conn.commit()
                        st.success(f"Successfully updated {len(clean)} inventory row(s).")
                        st.session_state.pop("ocr_rows", None)
                        st.session_state.pop("ocr_raw", None)
                    except Error as e:
                        if conn:
                            conn.rollback()
                        st.error(f"Inventory was not updated: {e}")
                    finally:
                        if cur:
                            cur.close()
                        if conn:
                            conn.close()

        st.divider()
        st.subheader("My current inventory")
        try:
            inventory = query_db(
                """SELECT m.name, m.strength, m.form, i.quantity, i.last_updated
                   FROM inventory i
                   JOIN medicine m ON m.medicine_id = i.medicine_id
                   WHERE i.pharmacy_id = %s
                   ORDER BY m.name""",
                (chemist["pharmacy_id"],),
            )
            if inventory:
                st.dataframe(pd.DataFrame(inventory), use_container_width=True, hide_index=True)
            else:
                st.info("No inventory uploaded yet.")

            st.subheader("Low-stock overview")
            low = [row for row in inventory if int(row["quantity"]) <= 5]
            st.metric("Items with 5 or fewer units", len(low))
            if low:
                st.dataframe(pd.DataFrame(low), use_container_width=True, hide_index=True)
        except Error as e:
            st.error("Could not load your inventory.")
            st.caption(str(e))


st.divider()
st.caption("Academic prototype • Always verify live stock by calling the pharmacy. Not a substitute for medical advice.")
