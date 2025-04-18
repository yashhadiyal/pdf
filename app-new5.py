import os
import fitz  # PyMuPDF
import re
import uuid
from flask import Flask, render_template, request, send_file, redirect, url_for
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)
BASE_UPLOAD_FOLDER = "pdf"
STATIC_FONT_FOLDER = "static/fonts"

os.makedirs(BASE_UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FONT_FOLDER, exist_ok=True)

app.config["ALLOWED_EXTENSIONS"] = {"pdf", "ttf", "otf"}

def extract_bill_no(text):
    match = re.search(r"[A-Z]{2,}[0-9]*\/?\d*(?:-\d+)?", text.upper())
    return match.group(0) if match else ""

def normalize_amount(amount):
    gujarati_digits = "૦૧૨૩૪૫૬૭૮૯"
    hindi = "०१२३४५६७८९"
    eng_digits = "0123456789"
    trans = str.maketrans(gujarati_digits, hindi, eng_digits)
    return re.sub(r"[^\d.]", "", amount.translate(trans))

def parse_entry(text):
    gujarati_digits = "૦૧૨૩૪૫૬૭૮૯"
    eng_digits = "0123456789"
    trans = str.maketrans(gujarati_digits, eng_digits)
    text = text.translate(trans)

    amount_match = re.search(r"([\d,]+\.\d{2})", text)
    date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)

    if amount_match:
        amount = amount_match.group(1).replace(",", "")
        date = date_match.group(1) if date_match else ""
        return {"Amount": amount, "Date": date, "Raw": text}
    return None

def extract_data(pdf_path):
    doc = fitz.open(pdf_path)
    credit, debit = [], []
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        mid_x = page.rect.width / 2
        for block in blocks:
            for line in block.get("lines", []):
                spans = [span["text"].strip() for span in line["spans"] if span["text"].strip()]
                text = " ".join(spans)
                if not text:
                    continue
                x = line["bbox"][0]
                entry = parse_entry(text)
                if entry:
                    (credit if x < mid_x else debit).append(entry)
    return credit, debit

def compare_entries(party1, party2, criteria):
    results = []
    matched_indices = set()

    def parse_date_safe(date_str):
        try:
            return datetime.strptime(date_str, "%d/%m/%Y")
        except Exception:
            return None

    def is_date_match(d1_str, d2_str):
        d1 = parse_date_safe(d1_str)
        d2 = parse_date_safe(d2_str)
        if d1 and d2:
            return abs((d1 - d2).days) <= 1
        return False

    def is_match(e1, e2):
        if e1["Amount"] != e2["Amount"]:
            return False
        if "Date" in criteria and not is_date_match(e1["Date"], e2["Date"]):
            return False
        if "BillNo" in criteria and extract_bill_no(e1["Raw"]) != extract_bill_no(e2["Raw"]):
            return False
        return True

    for i, e1 in enumerate(party1):
        match = None
        for j, e2 in enumerate(party2):
            if j in matched_indices:
                continue
            if is_match(e1, e2):
                match = e2
                matched_indices.add(j)
                break
        results.append({
            "status": "✅ Match" if match else "❌ No Match",
            "Party1_Amount": e1["Amount"],
            "Party2_Amount": match["Amount"] if match else "",
            "Party1_Date": e1["Date"],
            "Party2_Date": match["Date"] if match else "",
            "Party1_Credit": e1["Raw"],
            "Party2_Debit": match["Raw"] if match else ""
        })

    for idx, e2 in enumerate(party2):
        if idx not in matched_indices:
            results.append({
                "status": "❌ No Match",
                "Party1_Amount": "",
                "Party2_Amount": e2["Amount"],
                "Party1_Date": "",
                "Party2_Date": e2["Date"],
                "Party1_Credit": "",
                "Party2_Debit": e2["Raw"]
            })
    return results

def generate_html_table(all_results, font_url_path, output_path):
    font_face = f"""
    @font-face {{
        font-family: 'CustomFont';
        src: url('{font_url_path}');
    }}""" if font_url_path else ""

    html = f"""
    <html>
    <head>
        <title>Comparison Report</title>
        <style>
            {font_face}
            body {{
                font-family: {'CustomFont' if font_url_path else 'Arial'}, sans-serif;
                background-color: #f5f5f5;
                padding: 20px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background-color: white;
                margin-bottom: 30px;
                border-radius: 8px;
                overflow: hidden;
                box-shadow: 0 0 10px rgba(0,0,0,0.1);
            }}
            th, td {{
                border: 1px solid #ddd;
                padding: 8px 12px;
                text-align: left;
            }}
            th {{
                background-color: #343a40;
                color: white;
            }}
            .match {{
                background-color: #d4edda;
            }}
            .mismatch {{
                background-color: #f8d7da;
            }}
            h3 {{
                margin-top: 40px;
            }}
        </style>
    </head>
    <body>
        <h2>📄 Comparison Report</h2>
    """

    for section in all_results:
        html += f"<h3>{section['title']}</h3><table>"
        html += """
            <tr>
                <th>Status</th>
                <th>Party1 Amount</th>
                <th>Party2 Amount</th>
                <th>Party1 Date</th>
                <th>Party2 Date</th>
                <th>Party1 Entry</th>
                <th>Party2 Entry</th>
            </tr>
        """
        for row in section["rows"]:
            cls = "match" if row["status"].startswith("✅") else "mismatch"
            html += f"""
                <tr class="{cls}">
                    <td>{row['status']}</td>
                    <td>{row['Party1_Amount']}</td>
                    <td>{row['Party2_Amount']}</td>
                    <td>{row['Party1_Date']}</td>
                    <td>{row['Party2_Date']}</td>
                    <td>{row['Party1_Credit']}</td>
                    <td>{row['Party2_Debit']}</td>
                </tr>
            """
        html += "</table>"

    html += "</body></html>"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file1 = request.files.get("file1")
        file2 = request.files.get("file2")
        font = request.files.get("font")
        selected_criteria = request.form.getlist("criteria")

        if not file1 or not file2:
            return "❌ Please upload both PDF files.", 400

        today = datetime.today().strftime("%Y-%m-%d")
        unique_id = str(uuid.uuid4())
        session_folder = os.path.join(BASE_UPLOAD_FOLDER, today, unique_id)
        os.makedirs(session_folder, exist_ok=True)

        font_url_path = None
        if font and font.filename:
            font_filename = secure_filename(font.filename)
            static_font_path = os.path.join(STATIC_FONT_FOLDER, font_filename)
            font.save(static_font_path)
            font_url_path = f"/static/fonts/{font_filename}"

        file1_path = os.path.join(session_folder, secure_filename(file1.filename))
        file2_path = os.path.join(session_folder, secure_filename(file2.filename))
        file1.save(file1_path)
        file2.save(file2_path)

        p1_credit, p1_debit = extract_data(file1_path)
        p2_credit, p2_debit = extract_data(file2_path)

        all_results = []
        result_cd = compare_entries(p1_credit, p2_debit, selected_criteria)
        if result_cd:
            all_results.append({"title": "➡️ Party 1 Credit vs Party 2 Debit", "rows": result_cd})

        result_dc = compare_entries(p1_debit, p2_credit, selected_criteria)
        if result_dc:
            all_results.append({"title": "➡️ Party 1 Debit vs Party 2 Credit", "rows": result_dc})

        result_path = os.path.join(session_folder, "result.html")
        generate_html_table(all_results, font_url_path, result_path)

        return redirect(url_for("view_result", folder=today, session_id=unique_id))

    return render_template("upload.html")

@app.route("/view/<folder>/<session_id>")
def view_result(folder, session_id):
    result_path = os.path.join(BASE_UPLOAD_FOLDER, folder, session_id, "result.html")
    if not os.path.exists(result_path):
        return "❌ Result not found.", 404
    with open(result_path, encoding="utf-8") as f:
        return f.read()

@app.route("/download/<folder>/<session_id>")
def download_result(folder, session_id):
    result_path = os.path.join(BASE_UPLOAD_FOLDER, folder, session_id, "result.html")
    if os.path.exists(result_path):
        return send_file(result_path, as_attachment=True)
    return "❌ Result not found.", 404

if __name__ == "__main__":
    app.run(debug=True)
