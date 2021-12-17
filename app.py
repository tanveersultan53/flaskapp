from flask import Flask, jsonify, render_template
from flask import request
from flask import abort
import pathlib
import os
import subprocess
import json


class PDFParserError(Exception):
    pass


class InvalidOptionError(Exception):
    pass


class PDFParser:

    def __init__(self, tmp_path=None, clean_up=True):
        self.TEMP_FOLDER_PATH = tmp_path
        self._tmp_files = []
        self.clean_up = clean_up
        self.PDFPARSER_PATH = os.environ.get('PDFPARSER_PATH', 'pdfparser.jar')

    def _coerce_to_file_path(self, path_or_file_or_bytes):
        """This converts file-like objects and `bytes` into
        existing files and returns a filepath
        if strings are passed in, it is assumed that they are existing
        files
        """
        if not isinstance(path_or_file_or_bytes, str):
            if isinstance(path_or_file_or_bytes, bytes):
                return self._write_tmp_file(
                    bytestring=path_or_file_or_bytes)
            else:
                return self._write_tmp_file(
                    file_obj=path_or_file_or_bytes)
        return path_or_file_or_bytes

    def _write_tmp_file(self, file_obj=None, bytestring=None):
        """Take a file-like object or a bytestring,
        create a temporary file and return a file path.
        file-like objects will be read and written to the tempfile
        bytes objects will be written directly to the tempfile
        """
        tmp_path = self.TEMP_FOLDER_PATH
        # os_int, tmp_fp = mkstemp(dir=tmp_path)
        with open(tmp_path, 'wb') as tmp_file:
            if file_obj:
                tmp_file.write(file_obj.read())
            elif bytestring:
                tmp_file.write(bytestring)
        self._tmp_files.append(tmp_path)
        return tmp_path

    def _load_json(self, raw_string):
        return json.loads(raw_string)

    def _dump_json(self, data):
        return json.dumps(data)

    def clean_up_tmp_files(self):
        if not self._tmp_files:
            return
        for i in range(len(self._tmp_files)):
            path = self._tmp_files.pop()
            os.remove(path)

    def _get_name_option_lookup(self, field_data):
        return {
            item['name']: item.get('options', None)
            for item in field_data['fields']
        }

    def _get_file_contents(self, path):
        """given a file path, return the contents of the file
        if decode is True, the contents will be decoded using the default
        encoding
        """
        return open(path, 'rb').read()

    def run_command(self, args):
        """Run a command to pdftk on the command line.
            `args` is a list of command line arguments.
        This method is reponsible for handling errors that arise from
        pdftk's CLI
        """
        args = ['java', '-jar', self.PDFPARSER_PATH] + args
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        out, err = process.communicate()
        if err:
            raise PDFParserError(err.decode('utf-8'))
        return out.decode('unicode_escape')

    def _fill(self, pdf_path, output_path, option_check, answers):
        answer_fields = {'fields': []}
        for k, v in answers.items():
            if k in option_check:
                if option_check[k]:
                    if v not in option_check[k]:
                        raise InvalidOptionError(
                            "''{}' is not a valid option for '{}'. Choices: {}".format(
                                v, k, option_check[k]
                                ))
            answer_fields['fields'].append({k: v})
        self.run_command([
            'set_fields',
            pdf_path,
            output_path,
            self._dump_json(answer_fields)
        ])

    def join_pdfs(self, list_of_pdf_paths):
        paths = [self._coerce_to_file_path(p) for p in list_of_pdf_paths]
        output_path = self._write_tmp_file()
        args = ['concat_files'] + paths + [output_path]
        self.run_command(args)
        result = self._get_file_contents(output_path)
        if self.clean_up:
            self.clean_up_tmp_files()
        return result

    def get_field_data(self, pdf_file_path):
        pdf_file_path = self._coerce_to_file_path(pdf_file_path)
        string = self.run_command(['get_fields', pdf_file_path])
        return self._load_json(string)

    def fill_pdf(self, pdf_path, answers):
        pdf_path = self._coerce_to_file_path(pdf_path)
        field_data = self.get_field_data(pdf_path)
        option_check = self._get_name_option_lookup(field_data)
        output_path = self._write_tmp_file()
        self._fill(pdf_path, output_path, option_check, answers)
        result = self._get_file_contents(output_path)
        if self.clean_up:
            self.clean_up_tmp_files()
        return result

    def fill_many_pdfs(self, pdf_path, answers_list):

        # don't clean up while filling multiple pdfs
        _clean_up_setting = self.clean_up
        self.clean_up = False

        pdf_path = self._coerce_to_file_path(pdf_path)
        field_data = self.get_field_data(pdf_path)
        option_check = self._get_name_option_lookup(field_data)
        tmp_filled_pdf_paths = []
        for answers in answers_list:
            output_path = self._write_tmp_file()
            self._fill(pdf_path, output_path, option_check, answers)
            tmp_filled_pdf_paths.append(output_path)
        self.clean_up = _clean_up_setting
        return self.join_pdfs(tmp_filled_pdf_paths)


app = Flask(__name__)


@app.route('/', methods=['GET'])
def show_html():
    rootdir = pathlib.Path(parent_directory)
    file_list = [f.name for f in rootdir.glob('**/*') if f.is_file() and f.name.endswith(".pdf")]
    print(file_list)
    return render_template('index.html', data=file_list)


@app.route('/submit', methods=['POST'])
def submit_form():
    if not request.json or len(request.json) < 0:
        abort(400)
    json_obj = request.get_json()
    print(json_obj)
    data_to_replace_in_pdf = json_obj['courseInfo']
    assisting_instructors = json_obj['assistingInstructors']
    for key, value in assisting_instructors.items():
        if "assis-name" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Name-Instructor ID"
            else:
                key = "Name-Instructor ID " + str(int(key)+1)
        else:
            key = key.split('-')[-1]
            if key == "0":
                key = "Card Exp Date"
            else:
                key = "Card Exp Date " + str(int(key) + 1)
        data_to_replace_in_pdf[key] = value

    course_participants = json_obj['courseParticipants']
    data_to_replace_in_pdf.update(course_participants)
    # this is mainly for BLS
    data_to_replace_in_pdf['Signature'] = data_to_replace_in_pdf['Lead Instructor Signature']
    data_to_replace_in_pdf['Date 2'] = data_to_replace_in_pdf['Date']
    data_to_replace_in_pdf['Lead Instructor 2'] = data_to_replace_in_pdf['Lead Instructor']
    data_to_replace_in_pdf['Lead Instructor ID# 2'] = data_to_replace_in_pdf['Lead Instructor ID#']

    for k in range(13):
        data_to_replace_in_pdf['Student Name '+str(k)] = data_to_replace_in_pdf['Student Name']
        data_to_replace_in_pdf['Date of Test '+str(k)] = data_to_replace_in_pdf['Date of Test']
        data_to_replace_in_pdf['Instructor Initials '+str(k)] = data_to_replace_in_pdf['Instructor Initials']
        data_to_replace_in_pdf['Instructor Number '+str(k)] = data_to_replace_in_pdf['Instructor Number']
        data_to_replace_in_pdf['Date '+str(k)] = data_to_replace_in_pdf['Date']

    course_participants1 = json_obj['courseParticipants1']
    i = 1
    for key, value in course_participants1.items():
        if "cp-name" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Name"
            else:
                key = "Name " + str(int(i)+1)
        elif "cp-mailing" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Mailing Address"
            else:
                key = "Mailing Address " + str(int(i)+1)
        elif "cp-email" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Email"
            else:
                key = "Email " + str(int(i)+1)
        elif "cp-phone" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Telephone"
            else:
                key = "Telephone " + str(int(i)+1)
        elif "cp-psa" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "PSA"
            else:
                key = "PSA " + str(int(i)+1)
        elif "cp-comp-imcomp" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Complete-Incomplete"
            else:
                key = "Complete-Incomplete " + str(int(i)+1)
        elif "cp-remed" in key:
            key = key.split('-')[-1]
            if key == "0":
                key = "Remediation"
            else:
                key = "Remediation " + str(int(i)+1)
        data_to_replace_in_pdf[key] = value
    selected_options = json_obj['selectedOptions']
    pdf_path = None
    for each_selected in selected_options:
        pdf_path = each_selected
        # data_to_replace_in_pdf[each_selected] = "Yes"
    # print(json.dumps(data_to_replace_in_pdf))

    obj = PDFParser(tmp_path="k.pdf", clean_up=False)

    rootdir = pathlib.Path(parent_directory)
    file_list = [f for f in rootdir.glob('**/*') if f.is_file() and f.name.endswith(".pdf")]
    for x in file_list:
        if x.name == pdf_path:
            pdf_path = str(x)
            print(pdf_path)

    data = obj.get_field_data(pdf_path)
    print(json.dumps(data))

    new_pdf = obj.fill_pdf(pdf_path, data_to_replace_in_pdf)
    new_pdf = obj._write_tmp_file(bytestring=new_pdf)
    return jsonify({"status": "success", "filepath": new_pdf})


if __name__ == '__main__':
    os.environ.setdefault("PDFPARSER_PATH", "pdfparser.jar")
    output_file_path = "k.pdf"
    parent_directory = "./"
    app.run(host='0.0.0.0', debug=True, port=5000, threaded=True)

