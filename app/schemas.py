# backend/app/schemas.py
from pydantic import BaseModel, EmailStr
from typing import List, Dict, Optional
from datetime import datetime

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class QuestionIn(BaseModel):
    text: str
    choices: List[str]
    answer_index: int
    difficulty: str  # "easy"/"medium"/"hard"

class QuestionOut(BaseModel):
    id: str
    text: str
    choices: List[str]
    difficulty: str

class ExamCreateIn(BaseModel):
    title: str
    language: str
    question_count: int
    time_allowed_secs: int

class ExamOut(BaseModel):
    id: str
    title: str
    language: str
    question_count: int
    time_allowed_secs: int
    created_at: datetime
    is_active: bool

class ExamAssignIn(BaseModel):
    candidate_emails: List[EmailStr]

class ExamDetailOut(BaseModel):
    id: str
    questions: List[QuestionOut]
    time_allowed_secs: int
    time_elapsed: int
    status: str

class CandidateExamCreateOut(BaseModel):
    id: str
    question_ids: List[str]
    time_allowed_secs: int

class AnswerIn(BaseModel):
    question_id: str
    selected_index: int
    time_elapsed: int  # seconds elapsed so far on client (to help server

class ResumeQuestionOut(BaseModel):
    id: str
    text: str
    choices: list


class ResumeExamOut(BaseModel):
    candidate_exam_id: str
    questions: list[ResumeQuestionOut]
    answers: dict
    time_allowed_secs: int
    time_elapsed: int
    status: str
