# backend/app/models.py
import enum
import uuid
from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    Boolean,
    Enum,
    JSON,
    ForeignKey
)
from sqlalchemy.sql import func
from .db import Base


# -------------------------------------------------
# Helpe
# -------------------------------------------------
def gen_id():
    return str(uuid.uuid4())


# -------------------------------------------------
# Enums
# -------------------------------------------------
class Difficulty(str, enum.Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


# -------------------------------------------------
# User (Cognito-based authentication)
# -------------------------------------------------
class User(Base):
    """
    User authentication is handled by AWS Cognito.
    This table stores only profile & authorization info.
    """
    __tablename__ = "users"

    # Cognito User ID (sub)
    id = Column(String, primary_key=True)

    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=True)

    # Application-level role
    is_admin = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


# -------------------------------------------------
# Questions
# -------------------------------------------------
class Question(Base):
    __tablename__ = "questions"

    id = Column(String, primary_key=True, default=gen_id)
    text = Column(String, nullable=False)

    # List of choices
    choices = Column(JSON, nullable=False)

    # Index of correct answer (0-based)
    answer_index = Column(Integer, nullable=False)

    # Optional link to exam
    exam_id = Column(String, ForeignKey("exams.id"), nullable=True)

    difficulty = Column(
        Enum(Difficulty),
        nullable=False,
        default=Difficulty.easy
    )


# -------------------------------------------------
# Exams
# -------------------------------------------------
class Exam(Base):
    __tablename__ = "exams"

    id = Column(String, primary_key=True, default=gen_id)
    title = Column(String, nullable=False)
    language = Column(String, nullable=False)

    question_count = Column(Integer, nullable=False)
    time_allowed_secs = Column(Integer, nullable=False)

    # Cognito user id of admin who created exam
    created_by = Column(String, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True)


# -------------------------------------------------
# Exam Assignments
# -------------------------------------------------
class ExamAssignment(Base):
    __tablename__ = "exam_assignments"

    id = Column(String, primary_key=True, default=gen_id)

    exam_id = Column(String, ForeignKey("exams.id"), nullable=False)

    # Candidate email (may or may not exist in users table yet)
    candidate_email = Column(String, nullable=False)

    # Admin (Cognito user id)
    assigned_by = Column(String, ForeignKey("users.id"), nullable=False)

    assigned_at = Column(DateTime(timezone=True), server_default=func.now())

    # assigned / started / completed
    status = Column(String, default="assigned")


# -------------------------------------------------
# Candidate Exam Attempts
# -------------------------------------------------
class CandidateExam(Base):
    __tablename__ = "candidate_exams"

    id = Column(String, primary_key=True, default=gen_id)

    # Cognito user id
    user_id = Column(String, ForeignKey("users.id"), nullable=False)

    exam_id = Column(String, ForeignKey("exams.id"), nullable=False)

    # Ordered list of question IDs
    question_ids = Column(JSON, nullable=True)

    # question_id -> selected answer index
    answers = Column(JSON, nullable=True)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime, nullable=True)

    # not_started / in_progress / completed / timed_out
    status = Column(String, default="not_started")

    time_allowed_secs = Column(Integer, default=1800)
    time_elapsed = Column(Integer, default=0)

    score = Column(Integer, default=0)
