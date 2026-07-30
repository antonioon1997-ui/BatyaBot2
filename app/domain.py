from __future__ import annotations

DEPARTMENT_CLIENT = "client"
DEPARTMENT_PURCHASING = "purchasing"
DEPARTMENT_UNKNOWN = "unknown"

STATUS_NEW = "new"
STATUS_IN_WORK = "in_work"
STATUS_WAITING_ANSWER = "waiting_answer"
STATUS_WAITING_CONFIRMATION = "waiting_confirmation"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

OPEN_STATUSES = (
    STATUS_NEW,
    STATUS_IN_WORK,
    STATUS_WAITING_ANSWER,
    STATUS_WAITING_CONFIRMATION,
)
CLOSED_STATUSES = (STATUS_DONE, STATUS_CANCELLED)

PRIORITY_NORMAL = "normal"
PRIORITY_IMPORTANT = "important"
PRIORITY_URGENT = "urgent"
ALLOWED_PRIORITIES = {PRIORITY_NORMAL, PRIORITY_IMPORTANT, PRIORITY_URGENT}

CATEGORY_QUESTION = "question"
CATEGORY_TASK = "task"
CATEGORY_PROBLEM = "problem"
CATEGORY_DOCUMENTS = "documents"
ALLOWED_CATEGORIES = {
    None,
    CATEGORY_QUESTION,
    CATEGORY_TASK,
    CATEGORY_PROBLEM,
    CATEGORY_DOCUMENTS,
}

PURCHASING_ROLE_ALIASES = frozenset({
    "purchasing",
    "purchaser",
    "purchase",
    "buyer",
    "zakup",
    "zakupki",
    "закупка",
    "закупки",
    "закупщик",
})

CLIENT_ROLE_ALIASES = frozenset({
    "client",
    "customer",
    "sales",
    "manager",
    "client_department",
    "клиент",
    "клиентский",
    "клиентский отдел",
})

OBSERVER_ROLE_ALIASES = frozenset({
    "observer",
    "watcher",
    "viewer",
    "наблюдатель",
    "просмотр",
})


def normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    value = str(role).strip().lower()
    return value or None


def department_by_role(role: str | None) -> str | None:
    value = normalize_role(role)
    if value in PURCHASING_ROLE_ALIASES:
        return DEPARTMENT_PURCHASING
    if value in CLIENT_ROLE_ALIASES:
        return DEPARTMENT_CLIENT
    return None


def normalize_department(department: str | None) -> str | None:
    value = normalize_role(department)
    if value in PURCHASING_ROLE_ALIASES:
        return DEPARTMENT_PURCHASING
    if value in CLIENT_ROLE_ALIASES:
        return DEPARTMENT_CLIENT
    return value


def is_observer_role(role: str | None) -> bool:
    return normalize_role(role) in OBSERVER_ROLE_ALIASES


def opposite_department(department: str | None) -> str:
    normalized = normalize_department(department)
    if normalized == DEPARTMENT_CLIENT:
        return DEPARTMENT_PURCHASING
    if normalized == DEPARTMENT_PURCHASING:
        return DEPARTMENT_CLIENT
    return DEPARTMENT_UNKNOWN
