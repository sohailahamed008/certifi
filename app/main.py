from fastapi import FastAPI, Depends, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import and_
from dotenv import load_dotenv
import uuid
import requests
import traceback
import json
import re
import os
import boto3
from datetime import datetime
from dotenv import load_dotenv

from .db import Base, engine, SessionLocal
from . import models, schemas, exam
from .cognito_auth import get_current_user
from .email_utils import send_exam_assignment_email



def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

load_dotenv()
AWS_REGION = os.getenv("AWS_REGION")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID")
default_password = os.getenv("DEFAULT_PASSWORD")

cognito_admin = boto3.client(
    "cognito-idp",
    region_name=AWS_REGION
)
# APP SETUP


app = FastAPI(title="NMK Certification Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db = SessionLocal()

    # create tables
    Base.metadata.create_all(bind=engine)

    # create admin
    admin_email = "admin@nmk.com"

    admin = db.query(models.User).filter(
        models.User.email == admin_email
    ).first()

    if not admin:
        admin = models.User(
            id="admin-001",
            email=admin_email,
            name="Admin",
            is_admin=True
        )
        db.add(admin)
        db.commit()

    # create sample questions
    if db.query(models.Question).count() == 0:

        samples = [
            ("What is 2 + 2?", ["1","2","3","4"], 3),
            ("What is the capital of France?", ["Berlin","Paris","Madrid","Rome"], 1),
            ("Which of these is a Python data type?", ["map","list","array","table"], 1),
        ]

        for text, choices, ans in samples:
            q = models.Question(
                text=text,
                choices=choices,
                answer_index=ans
            )
            db.add(q)

        db.commit()

    db.close()
LLM_API_URL = os.getenv("LLM_API_URL")

# LLM RESPONSE PARSER


def parse_llm_response(raw_text: str):
    import json, re

    if not raw_text:
        return []

    # Remove markdown fences
    text = re.sub(r"```json|```", "", raw_text, flags=re.IGNORECASE).strip()

    # Find array start
    start = text.find("[")
    if start == -1:
        return []

    text = text[start:]  # do NOT force closing ]

    questions = []

    # 🔥 Extract COMPLETE JSON OBJECTS ONLY
    blocks = re.findall(r"\{[^{}]*\}", text, re.DOTALL)

    for block in blocks:
        try:
            item = json.loads(block)
        except Exception:
            continue

        q = item.get("Question")
        opts = item.get("Options")
        ans = item.get("Answer")

        if not q or not opts or not ans:
            continue

        answer_index = None
        for i, opt in enumerate(opts):
            if str(opt).strip() == str(ans).strip():
                answer_index = i
                break

        if answer_index is None:
            continue

        questions.append({
            "question": q,
            "options": opts,
            "answer_index": answer_index
        })

    return questions

# =========================
# COGNITO USER SYNC
# =========================

@app.post("/auth/sync")
def sync_user(
    payload=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    email = payload.get("email")
    sub = payload.get("sub")

    user = db.query(models.User).filter(
        models.User.email == email
    ).first()

    if not user:
        # 👑 Make NMK domain users admin
        is_admin_user = email.endswith("@nmkglobalinc.com")

        user = models.User(
            id=sub,
            email=email,
            name=email.split("@")[0],
            is_admin=is_admin_user
        )

        db.add(user)
        db.commit()
        db.refresh(user)

    return {"message": "User synced"}



@app.get("/auth/me")
def get_me(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    email = current_user.get("email")

    user = db.query(models.User).filter(
        models.User.email == email
    ).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "email": user.email,
        "is_admin": user.is_admin,
        "name": user.name
    }




# ADMIN
@app.post("/admin/exams", response_model=schemas.ExamOut)
def create_exam(
    exam_data: schemas.ExamCreateIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):

    # 🔍 Fetch user from DB using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    # 🔐 Admin check
    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    TOTAL_QUESTIONS = exam_data.question_count
    BATCH_SIZE = 10
    MAX_ATTEMPTS = 30

    all_questions = []
    attempts = 0

    try:
        while len(all_questions) < TOTAL_QUESTIONS and attempts < MAX_ATTEMPTS:
            attempts += 1

            batch_count = min(BATCH_SIZE, TOTAL_QUESTIONS - len(all_questions))

            response = requests.get(
                LLM_API_URL,
                json={
                    "questionscount": batch_count,
                    "language": exam_data.language
                },
                timeout=90
            )

            if response.status_code != 200:
                continue

            batch_questions = parse_llm_response(response.text)

            if not batch_questions:
                continue

            all_questions.extend(batch_questions)

        if len(all_questions) < TOTAL_QUESTIONS:
            raise HTTPException(
                status_code=500,
                detail=f"Could only generate {len(all_questions)} questions after retries"
            )

        llm_questions = all_questions[:TOTAL_QUESTIONS]

        # 🧾 Create Exam
        new_exam = models.Exam(
            title=exam_data.title,
            language=exam_data.language,
            question_count=len(llm_questions),
            time_allowed_secs=exam_data.time_allowed_secs,
            created_by=db_user.id,   # ✅ FIXED
            is_active=True
        )

        db.add(new_exam)
        db.flush()

        for q in llm_questions:
            db.add(models.Question(
                text=q["question"],
                choices=q["options"],
                answer_index=q["answer_index"],
                exam_id=new_exam.id
            ))

        db.commit()
        db.refresh(new_exam)
        return new_exam

    except Exception as e:
        db.rollback()
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/admin/exams/{exam_id}/assign")
def assign_exam(
    exam_id: str,
    payload: schemas.ExamAssignIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔐 Admin check
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    # 🔍 Validate exam
    exam_obj = db.query(models.Exam).filter(
        models.Exam.id == exam_id
    ).first()

    if not exam_obj:
        raise HTTPException(status_code=404, detail="Exam not found")

    assigned_count = 0
    emailed_count = 0
    created_users = 0

    for email in payload.candidate_emails:
        email = email.strip().lower()
        send_password = False

        # 🔍 Check DB user
        candidate = db.query(models.User).filter(
            models.User.email == email
        ).first()

        # =========================
        # CREATE USER IF NOT EXISTS IN DB
        # =========================
        if not candidate:
            try:
                # 🔥 Try creating in Cognito
                cognito_admin.admin_create_user(
                    UserPoolId=COGNITO_USER_POOL_ID,
                    Username=email,
                    UserAttributes=[
                        {"Name": "email", "Value": email},
                        {"Name": "email_verified", "Value": "true"}
                    ],
                    MessageAction="SUPPRESS"
                )

                cognito_admin.admin_set_user_password(
                    UserPoolId=COGNITO_USER_POOL_ID,
                    Username=email,
                    Password=default_password,
                    Permanent=True
                )

                print(f"✅ Cognito user created: {email}")
                send_password = True

            except cognito_admin.exceptions.UsernameExistsException:
                print(f"⚠️ Cognito user already exists: {email}")
                send_password = False

            except Exception as e:
                print(f"❌ Cognito creation failed: {e}")
                raise HTTPException(status_code=500, detail="Cognito user creation failed")

            # 🔥 Create DB user
            candidate = models.User(
                id=email,
                email=email,
                name=email.split("@")[0],
                is_admin=False
            )

            db.add(candidate)
            db.flush()
            created_users += 1

        # =========================
        # PREVENT DUPLICATE ASSIGNMENT
        # =========================
        existing_assignment = db.query(models.ExamAssignment).filter(
            models.ExamAssignment.exam_id == exam_id,
            models.ExamAssignment.candidate_email == email
        ).first()

        if existing_assignment:
            print(f"⚠️ Already assigned: {email}")
            continue

        # =========================
        # CREATE ASSIGNMENT
        # =========================
        assignment = models.ExamAssignment(
            exam_id=exam_id,
            candidate_email=email,
            assigned_by=db_user.id, 
            status="assigned"
        )

        db.add(assignment)
        assigned_count += 1

        # =========================
        # SEND EMAIL
        # =========================
        try:
            send_exam_assignment_email(
                to_email=email,
                exam_title=exam_obj.title,
                send_password=send_password
            )
            emailed_count += 1
        except Exception as e:
            print(f"❌ Email failed for {email}: {e}")

    db.commit()

    return {
        "message": "Exam assigned successfully",
        "assigned_count": assigned_count,
        "emailed_count": emailed_count,
        "created_users": created_users
    }



@app.get("/admin/candidates/results")
def get_all_candidate_results(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    # 🔐 Admin check
    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    # Get all candidate exams
    results = []
    candidate_exams = db.query(models.CandidateExam).all()

    for ce in candidate_exams:
        user = db.query(models.User).filter(
            models.User.id == ce.user_id
        ).first()

        exam_obj = db.query(models.Exam).filter(
            models.Exam.id == ce.exam_id
        ).first()

        if user and exam_obj:
            results.append({
                "candidate_exam_id": ce.id,
                "candidate_email": user.email,
                "candidate_name": user.name,
                "exam_title": exam_obj.title,
                "exam_language": exam_obj.language,
                "status": ce.status,
                "score": ce.score if ce.status == "completed" else None,
                "started_at": ce.started_at,
                "ended_at": ce.ended_at,
                "time_elapsed": ce.time_elapsed
            })

    return results

@app.get("/admin/exams/{exam_id}/assignments")
def get_exam_assignments(
    exam_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using email from JWT
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    # 🔐 Admin check
    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    # 🔎 Get assignments
    assignments = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.exam_id == exam_id
    ).all()

    return [
        {
            "candidate_email": a.candidate_email,
            "assigned_at": a.assigned_at,
        }
        for a in assignments
    ]



# ADMIN CONTROLS


@app.get("/admin/exams")
def list_all_exams(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    # 🔐 Admin check
    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    return db.query(models.Exam).order_by(
        models.Exam.created_at.desc()
    ).all()


@app.patch("/admin/exams/{exam_id}/toggle")
def toggle_exam_status(
    exam_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    # 🔐 Admin check
    if not db_user or not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")

    exam_obj = db.query(models.Exam).filter(
        models.Exam.id == exam_id
    ).first()

    if not exam_obj:
        raise HTTPException(status_code=404, detail="Exam not found")

    exam_obj.is_active = not exam_obj.is_active
    db.commit()

    return {
        "msg": "status updated",
        "is_active": exam_obj.is_active
    }


# USER: EXAMS


@app.get("/exams")
def list_all_exams(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Get candidate email from JWT
    email = current_user.get("email")

    # Get exams assigned to this candidate
    assignments = db.query(models.ExamAssignment).filter(
        models.ExamAssignment.candidate_email == email
    ).all()

    assigned_exam_ids = [a.exam_id for a in assignments]

    # Return only active exams that are assigned to this user
    exams = db.query(models.Exam).filter(
        and_(
            models.Exam.is_active == True,
            models.Exam.id.in_(assigned_exam_ids)
        )
    ).all()

    return exams





@app.post("/exam/{exam_id}/start", response_model=schemas.CandidateExamCreateOut)
def start_exam(
    exam_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # ✅ Check if exam is assigned to this candidate
    assignment = db.query(models.ExamAssignment).filter(
        and_(
            models.ExamAssignment.exam_id == exam_id,
            models.ExamAssignment.candidate_email == db_user.email
        )
    ).first()

    if not assignment:
        raise HTTPException(status_code=403, detail="This exam is not assigned to you")

    # ✅ If already in progress, return existing
    existing = db.query(models.CandidateExam).filter(
        models.CandidateExam.user_id == db_user.id,
        models.CandidateExam.status == "in_progress"
    ).first()

    if existing:
        return existing

    # ✅ Validate exam
    exam_obj = db.query(models.Exam).filter(
        models.Exam.id == exam_id,
        models.Exam.is_active == True
    ).first()

    if not exam_obj:
        raise HTTPException(status_code=404, detail="Exam not found")

    # ✅ Get questions
    questions = db.query(models.Question).filter(
        models.Question.exam_id == exam_id
    ).all()

    if not questions:
        raise HTTPException(status_code=400, detail="No questions found")

    # ✅ Create candidate exam
    candidate_exam = models.CandidateExam(
        user_id=db_user.id,
        exam_id=exam_id,
        question_ids=[q.id for q in questions],
        answers={},
        time_allowed_secs=exam_obj.time_allowed_secs,
        time_elapsed=0,
        status="in_progress"
    )

    db.add(candidate_exam)

    # ✅ Update assignment status
    assignment.status = "started"

    db.commit()
    db.refresh(candidate_exam)

    return candidate_exam


@app.get("/exam/{candidate_exam_id}")
def get_exam(
    candidate_exam_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Fetch candidate exam
    candidate_exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.id == candidate_exam_id,
        models.CandidateExam.user_id == db_user.id
    ).first()

    if not candidate_exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # ✅ Preserve question building logic
    questions = []
    for qid in candidate_exam.question_ids or []:
        question = db.query(models.Question).filter(
            models.Question.id == qid
        ).first()

        if question:
            questions.append({
                "id": question.id,
                "text": question.text,
                "choices": question.choices
            })

    return {
        "id": candidate_exam.id,
        "questions": questions,
        "time_allowed_secs": candidate_exam.time_allowed_secs,
        "time_elapsed": candidate_exam.time_elapsed,
        "status": candidate_exam.status
    }


# SAVE ANSWER

@app.post("/exam/{candidate_exam_id}/save-answer")
def save_answer(
    candidate_exam_id: str,
    payload: schemas.AnswerIn,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Fetch candidate exam
    candidate_exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.id == candidate_exam_id,
        models.CandidateExam.user_id == db_user.id
    ).first()

    if not candidate_exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # ✅ Preserve existing logic
    answers = dict(candidate_exam.answers or {})
    answers[str(payload.question_id)] = payload.selected_index

    candidate_exam.answers = answers
    candidate_exam.time_elapsed = payload.time_elapsed

    flag_modified(candidate_exam, "answers")

    db.commit()
    db.refresh(candidate_exam)

    return {"msg": "answer_saved"}

@app.post("/exam/{candidate_exam_id}/bulk-save")
def bulk_save_answers(
    candidate_exam_id: str,
    payload: dict,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Fetch candidate exam
    candidate_exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.id == candidate_exam_id,
        models.CandidateExam.user_id == db_user.id
    ).first()

    if not candidate_exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    answers_payload = payload.get("answers", [])

    # Get existing answers dict
    answers = dict(candidate_exam.answers or {})

    for item in answers_payload:
        question_id = str(item["question_id"])
        selected_index = item["selected_index"]
        time_elapsed = item["time_elapsed"]

        answers[question_id] = selected_index
        candidate_exam.time_elapsed = time_elapsed

    candidate_exam.answers = answers
    flag_modified(candidate_exam, "answers")

    db.commit()
    db.refresh(candidate_exam)

    return {"msg": "bulk_saved"}


@app.get("/exam/resume")
def resume_exam(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Find active exam
    exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.user_id == db_user.id,
        models.CandidateExam.status == "in_progress"
    ).first()

    if not exam:
        raise HTTPException(status_code=404, detail="No active exam")

    # ✅ Build question list (existing feature preserved)
    questions = []
    for qid in exam.question_ids or []:
        q = db.query(models.Question).filter(
            models.Question.id == qid
        ).first()

        if q:
            questions.append({
                "id": q.id,
                "text": q.text,
                "choices": q.choices
            })

    return {
        "candidate_exam_id": exam.id,
        "exam_id": exam.exam_id,
        "questions": questions,
        "answers": exam.answers or {},
        "time_allowed_secs": exam.time_allowed_secs,
        "time_elapsed": exam.time_elapsed,
        "status": exam.status
    }





@app.post("/exam/{candidate_exam_id}/submit")
def submit_exam(
    candidate_exam_id: str,
    final_time_elapsed: int = Body(..., embed=True),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Fetch candidate exam for this user
    candidate_exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.id == candidate_exam_id,
        models.CandidateExam.user_id == db_user.id
    ).first()

    if not candidate_exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    # ✅ Update exam status
    candidate_exam.time_elapsed = final_time_elapsed
    candidate_exam.status = "completed"
    candidate_exam.ended_at = datetime.utcnow()

    # ✅ Compute score (existing feature preserved)
    exam.compute_score(db, candidate_exam)

    db.commit()
    db.refresh(candidate_exam)

    return {
        "msg": "exam_submitted",
        "score": candidate_exam.score,
        "status": candidate_exam.status
    }


# RESULT

@app.get("/exam/{candidate_exam_id}/result")
def get_result(
    candidate_exam_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # 🔍 Fetch DB user using Cognito email
    db_user = db.query(models.User).filter(
        models.User.email == current_user.get("email")
    ).first()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 🔎 Fetch candidate exam
    candidate_exam = db.query(models.CandidateExam).filter(
        models.CandidateExam.id == candidate_exam_id,
        models.CandidateExam.user_id == db_user.id
    ).first()

    if not candidate_exam:
        raise HTTPException(status_code=404, detail="Exam not found")

    details = []
    answers = candidate_exam.answers or {}

    for qid in candidate_exam.question_ids or []:
        question = db.query(models.Question).filter(
            models.Question.id == qid
        ).first()

        if question:
            selected = answers.get(str(qid))
            details.append({
                "question": question.text,
                "choices": question.choices,
                "selected": selected,
                "correct_index": question.answer_index,
                "is_correct": selected == question.answer_index
            })

    return {
        "score": candidate_exam.score,
        "status": candidate_exam.status,
        "details": details
    }


@app.post("/auth/change-password-admin")
def change_password_admin(
    payload: dict = Body(...),
    current_user=Depends(get_current_user),
):
    email = current_user.get("email")
    old_password = payload.get("current_password")
    new_password = payload.get("new_password")

    try:
        # 🔐 Verify old password by trying login
        cognito_admin.admin_initiate_auth(
            UserPoolId=COGNITO_USER_POOL_ID,
            ClientId=os.getenv("COGNITO_CLIENT_ID"),
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": email,
                "PASSWORD": old_password
            }
        )

        # ✅ If login works → password is correct
        cognito_admin.admin_set_user_password(
            UserPoolId=COGNITO_USER_POOL_ID,
            Username=email,
            Password=new_password,
            Permanent=True
        )

        return {"message": "Password updated successfully"}

    except cognito_admin.exceptions.NotAuthorizedException:
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

