import os
import re
import math
import hashlib
import hmac
from datetime import datetime
from io import BytesIO
from streamlit_geolocation import streamlit_geolocation
from urllib.parse import quote

import pandas as pd
import streamlit as st
import mysql.connector
from mysql.connector import Error
from PIL import Image
import pytesseract
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="AI Medicine Availability & Health Assistant",
                   page_icon="💊", layout="wide")

# ---------------- DATABASE CONNECTION ----------------
def db_connection():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=os.getenv("DB_NAME", "medicine_assistant"),
    )

def query_db(sql, params=(), fetch=True):
    conn = db_connection()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        result = cur.fetchall() if fetch else None
        if not fetch:
            conn.commit()
        return result
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

def execute_many(sql, rows):
    conn = db_connection()
    try:
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
    finally:
        cur.close()
        conn.close()

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
    except Exception:
        return False

# ---------------- OCR HELPERS ----------------
STRENGTH_RE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|ml|%|iu)\b", re.I)
FORM_WORDS = ["tablet", "tablets", "tab", "capsule", "capsules", "cap",
              "syrup", "suspension", "injection", "cream", "ointment", "drops"]

def parse_stock_text(raw_text):
    """
    Basic OCR parser for lines such as:
    Paracetamol 500mg Tablet 20
    Cetirizine | 10 mg | Tablet | 15
    It deliberately sends uncertain lines to the review table.
    """
    rows = []
    for line in raw_text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" |:-\t")
        if len(line) < 3:
            continue
        # Skip common headings and page numbers
        if re.search(r"^(medicine|drug|item|name|stock|quantity|qty|sr\.?\s*no)", line, re.I):
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

# ---------------- INITIAL SESSION STATE ----------------
if "chemist" not in st.session_state:
    st.session_state.chemist = None

st.title("💊 AI Medicine Availability & Health Assistant")
st.caption("Find nearby medicine stock • Help pharmacies update inventory")

# ---------------- SIDEBAR ----------------
with st.sidebar:
    st.header("Navigation")
    page = st.radio("Choose a portal", ["Patient Search", "Chemist Portal"])
    st.divider()
    st.caption("Prototype note: stock results depend on the latest updates submitted by pharmacies.")


# ---------------- PATIENT SEARCH ----------------
if page == "Patient Search":
    st.header("Find medicines near you")
    medicine_search = st.text_input(
        "Medicine name",
        placeholder="e.g. Paracetamol"
    )

    # Automatically detect the patient's browser location
    location = streamlit_geolocation()

    user_lat = None
    user_lon = None

    if isinstance(location, dict):
        user_lat = location.get("latitude")
        user_lon = location.get("longitude")

    if user_lat is not None and user_lon is not None:
        st.success("Your current location has been detected!")
    else:
        st.info(
            "Allow location access in your browser "
            "to find nearby pharmacies."
        )

    if st.button("Search availability", type="primary"):
        if not medicine_search.strip():
            st.warning("Enter a medicine name first.")
        else:
            like = f"%{medicine_search.strip()}%"

            rows = query_db("""
                SELECT m.name, m.strength, m.form, p.name AS pharmacy,
                       p.address, p.contact, p.latitude, p.longitude,
                       i.quantity, i.last_updated
                FROM inventory i
                JOIN medicine m ON m.medicine_id = i.medicine_id
                JOIN pharmacy p ON p.pharmacy_id = i.pharmacy_id
                WHERE (m.name LIKE %s OR m.generic_group LIKE %s)
                  AND i.quantity > 0
                ORDER BY i.last_updated DESC
            """, (like, like))

            if not rows:
                st.info(
                    "No matching in-stock records were found. "
                    "Try another spelling or check again later."
                )
            else:
                # Calculate distance only when browser GPS is available
                if user_lat is not None and user_lon is not None:

                    def distance(row):
                        if row["latitude"] is None or row["longitude"] is None:
                            return 10**9

                        r = 6371
                        lat1 = math.radians(float(user_lat))
                        lon1 = math.radians(float(user_lon))
                        lat2 = math.radians(float(row["latitude"]))
                        lon2 = math.radians(float(row["longitude"]))

                        a = (
                            math.sin((lat2 - lat1) / 2) ** 2
                            + math.cos(lat1) * math.cos(lat2)
                            * math.sin((lon2 - lon1) / 2) ** 2
                        )

                        return 2 * r * math.asin(math.sqrt(a))

                    for row in rows:
                        row["distance_km"] = round(distance(row), 2)

                    rows.sort(
                        key=lambda x: x["distance_km"]
                    )
                else:
                    for row in rows:
                        row["distance_km"] = None

                st.success(
                    f"Found {len(rows)} matching pharmacy record(s)."
                )

                for row in rows:
                    with st.container(border=True):
                        st.subheader(
                            f'{row["name"]} {row["strength"] or ""} '
                            f'{row["form"] or ""}'
                        )

                        a, b, c = st.columns(3)
                        a.metric("Units reported", row["quantity"])
                        b.metric("Pharmacy", row["pharmacy"])

                        c.metric(
                            "Distance",
                            f'{row["distance_km"]} km'
                            if row["distance_km"] is not None
                            else "Not provided"
                        )

                        st.write(f'**Address:** {row["address"]}')
                        st.write(f'**Contact:** {row["contact"]}')

                        # Google Maps navigation
                        if row["latitude"] is not None and row["longitude"] is not None:
                            destination = (
                                f'{float(row["latitude"])},{float(row["longitude"])}'
                            )
                        else:
                            destination = quote(row["address"] or row["pharmacy"])

                        maps_url = (
                            "https://www.google.com/maps/dir/?api=1"
                            f"&destination={destination}"
                        )

                        st.link_button(
                            "Navigate to pharmacy",
                            maps_url,
                            use_container_width=True
                        )

                        st.caption(f'Last updated: {row["last_updated"]}')


                        age_hours = (
                            (datetime.now() - row["last_updated"]).total_seconds() / 3600
                            if isinstance(row["last_updated"], datetime)
                            else 999999
                        )

                        if age_hours > 24:
                            st.warning(
                                "This stock record is older than 24 hours. "
                                "Call the pharmacy to confirm before travelling."
                            )
                        else:
                            st.info(
                                "Stock reported recently. "
                                "Please call the pharmacy to confirm before travelling."
                            )

                # Display pharmacist-verified alternatives only
                alt_rows = query_db("""
                    SELECT DISTINCT ma.verified_alternative, ma.generic_group
                    FROM alternative_mapping ma
                    JOIN medicine m ON m.medicine_id = ma.medicine_id
                    WHERE m.name LIKE %s
                      AND ma.review_status = 'verified'
                """, (like,))

                if alt_rows:
                    st.subheader("Pharmacist-verified alternatives")

                    for alt in alt_rows:
                        st.write(
                            f'• {alt["verified_alternative"]} '
                            f'({alt["generic_group"] or "same mapped group"})'
                        )

                    st.caption(
                        "Confirm any alternative with a registered pharmacist "
                        "and the prescribing clinician."
                    )

    st.divider()
    st.caption(
        "This prototype provides stock discovery only. "
        "It does not diagnose conditions or prescribe medicines."
    )

# ---------------- CHEMIST PORTAL ----------------
else:
    st.header("Chemist / Pharmacy Portal")
    if st.session_state.chemist is None:
        login_tab, register_tab = st.tabs(["Login", "Register pharmacy"])
        with login_tab:
            st.subheader("Chemist login")
            username = st.text_input("Username", key="login_user")
            password = st.text_input("Password", type="password", key="login_pass")
            if st.button("Login", type="primary"):
                users = query_db("""
                    SELECT u.user_id, u.username, u.password_hash, u.pharmacy_id,
                           p.name AS pharmacy_name
                    FROM app_user u JOIN pharmacy p ON p.pharmacy_id=u.pharmacy_id
                    WHERE u.username=%s
                """, (username.strip(),))
                if users and verify_password(password, users[0]["password_hash"]):
                    st.session_state.chemist = users[0]
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")
        with register_tab:
            st.subheader("Create pharmacy account")
            st.caption("Use a unique username. The account is linked to the pharmacy you register here.")
            with st.form("register_pharmacy"):
                pharmacy_name = st.text_input("Pharmacy name")
                address = st.text_input("Full address")
                contact = st.text_input("Phone number")
                latitude = st.text_input("Latitude (optional)")
                longitude = st.text_input("Longitude (optional)")
                new_username = st.text_input("Choose username")
                new_password = st.text_input("Choose password", type="password")
                confirm_password = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Register pharmacy")
                if submitted:
                    if not all([pharmacy_name.strip(), address.strip(), contact.strip(), new_username.strip(), new_password]):
                        st.error("Please fill all required fields.")
                    elif new_password != confirm_password:
                        st.error("Passwords do not match.")
                    elif len(new_password) < 8:
                        st.error("Use a password with at least 8 characters.")
                    else:
                        try:
                            lat = float(latitude) if latitude.strip() else None
                            lon = float(longitude) if longitude.strip() else None
                            conn = db_connection()
                            cur = conn.cursor()
                            cur.execute("""INSERT INTO pharmacy(name,address,contact,latitude,longitude)
                                           VALUES(%s,%s,%s,%s,%s)""",
                                        (pharmacy_name.strip(), address.strip(), contact.strip(), lat, lon))
                            pharmacy_id = cur.lastrowid
                            cur.execute("""INSERT INTO app_user(username,password_hash,pharmacy_id)
                                           VALUES(%s,%s,%s)""",
                                        (new_username.strip(), hash_password(new_password), pharmacy_id))
                            conn.commit()
                            st.success("Pharmacy registered. Please log in.")
                        except Error as e:
                            st.error(f"Could not register. Check whether username already exists. Details: {e}")
                        finally:
                            try:
                                cur.close()
                                conn.close()
                            except Exception:
                                pass
    else:
        chemist = st.session_state.chemist
        st.success(f'Logged in: {chemist["pharmacy_name"]}')
        if st.button("Logout"):
            st.session_state.chemist = None
            st.rerun()

        st.subheader("Upload stock photograph")
        st.write("Take a clear, straight photograph of a printed or handwritten stock list. Ensure medicine names and quantities are readable.")
        uploaded = st.file_uploader("Choose stock image", type=["png", "jpg", "jpeg"])
        if uploaded:
            image = Image.open(BytesIO(uploaded.getvalue())).convert("RGB")
            st.image(image, caption="Uploaded stock sheet", width=550)
            if st.button("Extract stock using OCR"):
                try:
                    raw = pytesseract.image_to_string(image, config="--psm 6")
                    st.session_state.ocr_raw = raw
                    st.session_state.ocr_rows = parse_stock_text(raw)
                    st.rerun()
                except Exception as e:
                    st.error("OCR could not run. Make sure Tesseract OCR is installed and configured. " + str(e))

        if "ocr_raw" in st.session_state:
            with st.expander("View extracted OCR text"):
                st.text(st.session_state.ocr_raw or "No text detected.")
        if "ocr_rows" in st.session_state:
            st.subheader("Review extracted stock before saving")
            st.warning("OCR can misread handwriting. Correct every row and quantity. Rows with quantity 0 need attention.")
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
                key="stock_editor"
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
                    conn = db_connection()
                    cur = conn.cursor()
                    try:
                        for _, item in clean.iterrows():
                            cur.execute("""
                                SELECT medicine_id FROM medicine
                                WHERE LOWER(name)=LOWER(%s) AND LOWER(strength)=LOWER(%s)
                                  AND LOWER(form)=LOWER(%s)
                                LIMIT 1
                            """, (item["medicine_name"], item["strength"], item["form"]))
                            found = cur.fetchone()
                            if found:
                                medicine_id = found[0]
                            else:
                                cur.execute("""INSERT INTO medicine(name,strength,form)
                                               VALUES(%s,%s,%s)""",
                                            (item["medicine_name"], item["strength"], item["form"]))
                                medicine_id = cur.lastrowid

                            cur.execute("""
                                INSERT INTO inventory(medicine_id,pharmacy_id,quantity,last_updated)
                                VALUES(%s,%s,%s,NOW())
                                ON DUPLICATE KEY UPDATE quantity=VALUES(quantity), last_updated=NOW()
                            """, (medicine_id, chemist["pharmacy_id"], int(item["quantity"])))
                        conn.commit()
                        st.success(f"Successfully updated {len(clean)} inventory row(s).")
                        del st.session_state["ocr_rows"]
                        st.session_state.pop("ocr_raw", None)
                    except Error as e:
                        conn.rollback()
                        st.error(f"Inventory was not updated: {e}")
                    finally:
                        cur.close()
                        conn.close()

        st.divider()
        st.subheader("My current inventory")
        inventory = query_db("""
            SELECT m.name, m.strength, m.form, i.quantity, i.last_updated
            FROM inventory i JOIN medicine m ON m.medicine_id=i.medicine_id
            WHERE i.pharmacy_id=%s ORDER BY m.name
        """, (chemist["pharmacy_id"],))
        if inventory:
            st.dataframe(pd.DataFrame(inventory), use_container_width=True, hide_index=True)
        else:
            st.info("No inventory uploaded yet.")

        st.subheader("Low-stock overview")
        low = [r for r in inventory if int(r["quantity"]) <= 5]
        st.metric("Items with 5 or fewer units", len(low))
        if low:
            st.dataframe(pd.DataFrame(low), use_container_width=True, hide_index=True)

    st.caption("Academic prototype • Always verify live stock by calling the pharmacy. Not a substitute for medical advice.")
