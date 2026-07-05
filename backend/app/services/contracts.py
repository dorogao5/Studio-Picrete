GRADING_JSON_CONTRACT = """
{
  "unreadable": false,
  "unreadable_reason": null,
  "total_score": <число>,
  "max_score": <число>,
  "criteria_scores": [
    {"criterion_name": "название критерия", "score": <число>, "max_score": <число>, "comment": "комментарий"}
  ],
  "detailed_analysis": {
    "method_correctness": "анализ метода",
    "calculations": "анализ вычислений",
    "units_and_dimensions": "анализ размерностей",
    "chemical_rules": "проверка правил дисциплины",
    "errors_found": ["список ошибок"]
  },
  "feedback": "общий фидбек для студента с рекомендациями",
  "recommendations": ["рекомендация 1", "рекомендация 2"],
  "confidence": <число 0..1 — уверенность проверки>,
  "needs_teacher_review": <true|false>
}
""".strip()

GENERATION_JSON_CONTRACT = """
{
  "tasks": [
    {
      "statement": "полное условие задачи (Markdown, формулы в $...$)",
      "reference_solution": "подробное эталонное решение по шагам",
      "answer": "краткий финальный ответ (число с единицами измерения / формула / краткий текст)",
      "rubric": [
        {"criterion_name": "название критерия", "max_score": <число>, "description": "за что начисляется"}
      ],
      "max_score": <число>,
      "difficulty": "easy|medium|hard",
      "topic": "тема задачи",
      "data_used": [
        {"sheet_title": "название справочной таблицы", "values": ["какие именно значения взяты"]}
      ]
    }
  ]
}
""".strip()
