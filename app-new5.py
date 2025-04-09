import os
import fitz
import re
from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
STATIC_FONT_FOLDER = "static/fonts"
RESULT_FILE = "static/result2.html"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FONT_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["ALLOWED_EXTENSIONS"] = {"pdf", "ttf", "otf"}

font_url_path = None


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

    def is_match(e1, e2):
        for field in criteria:
            if field == "Amount" and e1["Amount"] != e2["Amount"]:
                return False
            if field == "Date" and e1["Date"] != e2["Date"]:
                return False
            if field == "BillNo" and extract_bill_no(e1["Raw"]) != extract_bill_no(e2["Raw"]):
                return False
        return True

    # Compare and collect matched/unmatched from Party1
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

    # Add unmatched Party2 entries that were not matched
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


def generate_html_table(all_results):
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

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        f.write(html)



@app.route("/", methods=["GET", "POST"])
def index():
    global font_url_path

    if request.method == "POST":
        file1 = request.files.get("file1")
        file2 = request.files.get("file2")
        font = request.files.get("font")
        selected_criteria = request.form.getlist("criteria")

        if not file1 or not file2:
            return "❌ Please upload both PDF files.", 400

        if font and font.filename:
            font_filename = secure_filename(font.filename)
            static_font_path = os.path.join(STATIC_FONT_FOLDER, font_filename)
            font.save(static_font_path)
            font_url_path = f"/static/fonts/{font_filename}"

        file1_path = os.path.join(UPLOAD_FOLDER, secure_filename(file1.filename))
        file2_path = os.path.join(UPLOAD_FOLDER, secure_filename(file2.filename))
        file1.save(file1_path)
        file2.save(file2_path)

        # Extract all data
        p1_credit, p1_debit = extract_data(file1_path)
        p2_credit, p2_debit = extract_data(file2_path)

        all_results = []

        # Comparison 1: Party1 Credit vs Party2 Debit
        result_cd = compare_entries(p1_credit, p2_debit, selected_criteria)
        if result_cd:
            all_results.append({"title": "➡️ Party 1 Credit vs Party 2 Debit", "rows": result_cd})

        # Comparison 2: Party1 Debit vs Party2 Credit
        result_dc = compare_entries(p1_debit, p2_credit, selected_criteria)
        if result_dc:
            all_results.append({"title": "➡️ Party 1 Debit vs Party 2 Credit", "rows": result_dc})

        generate_html_table(all_results)
        return render_template("result2.html")

    return render_template("upload.html")


@app.route("/download")
def download():
    return send_file(RESULT_FILE, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
