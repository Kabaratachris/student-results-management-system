from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cno = db.Column(db.String(20), unique=True)
    first_name = db.Column(db.String(50))
    middle_name = db.Column(db.String(50))
    last_name = db.Column(db.String(50))
    sex = db.Column(db.String(1))
    dob = db.Column(db.Date)
    stream = db.Column(db.String(20))
    combination = db.Column(db.String(20))
    curriculum = db.Column(db.String(20))
    optional_subjects = db.Column(db.String(200))
    parent_phone = db.Column(db.String(20))
    passport = db.Column(db.String(200))
    current_class = db.Column(db.String(20))
    is_deleted = db.Column(db.Boolean, default=False)
    deleted_at = db.Column(db.DateTime)
    
    # Relationships
    subject_registrations = db.relationship('StudentSubjectRegistration', backref='student', lazy=True)


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), nullable=False)
    necta_code = db.Column(db.String(5))
    name = db.Column(db.String(100))
    curriculum = db.Column(db.String(20))
    category = db.Column(db.String(20))
    level = db.Column(db.String(10))
    combination_group = db.Column(db.String(50))

    __table_args__ = (
        db.UniqueConstraint('code', 'level', name='unique_code_level'),
    )
    
    # Relationships
    registrations = db.relationship('StudentSubjectRegistration', backref='subject', lazy=True)


class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    term = db.Column(db.String(10))
    academic_year = db.Column(db.Integer)
    exam_type = db.Column(db.String(30))
    is_necta_exam = db.Column(db.Boolean, default=False)
    date = db.Column(db.Date)
    target_class = db.Column(db.String(20))
    month_year_label = db.Column(db.String(30))


class StudentSubject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'))
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'))
    marks = db.Column(db.Float, nullable=True)
    grade = db.Column(db.String(5), nullable=True)
    points = db.Column(db.Integer, nullable=True)
    behavior_comment = db.Column(db.Text, nullable=True)
    
    # Relationships
    student = db.relationship('Student', backref=db.backref('exam_subjects', lazy=True))
    exam = db.relationship('Exam', backref=db.backref('student_subjects', lazy=True))
    subject = db.relationship('Subject', backref=db.backref('student_subjects', lazy=True))


class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    exam_id = db.Column(db.Integer, db.ForeignKey('exam.id'))
    agg = db.Column(db.Integer)
    division = db.Column(db.String(5))


class Promotion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    from_class = db.Column(db.String(20))
    to_class = db.Column(db.String(20))
    academic_year = db.Column(db.Integer)
    date_promoted = db.Column(db.DateTime, default=datetime.utcnow)
    is_rollback = db.Column(db.Boolean, default=False)


class StudentSubjectRegistration(db.Model):
    """Tracks which subjects a student is registered for."""
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('student.id'))
    subject_id = db.Column(db.Integer, db.ForeignKey('subject.id'))