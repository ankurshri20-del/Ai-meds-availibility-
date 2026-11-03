AI MEDICINE AVAILABILITY & HEALTH ASSISTANT
Basic Python + Streamlit + MySQL prototype

WHAT IS INCLUDED
- Patient medicine search
- Pharmacy registration and chemist login
- Stock photo upload and OCR text extraction
- Editable OCR preview before saving
- MySQL medicine, pharmacy, inventory, alternative and consumption tables
- Nearby ranking when patient supplies latitude/longitude
- Low-stock overview for chemists

IMPORTANT
This is an academic prototype, not a production healthcare system.
OCR is not guaranteed to read every handwritten stock sheet correctly.
Always review the extracted rows before saving.
The app does not prescribe medicines or calculate dosages.
Only pharmacist/clinician-verified alternatives should be entered.
Patients should call pharmacies to confirm stock before travelling.

SETUP ON WINDOWS
1. Install Python 3.10 or newer.
2. Install MySQL Community Server and MySQL Workbench.
3. Install Tesseract OCR for Windows.
   Common install path:
   C:\Program Files\Tesseract-OCR\tesseract.exe
4. Open MySQL Workbench, connect to your local MySQL server.
5. Open schema.sql and execute the whole script (lightning icon).
6. Open this project folder in VS Code.
7. Open Terminal in this folder and run:
      py -m venv .venv
      .venv\Scripts\activate
      pip install -r requirements.txt
8. Copy .env.example to a new file named .env.
9. Edit .env and enter your MySQL password.
10. If Tesseract is not in PATH, add these lines near the top of app.py,
    after importing pytesseract:
      pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
11. Run:
      streamlit run app.py
12. Your browser should open the local application.

HOW TO TEST
1. Open Chemist Portal > Register pharmacy.
2. Create a pharmacy account and log in.
3. Upload a clear stock image.
4. Click Extract stock using OCR.
5. Correct the extracted medicine names, strengths, forms and quantities.
6. Click Confirm and update my inventory.
7. Open Patient Search and search for the medicine.

MYSQL CONNECTION
The Python app connects using mysql.connector and the .env settings.
Never publish your real database password or upload .env to GitHub.

DATABASE DESIGN
- medicine: master medicine catalogue
- pharmacy: chemist/shop details and optional coordinates
- inventory: quantity of each medicine at each pharmacy
- alternative_mapping: pharmacist-reviewed alternative records
- consumption_history: historical dispensed quantities for future demand analysis
- app_user: chemist login accounts linked to a pharmacy

PHOTO OCR LIMITATION
This starter version uses Tesseract OCR plus a simple line parser. It works best
with clear printed tables or neat handwriting where each line ends in a quantity.
Different stock-register layouts may need a more advanced table extraction model.
For real deployment, use a review/approval workflow and audit logs.
