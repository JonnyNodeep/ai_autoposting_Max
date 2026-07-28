from app.bot.dispatcher import UpdateDispatcher
from app.bot.handlers.channel_setup_flow import (
    FREQ_NAMES,
    TOPIC_NAMES,
    _parse_time,
    _show_slot_time_picker,
    finish_setup,
    register_setup_callback_handlers,
    register_setup_message_handlers,
)
from app.bot.handlers.channels import handle_channels_list, register_channel_handlers
from app.bot.handlers.new_plan_callbacks import _finish_plan_flow, _start_plan_flow, register_new_plan_handlers
from app.bot.handlers.start import register_start_handlers


def register_handlers(dispatcher: UpdateDispatcher) -> None:
    register_start_handlers(dispatcher)
    register_setup_message_handlers(dispatcher)
    register_channel_handlers(dispatcher)
    register_setup_callback_handlers(dispatcher)
    register_new_plan_handlers(dispatcher)
