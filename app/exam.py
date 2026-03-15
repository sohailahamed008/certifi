# backend/app/exam.py
from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import and_
from .models import Question, CandidateExam
from datetime import datetime

def compute_score(db: Session, candidate_exam: CandidateExam):
    if not candidate_exam.question_ids:
        return 0
    
    correct = 0
    total = len(candidate_exam.question_ids)
    answers = candidate_exam.answers or {}
    
    print(f"\n{'='*60}")
    print(f"COMPUTING SCORE")
    print(f"{'='*60}")
    print(f"Total questions: {total}")
    print(f"Stored answers: {answers}")
    print(f"{'='*60}\n")
    
    for qid in candidate_exam.question_ids:
        q = db.query(Question).filter(Question.id == qid).first()
        if not q:
            print(f"⚠️ Question {qid} not found in database")
            continue
        
        # ✅ Convert qid to string to match storage format
        qid_str = str(qid)
        sel = answers.get(qid_str)
        
        print(f"Question: {q.text[:50]}...")
        print(f"  Question ID: {qid} → lookup key: '{qid_str}'")
        print(f"  Selected: {sel}, Correct: {q.answer_index}")
        
        if sel is None:
            print(f"  ❌ No answer recorded")
            continue
        
        if sel == q.answer_index:
            correct += 1
            print(f"  ✅ CORRECT!")
        else:
            print(f"  ❌ WRONG!")
        print("-" * 40)
    
    percent = int((correct / total) * 100) if total > 0 else 0
    candidate_exam.score = percent
    
    print(f"\n{'='*60}")
    print(f"FINAL SCORE: {correct}/{total} = {percent}%")
    print(f"{'='*60}\n")
    
    return percent