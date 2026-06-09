    # ── 练习统计 ──
    async def get_stats(self, user_id: int) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            today_date = today()
            total_answers = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id))).scalar() or 0
            correct_answers = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_correct == True))).scalar() or 0
            answered_questions = (await db.execute(select(func.count(func.distinct(QuizRecord.question_id))).where(QuizRecord.user_id == user_id))).scalar() or 0
            total_questions = (await db.execute(select(func.count()).select_from(QuizQuestion))).scalar() or 0
            wrong_count = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_wrong == True))).scalar() or 0
            collected_count = (await db.execute(select(func.count()).select_from(QuizRecord).where(QuizRecord.user_id == user_id, QuizRecord.is_collected == True))).scalar() or 0
            today_row = (await db.execute(select(func.count(), func.sum(func.cast(QuizRecord.is_correct, Integer))).where(QuizRecord.user_id == user_id, func.date(QuizRecord.updated_at) == today_date))).first()
            today_answers = today_row[0] if today_row else 0
            today_correct = today_row[1] if today_row and today_row[1] else 0
            checkin_status = await self.get_checkin_status(user_id)
            streak_days = checkin_status.get("consecutive_days", 0)
            total_checkin_days = (await db.execute(select(func.count()).select_from(QuizCheckin).where(QuizCheckin.user_id == user_id))).scalar() or 0
        return {"total_answers": total_answers, "correct_answers": correct_answers, "accuracy": round(correct_answers / total_answers * 100, 1) if total_answers > 0 else 0.0, "total_questions": total_questions, "answered_questions": answered_questions, "completion_rate": round(answered_questions / total_questions * 100, 1) if total_questions > 0 else 0.0, "streak_days": streak_days, "total_checkin_days": total_checkin_days, "wrong_count": wrong_count, "collected_count": collected_count, "today_answers": today_answers, "today_correct": today_correct}

    # ── 模拟考试 ──
    async def start_exam(self, user_id: int, body) -> dict[str, Any]:
        import random
        from datetime import datetime as dt, timezone as tz
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizQuestion.id)
            if body.category_id is not None: base = base.where(QuizQuestion.category_id == body.category_id)
            if body.question_type is not None: base = base.where(QuizQuestion.question_type == body.question_type)
            rows = (await db.execute(base)).all()
            ids = [r[0] for r in rows]
            if len(ids) < body.question_count: raise ValidationException(f"题库仅 {len(ids)} 题")
            sampled = random.sample(ids, body.question_count)
            now = dt.now(tz.utc)
            exam = QuizExam(user_id=user_id, question_ids=sampled, total=body.question_count, duration_seconds=body.duration_minutes * 60, started_at=now)
            db.add(exam); await db.commit(); await db.refresh(exam)
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(sampled)))).scalars().all()
        return {"exam_id": exam.id, "questions": [question_payload(q) for q in questions], "total": body.question_count, "duration_seconds": body.duration_minutes * 60, "started_at": now.isoformat()}

    async def submit_exam(self, user_id: int, body) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = await db.get(QuizExam, body.exam_id)
            if exam is None or exam.user_id != user_id: raise NotFoundException("考试记录")
            if exam.status != "in_progress": raise ValidationException("该考试已提交")
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
            q_map = {q.id: q for q in questions}
            answer_dict = {str(a.question_id): a.user_answer for a in body.answers}
            details = []; correct = wrong = 0
            for qid in exam.question_ids:
                q = q_map.get(qid)
                if q is None: continue
                ua = answer_dict.get(str(qid), "")
                if not ua: continue
                is_correct = normalize_answer(ua, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                if is_correct: correct += 1
                else: wrong += 1
                details.append({"record_id": 0, "question_id": qid, "user_answer": ua, "is_correct": is_correct, "is_wrong": not is_correct, "correct_answer": q.correct_answer, "explanation": q.explanation})
            for a in body.answers:
                q = q_map.get(a.question_id)
                if q is None: continue
                is_correct = normalize_answer(a.user_answer, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                await self._upsert_record(db, user_id=user_id, question_id=a.question_id, values={"user_answer": a.user_answer, "is_correct": is_correct, "is_wrong": not is_correct})
            total = correct + wrong
            exam.answers = answer_dict; exam.correct_count = correct; exam.wrong_count = wrong
            exam.score = round(correct / exam.total * 100, 1) if exam.total > 0 else 0
            exam.elapsed_seconds = body.elapsed_seconds; exam.submitted_at = datetime.now(timezone.utc); exam.status = "completed"
            await db.commit()
        return {"exam_id": exam.id, "total": exam.total, "correct_count": correct, "wrong_count": wrong, "unanswered_count": exam.total - len(body.answers), "score": exam.score, "accuracy": round(correct / total * 100, 1) if total > 0 else 0, "elapsed_seconds": body.elapsed_seconds, "details": details}

    async def list_exams(self, user_id: int, page: int, page_size: int) -> PaginatedData[dict[str, Any]]:
        page, page_size = normalize_page(page, page_size)
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizExam).where(QuizExam.user_id == user_id, QuizExam.status != "in_progress")
            total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
            rows = (await db.execute(base.order_by(QuizExam.id.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return PaginatedData[dict[str, Any]](items=[{"id": e.id, "total": e.total, "correct_count": e.correct_count, "score": e.score, "elapsed_seconds": e.elapsed_seconds, "duration_seconds": e.duration_seconds, "started_at": e.started_at.isoformat() if e.started_at else "", "status": e.status} for e in rows], total=total, page=page, page_size=page_size)

    async def get_exam(self, user_id: int, exam_id: int) -> dict[str, Any]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = await db.get(QuizExam, exam_id)
            if exam is None or exam.user_id != user_id: raise NotFoundException("考试记录")
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
            q_map = {q.id: q for q in questions}
            answers = exam.answers or {}
            details = []; correct = 0
            for qid in exam.question_ids:
                q = q_map.get(qid)
                if q is None: continue
                ua = answers.get(str(qid), "")
                is_correct = False
                if ua: is_correct = normalize_answer(ua, q.question_type) == normalize_answer(q.correct_answer, q.question_type)
                if is_correct: correct += 1
                details.append({"record_id": 0, "question_id": qid, "user_answer": ua, "is_correct": is_correct, "is_wrong": bool(ua) and not is_correct, "correct_answer": q.correct_answer, "explanation": q.explanation})
        total = len(exam.question_ids)
        return {"id": exam.id, "total": exam.total, "correct_count": correct, "wrong_count": total - correct, "score": round(correct / total * 100, 1) if total > 0 else 0, "accuracy": round(correct / total * 100, 1) if total > 0 else 0, "elapsed_seconds": exam.elapsed_seconds, "duration_seconds": exam.duration_seconds, "started_at": exam.started_at.isoformat() if exam.started_at else "", "submitted_at": exam.submitted_at.isoformat() if exam.submitted_at else None, "status": exam.status, "details": details}

    async def get_current_exam(self, user_id: int) -> dict[str, Any] | None:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            exam = (await db.execute(select(QuizExam).where(QuizExam.user_id == user_id, QuizExam.status == "in_progress").order_by(QuizExam.id.desc()).limit(1))).scalar_one_or_none()
            if exam is None: return None
            questions = (await db.execute(select(QuizQuestion).where(QuizQuestion.id.in_(exam.question_ids)))).scalars().all()
        return {"exam_id": exam.id, "questions": [question_payload(q) for q in questions], "answers": exam.answers or {}, "total": exam.total, "duration_seconds": exam.duration_seconds, "started_at": exam.started_at.isoformat() if exam.started_at else ""}

    # ── 分类进度 ──
    async def get_progress(self, user_id: int, category_id: int | None = None) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            base = select(QuizCategory).where(QuizCategory.parent_id.is_(None))
            if category_id is not None: base = base.where(QuizCategory.id == category_id)
            parents = (await db.execute(base)).scalars().all()
            results = []
            for p in parents:
                sub_cats = (await db.execute(select(QuizCategory).where(QuizCategory.parent_id == p.id))).scalars().all()
                sub_ids = [p.id] + [c.id for c in sub_cats]
                total = (await db.execute(select(func.count()).select_from(QuizQuestion).where(QuizQuestion.category_id.in_(sub_ids)))).scalar() or 0
                answered = (await db.execute(select(func.count(func.distinct(QuizRecord.question_id))).where(QuizRecord.user_id == user_id, QuizRecord.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids)))))).scalar() or 0
                correct = (await db.execute(select(func.count()).where(QuizRecord.user_id == user_id, QuizRecord.is_correct == True, QuizRecord.question_id.in_(select(QuizQuestion.id).where(QuizQuestion.category_id.in_(sub_ids)))))).scalar() or 0
                results.append({"category_id": p.id, "category_name": p.name, "total": total, "answered": answered, "correct": correct, "accuracy": round(correct / answered * 100, 1) if answered > 0 else 0.0})
            return results

    # ── 近期记录 ──
    async def get_recent(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with get_db_ctx() as db:
            await self._require_user(db, user_id)
            rows = (await db.execute(select(QuizRecord, QuizQuestion).join(QuizQuestion, QuizRecord.question_id == QuizQuestion.id).where(QuizRecord.user_id == user_id).order_by(QuizRecord.updated_at.desc()).limit(limit))).all()
            return [{"id": r.id, "question_id": q.id, "question_text": q.question_text, "question_type": q.question_type, "user_answer": r.user_answer, "is_correct": r.is_correct, "updated_at": r.updated_at.isoformat() if r.updated_at else None} for r, q in rows]
