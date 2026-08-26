import os
import io, csv
from datetime import datetime, date
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, send_file, make_response, abort, Response)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
from xhtml2pdf import pisa
from models import (db, User, Student, Subject, Exam, StudentSubject,
                    Result, Promotion, StudentSubjectRegistration)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-this-in-production'
port = int(os.environ.get('PORT', 5000))

import os
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
else:
    raise RuntimeError("DATABASE_URL environment variable is not set!")
    
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
print(f"Database: PostgreSQL connected")
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# -------------------------------------------------------------------
# PDF Helper
# -------------------------------------------------------------------
def render_pdf(html_string):
    result = io.BytesIO()
    pisa.CreatePDF(io.StringIO(html_string), dest=result)
    result.seek(0)
    return result

# -------------------------------------------------------------------
# Subject Helpers
# -------------------------------------------------------------------
def get_compulsory_subjects(curriculum, level='O'):
    """Get compulsory subject codes based on curriculum and level."""
    if level == 'A':
        return []
    
    if curriculum == 'new':
        return ['HTM', 'KISW', 'GEO', 'ENG', 'MATH', 'B/STUDY']
    else:  # old curriculum
        return ['CIV', 'KISW', 'GEO', 'ENG', 'MATH', 'HIST', 'BIO']


def get_optional_subjects(curriculum, level='O'):
    """Get optional subject codes based on curriculum."""
    if level == 'A':
        return []
    
    if curriculum == 'new':
        return ['PHY', 'CHEM', 'BIO', 'HIST', 'ICT']
    else:  # old curriculum
        return ['PHY', 'CHEM', 'LIT/ENG']

def get_combination_subjects(comb_code):
    mapping = {
        'PCM': ['PHY', 'CHEM', 'ADV/MATHS'],
        'PCB': ['PHY', 'CHEM', 'BIO'],
        'CBG': ['CHEM', 'BIO', 'GEO'],
        'HGK': ['HIST', 'GEO', 'KISW'],
        'HKL': ['HIST', 'KISW', 'ENG'],
        'HGL': ['HIST', 'GEO', 'ENG'],
    }
    return mapping.get(comb_code, [])

def get_subsidiary_subjects(comb_code):
    subsidiary = ['A/C', 'HTM']
    if comb_code in ['PCB', 'CBG']:
        subsidiary.append('BAM')
    return subsidiary

def get_subjects_for_exam(exam):
    cls = exam.target_class
    level = 'A' if cls in ['Form5', 'Form6'] else 'O'
    return Subject.query.filter_by(level=level).order_by(Subject.necta_code).all()

def get_subjects_for_class(class_name):
    level = 'A' if class_name in ['Form5', 'Form6'] else 'O'
    return Subject.query.filter_by(level=level).order_by(Subject.necta_code).all()

# -------------------------------------------------------------------
# Grading
# -------------------------------------------------------------------
def o_level_grade_points(marks):
    if marks is None: return None, None
    if marks >= 75: return 'A', 1
    elif marks >= 65: return 'B', 2
    elif marks >= 45: return 'C', 3
    elif marks >= 30: return 'D', 4
    else: return 'F', 5

def a_level_grade_points(marks):
    if marks is None: return None, None
    if marks >= 80: return 'A', 1
    elif marks >= 70: return 'B', 2
    elif marks >= 60: return 'C', 3
    elif marks >= 50: return 'D', 4
    elif marks >= 40: return 'E', 5
    elif marks >= 30: return 'S', 6
    else: return 'F', 7

def process_student_results(student_id, exam_id):
    student = Student.query.get(student_id)
    exam = Exam.query.get(exam_id)
    if not student or not exam:
        return
    
    level = 'A' if student.current_class in ['Form5', 'Form6'] else 'O'
    records = StudentSubject.query.filter_by(student_id=student_id, exam_id=exam_id).all()

    # Update grades and points for all subjects
    for rec in records:
        if rec.marks is not None:
            if level == 'O':
                g, p = o_level_grade_points(rec.marks)
            else:
                g, p = a_level_grade_points(rec.marks)
            rec.grade = g
            rec.points = p
        else:
            rec.grade = rec.points = None

    if level == 'O':
        # Get compulsory subjects
        if student.curriculum == 'new':
            compulsory_codes = get_compulsory_subjects('new', 'O')
        else:
            compulsory_codes = get_compulsory_subjects('old', 'O')
        
        # Check if student has ALL compulsory subjects with marks
        compulsory_records = [r for r in records if r.subject.code in compulsory_codes]
        has_all_compulsory = all(r.marks is not None for r in compulsory_records) if compulsory_records else False
        
        # Check if student has NO marks at all
        has_any_marks = any(r.marks is not None for r in records)
        
        if not has_any_marks:
            agg, division = None, 'ABS'
        else:
            scored = [r for r in records if r.points is not None]
            scored_sorted = sorted(scored, key=lambda r: r.points)[:7]
            
            if len(scored_sorted) < 7:
                agg, division = None, 'INC'
            else:
                agg = sum(r.points for r in scored_sorted)
                if 7 <= agg <= 17:
                    division = 'I'
                elif 18 <= agg <= 21:
                    division = 'II'
                elif 22 <= agg <= 25:
                    division = 'III'
                elif 26 <= agg <= 33:
                    division = 'IV'
                else:
                    division = '0'
    else:
        comb_subj = get_combination_subjects(student.combination)
        comb_recs = [r for r in records if r.subject.code in comb_subj]
        
        # Check if student has NO marks at all
        has_any_marks = any(r.marks is not None for r in records)
        
        if not has_any_marks:
            agg, division = None, 'ABS'
        elif len(comb_recs) == 3 and all(r.points is not None for r in comb_recs):
            agg = sum(r.points for r in comb_recs)
            if 3 <= agg <= 9:
                division = 'I'
            elif 10 <= agg <= 12:
                division = 'II'
            elif 13 <= agg <= 17:
                division = 'III'
            elif 18 <= agg <= 19:
                division = 'IV'
            else:
                division = '0'
        else:
            agg, division = None, 'INC'

    # Save result
    result = Result.query.filter_by(student_id=student_id, exam_id=exam_id).first()
    if not result:
        result = Result(student_id=student_id, exam_id=exam_id)
        db.session.add(result)
    result.agg = agg
    result.division = division
    db.session.commit()


# -------------------------------------------------------------------
# Display Helpers
# -------------------------------------------------------------------
def get_full_name(student):
    parts = []
    if student.first_name: parts.append(student.first_name)
    if student.middle_name: parts.append(student.middle_name)
    if student.last_name: parts.append(student.last_name)
    return ' '.join(parts)

def get_class_number(class_name):
    mapping = {
        'Form1': 'FORM ONE', 'Form2': 'FORM TWO', 'Form3': 'FORM THREE',
        'Form4': 'FORM FOUR', 'Form5': 'FORM FIVE', 'Form6': 'FORM SIX'
    }
    return mapping.get(class_name, class_name.upper())

def get_exam_type_label(exam_type):
    mapping = {
        'monthly': 'MONTHLY TEST', 'midterm': 'MID TERM EXAMINATION',
        'terminal': 'TERMINAL EXAMINATION', 'pre-mock': 'PRE-MOCK EXAMINATION',
        'mock': 'MOCK EXAMINATION', 'prenecta': 'PRE-NECTA EXAMINATION',
        'annual': 'ANNUAL EXAMINATION'
    }
    return mapping.get(exam_type, exam_type.upper())

def get_exam_types_list():
    return [
        ('monthly', 'MONTHLY TEST'), ('midterm', 'MID TERM EXAMINATION'),
        ('terminal', 'TERMINAL EXAMINATION'), ('pre-mock', 'PRE-MOCK EXAMINATION'),
        ('mock', 'MOCK EXAMINATION'), ('prenecta', 'PRE-NECTA EXAMINATION'),
        ('annual', 'ANNUAL EXAMINATION')
    ]

def get_months_list():
    return [('01','January'),('02','February'),('03','March'),('04','April'),
            ('05','May'),('06','June'),('07','July'),('08','August'),
            ('09','September'),('10','October'),('11','November'),('12','December')]

def get_next_class(current_class):
    mapping = {'Form1':'Form2','Form2':'Form3','Form3':'Form4','Form4':'Graduate',
               'Form5':'Form6','Form6':'Graduate'}
    return mapping.get(current_class, current_class)

def get_subject_short_name(code):
    mapping = {
        'HTM':'HTM','KISW':'KISW','GEO':'GEO','ENG':'ENG','MATH':'MATH',
        'B/STUDY':'B/STUD','PHY':'PHY','CHEM':'CHEM','BIO':'BIO','HIST':'HIST',
        'ICT':'ICT','CIV':'CIV','LIT/ENG':'LIT/ENG','ADV/MATHS':'ADV/MATH',
        'A/C':'A/C','BAM':'BAM'
    }
    return mapping.get(code, code)

def format_detailed_subjects(student_id, exam_id, level='O'):
    records = StudentSubject.query.filter_by(student_id=student_id, exam_id=exam_id).order_by(StudentSubject.id).all()
    parts = []
    for rec in records:
        if rec.grade:
            short_name = get_subject_short_name(rec.subject.code)
            parts.append(f"{short_name} - '{rec.grade}'")
    return ' '.join(parts) if parts else ''

def calculate_subject_gpa(grade_dist, level='O'):
    """Calculate Subject GPA. Returns blank if no students sat."""
    if level == 'O':
        weights = {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'F': 5}
    else:
        weights = {'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'S': 6, 'F': 7}
    
    total_weighted = sum(weights.get(g, 0) * grade_dist.get(g, 0) for g in weights)
    total_sat = sum(grade_dist.values())
    
    if total_sat == 0:
        return None  # Return None instead of 0 for no students
    
    return round(total_weighted / total_sat, 2)

def get_competence_level(gpa, level='O'):
    """Return (label, color). Returns ('', '') if no GPA."""
    if gpa is None:
        return ('', '')
    
    if level == 'O':
        if gpa <= 1.4:
            return ('A - Excellent', '#006400')
        elif gpa <= 2.4:
            return ('B - Very Good', '#90EE90')
        elif gpa <= 3.4:
            return ('C - Good', '#FFFF00')
        elif gpa <= 4.4:
            return ('D - Satisfactory', '#FFA500')
        else:
            return ('F - Fail', '#FF0000')
    else:
        if gpa <= 1.4:
            return ('A - Excellent', '#006400')
        elif gpa <= 2.4:
            return ('B - Very Good', '#90EE90')
        elif gpa <= 3.4:
            return ('C - Good', '#90EE90')
        elif gpa <= 4.4:
            return ('D - Average', '#FFFF00')
        elif gpa <= 5.4:
            return ('E - Satisfactory', '#FFFF00')
        elif gpa <= 6.4:
            return ('S - Unsatisfactory', '#FF8C00')
        else:
            return ('F - Fail', '#FF0000')

# -------------------------------------------------------------------
# School Info
# -------------------------------------------------------------------
SCHOOL_INFO = {
    'school_name': 'UCHILE SECONDARY SCHOOL',
    'school_code': 'S3560',
    'district': 'SUMBAWANGA DC - RUKWA',
    'region': 'REGIONAL ADMINISTRATION AND LOCAL GOVERNMENT',
    'ministry': "THE PRIME MINISTER'S OFFICE",
    'country': 'THE UNITED REPUBLIC OF TANZANIA'
}

# -------------------------------------------------------------------
# Init DB
# -------------------------------------------------------------------
def init_db():
    """Initialize the database and create default users/subjects."""
    db.create_all()
    
    # Create default users
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', password_hash=generate_password_hash('admin123'), role='admin')
        db.session.add(admin)
        print("Admin created: admin / admin123")
    
    if not User.query.filter_by(username='teacher').first():
        teacher = User(username='teacher', password_hash=generate_password_hash('teacher123'), role='teacher')
        db.session.add(teacher)
        print("Teacher created: teacher / teacher123")
    
    db.session.commit()
    
    # Create subjects if they don't exist
    if Subject.query.count() == 0:
        subjects_data = [
            # ========== O-LEVEL NEW CURRICULUM (Form 1 & 2) ==========
            # Compulsory (6 subjects)
            ('HTM', '060', 'Historia ya Tanzania na Maadili', 'new', 'compulsory', 'O'),
            ('KISW', '021', 'Kiswahili', 'new', 'compulsory', 'O'),
            ('GEO', '013', 'Geography', 'new', 'compulsory', 'O'),
            ('ENG', '022', 'English', 'new', 'compulsory', 'O'),
            ('MATH', '043', 'Mathematics', 'new', 'compulsory', 'O'),
            ('B/STUDY', '065', 'Business Studies', 'new', 'compulsory', 'O'),
            # Optional (min 2, max 3)
            ('PHY', '031', 'Physics', 'new', 'optional', 'O'),
            ('CHEM', '032', 'Chemistry', 'new', 'optional', 'O'),
            ('BIO', '033', 'Biology', 'new', 'optional', 'O'),
            ('HIST', '012', 'History', 'new', 'optional', 'O'),
            ('ICT', '072', 'Information and Communication Technology', 'new', 'optional', 'O'),
            
            # ========== O-LEVEL OLD CURRICULUM (Form 3 & 4) ==========
            # Compulsory (7 subjects)
            ('CIV', '011', 'Civics', 'old', 'compulsory', 'O'),
            # KISW, GEO, ENG, MATH already exist above but with 'new' curriculum
            # We need them for 'old' too - but code+level is unique
            # So we use same code but update curriculum to 'both'
            ('KISW', '021', 'Kiswahili', 'both', 'compulsory', 'O'),
            ('GEO', '013', 'Geography', 'both', 'compulsory', 'O'),
            ('ENG', '022', 'English', 'both', 'compulsory', 'O'),
            ('MATH', '043', 'Mathematics', 'both', 'compulsory', 'O'),
            ('HIST', '012', 'History', 'both', 'compulsory', 'O'),
            ('BIO', '033', 'Biology', 'both', 'optional', 'O'),
            # Optional
            ('PHY', '031', 'Physics', 'both', 'optional', 'O'),
            ('CHEM', '032', 'Chemistry', 'both', 'optional', 'O'),
            ('LIT/ENG', '024', 'Literature in English', 'old', 'optional', 'O'),
            
            # ========== A-LEVEL SUBJECTS ==========
            # Combination subjects
            ('PHY', '131', 'Physics', 'both', 'combination', 'A'),
            ('CHEM', '132', 'Chemistry', 'both', 'combination', 'A'),
            ('BIO', '133', 'Biology', 'both', 'combination', 'A'),
            ('ADV/MATHS', '142', 'Advanced Mathematics', 'both', 'combination', 'A'),
            ('GEO', '113', 'Geography', 'both', 'combination', 'A'),
            ('KISW', '121', 'Kiswahili', 'both', 'combination', 'A'),
            ('ENG', '122', 'English Language', 'both', 'combination', 'A'),
            ('HIST', '112', 'History', 'both', 'combination', 'A'),
            # Subsidiary subjects
            ('A/C', '128', 'Academic Communication', 'both', 'subsidiary', 'A'),
            ('HTM', '160', 'Historia ya Tanzania na Maadili', 'both', 'subsidiary', 'A'),
            ('BAM', '141', 'Basic Applied Mathematics', 'both', 'subsidiary', 'A'),
        ]
        
        # Use set to avoid duplicates (code + level)
        seen = set()
        count = 0
        for code, ncode, name, curriculum, category, level in subjects_data:
            key = (code, level)
            if key not in seen:
                db.session.add(Subject(
                    code=code,
                    necta_code=ncode,
                    name=name,
                    curriculum=curriculum,
                    category=category,
                    level=level
                ))
                seen.add(key)
                count += 1
        
        db.session.commit()
        print(f"Created {count} subjects successfully!")

# -------------------------------------------------------------------
# Auth
# -------------------------------------------------------------------
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        role = request.form.get('role', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            if user.role != role:
                flash('Role mismatch.')
                return render_template('login.html')
            login_user(user)
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('teacher_dashboard'))
        flash('Invalid credentials.')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# -------------------------------------------------------------------
# Admin Dashboard
# -------------------------------------------------------------------
@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.role != 'admin': abort(403)
    exams = Exam.query.order_by(Exam.date.desc()).all()
    return render_template('admin_dashboard.html', exams=exams, school_info=SCHOOL_INFO)

# -------------------------------------------------------------------
# Teacher Dashboard
# -------------------------------------------------------------------
@app.route('/teacher')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher': abort(403)
    exams = Exam.query.order_by(Exam.date.desc()).all()
    return render_template('teacher_dashboard.html', exams=exams)

# -------------------------------------------------------------------
# Register Student
# -------------------------------------------------------------------
@app.route('/register', methods=['GET','POST'])
@login_required
def register_student():
    if current_user.role != 'admin': abort(403)
    if request.method == 'POST':
        try:
            first_name = request.form.get('first_name','').strip()
            middle_name = request.form.get('middle_name','').strip()
            last_name = request.form.get('last_name','').strip()
            current_class = request.form.get('current_class','Form1')
            curriculum = request.form.get('curriculum','new')
            combination = request.form.get('combination','').strip().upper()
            optional_subjects = request.form.get('optional_subjects','').strip()
            
            if not first_name or not middle_name or not last_name:
                flash('All three names required.')
                return redirect(url_for('register_student'))
            
            dob_str = request.form.get('dob','')
            dob = datetime.strptime(dob_str, '%Y-%m-%d') if dob_str else None
            stream = request.form.get('stream','').strip() or 'A'
            
            student = Student(
                first_name=first_name, middle_name=middle_name, last_name=last_name,
                sex=request.form.get('sex','M'), dob=dob, stream=stream,
                combination=combination, curriculum=curriculum,
                optional_subjects=optional_subjects,
                parent_phone=request.form.get('parent_phone',''),
                current_class=current_class
            )
            db.session.add(student)
            db.session.flush()
            
            level = 'A' if current_class in ['Form5','Form6'] else 'O'
            if level == 'O':
                compulsory = get_compulsory_subjects(curriculum, 'O')
                optional = [s.strip() for s in optional_subjects.split(',') if s.strip()] if optional_subjects else []
                all_codes = compulsory + optional
            else:
                all_codes = get_combination_subjects(combination) + get_subsidiary_subjects(combination)
            
            for code in all_codes:
                subj = Subject.query.filter_by(code=code, level=level).first()
                if subj:
                    db.session.add(StudentSubjectRegistration(student_id=student.id, subject_id=subj.id))
            
            db.session.commit()
            
            # Auto-assign CNO based on alphabetical position
            reassign_cno(student.current_class)
            
            flash('Student registered and CNO assigned in alphabetical order.')
            return redirect(url_for('admin_dashboard'))

            db.session.commit()
            
            flash('Student registered successfully.')
            return redirect(url_for('admin_dashboard'))

            flash('Student registered.')
            return redirect(url_for('registry'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}')
    classes = ['Form1','Form2','Form3','Form4','Form5','Form6']
    return render_template('register_student.html', classes=classes)

# -------------------------------------------------------------------
# Bulk Upload
# -------------------------------------------------------------------
@app.route('/bulk_upload', methods=['GET','POST'])
@login_required
def bulk_upload():
    if current_user.role != 'admin':
        abort(403)
    
    if request.method == 'POST':
        try:
            file = request.files['file']
            class_name = request.form.get('class_name', 'Form1')
            df = pd.read_csv(file, dtype=str)
            
            success = 0
            errors = 0
            
            for _, row in df.iterrows():
                try:
                    first_name = str(row.get('first_name', '')).strip()
                    middle_name = str(row.get('middle_name', '')).strip()
                    last_name = str(row.get('last_name', '')).strip()
                    
                    if not first_name or not middle_name or not last_name:
                        errors += 1
                        continue
                    
                    dob_str = str(row.get('dob', '')).strip()
                    dob = None
                    if dob_str and dob_str.lower() != 'nan' and dob_str != '':
                        try:
                            dob = datetime.strptime(dob_str, '%Y-%m-%d')
                        except:
                            pass
                    
                    stream = str(row.get('stream', '')).strip() or 'A'
                    combination = str(row.get('combination', '')).strip().upper()
                    curriculum = str(row.get('curriculum', 'old')).strip().lower()
                    optional_subjects = str(row.get('optional_subjects', '')).strip()
                    
                    student = Student(
                        first_name=first_name,
                        middle_name=middle_name,
                        last_name=last_name,
                        sex=str(row.get('sex', 'M')).strip().upper(),
                        dob=dob,
                        stream=stream,
                        combination=combination,
                        curriculum=curriculum,
                        optional_subjects=optional_subjects,
                        parent_phone=str(row.get('parent_phone', '')).strip(),
                        current_class=class_name
                    )
                    db.session.add(student)
                    db.session.flush()
                    
                    level = 'A' if class_name in ['Form5', 'Form6'] else 'O'
                    if level == 'O':
                        compulsory = get_compulsory_subjects(curriculum, 'O')
                        optional = [s.strip() for s in optional_subjects.split(',') if s.strip()] if optional_subjects else []
                        all_codes = compulsory + optional
                    else:
                        all_codes = get_combination_subjects(combination) + get_subsidiary_subjects(combination)
                    
                    for code in all_codes:
                        subj = Subject.query.filter_by(code=code, level=level).first()
                        if subj:
                            db.session.add(StudentSubjectRegistration(
                                student_id=student.id,
                                subject_id=subj.id
                            ))
                    
                    success += 1
                    
                except Exception as e:
                    errors += 1
                    print(f"Row error: {e}")
                    continue
            
            db.session.commit()
            
            reassign_cno(class_name)
            
            flash(f'Uploaded {success} students to {class_name}. CNOs assigned alphabetically.')
            if errors > 0:
                flash(f'{errors} rows skipped.', 'warning')
            
            return redirect(url_for('admin_dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error uploading students: {str(e)}')
            print(f"Upload error: {e}")
    
    classes = ['Form1', 'Form2', 'Form3', 'Form4', 'Form5', 'Form6']
    return render_template('bulk_upload.html', classes=classes)

@app.route('/download_student_template/<class_name>')
@login_required
def download_student_template(class_name):
    header = ['first_name','middle_name','last_name','sex','dob','stream','combination','curriculum','optional_subjects','parent_phone']
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(header)
    cw.writerow(['John','Michael','Doe','M','','A','','new','PHY,CHEM,BIO',''])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=student_template_{class_name}.csv"
    output.headers["Content-type"] = "text/csv"
    return output

# -------------------------------------------------------------------
# Registry
# -------------------------------------------------------------------
@app.route('/registry', methods=['GET','POST'])
@login_required
def registry():
    if current_user.role != 'admin': abort(403)
    selected_class = request.form.get('class_name','Form1') if request.method == 'POST' else 'Form1'
    students = Student.query.filter_by(current_class=selected_class, is_deleted=False).order_by(Student.cno).all()
    subjects = get_subjects_for_class(selected_class)
    classes = ['Form1','Form2','Form3','Form4','Form5','Form6']
    
    level = 'A' if selected_class in ['Form5','Form6'] else 'O'
    auto_assigned = 0
    for stu in students:
        reg_count = StudentSubjectRegistration.query.filter_by(student_id=stu.id).count()
        if reg_count == 0:
            if level == 'O':
                compulsory = get_compulsory_subjects(stu.curriculum, 'O')
                optional = [s.strip() for s in stu.optional_subjects.split(',') if s.strip()] if stu.optional_subjects else []
                all_codes = compulsory + optional
            else:
                all_codes = get_combination_subjects(stu.combination) + get_subsidiary_subjects(stu.combination)
            for code in all_codes:
                subj = Subject.query.filter_by(code=code, level=level).first()
                if subj:
                    db.session.add(StudentSubjectRegistration(student_id=stu.id, subject_id=subj.id))
            auto_assigned += 1
    
    if auto_assigned > 0:
        db.session.commit()
        flash(f'Auto-assigned subjects for {auto_assigned} students.')
    
    reg_data = {}
    for stu in students:
        regs = StudentSubjectRegistration.query.filter_by(student_id=stu.id).all()
        reg_data[stu.id] = [reg.subject.code for reg in regs]
    
    return render_template('registry.html', selected_class=selected_class, classes=classes,
                           students=students, subjects=subjects, reg_data=reg_data)

# -------------------------------------------------------------------
# Edit Student
# -------------------------------------------------------------------
@app.route('/edit_student/<int:student_id>', methods=['GET','POST'])
@login_required
def edit_student(student_id):
    if current_user.role != 'admin': abort(403)
    student = Student.query.get_or_404(student_id)
    if request.method == 'POST':
        try:
            student.first_name = request.form.get('first_name','').strip()
            student.middle_name = request.form.get('middle_name','').strip()
            student.last_name = request.form.get('last_name','').strip()
            student.sex = request.form.get('sex','M')
            dob_str = request.form.get('dob','')
            if dob_str: student.dob = datetime.strptime(dob_str, '%Y-%m-%d')
            student.stream = request.form.get('stream','').strip() or 'A'
            student.combination = request.form.get('combination','').strip().upper()
            student.curriculum = request.form.get('curriculum','new')
            student.optional_subjects = request.form.get('optional_subjects','').strip()
            student.parent_phone = request.form.get('parent_phone','')
            
            level = 'A' if student.current_class in ['Form5','Form6'] else 'O'
            StudentSubjectRegistration.query.filter_by(student_id=student.id).delete()
            
            if level == 'O':
                compulsory = get_compulsory_subjects(student.curriculum, 'O')
                optional = [s.strip() for s in student.optional_subjects.split(',') if s.strip()] if student.optional_subjects else []
                all_codes = compulsory + optional
            else:
                all_codes = get_combination_subjects(student.combination) + get_subsidiary_subjects(student.combination)
            
            for code in all_codes:
                subj = Subject.query.filter_by(code=code, level=level).first()
                if subj:
                    db.session.add(StudentSubjectRegistration(student_id=student.id, subject_id=subj.id))
            
            db.session.commit()
            flash('Student updated.')
            return redirect(url_for('registry'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}')
    
    level = 'A' if student.current_class in ['Form5','Form6'] else 'O'
    subjects = get_subjects_for_class(student.current_class)
    regs = StudentSubjectRegistration.query.filter_by(student_id=student.id).all()
    registered_subs = [reg.subject.code for reg in regs]
    
    if level == 'O':
        compulsory = get_compulsory_subjects(student.curriculum, 'O')
        optional = [s.strip() for s in student.optional_subjects.split(',') if s.strip()] if student.optional_subjects else []
        expected_codes = compulsory + optional
    else:
        expected_codes = get_combination_subjects(student.combination) + get_subsidiary_subjects(student.combination)
    
    return render_template('edit_student.html', student=student, subjects=subjects,
                           registered_subs=registered_subs, expected_codes=expected_codes)

# -------------------------------------------------------------------
# Delete Student
# -------------------------------------------------------------------
@app.route('/delete_student/<int:student_id>')
@login_required
def delete_student(student_id):
    if current_user.role != 'admin': abort(403)
    student = Student.query.get_or_404(student_id)
    student.is_deleted = True
    student.deleted_at = datetime.utcnow()
    db.session.commit()
    flash('Student moved to trash.')
    return redirect(url_for('registry'))

@app.route('/deleted_students')
@login_required
def deleted_students():
    if current_user.role != 'admin': abort(403)
    students = Student.query.filter_by(is_deleted=True).all()
    return render_template('deleted_students.html', students=students)

@app.route('/readmit_student/<int:student_id>')
@login_required
def readmit_student(student_id):
    if current_user.role != 'admin': abort(403)
    student = Student.query.get_or_404(student_id)
    student.is_deleted = False
    student.deleted_at = None
    db.session.commit()
    flash('Student re-admitted.')
    return redirect(url_for('deleted_students'))

@app.route('/permanent_delete/<int:student_id>')
@login_required
def permanent_delete(student_id):
    if current_user.role != 'admin': abort(403)
    student = Student.query.get_or_404(student_id)
    StudentSubject.query.filter_by(student_id=student.id).delete()
    StudentSubjectRegistration.query.filter_by(student_id=student.id).delete()
    Result.query.filter_by(student_id=student.id).delete()
    db.session.delete(student)
    db.session.commit()
    flash('Permanently deleted.')
    return redirect(url_for('deleted_students'))

# -------------------------------------------------------------------
# Create Exam
# -------------------------------------------------------------------
@app.route('/create_exam', methods=['GET','POST'])
@login_required
def create_exam():
    if current_user.role != 'admin': abort(403)
    if request.method == 'POST':
        try:
            exam_date = datetime.strptime(request.form.get('date',''), '%Y-%m-%d') if request.form.get('date') else datetime.now()
            month_year = exam_date.strftime('%B').upper() + ', ' + str(exam_date.year)
            exam = Exam(
                name=request.form.get('name',''),
                term=request.form.get('term',''),
                academic_year=int(request.form.get('academic_year', datetime.now().year)),
                exam_type=request.form.get('exam_type','monthly'),
                is_necta_exam=request.form.get('is_necta_exam','false').lower()=='true',
                date=exam_date,
                target_class=request.form.get('target_class','Form1'),
                month_year_label=month_year
            )
            db.session.add(exam)
            db.session.commit()
            
            # Auto-create StudentSubject records for all students in this class
            students = Student.query.filter_by(current_class=exam.target_class, is_deleted=False).all()
            level = 'A' if exam.target_class in ['Form5','Form6'] else 'O'
            for stu in students:
                if level == 'O':
                    compulsory = get_compulsory_subjects(stu.curriculum, 'O')
                    optional = [s.strip() for s in stu.optional_subjects.split(',') if s.strip()] if stu.optional_subjects else []
                    all_codes = compulsory + optional
                else:
                    all_codes = get_combination_subjects(stu.combination) + get_subsidiary_subjects(stu.combination)
                for code in all_codes:
                    subj = Subject.query.filter_by(code=code, level=level).first()
                    if subj:
                        db.session.add(StudentSubject(student_id=stu.id, exam_id=exam.id, subject_id=subj.id))
            db.session.commit()
            
            flash(f'Exam created with subject slots for {len(students)} students.')
            return redirect(url_for('admin_dashboard'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}')
    classes = ['Form1','Form2','Form3','Form4','Form5','Form6']
    exam_types = get_exam_types_list()
    months = get_months_list()
    years = list(range(datetime.now().year, datetime.now().year + 3))
    return render_template('create_exam.html', classes=classes, exam_types=exam_types, months=months, years=years)

# -------------------------------------------------------------------
# Teacher Fill Scores
# -------------------------------------------------------------------
@app.route('/teacher/fill_scores/<int:exam_id>', methods=['GET','POST'])
@login_required
def teacher_fill_scores(exam_id):
    if current_user.role != 'teacher':
        abort(403)
    
    exam = Exam.query.get_or_404(exam_id)
    students = Student.query.filter_by(
        current_class=exam.target_class,
        is_deleted=False
    ).order_by(Student.cno).all()
    subjects = get_subjects_for_exam(exam)
    
    if request.method == 'POST':
        try:
            saved_count = 0
            for stu in students:
                for subj in subjects:
                    marks_key = f'marks_{stu.id}_{subj.id}'
                    if marks_key in request.form:
                        val = request.form[marks_key]
                        marks = None
                        if val and val.strip() != '':
                            try:
                                marks = float(val)
                            except:
                                marks = None
                        
                        # Find existing record
                        ss = StudentSubject.query.filter_by(
                            student_id=stu.id,
                            exam_id=exam.id,
                            subject_id=subj.id
                        ).first()
                        
                        if ss:
                            ss.marks = marks
                            saved_count += 1
                        elif marks is not None:
                            db.session.add(StudentSubject(
                                student_id=stu.id,
                                exam_id=exam.id,
                                subject_id=subj.id,
                                marks=marks
                            ))
                            saved_count += 1
                
                # Behavior comment
                beh_key = f'behavior_{stu.id}'
                if beh_key in request.form:
                    comment = request.form[beh_key]
                    first_ss = StudentSubject.query.filter_by(
                        student_id=stu.id,
                        exam_id=exam.id
                    ).first()
                    if first_ss:
                        first_ss.behavior_comment = comment
            
            db.session.commit()
            flash(f'Scores saved successfully! ({saved_count} marks saved)')
            return redirect(url_for('teacher_dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving scores: {str(e)}')
            print(f"Teacher score save error: {e}")
    
    # GET request
    marks_data = {}
    behavior_data = {}
    for stu in students:
        for subj in subjects:
            ss = StudentSubject.query.filter_by(
                student_id=stu.id,
                exam_id=exam.id,
                subject_id=subj.id
            ).first()
            marks_data[(stu.id, subj.id)] = ss.marks if ss and ss.marks is not None else ''
        
        first_ss = StudentSubject.query.filter_by(
            student_id=stu.id,
            exam_id=exam.id
        ).first()
        behavior_data[stu.id] = first_ss.behavior_comment if first_ss else ''
    
    return render_template(
        'teacher_fill_scores.html',
        exam=exam,
        students=students,
        subjects=subjects,
        marks_data=marks_data,
        behavior_data=behavior_data
    )

# -------------------------------------------------------------------
# Process Results (Admin)
# -------------------------------------------------------------------
@app.route('/process_results/<int:exam_id>')
@login_required
def process_results_route(exam_id):
    if current_user.role != 'admin': abort(403)
    exam = Exam.query.get_or_404(exam_id)
    students = Student.query.filter_by(current_class=exam.target_class, is_deleted=False).all()
    for stu in students:
        process_student_results(stu.id, exam_id)
    flash(f'Results processed for {len(students)} students.')
    return redirect(url_for('view_results', exam_id=exam_id))

# -------------------------------------------------------------------
# View Results (Admin)
# -------------------------------------------------------------------
@app.route('/view_results/<int:exam_id>')
@login_required
def view_results(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    level = 'A' if exam.target_class in ['Form5','Form6'] else 'O'
    students = Student.query.filter_by(current_class=exam.target_class, is_deleted=False).order_by(Student.cno).all()
    subjects = get_subjects_for_exam(exam)

    total_registered = len(students)
    total_reg_m = sum(1 for s in students if s.sex=='M')
    total_reg_f = sum(1 for s in students if s.sex=='F')
    
    sat_count, sat_m, sat_f = 0, 0, 0
    for stu in students:
        marks_exist = StudentSubject.query.filter(StudentSubject.student_id==stu.id,
                                                   StudentSubject.exam_id==exam.id,
                                                   StudentSubject.marks!=None).count()
        if marks_exist > 0:
            sat_count += 1
            if stu.sex=='M': sat_m += 1
            else: sat_f += 1
    
    abs_count = total_registered - sat_count
    abs_m = total_reg_m - sat_m
    abs_f = total_reg_f - sat_f

    div_counts = {'I':{'M':0,'F':0,'T':0}, 'II':{'M':0,'F':0,'T':0},
                  'III':{'M':0,'F':0,'T':0}, 'IV':{'M':0,'F':0,'T':0}, '0':{'M':0,'F':0,'T':0}}
    for stu in students:
        result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
        if result and result.division in div_counts:
            div_counts[result.division][stu.sex] += 1
            div_counts[result.division]['T'] += 1
    
    total_passed = sum(div_counts[d]['T'] for d in ['I','II','III','IV'])
    passed_m = sum(div_counts[d]['M'] for d in ['I','II','III','IV'])
    passed_f = sum(div_counts[d]['F'] for d in ['I','II','III','IV'])

    student_results = []
    for stu in students:
        result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
        detailed = format_detailed_subjects(stu.id, exam_id, level)
        full_name = get_full_name(stu)
        student_results.append({'student': stu, 'result': result, 'detailed': detailed, 'full_name': full_name})

    subject_performance = []
    subject_gpas = []
    for idx, subj in enumerate(subjects, 1):
        grade_dist = {'A':0,'B':0,'C':0,'D':0,'F':0} if level=='O' else {'A':0,'B':0,'C':0,'D':0,'E':0,'S':0,'F':0}
        regist_m, regist_f, sat_subj = 0, 0, 0
        
        for stu in students:
            reg = StudentSubjectRegistration.query.filter_by(student_id=stu.id, subject_id=subj.id).first()
            if reg:
                if stu.sex == 'M':
                    regist_m += 1
                else:
                    regist_f += 1
            
            ss = StudentSubject.query.filter_by(student_id=stu.id, exam_id=exam.id, subject_id=subj.id).first()
            if ss and ss.grade:
                sat_subj += 1
                if ss.grade in grade_dist:
                    grade_dist[ss.grade] += 1
        
        regist_total = regist_m + regist_f
        
        # If no students registered, leave blank
        if regist_total == 0:
            gpa = None
            comp_level, comp_color = '', ''
            total_passed_subj = ''
        else:
            total_passed_subj = sum(grade_dist.values()) - grade_dist.get('F', 0)
            gpa = calculate_subject_gpa(grade_dist, level)
            comp_level, comp_color = get_competence_level(gpa, level)
        
        subject_gpas.append(gpa if gpa is not None else 0)
        
        subject_performance.append({
            'sn': idx,
            'necta_code': subj.necta_code,
            'name': subj.name,
            'regist_m': regist_m,
            'regist_f': regist_f,
            'regist_total': regist_total,
            'sat': sat_subj,
            'grades': grade_dist,
            'total_passed': total_passed_subj,
            'gpa': gpa,
            'competence_label': comp_level,
            'competence_color': comp_color
        })

        # Only average GPAs that are not None
    valid_gpas = [g for g in subject_gpas if g is not None and g > 0]
    overall_gpa = round(sum(valid_gpas) / len(valid_gpas), 2) if valid_gpas else 0

    return render_template('view_results.html', exam=exam, subjects=subjects,
                           school_info=SCHOOL_INFO, level=level,
                           total_registered=total_registered, total_reg_m=total_reg_m, total_reg_f=total_reg_f,
                           abs_count=abs_count, abs_m=abs_m, abs_f=abs_f,
                           sat_count=sat_count, sat_m=sat_m, sat_f=sat_f,
                           div_counts=div_counts, total_passed=total_passed,
                           passed_m=passed_m, passed_f=passed_f,
                           student_results=student_results,
                           subject_performance=subject_performance,
                           overall_gpa=overall_gpa,
                           get_full_name=get_full_name,
                           get_class_number=get_class_number,
                           get_exam_type_label=get_exam_type_label)

# -------------------------------------------------------------------
# Results PDF Download (Admin)
# -------------------------------------------------------------------
@app.route('/results_pdf/<int:exam_id>')
@login_required
def results_pdf(exam_id):
    if current_user.role != 'admin':
        abort(403)
    
    exam = Exam.query.get_or_404(exam_id)
    level = 'A' if exam.target_class in ['Form5', 'Form6'] else 'O'
    students = Student.query.filter_by(
        current_class=exam.target_class,
        is_deleted=False
    ).order_by(Student.cno).all()
    subjects = get_subjects_for_exam(exam)

    total_registered = len(students)
    total_reg_m = sum(1 for s in students if s.sex == 'M')
    total_reg_f = sum(1 for s in students if s.sex == 'F')
    
    sat_count, sat_m, sat_f = 0, 0, 0
    for stu in students:
        marks_exist = StudentSubject.query.filter(
            StudentSubject.student_id == stu.id,
            StudentSubject.exam_id == exam.id,
            StudentSubject.marks != None
        ).count()
        if marks_exist > 0:
            sat_count += 1
            if stu.sex == 'M':
                sat_m += 1
            else:
                sat_f += 1
    
    abs_count = total_registered - sat_count
    abs_m = total_reg_m - sat_m
    abs_f = total_reg_f - sat_f

    div_counts = {'I': {'M': 0, 'F': 0, 'T': 0}, 'II': {'M': 0, 'F': 0, 'T': 0},
                  'III': {'M': 0, 'F': 0, 'T': 0}, 'IV': {'M': 0, 'F': 0, 'T': 0},
                  '0': {'M': 0, 'F': 0, 'T': 0}}
    
    for stu in students:
        result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
        if result and result.division in div_counts:
            div_counts[result.division][stu.sex] += 1
            div_counts[result.division]['T'] += 1
    
    total_passed = sum(div_counts[d]['T'] for d in ['I', 'II', 'III', 'IV'])
    passed_m = sum(div_counts[d]['M'] for d in ['I', 'II', 'III', 'IV'])
    passed_f = sum(div_counts[d]['F'] for d in ['I', 'II', 'III', 'IV'])

    student_results = []
    for stu in students:
        result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
        detailed = format_detailed_subjects(stu.id, exam_id, level)
        full_name = get_full_name(stu)
        student_results.append({
            'student': stu,
            'result': result,
            'detailed': detailed,
            'full_name': full_name
        })

    subject_performance = []
    subject_gpas = []
    for idx, subj in enumerate(subjects, 1):
        grade_dist = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0} if level == 'O' else {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0, 'S': 0, 'F': 0}
        regist_m, regist_f, sat_subj = 0, 0, 0
        
        for stu in students:
            reg = StudentSubjectRegistration.query.filter_by(student_id=stu.id, subject_id=subj.id).first()
            if reg:
                if stu.sex == 'M':
                    regist_m += 1
                else:
                    regist_f += 1
            
            ss = StudentSubject.query.filter_by(student_id=stu.id, exam_id=exam.id, subject_id=subj.id).first()
            if ss and ss.grade:
                sat_subj += 1
                if ss.grade in grade_dist:
                    grade_dist[ss.grade] += 1
        
        regist_total = regist_m + regist_f
        
        if regist_total == 0:
            gpa = None
            comp_level, comp_color = '', ''
            total_passed_subj = ''
        else:
            total_passed_subj = sum(grade_dist.values()) - grade_dist.get('F', 0)
            gpa = calculate_subject_gpa(grade_dist, level)
            comp_level, comp_color = get_competence_level(gpa, level)
        
        subject_gpas.append(gpa if gpa is not None else 0)
        subject_performance.append({
            'sn': idx,
            'necta_code': subj.necta_code,
            'name': subj.name,
            'regist_m': regist_m,
            'regist_f': regist_f,
            'regist_total': regist_total,
            'sat': sat_subj,
            'grades': grade_dist,
            'total_passed': total_passed_subj,
            'gpa': gpa,
            'competence_label': comp_level,
            'competence_color': comp_color
        })

    valid_gpas = [g for g in subject_gpas if g is not None and g > 0]
    overall_gpa = round(sum(valid_gpas) / len(valid_gpas), 2) if valid_gpas else 0

    html = render_template(
        'results_pdf.html',
        exam=exam,
        subjects=subjects,
        school_info=SCHOOL_INFO,
        level=level,
        total_registered=total_registered,
        total_reg_m=total_reg_m,
        total_reg_f=total_reg_f,
        abs_count=abs_count,
        abs_m=abs_m,
        abs_f=abs_f,
        sat_count=sat_count,
        sat_m=sat_m,
        sat_f=sat_f,
        div_counts=div_counts,
        total_passed=total_passed,
        passed_m=passed_m,
        passed_f=passed_f,
        student_results=student_results,
        subject_performance=subject_performance,
        overall_gpa=overall_gpa,
        get_full_name=get_full_name,
        get_class_number=get_class_number,
        get_exam_type_label=get_exam_type_label
    )
    
    pdf = render_pdf(html)
    return send_file(pdf, mimetype='application/pdf', as_attachment=False,
                     download_name=f'results_{exam.target_class}_{exam.exam_type}.pdf')

# -------------------------------------------------------------------
# Student/Public View - CNO Only, No Names
# -------------------------------------------------------------------
@app.route('/student_view', methods=['GET', 'POST'])
def student_view():
    classes = ['Form1', 'Form2', 'Form3', 'Form4', 'Form5', 'Form6']
    exam_types_list = get_exam_types_list()
    current_year = datetime.now().year
    years = list(range(current_year - 3, current_year + 1))
    
    results_data = None
    exam = None
    subjects = []
    level = 'O'
    selected_class = None
    selected_exam_type = None
    selected_year = None
    subject_performance = []
    
    if request.method == 'POST':
        selected_class = request.form.get('class_name', '')
        selected_exam_type = request.form.get('exam_type', '')
        selected_year = request.form.get('year', '')
        
        if selected_class and selected_exam_type and selected_year:
            exam = Exam.query.filter_by(
                target_class=selected_class,
                exam_type=selected_exam_type,
                academic_year=int(selected_year)
            ).order_by(Exam.date.desc()).first()
            
            if exam:
                level = 'A' if exam.target_class in ['Form5', 'Form6'] else 'O'
                subjects = get_subjects_for_exam(exam)
                students = Student.query.filter_by(
                    current_class=exam.target_class,
                    is_deleted=False
                ).order_by(Student.cno).all()
                
                # Build results data (CNO only, no names)
                results_data = []
                for stu in students:
                    result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
                    detailed = format_detailed_subjects(stu.id, exam.id, level)
                    results_data.append({
                        'cno': stu.cno,
                        'sex': stu.sex,
                        'result': result,
                        'detailed': detailed
                    })
                
                # Calculate subject performance
                for idx, subj in enumerate(subjects, 1):
                    if level == 'O':
                        grade_dist = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'F': 0}
                    else:
                        grade_dist = {'A': 0, 'B': 0, 'C': 0, 'D': 0, 'E': 0, 'S': 0, 'F': 0}
                    
                    regist_m, regist_f, sat_subj = 0, 0, 0
                    for stu in students:
                        reg = StudentSubjectRegistration.query.filter_by(
                            student_id=stu.id,
                            subject_id=subj.id
                        ).first()
                        if reg:
                            if stu.sex == 'M':
                                regist_m += 1
                            else:
                                regist_f += 1
                        
                        ss = StudentSubject.query.filter_by(
                            student_id=stu.id,
                            exam_id=exam.id,
                            subject_id=subj.id
                        ).first()
                        if ss and ss.grade:
                            sat_subj += 1
                            if ss.grade in grade_dist:
                                grade_dist[ss.grade] += 1
                    
                    regist_total = regist_m + regist_f
                    total_passed_subj = sum(grade_dist.values()) - grade_dist.get('F', 0)
                    gpa = calculate_subject_gpa(grade_dist, level)
                    comp_level, comp_color = get_competence_level(gpa, level)
                    
                    subject_performance.append({
                        'sn': idx,
                        'necta_code': subj.necta_code,
                        'name': subj.name,
                        'regist_m': regist_m,
                        'regist_f': regist_f,
                        'regist_total': regist_total,
                        'sat': sat_subj,
                        'grades': grade_dist,
                        'total_passed': total_passed_subj,
                        'gpa': gpa,
                        'competence_label': comp_level,
                        'competence_color': comp_color
                    })
            else:
                flash('No results found for the selected criteria.')
    
    return render_template(
        'student_view_form.html',
        classes=classes,
        exam_types=exam_types_list,
        years=years,
        selected_class=selected_class,
        selected_exam_type=selected_exam_type,
        selected_year=selected_year,
        exam=exam,
        subjects=subjects,
        results_data=results_data,
        level=level,
        school_info=SCHOOL_INFO,
        get_class_number=get_class_number,
        get_exam_type_label=get_exam_type_label,
        subject_performance=subject_performance
    )

# -------------------------------------------------------------------
# Promotion
# -------------------------------------------------------------------
@app.route('/promote', methods=['GET','POST'])
@login_required
def promote():
    if current_user.role != 'admin': abort(403)
    if request.method == 'POST':
        class_name = request.form.get('class_name','')
        exam_type_needed = 'prenecta' if class_name in ['Form4','Form6'] else 'annual'
        exam = Exam.query.filter_by(target_class=class_name, exam_type=exam_type_needed,
                                     academic_year=datetime.now().year).first()
        if not exam:
            flash(f'No {exam_type_needed} exam found.')
            return render_template('promote.html', classes=['Form1','Form2','Form3','Form4','Form5','Form6'])
        students = Student.query.filter_by(current_class=class_name, is_deleted=False).all()
        for stu in students:
            result = Result.query.filter_by(student_id=stu.id, exam_id=exam.id).first()
            if result and result.division not in ['0','ABS','INC',None]:
                next_class = get_next_class(stu.current_class)
                db.session.add(Promotion(student_id=stu.id, from_class=stu.current_class, to_class=next_class,
                                          academic_year=datetime.now().year))
                stu.current_class = next_class
        db.session.commit()
        flash('Promotion completed.')
        return redirect(url_for('admin_dashboard'))
    return render_template('promote.html', classes=['Form1','Form2','Form3','Form4','Form5','Form6'])

# -------------------------------------------------------------------
# All Students List
# -------------------------------------------------------------------
@app.route('/all_students_list')
@login_required
def all_students_list():
    if current_user.role != 'admin': abort(403)
    classes = ['Form1','Form2','Form3','Form4','Form5','Form6']
    all_data = {}
    for cls in classes:
        all_data[cls] = Student.query.filter_by(current_class=cls, is_deleted=False).order_by(Student.cno).all()
    html = render_template('all_students_list.html', all_data=all_data, classes=classes, school_info=SCHOOL_INFO)
    pdf = render_pdf(html)
    return send_file(pdf, mimetype='application/pdf', download_name='all_students.pdf')

# -------------------------------------------------------------------
# Reassign CNOs for a Class
# -------------------------------------------------------------------
def reassign_cno(class_name, school_prefix='S3560'):
    """Reassign CNOs alphabetically: Female A-Z first, then Male A-Z.
    Uses two-step commit to avoid unique constraint conflicts."""
    
    females = Student.query.filter_by(
        current_class=class_name,
        is_deleted=False,
        sex='F'
    ).order_by(
        Student.first_name.asc(),
        Student.middle_name.asc(),
        Student.last_name.asc()
    ).all()
    
    males = Student.query.filter_by(
        current_class=class_name,
        is_deleted=False,
        sex='M'
    ).order_by(
        Student.first_name.asc(),
        Student.middle_name.asc(),
        Student.last_name.asc()
    ).all()
    
    students = females + males
    
    if not students:
        return 0
    
    if class_name in ['Form5', 'Form6']:
        start = 501
    else:
        start = 1
    
    # Step 1: Set ALL CNOs to temporary unique values
    for idx, s in enumerate(students):
        s.cno = f"TEMP-{idx:06d}"
    
    db.session.commit()
    
    # Step 2: Assign final sequential CNOs
    for idx, s in enumerate(students):
        s.cno = f"{school_prefix}-{start + idx:04d}"
    
    db.session.commit()
    
    print(f"{class_name}: {len(students)} students reordered")
    return len(students)

# -------------------------------------------------------------------
# Delete All Students in a Class
# -------------------------------------------------------------------
@app.route('/delete_all_students/<class_name>')
@login_required
def delete_all_students(class_name):
    if current_user.role != 'admin':
        abort(403)
    
    students = Student.query.filter_by(
        current_class=class_name,
        is_deleted=False
    ).all()
    
    count = 0
    for student in students:
        student.is_deleted = True
        student.deleted_at = datetime.utcnow()
        count += 1
    
    db.session.commit()
    flash(f'{count} students from {class_name} moved to trash.')
    return redirect(url_for('registry'))

# -------------------------------------------------------------------
# Reassign CNOs for a Class
# -------------------------------------------------------------------
@app.route('/reassign_cno/<class_name>')
@login_required
def reassign_cno_route(class_name):
    if current_user.role != 'admin':
        abort(403)
    
    try:
        count = reassign_cno(class_name)
        flash(f'CNOs reassigned for {class_name}. {count} students reordered.')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}')
    
    return redirect(url_for('registry'))

# -------------------------------------------------------------------
# Clear All CNOs for a Class (Temporary fix)
# -------------------------------------------------------------------
@app.route('/clear_cnos/<class_name>')
@login_required
def clear_cnos(class_name):
    if current_user.role != 'admin':
        abort(403)
    
    try:
        students = Student.query.filter_by(
            current_class=class_name,
            is_deleted=False
        ).all()
        
        for s in students:
            s.cno = None
        
        db.session.commit()
        flash(f'CNOs cleared for {class_name}. Now click Reassign CNOs.')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}')
    
    return redirect(url_for('registry'))

# -------------------------------------------------------------------
# Fix duplicate subjects in EXISTING database
# -------------------------------------------------------------------
@app.route('/fix_bio')
@login_required
def fix_bio():
    if current_user.role != 'admin':
        abort(403)
    
    try:
        # Find ALL BIO subjects
        bio_subjects = Subject.query.filter_by(code='BIO', level='O').all()
        
        flash(f'Found {len(bio_subjects)} BIO subjects for O-Level')
        
        # If more than 1, keep the first, merge others
        if len(bio_subjects) > 1:
            keep = bio_subjects[0]
            
            for dup in bio_subjects[1:]:
                # Move all StudentSubject records to the kept subject
                ss_records = StudentSubject.query.filter_by(subject_id=dup.id).all()
                for ss in ss_records:
                    # Check if student already has record for kept subject
                    existing = StudentSubject.query.filter_by(
                        student_id=ss.student_id,
                        exam_id=ss.exam_id,
                        subject_id=keep.id
                    ).first()
                    
                    if existing:
                        # Merge marks
                        if existing.marks is None and ss.marks is not None:
                            existing.marks = ss.marks
                            existing.grade = ss.grade
                            existing.points = ss.points
                        db.session.delete(ss)
                    else:
                        # Move to kept subject
                        ss.subject_id = keep.id
                
                # Move registrations
                reg_records = StudentSubjectRegistration.query.filter_by(subject_id=dup.id).all()
                for reg in reg_records:
                    existing_reg = StudentSubjectRegistration.query.filter_by(
                        student_id=reg.student_id,
                        subject_id=keep.id
                    ).first()
                    if existing_reg:
                        db.session.delete(reg)
                    else:
                        reg.subject_id = keep.id
                
                # Delete duplicate subject
                db.session.delete(dup)
            
            db.session.commit()
            flash(f'BIO fixed! Merged {len(bio_subjects)} subjects into 1.')
        else:
            flash(f'Only 1 BIO subject found. No fix needed.')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}')
    
    return redirect(url_for('admin_dashboard'))

# -------------------------------------------------------------------
# Run
# -------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)