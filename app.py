from flask import Flask, request, redirect, url_for, render_template, send_from_directory, jsonify, flash
import os
from werkzeug.utils import secure_filename
import openai
import base64
import shutil
import csv

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
UPLOAD_FOLDER = 'trips'
REPORT_FILE_NAME = 'reimbursement.csv'
client = openai.OpenAI()

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
currently_generating = set()

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        trip_name = request.form['trip_name']
        files = request.files.getlist('file')
        
        trip_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(trip_name))
        if os.path.exists(trip_path):
            error_message = f"Trip '{trip_name}' already exists. Please choose a different name."
            return render_template('upload.html', error=error_message)
        
        os.makedirs(trip_path, exist_ok=True)

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file.save(os.path.join(trip_path, filename))

        return redirect(url_for('view_trips'))

    return render_template('upload.html')

@app.route('/trips')
def view_trips():
    trips = {}
    for trip_name in os.listdir(UPLOAD_FOLDER):
        path = os.path.join(UPLOAD_FOLDER, trip_name)
        if os.path.isdir(path):
            trips[trip_name] = os.listdir(path)
    return render_template('trips.html', trips=trips)

@app.route('/trips/<trip_name>/<filename>')
def uploaded_file(trip_name, filename):
    return send_from_directory(os.path.join(app.config['UPLOAD_FOLDER'], trip_name), filename)

@app.route('/api/generate-reimbursement/<trip_name>', methods=['POST'])
def generate_reimbursement(trip_name):
    if trip_name in currently_generating:
        return jsonify({'message': 'Report is already being generated, please wait and try later'})
    
    trip_path = os.path.join(UPLOAD_FOLDER, trip_name)
    reimbursement_path = os.path.join(trip_path, REPORT_FILE_NAME)

    
    if os.path.exists(reimbursement_path):
        return jsonify({'message': 'Reimbursement CSV already exists.'})

    try:
        currently_generating.add(trip_name)

        input_payload = []

        for filename in os.listdir(trip_path):
            filepath = os.path.join(trip_path, filename)
            if os.path.isfile(filepath) and allowed_file(filename):
                ext = filename.rsplit('.', 1)[1].lower()
                
                if ext == 'pdf':
                    with open(filepath, 'rb') as f:
                        pdf_file = client.files.create(
                            file=(filename, f),
                            purpose="user_data"
                        )
                        input_payload.append({"type": "input_text", "text": f"The following pdf file is named: {filename}, this file has an expense record."})
                        input_payload.append({
                            "type": "input_file",
                            "file_id": pdf_file.id
                        })

                elif ext in ['jpg', 'jpeg', 'png']:
                    with open(filepath, 'rb') as image_file:
                        base64_image = base64.b64encode(image_file.read()).decode("utf-8")
                        input_payload.append({"type": "input_text", "text": f"The next image is named: {filename}, this file has an expense record."})
                        input_payload.append({
                            "type": "input_image",
                            "image_url": f"data:image/{ext};base64,{base64_image}"
                        })

        if not input_payload:
            return jsonify({'error': 'No valid files to process'}), 400

        CSV_HEADER = 'Category|Item|Location|Date & time|Price|File name|Currency'
        # Construct the prompt
        prompt_text = (
            "Go over the attached travel-related files and generate a CSV summarizing expenses. "
            "The CSV format must be: Category,item,location,Date & time,Price,file name,currency. "
            "Categories allowed: Air travel, ground transport, accommodation, food, others. "
            "Provide only the CSV text as output with | as the delimiter. Do not include anything else in the response."
            "Your response should be of the format {}. Generate one row for every file I have uploaded".format(CSV_HEADER)
        )

        # Combine all inputs
        input_payload.append({
            "type": "input_text",
            "text": prompt_text
        })

        response = client.responses.create(
            model="gpt-4o",
            input=[
                {
                    "role": "user",
                    "content": input_payload
                }
            ]
        )
        
        # raw_path = os.path.join(trip_path, 'raw.csv')
        # with open(raw_path, 'w') as raw_file:
        #     raw_file.write(response.output_text)
        
        # Save response to CSV
        with open(reimbursement_path, 'w') as f:
            f.write(CSV_HEADER)
            f.write('\n')
            lines = response.output_text.splitlines()
            for line in lines:
                if '|' in line:
                    f.write(line.strip())
                    f.write('\n')
        
        currently_generating.remove(trip_name)

        return jsonify({'message': 'Reimbursement CSV generated.'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/trips/<trip_name>/report')
def show_report(trip_name):
    report_path = os.path.join('trips', trip_name, 'reimbursement.csv')
    report_data = []
    if os.path.isfile(report_path):
        with open(report_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile, delimiter='|')
            for row in reader:
                report_data.append(row)
    return render_template("report.html", trip_name=trip_name, report=report_data)


@app.route('/api/delete-trip/<trip_name>', methods=['DELETE'])
def delete_trip(trip_name):
    trip_path = os.path.join('trips', trip_name)
    if os.path.isdir(trip_path):
        shutil.rmtree(trip_path)
        return jsonify({"message": f"Trip '{trip_name}' deleted."})
    return jsonify({"message": f"Trip '{trip_name}' not found."}), 404

@app.route('/api/delete-report/<trip_name>', methods=['DELETE'])
def delete_report(trip_name):
    report_path = os.path.join('trips', trip_name, REPORT_FILE_NAME)
    if os.path.isfile(report_path):
        os.remove(report_path)
        return jsonify({"message": "Report deleted."})
    return jsonify({"message": "Report not found."}), 404

@app.route('/api/delete-file/<trip>/<filename>', methods=['DELETE'])
def delete_file(trip, filename):
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], trip, filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        return jsonify({"message": f"File '{filename}' deleted from trip '{trip}'."})
    return jsonify({"message": "File not found."}), 404

@app.route('/trips/<trip>/add-files', methods=['POST'])
def add_files_to_trip(trip):
    files = request.files.getlist('new_files')
    trip_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(trip))
    os.makedirs(trip_path, exist_ok=True)

    for file in files:
        if file:
            filename = secure_filename(file.filename)
            file.save(os.path.join(trip_path, filename))
    return redirect(url_for('show_trips'))  # Adjust this if your main trip page uses a different route

if __name__ == '__main__':
    app.run(debug=True)