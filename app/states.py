from aiogram.fsm.state import State, StatesGroup


class CreateTicketStates(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()
    waiting_attachments = State()
    waiting_order_number_correction = State()
    waiting_duplicate_confirmation = State()


class OrderStatusStates(StatesGroup):
    waiting_order_number = State()
    waiting_question = State()


class TicketActionStates(StatesGroup):
    waiting_comment = State()
    waiting_resolution = State()
    waiting_return_reason = State()
    waiting_cancel_reason = State()
    waiting_archive_search_query = State()


class AdminStates(StatesGroup):
    waiting_reminder_time = State()
    waiting_note_title = State()
    waiting_note_body = State()
    waiting_note_edit_title = State()
    waiting_note_edit_body = State()

class TicketFilterStates(StatesGroup):
    waiting_value = State()


class BotUpdateStates(StatesGroup):
    waiting_archive = State()
    waiting_confirmation = State()

class ProductivityStates(StatesGroup):
    waiting_active_search = State()
    waiting_summary = State()
    waiting_next_action = State()
    waiting_snooze_datetime = State()
    waiting_template_edit = State()
    waiting_template_confirm = State()


class AdminProductivityStates(StatesGroup):
    waiting_template_title = State()
    waiting_template_body = State()
    waiting_template_edit_title = State()
    waiting_template_edit_body = State()


class HelpStates(StatesGroup):
    waiting_feedback = State()
    waiting_question = State()


class AdminPollStates(StatesGroup):
    waiting_question = State()
    waiting_options = State()
    waiting_none_custom = State()
    waiting_publish = State()
